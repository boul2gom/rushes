"""Shared test helpers: context factory, state snapshot, a configurable step."""

import dataclasses
import hashlib
import io
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from fakes.clock import FakeClock
from fakes.models import FakeVad
from fakes.net import FakeHttp, FakeYtDlp
from rushes.clean.clean import CleanStep
from rushes.config import load_config
from rushes.context import Context, Filters
from rushes.db.engine import make_engine
from rushes.db.migrate import upgrade
from rushes.db.models import Assignment, Episode, Segment
from rushes.db.sync import sync_config
from rushes.fsutil import atomic_path
from rushes.ids import episode_key
from rushes.ingest.ingest import IngestStep
from rushes.media.ffmpeg import run_ffmpeg
from rushes.models.hub import ModelHub
from rushes.net.base import NetClients
from rushes.paths import Paths
from rushes.runner import BaseStep, Outcome, run_step
from rushes.schemas import AudioFile, LoudPoint, ScenesFile, Shot, write_json
from rushes.ui import make_emitter

START = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)

# Ids taken from the example config (config/): the one place to edit if they change.
ACCOUNT = "st_tt"  # cartoons brand A, TikTok
ACCOUNT_SAME_BRAND = "st_ig"  # same brand as ACCOUNT, other platform
ACCOUNT_OTHER_BRAND = "tt_tt"  # another cartoons brand
CREATOR_ACCOUNT = "ic_tt"  # creators brand C, TikTok
CREATOR_ACCOUNT_SAME_BRAND = "ic_ig"
CREATOR_ACCOUNT_OTHER_BRAND = "pm_tt"  # another creators brand
SERIES = "tom_and_jerry"
CREATOR = "squeezie"
CREATOR_OTHER = "caseoh"


def make_context(
    tmp_path: Path,
    config_dir: Path,
    *,
    clock: FakeClock | None = None,
    hub: Any = None,
    net: NetClients | None = None,
) -> Context:
    """Migrated and synced database in tmp_path; JSON events go to a StringIO (ctx_output)."""
    paths = Paths(config_dir=config_dir, data_dir=tmp_path / "data")
    clock = clock or FakeClock(START)
    config = load_config(config_dir)
    upgrade(paths.db)
    engine = make_engine(paths.db)
    sync_config(engine, config)
    ui = make_emitter(plain=False, json=True, verbose=False, stream=io.StringIO(), clock=clock.now)
    return Context(
        paths=paths,
        config=config,
        engine=engine,
        ui=ui,
        clock=clock,
        models=hub or ModelHub(paths, config.settings, factories={"vad": FakeVad}),
        net=net or NetClients(ytdlp=FakeYtDlp(), http=FakeHttp()),
    )


def with_config(ctx: Context, config_dir: Path) -> Context:
    """The same context (same database) with the config re-read from `config_dir`."""
    return dataclasses.replace(ctx, config=load_config(config_dir))


def audio_peak(path: Path, start_s: float = 0.0, length_s: float | None = None) -> float:
    """Peak amplitude (0 to 1) of the decoded mono audio, read as raw PCM (no log parsing)."""
    seek = ["-ss", f"{start_s:.3f}"] + ([] if length_s is None else ["-t", f"{length_s:.3f}"])
    with tempfile.TemporaryDirectory() as folder:
        raw = Path(folder) / "audio.raw"
        run_ffmpeg([*seek, "-i", path, "-vn", "-ac", "1", "-ar", "48000", "-f", "s16le", raw])
        samples = np.frombuffer(raw.read_bytes(), dtype="<i2")
    return float(np.abs(samples.astype(np.int32)).max() / 32768) if samples.size else 0.0


def age_file(ctx: Context, path: Path, age_s: float = 120) -> Path:
    """Set the mtime `age_s` seconds before the context clock (ingest skips files < 60 s old)."""
    moment = ctx.clock.now().timestamp() - age_s
    os.utime(path, (moment, moment))
    return path


