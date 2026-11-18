import json
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import typer
from sqlalchemy import text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from fixtures.generator import Synth, SynthSpec
from helpers import FakeStep, analyzed_episode
from rushes import cli, steps
from rushes.cli import app
from rushes.context import Context, Filters, app_version
from rushes.errors import LockBusy
from rushes.ui import Message

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_cli_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flags live on a module-level state: start every test from the defaults."""
    for flag in ("plain", "json", "verbose"):
        monkeypatch.setattr(cli.state, flag, False)


# command -> task that implements it (a string: tasks have suffixes such as "8a").
# A task that implements a command moves it from PENDING to IMPLEMENTED.
PENDING: dict[str, str] = {
    "discover series": "38",
    "rights list": "38",
    "serve": "36",
    "market collect": "31",
    "market analyze": "31",
    "compile themes": "30",
    "hooks propose": "14",
    "queue rank": "16b",
    "stats collect": "17",
    "posts detect-published": "17",
    "creators prospect": "27",
    "creators snapshot": "27",
    "creators backfill": "28",
    "creators watch": "28",
    "creators express": "28",
    "creators revisit": "28",
    "review build": "32",
    "review apply": "32",
}
IMPLEMENTED: tuple[str, ...] = (
    "status",
    "ingest",
    "clean",
    "analyze",
    "cut",
    "approve",
    "assign",
    "render",
    "meta",
    "package",
    "run",
    "models fetch",
    "models check",
    "discover youtube",
    "scenes search",
)


def _argv(commands: list[str]) -> list[list[str]]:
    return [name.split() for name in commands]


ALL = [*PENDING, *IMPLEMENTED]


def test_help_lists_every_planned_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in {command.split()[0] for command in ALL}:
        assert name in result.output


@pytest.mark.parametrize("argv", _argv(ALL), ids=lambda a: " ".join(a))
def test_every_command_answers_help(argv: list[str]) -> None:
    result = runner.invoke(app, [*argv, "--help"])
    assert result.exit_code == 0


@pytest.mark.parametrize("command", PENDING, ids=str)
def test_unimplemented_command_exits_1_with_message(command: str) -> None:
    result = runner.invoke(app, command.split())
    assert result.exit_code == 1
    assert "non implémentée" in result.output
    assert f"tâche {PENDING[command]}" in result.output


def test_pending_and_implemented_do_not_overlap() -> None:
    assert not set(PENDING) & set(IMPLEMENTED)


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "rushes" in result.output


def test_help_works_without_config_or_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUSHES_CONFIG_DIR", str(tmp_path / "absent"))
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_config_check_accepts_the_example_config(config_dir: Path, data_dir: Path) -> None:
    result = runner.invoke(
        app,
        ["config", "check"],
        env={"RUSHES_CONFIG_DIR": str(config_dir), "RUSHES_DATA_DIR": str(data_dir)},
    )
    assert result.exit_code == 0
    assert "5 créateurs" in result.output


def test_config_check_warns_about_a_brand_without_creators(
    config_dir: Path, data_dir: Path, edit_yaml: Callable[[Path, Callable[[Any], Any]], None]
) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["creators_by_brand"].update(peak_moments=[]),
    )
    result = runner.invoke(
        app,
        ["config", "check"],
        env={"RUSHES_CONFIG_DIR": str(config_dir), "RUSHES_DATA_DIR": str(data_dir)},
    )
    assert result.exit_code == 0
    assert "peak_moments" in result.output  # warning for the empty creators list


def test_invalid_config_is_refused_with_clear_message(config_dir: Path, data_dir: Path) -> None:
    (config_dir / "accounts.yaml").write_text(
        (config_dir / "accounts.yaml")
        .read_text(encoding="utf-8")
        .replace("min_s: 65", "min_s: 30"),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["config", "check"],
        env={"RUSHES_CONFIG_DIR": str(config_dir), "RUSHES_DATA_DIR": str(data_dir)},
    )
    assert result.exit_code == 2
    assert "accounts.yaml" in result.output
    assert "min_s" in result.output
    assert "Traceback" not in result.output


def test_db_upgrade_twice(config_dir: Path, data_dir: Path) -> None:
    env = {"RUSHES_CONFIG_DIR": str(config_dir), "RUSHES_DATA_DIR": str(data_dir)}
    first = runner.invoke(app, ["db", "upgrade"], env=env)
    second = runner.invoke(app, ["db", "upgrade"], env=env)
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert (data_dir / "rushes.db").exists()


# --- task 2: pipeline plumbing -----------------------------------------------------------------


def _probe(step_names: tuple[str, ...]) -> typer.Typer:
    probe = typer.Typer()

    @probe.command()
    def go() -> None:
        cli.execute("probe", step_names, Filters())

    return probe


def _logs(data_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (data_dir / "logs").glob("*.jsonl"))


def test_execute_runs_the_registered_steps_and_exits_with_their_code(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setitem(steps.STEPS, "fake", FakeStep)
    result = cli_env.invoke(_probe(("fake",)), [])
    assert result.exit_code == 0, result.output
    assert '"event": "summary"' in result.output
    assert (data_dir / "out" / "3.txt").exists()
    again = cli_env.invoke(_probe(("fake",)), [])  # R16 through the CLI: nothing left to do
    assert again.exit_code == 0 and '"event": "step"' not in again.output


def test_execute_failed_item_exits_4(cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(steps.STEPS, "fake", lambda: FakeStep(fail={2}))
    assert cli_env.invoke(_probe(("fake",)), []).exit_code == 4


def test_execute_unknown_step_exits_1(cli_env: CliRunner) -> None:
    result = cli_env.invoke(_probe(("nope",)), [])
    assert result.exit_code == 1 and "nope" in result.output


def test_execute_rushes_error_uses_its_exit_code_and_hint(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy() -> FakeStep:
        raise LockBusy("verrou occupé", hint="Relance sans --no-wait.")

    monkeypatch.setitem(steps.STEPS, "fake", busy)
    result = cli_env.invoke(_probe(("fake",)), [])
    assert result.exit_code == 3
    assert "verrou occupé" in result.output and "Relance sans --no-wait." in result.output


def test_execute_unexpected_exception_exits_1_with_trace_only_in_the_log(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    def broken() -> FakeStep:
        raise RuntimeError("kaboom")

    monkeypatch.setitem(steps.STEPS, "fake", broken)
    result = cli_env.invoke(_probe(("fake",)), [])
    assert result.exit_code == 1
    assert "kaboom" in result.output and "Traceback" not in result.output
    assert "Traceback" in _logs(data_dir)


def test_verbose_flag_prints_the_trace(cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> FakeStep:
        raise RuntimeError("kaboom")

    monkeypatch.setitem(steps.STEPS, "fake", broken)
    monkeypatch.setattr(cli.state, "verbose", True)
    result = cli_env.invoke(_probe(("fake",)), [])
    assert result.exit_code == 1 and "Traceback" in result.output


def test_filters_from_options() -> None:
    filters = cli.make_filters(
        series="tom_and_jerry", creator=None, episode="e_000045", account="a", limit=3, force=True
    )
    assert filters == Filters(series="tom_and_jerry", episode=45, account="a", limit=3, force=True)
    assert cli.make_filters(episode="12").episode == 12
    with pytest.raises(typer.BadParameter):
        cli.make_filters(episode="s_000001")
    with pytest.raises(typer.BadParameter):
        cli.make_filters(episode="abc")


def test_status_does_not_open_a_run(cli_env: CliRunner, data_dir: Path) -> None:
    assert cli_env.invoke(app, ["status"]).exit_code == 0
    con = sqlite3.connect(data_dir / "rushes.db")
    assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    con.close()


def test_config_check_and_db_upgrade_emit_messages(cli_env: CliRunner) -> None:
    check = cli_env.invoke(app, ["--json", "config", "check"])
    messages = [json.loads(line) for line in check.output.splitlines() if line.startswith("{")]
    assert messages and all(m["event"] == "message" for m in messages)
    upgrade = cli_env.invoke(app, ["--json", "db", "upgrade"])
    assert json.loads(upgrade.output.splitlines()[-1])["event"] == "message"


def test_version_goes_through_the_emitter(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    class Recorder:
        def emit(self, event: Any) -> None:
            seen.append(event)

        def close(self) -> None:
            pass

    def fake_make_emitter(**kwargs: Any) -> Recorder:
        seen.append(kwargs)
        return Recorder()

    monkeypatch.setattr(cli, "app_version", lambda: "9.9.9")
    monkeypatch.setattr(cli, "make_emitter", fake_make_emitter)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and result.output == ""  # nothing printed outside the emitter
    assert seen == [
        {"plain": True, "json": False, "verbose": False},
        Message("info", "rushes 9.9.9"),
    ]


def test_version_prints_the_real_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"rushes {app_version()}"


def test_usage_error_inside_a_command_is_a_clean_message_not_an_unexpected_error() -> None:
    result = runner.invoke(app, ["ingest", "--episode", "abc"])
    assert result.exit_code == 1
    assert "épisode invalide" in result.output
    assert "inattendue" not in result.output and "Traceback" not in result.output


def test_unexpected_error_before_any_log_does_not_point_to_a_log_file(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "load_config", broken)
    result = cli_env.invoke(app, ["config", "check"])
    assert result.exit_code == 1 and "kaboom" in result.output
    assert "data/logs" not in result.output


def test_unexpected_error_after_the_log_is_open_points_to_it(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> FakeStep:
        raise RuntimeError("kaboom")

    monkeypatch.setitem(steps.STEPS, "fake", broken)
    result = cli_env.invoke(_probe(("fake",)), [])
    assert "data/logs" in result.output


# --- task 4: ingest and clean -------------------------------------------------------------------


def test_ingest_then_clean_commands_run_the_steps(
    cli_env: CliRunner, data_dir: Path, synth: Synth
) -> None:
    folder = data_dir / "import" / "tom_and_jerry"
    folder.mkdir(parents=True)
    video = synth(SynthSpec(duration_ms=3000, size=(64, 36), black_ms=[(0, 2500)]))
    (folder / "a.mp4").write_bytes(video.read_bytes())
    os.utime(folder / "a.mp4", (1_700_000_000, 1_700_000_000))  # not "being copied"

    ingest = cli_env.invoke(app, ["--json", "ingest"])
    assert ingest.exit_code == 0, ingest.output
    assert (
        '"step": "ingest"' in ingest.output and (data_dir / "import" / "_done" / "a.mp4").exists()
    )

    clean = cli_env.invoke(app, ["--json", "clean", "--limit", "1"])
    assert clean.exit_code == 0, clean.output
    assert '"step": "clean"' in ingest.output + clean.output
    assert list((data_dir / "episodes").glob("e_*/clean.mp4"))

    again = cli_env.invoke(app, ["--json", "clean"])  # R16 through the CLI
    assert again.exit_code == 0 and '"event": "step"' not in again.output


def test_ingest_command_with_an_unknown_folder_exits_0_with_a_warning(
    cli_env: CliRunner, data_dir: Path
) -> None:
    stray = data_dir / "import" / "nobody" / "a.mp4"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"x")
    os.utime(stray, (1_700_000_000, 1_700_000_000))
    result = cli_env.invoke(app, ["ingest"])
    assert result.exit_code == 0 and "nobody" in result.output


def test_steps_registry_has_ingest_and_clean() -> None:
    assert {"ingest", "clean"} <= set(steps.STEPS)
    assert steps.RUN_ORDER[1:3] == ("ingest", "clean")


# --- task 5: analyze ----------------------------------------------------------------------------


def test_analyze_command_runs_the_step_and_is_idempotent(
    cli_env: CliRunner, data_dir: Path, synth: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fakes.models import FakeImageEncoder, FakeVad
    from rushes.models import hub

    monkeypatch.setitem(hub.DEFAULT_FACTORIES, "vad", FakeVad)  # no real model in tests
    monkeypatch.setitem(hub.DEFAULT_FACTORIES, "clip", FakeImageEncoder)
    folder = data_dir / "import" / "tom_and_jerry"
    folder.mkdir(parents=True)
    video = synth(SynthSpec(duration_ms=3000, size=(64, 36), fps=10))
    (folder / "a.mp4").write_bytes(video.read_bytes())
    os.utime(folder / "a.mp4", (1_700_000_000, 1_700_000_000))
    assert cli_env.invoke(app, ["--json", "ingest"]).exit_code == 0
    assert cli_env.invoke(app, ["--json", "clean"]).exit_code == 0

    analyze = cli_env.invoke(app, ["--json", "analyze"])
    assert analyze.exit_code == 0, analyze.output
    assert '"step": "analyze"' in analyze.output
    assert list((data_dir / "episodes").glob("e_*/scenes.json"))
    # spec step 5: `analyze` also produces the scene vectors (the vision step follows the shots)
    assert '"step": "vision"' in analyze.output
    con = sqlite3.connect(data_dir / "rushes.db")
    scenes, vectors = con.execute(
        "SELECT (SELECT count(*) FROM scenes), (SELECT count(*) FROM scene_vectors)"
    ).fetchone()
    con.close()
    assert scenes >= 1 and vectors == scenes

    again = cli_env.invoke(app, ["--json", "analyze"])  # R16 through the CLI
    assert again.exit_code == 0 and '"event": "step"' not in again.output


# --- task 6: cut --------------------------------------------------------------------------------


def test_cut_command_runs_the_step_and_is_idempotent(cli_env: CliRunner, ctx: Context) -> None:
    analyzed_episode(ctx, duration_ms=240_000)
    first = cli_env.invoke(app, ["--json", "cut"])
    assert first.exit_code == 0, first.output
    assert '"step": "cut"' in first.output
    with ctx.engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM segments WHERE kind='part'")).scalar_one()
    assert count >= 2
    again = cli_env.invoke(app, ["--json", "cut"])  # R16 through the CLI
    assert again.exit_code == 0 and '"event": "step"' not in again.output


# --- task 8a: approve, assign, meta -------------------------------------------------------------


def test_steps_registry_runs_approve_and_assign_between_cut_and_render_then_meta() -> None:
    assert {"approve", "assign", "meta"} <= set(steps.STEPS)
    order = steps.RUN_ORDER
    assert order.index("cut") < order.index("approve") < order.index("assign")
    assert order.index("assign") < order.index("render") < order.index("meta")
    assert set(order) <= set(steps.STEPS)


def test_approve_then_assign_commands_run_the_steps_and_are_idempotent(
    cli_env: CliRunner, ctx: Context
) -> None:
    analyzed_episode(ctx, duration_ms=300_000)
    assert cli_env.invoke(app, ["--json", "cut"]).exit_code == 0
    approve = cli_env.invoke(app, ["--json", "approve"])
    assert approve.exit_code == 0, approve.output
    assert '"step": "approve"' in approve.output
    with ctx.engine.connect() as conn:
        approved = int(
            conn.execute(text("SELECT count(*) FROM segments WHERE status='approved'")).scalar_one()
        )
    assert approved >= 2
    assign = cli_env.invoke(app, ["--json", "assign"])
    assert assign.exit_code == 0, assign.output
    assert '"step": "assign"' in assign.output
    with ctx.engine.connect() as conn:
        planned = conn.execute(text("SELECT count(*) FROM posts WHERE status='planned'"))
        assert planned.scalar_one() == approved
    for command in ("approve", "assign"):  # R16 through the CLI
        again = cli_env.invoke(app, ["--json", command])
        assert again.exit_code == 0 and '"event": "step"' not in again.output


def test_meta_command_runs_the_step_and_is_idempotent(
    cli_env: CliRunner, ctx: Context, synth: Synth
) -> None:
    from helpers import post_on_video

    video = synth(SynthSpec(duration_ms=3000, size=(64, 36), fps=10))
    post_id = post_on_video(
        ctx,
        video,
        span=(500, 2500),
        status="rendered",
        layout="square",
        render_version="plate-1+ov-1",
    )
    first = cli_env.invoke(app, ["--json", "meta"])
    assert first.exit_code == 0, first.output
    assert '"step": "meta"' in first.output
    with ctx.engine.connect() as conn:
        caption = conn.execute(text("SELECT caption FROM posts WHERE id=:i"), {"i": post_id})
        assert caption.scalar_one().startswith("Tom and Jerry, Part 1.")
    again = cli_env.invoke(app, ["--json", "meta"])
    assert again.exit_code == 0 and '"event": "step"' not in again.output


def _settled(ctx: Context, post_id: int, delivery: Path) -> None:
    """Give a packable post its assignment and its plate, so that no earlier step has work."""
    from sqlalchemy.orm import Session

    from helpers import assign_raw
    from rushes.db.models import Post, Segment

    with Session(ctx.engine) as session:
        post = session.get_one(Post, post_id)
        assign_raw(session, session.get_one(Segment, post.segment_id or 0), post.account_id)
        session.commit()
    final = ctx.paths.renders / f"p_{post_id:06d}.mp4"
    plate = final.with_name(f"p_{post_id:06d}.plate.mp4")
    plate.write_bytes(delivery.read_bytes())
    os.utime(plate, (1_700_000_000, 1_700_000_000))  # older than the final: a consistent pair


# --- task 8b: package, run ----------------------------------------------------------------------


def test_run_order_starts_with_discovery_and_ends_with_package() -> None:
    assert steps.RUN_ORDER == (
        "discover_youtube", "ingest", "clean", "analyze", "vision", "cut", "approve", "assign",
        "render", "meta", "package",
    )  # fmt: skip
    assert steps.STEPS["package"].name == "package"


def test_package_command_runs_the_step_and_is_idempotent(
    cli_env: CliRunner, ctx: Context, delivery: Path
) -> None:
    from helpers import packable_post

    post_id = packable_post(ctx, delivery, test=True)
    first = cli_env.invoke(app, ["--json", "package"])
    assert first.exit_code == 0, first.output
    assert '"step": "package"' in first.output
    with ctx.engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM posts WHERE id=:i"), {"i": post_id})
        assert status.scalar_one() == "ready"
    assert (ctx.paths.outbox_test / "INDEX.html").is_file()
    again = cli_env.invoke(app, ["--json", "package"])  # R16 through the CLI
    assert again.exit_code == 0 and '"event": "step"' not in again.output


def test_run_command_chains_the_steps_and_names_the_index(
    cli_env: CliRunner,
    ctx: Context,
    delivery: Path,
    config_dir: Path,
    edit_yaml: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from helpers import packable_post

    monkeypatch.setitem(steps.STEPS, "discover_youtube", lambda: FakeStep(items=()))  # no network

    # a post that is not a test needs a licensed series (R14): the packer checks it again
    edit_yaml(config_dir / "series" / "tom_and_jerry.yaml", lambda d: d.update(licensed=True))

    post_id = packable_post(ctx, delivery)  # a post with its files already: only package has work
    _settled(ctx, post_id, delivery)
    result = cli_env.invoke(app, ["--json", "run"])
    assert result.exit_code == 0, result.output
    events = [json.loads(line) for line in result.output.splitlines() if line.startswith("{")]
    assert [e["step"] for e in events if e.get("event") == "step"] == ["package"]
    summary = events[-1]
    assert summary["event"] == "summary" and summary["ready_by_account"] == {"st_tt": 1}
    assert summary["index_path"] == str(ctx.paths.outbox / "INDEX.html")
    header = next(e for e in events if e["event"] == "header")
    assert header["command"] == "run"
    with ctx.engine.connect() as conn:
        assert conn.execute(text("SELECT command FROM runs")).scalar_one() == "run"


def test_run_summary_names_the_test_index_when_only_showcase_posts_exist(
    cli_env: CliRunner, ctx: Context, delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from helpers import packable_post

    monkeypatch.setitem(steps.STEPS, "discover_youtube", lambda: FakeStep(items=()))  # no network

    post_id = packable_post(ctx, delivery, test=True)
    _settled(ctx, post_id, delivery)
    result = cli_env.invoke(app, ["--json", "run"])
    summary = json.loads(result.output.splitlines()[-1])
    assert summary["index_path"] == str(ctx.paths.outbox_test / "INDEX.html")
    assert summary["ready_by_account"] == {}  # a showcase post is not "ready to publish"
    assert summary["showcase_by_account"] == {"st_tt": 1}


# --- models fetch / models check (task 9) -------------------------------------------------------


def _models_manifest(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """A two-model manifest of tiny files; returns the bytes behind each URL."""
    import hashlib

    from rushes.models import fetch as model_fetch

    blobs = {"https://example.test/one": b"one", "https://example.test/two": b"two!"}
    manifest = [
        model_fetch.ModelFile(
            name=name,
            file=f"{name}/w.bin",
            url=f"https://example.test/{name}",
            sha256=hashlib.sha256(data).hexdigest(),
            bytes=len(data),
            license="MIT",
        )
        for name, data in (("one", b"one"), ("two", b"two!"))
    ]
    monkeypatch.setattr(model_fetch, "load_manifest", lambda: manifest)
    return blobs


def test_models_check_exits_1_and_lists_what_is_missing(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _models_manifest(monkeypatch)
    result = cli_env.invoke(app, ["models", "check"])
    assert result.exit_code == 1
    assert "one" in result.output and "two" in result.output and "missing" in result.output
    assert "rushes models fetch" in result.output


def test_r16_models_fetch_twice_downloads_nothing_the_second_time(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fakes.net import FakeHttp

    blobs = _models_manifest(monkeypatch)
    http = FakeHttp(blobs=blobs)
    monkeypatch.setattr(cli, "make_http", lambda: http)
    first = cli_env.invoke(app, ["models", "fetch"])
    assert first.exit_code == 0, first.output
    assert "one téléchargé" in first.output and "two téléchargé" in first.output
    again = cli_env.invoke(app, ["models", "fetch"])  # nothing to download the second time
    assert again.exit_code == 0
    assert "one déjà présent" in again.output and "two déjà présent" in again.output
    assert len(http.downloads) == 2
    check = cli_env.invoke(app, ["models", "check"])
    assert check.exit_code == 0 and "ok" in check.output and "missing" not in check.output


def test_models_check_reports_a_wrong_sha256(
    cli_env: CliRunner, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _models_manifest(monkeypatch)
    target = data_dir / "cache" / "models" / "one" / "w.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"nope")
    result = cli_env.invoke(app, ["models", "check"])
    assert result.exit_code == 1 and "bad_sha256" in result.output


def test_models_fetch_only_limits_the_models(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fakes.net import FakeHttp

    http = FakeHttp(blobs=_models_manifest(monkeypatch))
    monkeypatch.setattr(cli, "make_http", lambda: http)
    result = cli_env.invoke(app, ["models", "fetch", "--only", "two"])
    assert result.exit_code == 0
    assert [url for url, _ in http.downloads] == ["https://example.test/two"]
    assert "one" not in result.output


def test_models_fetch_unknown_only_lists_the_valid_names(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fakes.net import FakeHttp

    _models_manifest(monkeypatch)
    monkeypatch.setattr(cli, "make_http", lambda: FakeHttp())
    result = cli_env.invoke(app, ["models", "fetch", "--only", "nope"])
    assert result.exit_code == 1
    assert "modèle inconnu : nope" in result.output
    assert "one" in result.output and "two" in result.output


def test_models_commands_emit_json_events(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _models_manifest(monkeypatch)
    result = cli_env.invoke(app, ["--json", "models", "check"])
    assert result.exit_code == 1
    events = [json.loads(line) for line in result.output.splitlines() if line.startswith("{")]
    assert [e["event"] for e in events] == ["table", "message"]
    assert events[1]["level"] == "error"


def test_models_fetch_network_failure_is_a_clean_error(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rushes.net.base import HttpError

    class Down:
        def download(self, url: str, dest: Path, *, max_bytes: int | None = None) -> None:
            raise HttpError("échec de la connexion à example.test", hint="Vérifie le réseau.")

    _models_manifest(monkeypatch)
    monkeypatch.setattr(cli, "make_http", lambda: Down())
    result = cli_env.invoke(app, ["models", "fetch"])
    assert result.exit_code == 5  # SourceUnavailable
    assert "échec de la connexion" in result.output and "Traceback" not in result.output


# --- task 10a: discover youtube ----------------------------------------------------------------


def test_discover_youtube_command_runs_its_step(
    cli_env: CliRunner, monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setitem(steps.STEPS, "discover_youtube", FakeStep)
    result = cli_env.invoke(app, ["--json", "discover", "youtube", "--series", "oggy", "--force"])
    assert result.exit_code == 0, result.output
    assert '"step": "fake"' in result.output
    con = sqlite3.connect(data_dir / "rushes.db")
    assert con.execute("SELECT command, exit_code FROM runs").fetchall() == [
        ("discover youtube", 0)
    ]
    con.close()


# --- task 12a: scenes search --------------------------------------------------------------------


def _indexed_scene(session: Any, episode: Any, word: str, start_ms: int = 0) -> int:
    """A scene whose vector is the one the double gives a shot labelled `word`."""
    from fakes.models import FakeImageEncoder
    from rushes.analyze.index import vector_blob
    from rushes.db.models import Scene, SceneVector

    scene = Scene(episode_id=episode.id, start_ms=start_ms, end_ms=start_ms + 1000)
    session.add(scene)
    session.flush()
    vector = FakeImageEncoder().encode_texts([word])[0]
    session.add(
        SceneVector(
            scene_id=scene.id,
            model=FakeImageEncoder.model_name,
            dim=FakeImageEncoder.dim,
            embedding=vector_blob(vector),
        )
    )
    return int(scene.id)


@pytest.fixture
def indexed(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Two episodes (two accounts, two series), three indexed scenes; CLIP is the double."""
    from fakes.models import FakeImageEncoder
    from helpers import ACCOUNT, ACCOUNT_OTHER_BRAND, make_episode
    from rushes.models import hub

    monkeypatch.setitem(hub.DEFAULT_FACTORIES, "clip", FakeImageEncoder)
    with Session(ctx.engine) as session:
        tom = make_episode(session, owner=ACCOUNT, series_id="tom_and_jerry")
        oggy = make_episode(session, owner=ACCOUNT_OTHER_BRAND, series_id="oggy")
        ids = {
            "tom_rocket": _indexed_scene(session, tom, "rocket", 5000),
            "tom_banana": _indexed_scene(session, tom, "banana"),
            "oggy_rocket": _indexed_scene(session, oggy, "rocket", 12_000),
        }
        session.commit()
    return ids


