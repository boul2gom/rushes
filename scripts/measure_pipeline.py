r"""Phase 1 measurements (task 9): seconds per video minute for each step, peak memory, render time.

Run it inside the image, on the server, after dropping the episodes in `data/import/<series>/`.
`RUSHES_DATA_DIR` must be defined in the environment (see docker/compose.yaml); build both images
first, so that the code measured is the code of the current commit:

    docker compose -f docker/compose.yaml --profile test build \
        --build-arg RUSHES_COMMIT=$(git rev-parse --short HEAD)
    docker compose -f docker/compose.yaml run --rm -T rushes python -I scripts/measure_pipeline.py

It runs `rushes --json ingest` once, then `rushes --json run --episode <e_key>` for every episode
that ingest just created (first pass only, never `--force`); it exits 1 when ingest creates nothing.
`--episodes e_000001 e_000002` measures episodes that are already ingested instead (ingest is
skipped): steps already done are skipped by the pipeline (R16) and show about 0 s, so this is only
meaningful to finish an episode whose run failed or was interrupted.

Seconds per step come from the `runs` table (`summary_json`), per-item times from the command's
`--json` stream (the same events as `logs/<date>.jsonl`). Memory: `peak_rss_mb` is the `ru_maxrss`
of the command's process (the maximum over that process and the children it waited for, not their
sum). `cgroup_peak_mib` is `memory.peak` of the container's cgroup v2 (all processes, e.g. Python
plus ffmpeg), read after each command and CUMULATIVE since the container started; it counts the
page cache (a model read with mmap, the videos), so it is an upper bound of the memory used.
`cgroup_anon_mib` is `anon` of `memory.stat`: the processes' own memory at the moment of reading,
after the command, not a peak. Both are null when the files are absent (cgroup v1, macOS).
A command running longer than RUN_TIMEOUT_S is killed (its whole process group) and reported as
failed.
The report goes to `<data>/reports/measures-<date>.json`.
"""

import argparse
import contextlib
import json
import os
import signal
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from rushes.ids import episode_key, parse_key
from rushes.paths import Paths

RUN_TIMEOUT_S = 4 * 3600  # a real episode takes minutes; this only stops a hung command
CGROUP_PEAK = Path("/sys/fs/cgroup/memory.peak")  # cgroup v2
CGROUP_STAT = Path("/sys/fs/cgroup/memory.stat")

Runner = Callable[[list[str]], dict[str, Any]]


# --- pure helpers (unit-tested) ------------------------------------------------------------


def rss_mb(raw: int, platform: str = sys.platform) -> float:
    """ru_maxrss is in bytes on macOS and in KiB on Linux."""
    return raw / (1024 * 1024) if platform == "darwin" else raw / 1024


def cgroup_peak_mib(path: Path = CGROUP_PEAK) -> float | None:
    """`memory.peak` of the cgroup in MiB; None when the file is absent or unreadable."""
    try:
        return round(int(path.read_text(encoding="utf-8").strip()) / (1024 * 1024), 1)
    except OSError, ValueError:
        return None


