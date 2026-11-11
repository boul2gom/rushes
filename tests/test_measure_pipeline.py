"""Pure parts of scripts/measure_pipeline.py (task 9): no real episode, no heavy ffmpeg."""

import importlib.util
import json
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from rushes.db.migrate import upgrade
from rushes.paths import Paths

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "measure_pipeline.py"


@pytest.fixture
def mp(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure_pipeline", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "measure_pipeline", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data")
    paths.data_dir.mkdir()
    upgrade(paths.db)
    return paths


def add_episode(db: Path, episode_id: int, duration_ms: int) -> None:
    with sqlite3.connect(db) as con:  # foreign keys are off on a raw connection
        con.execute(
            "INSERT INTO episodes(id, series_id, provenance, owner_account_id, file_path, sha256,"
            " duration_ms, status) VALUES (?, 's', 'official', 'a', ?, ?, ?, 'imported')",
            (episode_id, f"e{episode_id}.mp4", f"sha{episode_id}", duration_ms),
        )


def add_run(db: Path, command: str, reports: list[dict[str, Any]], code: int = 0) -> int:
    summary = {"reports": reports, "errors": []}
    with sqlite3.connect(db) as con:
        cursor = con.execute(
            "INSERT INTO runs(command, started_at, ended_at, status, summary_json, exit_code)"
            " VALUES (?, '2026-11-10T10:00:00', '2026-11-10T10:05:00', ?, ?, ?)",
            (command, "ok" if code == 0 else "partial", json.dumps(summary), code),
        )
        return int(cursor.lastrowid or 0)


def report(step: str, ms: int) -> dict[str, Any]:
    return {"step": step, "duration_ms": ms, "done": 1, "skipped": 0, "warned": 0, "failed": 0}


def step_line(step: str, item: str | None, status: str, ms: int) -> str:
    event = {"event": "step", "step": step, "item": item, "status": status, "duration_ms": ms}
    return json.dumps(event)


# --- units and parsing -------------------------------------------------------------------------


def test_rss_is_bytes_on_macos_and_kib_on_linux(mp: ModuleType) -> None:
    assert mp.rss_mb(512 * 1024 * 1024, "darwin") == pytest.approx(512.0)
    assert mp.rss_mb(512 * 1024, "linux") == pytest.approx(512.0)


def test_parse_events_keeps_only_json_objects(mp: ModuleType) -> None:
    text = "\n".join(["warning: not json", step_line("clean", "e_000001", "done", 10), "{broken"])
    events = mp.parse_events(text)
    assert [e["step"] for e in events] == ["clean"]


def test_seconds_per_step_come_from_the_run_summary(mp: ModuleType) -> None:
    summary = json.dumps({"reports": [report("clean", 1500), report("analyze", 60_000)]})
    assert mp.step_seconds([summary]) == {"clean": 1.5, "analyze": 60.0}
    again = json.dumps({"reports": [report("clean", 500)]})
    assert mp.step_seconds([summary, again])["clean"] == 2.0  # runs of one episode add up
    assert mp.step_seconds([None, "not json"]) == {}


def test_seconds_per_video_minute(mp: ModuleType) -> None:
    per_minute = mp.per_video_minute({"analyze": 120.0, "clean": 30.0}, video_ms=120_000)
    assert per_minute == {"analyze": 60.0, "clean": 15.0}
    assert mp.per_video_minute({"analyze": 1.0}, video_ms=0) == {}  # no division by zero


def test_render_parts_only_count_rendered_posts(mp: ModuleType) -> None:
    events = [
        json.loads(step_line("render", "p_000001", "done", 20_000)),
        json.loads(step_line("render", "p_000002", "done", 40_000)),
        json.loads(step_line("render", "p_000003", "skipped", 5)),
        json.loads(step_line("render", "p_000004", "failed", 9_000)),
        json.loads(step_line("meta", "p_000001", "done", 1_000)),
    ]
    stats = mp.render_part_stats(events, preset="medium")
    assert stats == {
        "preset": "medium",
        "parts": 2,
        "seconds_mean": 30.0,
        "seconds_max": 40.0,
        "is_medium": True,
    }
    assert mp.render_part_stats([], preset="slow")["parts"] == 0
    assert mp.render_part_stats(events, preset="faster")["is_medium"] is False


# --- SQLite ------------------------------------------------------------------------------------


def test_read_runs_and_episodes_from_the_database(mp: ModuleType, paths: Paths) -> None:
    first = add_run(paths.db, "ingest", [report("ingest", 2_000)])
    add_episode(paths.db, 1, 90_000)
    add_episode(paths.db, 2, 30_000)
    second = add_run(paths.db, "run", [report("clean", 3_000)], code=4)
    runs = mp.read_runs(paths.db, after=first)
    assert [(r["id"], r["command"], r["exit_code"]) for r in runs] == [(second, "run", 4)]
    assert mp.max_run_id(paths.db) == second
    assert mp.new_episodes(paths.db, after=1) == [(2, 30_000)]
    assert mp.max_episode_id(paths.db) == 2


