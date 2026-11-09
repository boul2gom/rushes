"""`rushes run` end to end (task 8b): a synthetic episode becomes folders of outbox_test/."""

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from fakes.models import FakeVad
from fixtures.generator import Synth, SynthSpec
from helpers import snapshot
from rushes.cli import app
from rushes.context import Context
from rushes.media.verify import check_delivery
from rushes.models import hub
from rushes.package.upload import build_upload
from rushes.schemas import UploadFile, read_json

EPISODE = SynthSpec(duration_ms=180_000, size=(320, 180), fps=10)  # 3 minutes, tiny picture
FILES = ["caption.txt", "cover.jpg", "upload.json", "video.mp4"]

pytestmark = pytest.mark.slow


@pytest.fixture
def episode_run(
    cli_env: CliRunner,
    config_dir: Path,
    data_dir: Path,
    synth: Synth,
    edit_yaml: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], Any]:
    """`run()` drops the 3-minute episode in data/import/tom_and_jerry/ once and runs the pipeline.

    The cheapest settings: ultrafast encode, a fake speech detector, every account on the base
    originality (the advanced module is task 35) and no network."""
    monkeypatch.setitem(hub.DEFAULT_FACTORIES, "vad", FakeVad)
    edit_yaml(
        config_dir / "settings.yaml", lambda d: d.update(render={"preset": "ultrafast", "crf": 30})
    )

    def base(data: Any) -> None:
        for account in data["accounts"]:
            account.pop("originality", None)

    edit_yaml(config_dir / "accounts.yaml", base)
    folder = data_dir / "import" / "tom_and_jerry"
    folder.mkdir(parents=True)
    (folder / "episode.mp4").write_bytes(synth(EPISODE).read_bytes())
    os.utime(folder / "episode.mp4", (1_700_000_000, 1_700_000_000))  # not "being copied"
    return lambda: cli_env.invoke(app, ["--json", "run"])


def test_run_e2e_synthetic_episode(episode_run: Callable[[], Any], ctx: Context) -> None:
    result = episode_run()
    assert result.exit_code == 0, result.output
    with ctx.engine.connect() as conn:
        posts = conn.execute(
            text("SELECT id, account_id, status, is_test, part_number FROM posts ORDER BY id")
        ).all()
    assert len(posts) >= 2
    assert {p.status for p in posts} == {"ready"} and {p.is_test for p in posts} == {1}
    assert [p.part_number for p in posts] == list(range(1, len(posts) + 1))
    assert not ctx.paths.outbox.exists()  # tom_and_jerry has no licence (R14)
    for post in posts:
        folder = ctx.paths.outbox_test / post.account_id / f"p_{post.id:06d}"
        assert sorted(p.name for p in folder.iterdir()) == FILES
        assert check_delivery(folder / "video.mp4") == []
        upload = read_json(UploadFile, folder / "upload.json")
        assert upload.checklist[0] == "NE PAS PUBLIER : série sans licence (vitrine)"
        assert upload.caption.startswith("NE PAS PUBLIER")
        assert (upload.order_note is None) == (post.part_number == 1)
        # the same file `rushes serve` would rebuild from the row
        with Session(ctx.engine) as session:
            rebuilt = build_upload(ctx.config, ctx.paths, session, post.id)
        assert rebuilt == upload
    page = (ctx.paths.outbox_test / "INDEX.html").read_text(encoding="utf-8")
    assert all(f"p_{p.id:06d}" in page for p in posts) and "<script" not in page
    summary = [json.loads(line) for line in result.output.splitlines() if '"summary"' in line][-1]
    assert summary["index_path"].endswith("outbox_test/INDEX.html")
    steps = {
        json.loads(line).get("step") for line in result.output.splitlines() if line.startswith("{")
    }
    assert {
        "ingest",
        "clean",
        "analyze",
        "cut",
        "approve",
        "assign",
        "render",
        "meta",
        "package",
    } <= steps
    slots = [
        read_json(
            UploadFile, ctx.paths.outbox_test / p.account_id / f"p_{p.id:06d}" / "upload.json"
        ).publish_at.paris
        for p in posts
    ]
    assert slots == sorted(slots) and len(set(slots)) == len(slots)  # parts in order, one a day


def test_r16_run_twice_changes_nothing(episode_run: Callable[[], Any], ctx: Context) -> None:
    assert episode_run().exit_code == 0
    before = snapshot(ctx)
    again = episode_run()
    assert again.exit_code == 0, again.output
    assert '"event": "step"' not in again.output  # not a single item had anything to do
    assert snapshot(ctx) == before  # every table and every byte of data/ (logs and runs aside)