def drop_import(
    ctx: Context, folder: str, video: Path, name: str = "video.mp4", age_s: float = 120
) -> Path:
    """Copy a video into data/import/<folder>/<name> (the cached synthetic files stay intact)."""
    target = ctx.paths.imports / folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(video, target)
    return age_file(ctx, target, age_s)


def cleaned_episode(ctx: Context, video: Path, folder: str = SERIES) -> int:
    """Ingest then clean `video` (ultrafast encode); returns the episode id."""
    drop_import(ctx, folder, video)
    assert run_step(ctx, IngestStep(), Filters()).done == 1
    report = run_step(ctx, CleanStep(preset="ultrafast"), Filters())
    failures = [e.get("message") for e in ctx_events(ctx) if e.get("status") == "failed"]
    assert (report.done, report.failed) == (1, 0), failures
    with ctx.engine.connect() as conn:
        return int(conn.execute(text("SELECT max(id) FROM episodes")).scalar_one())


def ctx_output(ctx: Context) -> str:
    stream = ctx.ui.stream  # type: ignore[attr-defined]
    return str(stream.getvalue())


def ctx_events(ctx: Context) -> list[dict[str, Any]]:
    return [json.loads(line) for line in ctx_output(ctx).splitlines() if line.strip()]


def snapshot(ctx: Context, *, include_runs: bool = False) -> dict[str, object]:
    """Every table (sorted rows) + {relative path: sha256} of data/.

    The database files, `data/logs` and (unless `include_runs`) the `runs` table are left out:
    a command always writes its own run row and log lines, which is not "work" (R16).
    """
    state: dict[str, object] = {}
    with ctx.engine.connect() as conn:
        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ).scalars()
        for name in sorted(tables):
            if name == "runs" and not include_runs:
                continue
            rows = conn.execute(text(f'SELECT * FROM "{name}"')).all()
            state[f"table:{name}"] = sorted(repr(tuple(row)) for row in rows)
    files: dict[str, str] = {}
    root = ctx.paths.data_dir
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_file() and not path.name.startswith("rushes.db") and relative.parts[0] != "logs":
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    state["files"] = files
    return state


class FakeStep(BaseStep):
    """Items [1, 2, 3]: writes data/out/<n>.txt then a runs_test row; select skips the done ones."""

    name: ClassVar[str] = "fake"

    def __init__(
        self,
        *,
        items: Iterable[int] = (1, 2, 3),
        fail: Iterable[int] = (),
        skip: Iterable[int] = (),
        crash_during_write: Iterable[int] = (),
        heavy: bool = False,
        outcomes: Mapping[int, Outcome] | None = None,
        on_run: Any = None,
    ) -> None:
        self.items = tuple(items)
        self.fail = set(fail)
        self.skip = set(skip)
        self.crash_during_write = set(crash_during_write)
        self.outcomes = dict(outcomes or {})
        self.on_run = on_run
        self.heavy = heavy  # type: ignore[misc]
        self.finished = 0
        self.ran: list[int] = []

    def key(self, item: int) -> str:
        return f"i_{item}"

    def _ensure_table(self, ctx: Context) -> None:
        with ctx.engine.begin() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS runs_test(n INTEGER PRIMARY KEY)"))

    def select(self, ctx: Context, filters: Filters) -> list[int]:
        self._ensure_table(ctx)
        if filters.force:
            return list(self.items)
        with ctx.engine.connect() as conn:
            done = set(conn.execute(text("SELECT n FROM runs_test")).scalars())
        return [i for i in self.items if i not in done]

    def run(self, ctx: Context, item: int, *, force: bool) -> Outcome:
        self.ran.append(item)
        if self.on_run is not None:
            self.on_run(item)
        if item in self.outcomes:
            return self.outcomes[item]
        if item in self.skip:
            return Outcome("skipped", "déjà fait")
        if item in self.fail:
            raise RuntimeError(f"boom {item}")
        out = ctx.paths.data_dir / "out" / f"{item}.txt"
        with atomic_path(out) as tmp:
            tmp.write_text(f"item {item}", encoding="utf-8")
            if item in self.crash_during_write:
                raise RuntimeError("killed while writing")
        with ctx.engine.begin() as conn:
            conn.execute(text("INSERT OR REPLACE INTO runs_test(n) VALUES (:n)"), {"n": item})
        return Outcome("done")

    def finish(self, ctx: Context) -> None:
        self.finished += 1