def test_missing_database_reads_as_empty(mp: ModuleType, tmp_path: Path) -> None:
    absent = tmp_path / "none.db"
    assert mp.max_run_id(absent) == 0 and mp.max_episode_id(absent) == 0
    assert mp.read_runs(absent, after=0) == [] and mp.new_episodes(absent, after=0) == []


# --- the whole measurement, with a fake `rushes` ---------------------------------------


class FakeRunner:
    """Plays `rushes --json ...`: writes what the real command would, returns its events."""

    def __init__(self, paths: Paths, *, run_code: int = 0) -> None:
        self.paths = paths
        self.run_code = run_code
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> Any:
        self.calls.append(args)
        db = self.paths.db
        if args == ["ingest"]:
            add_episode(db, 1, 120_000)  # a 2-minute episode
            add_episode(db, 2, 60_000)
            add_run(db, "ingest", [report("ingest", 6_000)])
            return {"returncode": 0, "events": [], "peak_rss_mb": 150.0, "wall_s": 6.5}
        add_run(db, "run", [report("clean", 12_000), report("render", 60_000)], self.run_code)
        events = [
            json.loads(step_line("render", "p_000001", "done", 30_000)),
            json.loads(step_line("render", "p_000002", "done", 30_000)),
        ]
        peak = 900.0 if args[-1] == "e_000001" else 400.0
        return {
            "returncode": self.run_code,
            "events": events,
            "peak_rss_mb": peak,
            "cgroup_peak_mib": peak * 2,
            "cgroup_anon_mib": peak / 2,
            "wall_s": 80.0,
        }


def test_measure_runs_ingest_then_one_run_per_new_episode(mp: ModuleType, paths: Paths) -> None:
    runner = FakeRunner(paths)
    data = mp.measure(paths, runner, preset="medium")
    assert runner.calls == [
        ["ingest"],
        ["run", "--episode", "e_000001"],
        ["run", "--episode", "e_000002"],
    ]
    assert not any("--force" in call for call in runner.calls)  # first pass only
    first, second = data["episodes"]
    assert first["episode"] == "e_000001" and first["video_s"] == 120.0
    assert first["seconds_per_video_minute"] == {"clean": 6.0, "render": 30.0}
    assert second["seconds_per_video_minute"] == {"clean": 12.0, "render": 60.0}
    assert first["peak_rss_mb"] == 900.0 and second["peak_rss_mb"] == 400.0
    assert data["ingest"]["seconds_per_video_minute"] == {
        "ingest": pytest.approx(2.0)
    }  # 6 s / 3 min
    assert data["peak_rss_mb_max"] == 900.0
    assert first["cgroup_peak_mib"] == 1800.0 and data["cgroup_peak_mib_max"] == 1800.0
    assert first["cgroup_anon_mib"] == 450.0 and data["ingest"]["cgroup_anon_mib"] is None
    assert data["ingest"]["cgroup_peak_mib"] is None  # no cgroup file: null, not an error
    assert data["steps"]["clean"] == {
        "episodes": 2,
        "seconds_per_video_minute_mean": 9.0,
        "seconds_per_video_minute_max": 12.0,
    }
    assert data["render_part"]["parts"] == 4 and data["render_part"]["is_medium"] is True
    assert data["video_minutes"] == pytest.approx(3.0)


def test_measure_keeps_going_when_an_episode_run_fails(mp: ModuleType, paths: Paths) -> None:
    data = mp.measure(paths, FakeRunner(paths, run_code=4), preset="medium")
    assert [e["exit_code"] for e in data["episodes"]] == [4, 4]
    assert data["ok"] is False


def test_measure_without_new_episode_runs_nothing_more(mp: ModuleType, paths: Paths) -> None:
    class Nothing:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def __call__(self, args: list[str]) -> Any:
            self.calls.append(args)
            return {"returncode": 0, "events": [], "peak_rss_mb": 100.0, "wall_s": 1.0}

    runner = Nothing()
    data = mp.measure(paths, runner, preset="medium")
    assert runner.calls == [["ingest"]]
    assert data["episodes"] == [] and data["ok"] is False  # exit code 1: nothing was measured
    assert "aucun épisode ingéré" in data["note"] and "data/import/<série>/" in data["note"]


def test_report_is_written_under_reports_with_the_date(mp: ModuleType, paths: Paths) -> None:
    path = mp.write_report(paths, {"ok": True}, day=date(2026, 11, 10))
    assert path == paths.reports / "measures-2026-11-10.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}