def cgroup_anon_mib(path: Path = CGROUP_STAT) -> float | None:
    """`anon` of the cgroup's `memory.stat` in MiB (process memory, no page cache); else None."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(" ")
            if key == "anon":
                return round(int(value) / (1024 * 1024), 1)
    except OSError, ValueError:
        pass
    return None


def parse_episodes(values: Sequence[str]) -> list[int]:
    """`e_000045` or `45`, as the `--episode` option of rushes takes them."""
    ids = []
    for value in values:
        try:
            if value.isdigit():
                ids.append(int(value))
                continue
            kind, number = parse_key(value)
        except ValueError:
            kind, number = "", 0
        if kind != "e":
            sys.exit(f"épisode invalide : {value!r} (45 ou e_000045)")
        ids.append(number)
    return ids


def parse_events(text: str) -> list[dict[str, Any]]:
    """The JSON objects of a `--json` stream; other lines are ignored."""
    events = []
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def step_seconds(summaries: Iterable[str | None]) -> dict[str, float]:
    """Seconds per step, summed over the `summary_json` of the given runs."""
    totals: dict[str, float] = {}
    for summary in summaries:
        try:
            reports = json.loads(summary or "")["reports"]
        except ValueError, KeyError, TypeError:
            continue
        for item in reports:
            totals[item["step"]] = totals.get(item["step"], 0.0) + item["duration_ms"] / 1000
    return totals


def per_video_minute(seconds: dict[str, float], *, video_ms: int) -> dict[str, float]:
    """Seconds of work per minute of video, step by step."""
    if video_ms <= 0:
        return {}
    minutes = video_ms / 60_000
    return {step: round(value / minutes, 3) for step, value in seconds.items()}


def render_part_stats(events: Sequence[dict[str, Any]], *, preset: str) -> dict[str, Any]:
    """Time to render one part (one post), from the `render` step events that did render."""
    durations = [
        e["duration_ms"] / 1000
        for e in events
        if e.get("event") == "step" and e.get("step") == "render" and e.get("status") == "done"
    ]
    return {
        "preset": preset,
        "parts": len(durations),
        "seconds_mean": round(statistics.fmean(durations), 3) if durations else None,
        "seconds_max": round(max(durations), 3) if durations else None,
        "is_medium": preset == "medium",
    }


# --- database (stdlib only: no import of the application) ------------------------------------


def _query(db: Path, sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        return [tuple(row) for row in con.execute(sql, args)]
    except sqlite3.OperationalError:  # tables not created yet
        return []
    finally:
        con.close()


def max_run_id(db: Path) -> int:
    rows = _query(db, "SELECT max(id) FROM runs")
    return int(rows[0][0] or 0) if rows else 0


def max_episode_id(db: Path) -> int:
    rows = _query(db, "SELECT max(id) FROM episodes")
    return int(rows[0][0] or 0) if rows else 0


def read_runs(db: Path, *, after: int) -> list[dict[str, Any]]:
    rows = _query(
        db,
        "SELECT id, command, status, exit_code, summary_json FROM runs WHERE id > ? ORDER BY id",
        (after,),
    )
    keys = ("id", "command", "status", "exit_code", "summary_json")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def episode_durations(db: Path, ids: Sequence[int]) -> dict[int, int]:
    """duration_ms of the given episodes (only those that exist)."""
    rows = _query(db, "SELECT id, duration_ms FROM episodes")
    wanted = set(ids)
    return {int(i): int(d) for i, d in rows if int(i) in wanted}


def new_episodes(db: Path, *, after: int) -> list[tuple[int, int]]:
    """(id, duration_ms) of the episodes created after `after`."""
    rows = _query(db, "SELECT id, duration_ms FROM episodes WHERE id > ? ORDER BY id", (after,))
    return [(int(row[0]), int(row[1])) for row in rows]


# --- the measurement -------------------------------------------------------------------------


def run_rushes(
    args: list[str],
    *,
    timeout_s: float = RUN_TIMEOUT_S,
    argv_prefix: Sequence[str] | None = None,
    cgroup_path: Path = CGROUP_PEAK,
    cgroup_stat_path: Path = CGROUP_STAT,
) -> dict[str, Any]:
    """Run `rushes --json <args>` in its own process group; kill the group after `timeout_s`."""
    cmd = [*(argv_prefix or [sys.executable, "-m", "rushes", "--json"]), *args]
    timed_out = threading.Event()
    # own session: the whole group can be killed, grandchildren holding the output pipe included
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, start_new_session=True) as proc:
        assert proc.stdout is not None

        def kill_group() -> None:
            with contextlib.suppress(ProcessLookupError):  # already gone
                os.killpg(proc.pid, signal.SIGKILL)

        def on_timeout() -> None:
            timed_out.set()
            kill_group()

        timer = threading.Timer(timeout_s, on_timeout)
        timer.start()
        try:
            stdout = proc.stdout.read()
            # wait4 gives this child's own ru_maxrss: the maximum over the process and the
            # children it waited for, not their sum (the container's peak is the cgroup's)
            _, status, usage = os.wait4(proc.pid, 0)
        except BaseException:  # Ctrl-C: the group no longer shares our terminal, stop it here
            kill_group()
            raise
        finally:
            timer.cancel()
        proc.returncode = os.waitstatus_to_exitcode(status)
    result: dict[str, Any] = {
        "returncode": proc.returncode,
        "events": parse_events(stdout),
        "peak_rss_mb": round(rss_mb(usage.ru_maxrss), 1),
        "cgroup_peak_mib": cgroup_peak_mib(cgroup_path),
        "cgroup_anon_mib": cgroup_anon_mib(cgroup_stat_path),
        "timed_out": timed_out.is_set(),
    }
    if timed_out.is_set():
        result["error"] = f"délai de {timeout_s:g} s dépassé : commande interrompue"
    return result


def _timed_runner() -> Runner:
    def run(args: list[str]) -> dict[str, Any]:
        began = time.monotonic()
        result = run_rushes(args)
        result["wall_s"] = round(time.monotonic() - began, 1)
        return result

    return run


def _record(
    paths: Paths, runner: Runner, args: list[str], *, video_ms: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    mark = max_run_id(paths.db)
    result = runner(args)
    runs = read_runs(paths.db, after=mark)
    seconds = step_seconds(run["summary_json"] for run in runs)
    record = {
        "command": " ".join(args),
        "exit_code": result["returncode"],
        "wall_s": result.get("wall_s"),
        "peak_rss_mb": result["peak_rss_mb"],
        "cgroup_peak_mib": result.get("cgroup_peak_mib"),
        "cgroup_anon_mib": result.get("cgroup_anon_mib"),
        "error": result.get("error"),
        "run_ids": [run["id"] for run in runs],
        "step_seconds": {step: round(value, 3) for step, value in seconds.items()},
        "seconds_per_video_minute": per_video_minute(seconds, video_ms=video_ms),
    }
    return record, result["events"]


def measure(
    paths: Paths, runner: Runner, *, preset: str, episodes: Sequence[int] | None = None
) -> dict[str, Any]:
    """Ingest what was dropped, then run the pipeline once per new episode (never `--force`).

    With `episodes`, ingest is skipped and those already-ingested episodes are measured.
    """
    note = ""
    ingest: dict[str, Any] | None = None
    if episodes is None:
        episode_mark = max_episode_id(paths.db)
        ingest, _ = _record(paths, runner, ["ingest"], video_ms=0)
        created = new_episodes(paths.db, after=episode_mark)
        ingest["seconds_per_video_minute"] = per_video_minute(
            ingest["step_seconds"], video_ms=sum(duration for _, duration in created)
        )
        if not created:
            note = (
                "aucun épisode ingéré : dépose les vidéos dans "
                f"data/{paths.relative(paths.imports)}/<série>/ puis relance "
                "(un fichier modifié depuis moins de 60 s est ignoré : "
                "attends une minute après la copie)"
            )
    else:
        known = episode_durations(paths.db, episodes)
        created = [(i, known[i]) for i in episodes if i in known]
        missing = [episode_key(i) for i in episodes if i not in known]
        if missing:
            note = f"épisode(s) introuvable(s) dans la base : {', '.join(missing)}"
            created = []
    total_ms = sum(duration for _, duration in created)

    measured: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    for episode_id, duration_ms in created:
        key = episode_key(episode_id)
        record, events = _record(paths, runner, ["run", "--episode", key], video_ms=duration_ms)
        all_events.extend(events)
        measured.append({"episode": key, "video_s": duration_ms / 1000, **record})

    steps: dict[str, dict[str, Any]] = {}
    for step in dict.fromkeys(s for e in measured for s in e["seconds_per_video_minute"]):
        values = [
            e["seconds_per_video_minute"][step]
            for e in measured
            if step in e["seconds_per_video_minute"]
        ]
        steps[step] = {
            "episodes": len(values),
            "seconds_per_video_minute_mean": round(statistics.fmean(values), 3),
            "seconds_per_video_minute_max": max(values),
        }
    runs = [r for r in (ingest, *measured) if r is not None]
    cgroups = [r["cgroup_peak_mib"] for r in runs if r["cgroup_peak_mib"] is not None]
    failed = [r for r in runs if r["exit_code"] != 0 or r["error"]]
    return {
        "date": date.today().isoformat(),
        "platform": sys.platform,
        "ok": bool(created) and not failed and not note,
        "note": note,
        "video_minutes": round(total_ms / 60_000, 3),
        "ingest": ingest,
        "episodes": measured,
        "steps": steps,
        "peak_rss_mb_max": max((r["peak_rss_mb"] for r in runs), default=None),
        "cgroup_peak_mib_max": max(cgroups, default=None),
        "render_part": render_part_stats(all_events, preset=preset),
    }


def write_report(paths: Paths, data: dict[str, Any], *, day: date) -> Path:
    paths.reports.mkdir(parents=True, exist_ok=True)
    path = paths.reports / f"measures-{day.isoformat()}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mesures de la phase 1 (voir l'en-tête du script)."
    )
    parser.add_argument(
        "--episodes", nargs="+", metavar="E_KEY", help="mesurer ces épisodes déjà ingérés"
    )
    args = parser.parse_args(argv)
    episodes = parse_episodes(args.episodes) if args.episodes else None

    from rushes.config import load_config  # heavy: only when really measuring

    paths = Paths.from_env()
    preset = load_config(paths.config_dir).settings.render.preset
    data = measure(paths, _timed_runner(), preset=preset, episodes=episodes)
    path = write_report(paths, data, day=date.today())
    print(f"rapport écrit : {path}")
    if data["note"]:
        print(data["note"])
    if not data["render_part"]["is_medium"]:
        print(f"attention : preset de rendu {preset!r}, pas « medium »")
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