def test_scenes_search_lists_the_best_scenes(cli_env: CliRunner, indexed: dict[str, int]) -> None:
    result = cli_env.invoke(app, ["--plain", "scenes", "search", "a rocket"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[1].split() == ["Scène", "Épisode", "Début", "Cosinus"]
    rows = [line.split() for line in lines[2:] if line.strip()]
    assert [row[:2] for row in rows] == [
        [str(indexed["tom_rocket"]), "e_000001"],
        [str(indexed["oggy_rocket"]), "e_000002"],
        [str(indexed["tom_banana"]), "e_000001"],
    ]
    assert rows[0][2:] == ["5,0", "s", "1.000"]
    assert rows[1][2:] == ["12,0", "s", "1.000"]
    assert rows[2][-1] == "0.000"


def test_scenes_search_filters_by_series_and_account(
    cli_env: CliRunner, indexed: dict[str, int]
) -> None:
    by_series = cli_env.invoke(app, ["--plain", "scenes", "search", "a rocket", "--series", "oggy"])
    assert by_series.exit_code == 0, by_series.output
    assert "e_000002" in by_series.output and "e_000001" not in by_series.output
    by_account = cli_env.invoke(
        app, ["--plain", "scenes", "search", "a rocket", "--account", "st_tt"]
    )
    assert by_account.exit_code == 0, by_account.output
    assert "e_000001" in by_account.output and "e_000002" not in by_account.output
    none = cli_env.invoke(app, ["--plain", "scenes", "search", "a rocket", "--account", "nobody"])
    assert none.exit_code == 0 and "Aucune scène" in none.output


def test_scenes_search_on_an_empty_index_says_so(cli_env: CliRunner, ctx: Context) -> None:
    result = cli_env.invoke(app, ["--plain", "scenes", "search", "a rocket"])
    assert result.exit_code == 0, result.output
    assert "Aucune scène indexée" in result.output


def test_scenes_search_tells_when_only_another_model_is_indexed(
    cli_env: CliRunner, ctx: Context, indexed: dict[str, int]
) -> None:
    with ctx.engine.begin() as conn:
        conn.execute(text("UPDATE scene_vectors SET model='ViT-B-32/old'"))
    result = cli_env.invoke(app, ["--plain", "scenes", "search", "a rocket"])
    assert result.exit_code == 0, result.output
    assert "modèle actuel" in result.output and "ViT-B-32/datacomp_xl_s13b_b90k" in result.output
    assert "rushes analyze" in result.output and "e_00000" not in result.output


def test_scenes_search_holds_no_database_transaction_while_clip_works(
    cli_env: CliRunner, indexed: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    from fakes.models import FakeImageEncoder
    from rushes.models import hub

    opened: list[Session] = []

    class Tracked(Session):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            opened.append(self)

    def idle() -> bool:
        return not any(session.in_transaction() for session in opened)

    seen: list[str] = []

    class Probe(FakeImageEncoder):
        def __init__(self) -> None:  # the model load
            seen.append(f"load:{idle()}")
            super().__init__()

        def encode_texts(self, texts: Any) -> Any:
            seen.append(f"encode:{idle()}")
            return super().encode_texts(texts)

    monkeypatch.setattr(cli, "Session", Tracked)
    monkeypatch.setitem(hub.DEFAULT_FACTORIES, "clip", Probe)
    result = cli_env.invoke(app, ["--plain", "scenes", "search", "a rocket"])
    assert result.exit_code == 0, result.output
    assert seen == ["load:True", "encode:True"]


def test_scenes_search_without_the_weights_says_how_to_fetch_them(
    cli_env: CliRunner, indexed: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rushes.models import hub

    monkeypatch.delitem(hub.DEFAULT_FACTORIES, "clip")  # the real encoder, whose file is absent
    result = cli_env.invoke(app, ["--plain", "scenes", "search", "a rocket"])
    assert result.exit_code == 1
    assert "models fetch --only clip" in result.output and "Traceback" not in result.output


def test_scenes_search_exits_3_when_the_heavy_lock_is_taken(
    cli_env: CliRunner, ctx: Context, indexed: dict[str, int]
) -> None:
    from rushes.lock import heavy_lock

    with heavy_lock(ctx.paths, wait=False):
        result = cli_env.invoke(app, ["--plain", "scenes", "search", "a rocket"])
    assert result.exit_code == 3  # LockBusy: CLIP is a heavy model (R19)


def test_vision_runs_right_after_analyze() -> None:
    assert "vision" in steps.STEPS
    assert steps.RUN_ORDER.index("vision") == steps.RUN_ORDER.index("analyze") + 1