def test_the_script_imports_nothing_heavy_at_the_top(mp: ModuleType) -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    top = source.split("def ", 1)[0]
    for heavy in (
        "torch",
        "numpy",
        "cv2",
        "sqlalchemy",
        "pydantic",
        "faster_whisper",
        "rushes.config",
    ):
        assert f"import {heavy}" not in top and f"from {heavy}" not in top


# --- memory, timeout, --episodes ---------------------------------------------------------------


def test_cgroup_peak_reads_memory_peak_in_mib(mp: ModuleType, tmp_path: Path) -> None:
    peak = tmp_path / "memory.peak"
    peak.write_text("1073741824\n")
    assert mp.cgroup_peak_mib(peak) == pytest.approx(1024.0)
    peak.write_text("garbage")
    assert mp.cgroup_peak_mib(peak) is None
    assert mp.cgroup_peak_mib(tmp_path / "absent") is None  # cgroup v1 or a Mac


def test_cgroup_anon_reads_memory_stat_in_mib(mp: ModuleType, tmp_path: Path) -> None:
    stat = tmp_path / "memory.stat"
    stat.write_text("file 5242880\nanon 2097152\nkernel 4096\n")
    assert mp.cgroup_anon_mib(stat) == pytest.approx(2.0)
    stat.write_text("file 5242880\n")  # no anon line
    assert mp.cgroup_anon_mib(stat) is None
    stat.write_text("anon lots\n")
    assert mp.cgroup_anon_mib(stat) is None
    assert mp.cgroup_anon_mib(tmp_path / "absent") is None


def test_run_rushes_reports_both_cgroup_figures(mp: ModuleType, tmp_path: Path) -> None:
    peak, stat = tmp_path / "memory.peak", tmp_path / "memory.stat"
    peak.write_text(str(3 * 1024 * 1024))
    stat.write_text("anon " + str(1024 * 1024))
    quick = [sys.executable, "-c", "pass"]
    result = mp.run_rushes([], argv_prefix=quick, cgroup_path=peak, cgroup_stat_path=stat)
    assert result["cgroup_peak_mib"] == 3.0 and result["cgroup_anon_mib"] == 1.0


def test_a_command_over_the_timeout_is_killed_and_reported(mp: ModuleType) -> None:
    slow = [sys.executable, "-c", "import sys, time; time.sleep(float(sys.argv[1]))"]
    result = mp.run_rushes(["30"], timeout_s=0.5, argv_prefix=slow, cgroup_path=Path("/absent"))
    assert result["timed_out"] is True
    assert result["returncode"] != 0
    assert "délai" in result["error"]


def test_the_timeout_kills_the_whole_process_group(mp: ModuleType) -> None:
    """A grandchild that keeps the output pipe open must not keep the script waiting."""
    shell = ["sh", "-c", "sleep 12 & exec sleep 12"]
    began = time.monotonic()
    result = mp.run_rushes([], timeout_s=1, argv_prefix=shell, cgroup_path=Path("/absent"))
    assert time.monotonic() - began < 4
    assert result["timed_out"] is True


def test_a_timed_out_command_makes_the_measurement_fail(mp: ModuleType, paths: Paths) -> None:
    class Hung(FakeRunner):
        def __call__(self, args: list[str]) -> Any:
            result = FakeRunner.__call__(self, args)
            if args[0] == "run":
                result.update(timed_out=True, returncode=-9, error="délai de 1 s dépassé")
            return result

    data = mp.measure(paths, Hung(paths), preset="medium")
    assert data["ok"] is False
    assert data["episodes"][0]["error"] == "délai de 1 s dépassé"


def test_parse_episode_arguments(mp: ModuleType) -> None:
    assert mp.parse_episodes(["e_000001", "12"]) == [1, 12]
    with pytest.raises(SystemExit):
        mp.parse_episodes(["s_000001"])


def test_episodes_option_skips_ingest_and_never_forces(mp: ModuleType, paths: Paths) -> None:
    add_episode(paths.db, 3, 90_000)
    runner = FakeRunner(paths)
    data = mp.measure(paths, runner, preset="medium", episodes=[3])
    assert runner.calls == [["run", "--episode", "e_000003"]]
    assert data["ingest"] is None and data["ok"] is True
    assert data["episodes"][0]["video_s"] == 90.0


def test_episodes_option_with_an_unknown_episode_fails(mp: ModuleType, paths: Paths) -> None:
    runner = FakeRunner(paths)
    data = mp.measure(paths, runner, preset="medium", episodes=[99])
    assert runner.calls == [] and data["ok"] is False
    assert "e_000099" in data["note"]


def test_the_docstring_documents_cgroup_episodes_and_the_test_profile_build(mp: ModuleType) -> None:
    doc = mp.__doc__ or ""
    assert "--episodes" in doc and "memory.peak" in doc and "--profile test build" in doc
    assert "anon" in doc and "page cache" in doc