def make_episode(
    session: Session,
    *,
    owner: str = ACCOUNT,
    series_id: str | None = None,
    creator_id: str | None = None,
) -> Episode:
    """Episode owned by `owner`; a series episode by default (creator_id set -> creator episode)."""
    if series_id is None and creator_id is None:
        series_id = SERIES
    number = session.scalar(select(func.count()).select_from(Episode)) or 0
    episode = Episode(
        series_id=series_id,
        creator_id=creator_id,
        provenance="local",
        owner_account_id=owner,
        file_path=f"episodes/{number}.mp4",
        sha256=hashlib.sha256(f"episode-{number}".encode()).hexdigest(),
        duration_ms=600_000,
        status="analyzed",
    )
    session.add(episode)
    session.flush()
    return episode


def make_segment(
    session: Session,
    episode: Episode,
    kind: str,
    start_ms: int,
    end_ms: int,
    *,
    phash: list[str] | None = None,
) -> Segment:
    segment = Segment(
        episode_id=episode.id,
        kind=kind,
        start_ms=start_ms,
        end_ms=end_ms,
        family="monetizable",
        status="approved",
        phash_json=None if phash is None else json.dumps(phash),
    )
    session.add(segment)
    session.flush()
    return segment


def assign_raw(session: Session, segment: Segment, account_id: str) -> Assignment:
    """Insert an assignment without any check: sets up the state a test starts from."""
    assignment = Assignment(
        segment_id=segment.id,
        account_id=account_id,
        assigned_at=START.isoformat(timespec="seconds"),
    )
    session.add(assignment)
    session.flush()
    return assignment


def analyzed_episode(
    ctx: Context,
    *,
    duration_ms: int,
    shot_starts: Sequence[int] | None = None,
    motion: float = 0.5,
    loudness: Sequence[tuple[int, float]] = (),
    speech: Sequence[tuple[int, int]] = (),
) -> int:
    """An 'analyzed' episode with scenes.json and audio.json written directly (no ffmpeg).

    Shots start at `shot_starts` (default: every 5 s); `loudness` is (t_ms, lufs) pairs as
    stored in audio.json (t_ms = end of the 3 s window). Returns the episode id.
    """
    with Session(ctx.engine) as session:
        episode = make_episode(session)
        episode.duration_ms = duration_ms
        session.commit()
        episode_id = episode.id
    starts = list(range(0, duration_ms, 5000)) if shot_starts is None else list(shot_starts)
    ends = [*starts[1:], duration_ms]
    shots = [
        Shot(
            id=i,
            start_ms=a,
            end_ms=b,
            keyframe_ms=(a + b) // 2,
            motion=motion,
            loudness_lufs=None,
            speech_ratio=0.0,
            phash=f"{i:016x}",
        )
        for i, (a, b) in enumerate(zip(starts, ends, strict=True))
    ]
    folder = ctx.paths.episode_dir(episode_key(episode_id))
    write_json(
        ScenesFile(
            version=1, episode_id=episode_id, duration_ms=duration_ms, detector={}, shots=shots
        ),
        folder / "scenes.json",
    )
    write_json(
        AudioFile(
            version=1,
            episode_id=episode_id,
            integrated_lufs=-20.0,
            loudness=[LoudPoint(t_ms=t, lufs=lufs) for t, lufs in loudness],
            speech=list(speech),
        ),
        folder / "audio.json",
    )
    return episode_id
