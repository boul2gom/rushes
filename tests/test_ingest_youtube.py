"""YouTube ingestion (task 10b): stock-driven selection, download into sources/, owner balance.

No network: `CassetteYtDlp.download` copies a synthetic video the test registered, and the yt-dlp
client itself runs against a scripted `YoutubeDL`.
"""

import dataclasses
import fcntl
import io
import json
import os
import stat
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from yt_dlp.utils import DownloadError

from fakes.net import CassetteYtDlp, FakeHttp
from fixtures.generator import Synth, SynthSpec
from helpers import (
    ACCOUNT,
    ACCOUNT_SAME_BRAND,
    SERIES,
    ctx_events,
    drop_import,
    make_episode,
    make_segment,
    snapshot,
    with_config,
)
from rushes.config import IngestSettings, Settings, YoutubeSettings, load_config
from rushes.context import Context, Filters
from rushes.cut.parts import CutStep
from rushes.db.models import Episode, Post, SourceVideo
from rushes.ids import episode_key, source_key
from rushes.ingest.ingest import IngestStep
from rushes.ingest.select import select_downloads, stock_days
from rushes.net.base import NetClients
from rushes.net.ratelimit import RateLimiter
from rushes.net.ytdlp import ExtractError, YtDlpClient
from rushes.paths import Paths
from rushes.runner import StepReport, run_step
from rushes.ui import make_emitter, register_secret

TODAY = date(2026, 11, 16)  # first day of the rotation of config/accounts.yaml
OTHER_SERIES = "oggy"  # toon_tape on TODAY (accounts tt_ig, tt_tt)
SIGNIN = "Sign in to confirm you're not a bot"


def _tiny(tone: int = 440) -> SynthSpec:
    return SynthSpec(duration_ms=3000, size=(64, 36), tone_hz=tone)


@pytest.fixture
def yt(ctx: Context) -> tuple[Context, CassetteYtDlp]:
    """The context with a cassette yt-dlp whose downloads the test registers."""
    cassettes = CassetteYtDlp()
    return dataclasses.replace(ctx, net=NetClients(ytdlp=cassettes, http=FakeHttp())), cassettes


def add_video(
    ctx: Context,
    *,
    series: str | None = SERIES,
    kind: str = "official",
    status: str = "retained",
    score: float = 1.0,
    duration_ms: int | None = 600_000,
    platform: str = "youtube",
) -> int:
    """A discovered video (row of source_videos); returns its id."""
    with Session(ctx.engine) as db, db.begin():
        number = (db.scalar(text("SELECT count(*) FROM source_videos")) or 0) + 1
        video = SourceVideo(
            platform=platform,
            url=f"https://www.youtube.com/watch?v=vid{number:08d}",
            title=f"video {number}",
            kind=kind,
            series_id=series,
            duration_ms=duration_ms,
            score=score,
            status=status,
        )
        db.add(video)
        db.flush()
        return video.id


def _url(ctx: Context, video_id: int) -> str:
    with Session(ctx.engine) as db:
        return db.scalars(select(SourceVideo.url).where(SourceVideo.id == video_id)).one()


def _status(ctx: Context, video_id: int) -> str:
    with Session(ctx.engine) as db:
        return db.scalars(select(SourceVideo.status).where(SourceVideo.id == video_id)).one()


def _episodes(ctx: Context) -> list[Episode]:
    with Session(ctx.engine) as db:
        return list(db.scalars(select(Episode).order_by(Episode.id)))


def _ingest(ctx: Context, **filters: Any) -> StepReport:
    return run_step(ctx, IngestStep(), Filters(**filters))


def _plan_posts(session: Session, account: str, count: int, *, is_test: bool = False) -> None:
    """`count` unpublished posts of `account` (stock), on a throw-away part of a new episode."""
    episode = make_episode(session, owner=account)
    segment = make_segment(session, episode, "part", 0, 70_000)
    for _ in range(count):
        session.add(
            Post(account_id=account, segment_id=segment.id, status="planned", is_test=is_test)
        )
    session.commit()  # release the write lock: add_video writes through another connection


def _settings(ctx: Context, **changes: Any) -> IngestSettings:
    return ctx.config.settings.ingest.model_copy(update=changes)


def _only_oggy(config_dir: Path, edit_yaml: Callable[..., None]) -> None:
    """A period covers TODAY and gives only oggy to a brand: tom_and_jerry has no account."""
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d.update(
            rotation=[{"from": date(2026, 11, 9), "to": date(2026, 11, 29), "toon_tape": ["oggy"]}]
        ),
    )


# --- stock_days -------------------------------------------------------------------------------


def test_stock_days_counts_unpublished_posts_and_uncut_episodes(
    ctx: Context, session: Session
) -> None:
    assert stock_days(session, ctx.config, ACCOUNT) == 0
    episode = make_episode(session, owner=ACCOUNT)  # 600 s, analyzed, no part: 600/90 posts
    posts_per_day = ctx.config.accounts.posts_per_day["tiktok"]
    assert stock_days(session, ctx.config, ACCOUNT) == pytest.approx(600 / 90 / posts_per_day)
    make_segment(session, episode, "part", 0, 70_000)  # cut: the episode leaves the stock
    assert stock_days(session, ctx.config, ACCOUNT) == 0
    segment = make_segment(session, episode, "part", 70_000, 140_000)
    for status in ("planned", "rendered", "ready", "published", "discarded", "expired"):
        session.add(Post(account_id=ACCOUNT, segment_id=segment.id, status=status))
    session.flush()
    assert stock_days(session, ctx.config, ACCOUNT) == pytest.approx(3 / posts_per_day)


def test_stock_days_ignores_community_and_split_parents(ctx: Context, session: Session) -> None:
    community = make_episode(session, owner=ACCOUNT)
    community.provenance = "community"  # a signal, never cut (R15)
    parent = make_episode(session, owner=ACCOUNT)
    parent.status = "split"  # counted by its children
    session.flush()
    assert stock_days(session, ctx.config, ACCOUNT) == 0


def test_stock_days_ignores_test_posts_of_a_prospect_subject(
    ctx: Context, session: Session
) -> None:
    session.execute(text("UPDATE series SET origin='prospect' WHERE id=:s"), {"s": SERIES})
    _plan_posts(session, ACCOUNT, 3, is_test=True)  # showcase of a candidate: no brake
    assert stock_days(session, ctx.config, ACCOUNT) == 0
    _plan_posts(session, ACCOUNT, 2)  # real posts of that same subject still count
    assert stock_days(session, ctx.config, ACCOUNT) == pytest.approx(2 / 1)
    session.execute(text("UPDATE series SET origin='config' WHERE id=:s"), {"s": SERIES})
    assert stock_days(session, ctx.config, ACCOUNT) == pytest.approx(5 / 1)  # tests count in config


# --- select_downloads -------------------------------------------------------------------------


def test_select_downloads_fills_lowest_stock_first(ctx: Context, session: Session) -> None:
    # toon_tape holds about 7 days of stock: its need (11 posts) is half of saturday_toons' (22)
    _plan_posts(session, "tt_tt", 7)
    _plan_posts(session, "tt_ig", 4)
    long = 1_800_000  # one video = 20 posts
    tom_best = add_video(ctx, series=SERIES, score=0.9, duration_ms=long)
    tom_next = add_video(ctx, series=SERIES, score=0.5, duration_ms=long)
    oggy = add_video(ctx, series=OTHER_SERIES, score=0.7, duration_ms=long)
    s = _settings(ctx, max_downloads_per_run=1)
    assert select_downloads(session, ctx.config, s, today=TODAY) == [tom_best]
    s = _settings(ctx, max_downloads_per_run=6)
    # tom_best leaves 2 posts of need, below oggy's 11: oggy goes next, then tom_next
    assert select_downloads(session, ctx.config, s, today=TODAY) == [tom_best, oggy, tom_next]


def test_select_downloads_respects_max_per_run(ctx: Context, session: Session) -> None:
    for score in (0.9, 0.8, 0.7, 0.6, 0.5):
        add_video(ctx, series=SERIES, score=score, duration_ms=300_000)  # need 22 posts: 7 videos
    picked = select_downloads(
        session, ctx.config, _settings(ctx, max_downloads_per_run=2), today=TODAY
    )
    assert len(picked) == 2
    best = select_downloads(
        session, ctx.config, _settings(ctx, max_downloads_per_run=6), today=TODAY
    )
    assert len(best) == 5 and picked == best[:2]  # best scores first


def test_select_downloads_stops_when_the_stock_is_full(ctx: Context, session: Session) -> None:
    _plan_posts(session, ACCOUNT, 14)
    _plan_posts(session, ACCOUNT_SAME_BRAND, 9)  # 9 / 0.6 = 15 days
    add_video(ctx, series=SERIES, duration_ms=600_000)
    assert select_downloads(session, ctx.config, _settings(ctx), today=TODAY) == []


def test_select_downloads_takes_no_more_than_the_need(ctx: Context, session: Session) -> None:
    _plan_posts(session, ACCOUNT, 14)
    _plan_posts(session, ACCOUNT_SAME_BRAND, 6)  # need left: 14 - 10 = 4 days x 0.6 = 2.4 posts
    first = add_video(ctx, series=SERIES, score=0.9, duration_ms=300_000)  # 3.3 posts: covers it
    add_video(ctx, series=SERIES, score=0.8, duration_ms=300_000)
    assert select_downloads(session, ctx.config, _settings(ctx), today=TODAY) == [first]


def test_select_downloads_one_community_video_per_series_within_the_limit(
    ctx: Context, session: Session
) -> None:
    _plan_posts(session, ACCOUNT, 14)
    _plan_posts(session, ACCOUNT_SAME_BRAND, 9)  # no official need
    best = add_video(ctx, kind="community", score=0.9)
    add_video(ctx, kind="community", score=0.8)
    other = add_video(ctx, series=OTHER_SERIES, kind="community", score=0.4)
    s = _settings(ctx, community_max_per_series=2)
    # the series with the larger need (oggy: toon_tape is empty) comes first
    assert select_downloads(session, ctx.config, s, today=TODAY) == [other, best]
    # one community episode of the series already exists: one more is allowed, two are not
    done = add_video(ctx, kind="community", status="downloaded")
    episode = make_episode(session, owner=ACCOUNT)
    episode.provenance, episode.source_video_id = "community", done
    session.flush()
    assert select_downloads(session, ctx.config, s, today=TODAY) == [other, best]
    s = _settings(ctx, community_max_per_series=1)
    assert select_downloads(session, ctx.config, s, today=TODAY) == [other]


def test_select_downloads_community_counts_in_the_run_budget(
    ctx: Context, session: Session
) -> None:
    official = add_video(ctx, kind="official", score=0.9, duration_ms=300_000)
    add_video(ctx, kind="community", score=0.9)
    s = _settings(ctx, max_downloads_per_run=1)
    assert select_downloads(session, ctx.config, s, today=TODAY) == [official]


def test_select_downloads_retries_failed_videos_after_the_retained_ones(
    ctx: Context, session: Session
) -> None:
    failed = add_video(ctx, status="failed", score=0.95, duration_ms=300_000)
    retained = add_video(ctx, status="retained", score=0.5, duration_ms=300_000)
    add_video(ctx, status="found", score=0.99, duration_ms=300_000)
    add_video(ctx, status="downloaded", score=0.99, duration_ms=300_000)
    add_video(ctx, status="rejected", score=0.99, duration_ms=300_000)
    picked = select_downloads(session, ctx.config, _settings(ctx), today=TODAY)
    assert picked == [retained, failed]


def test_select_downloads_skips_what_it_cannot_download(ctx: Context, session: Session) -> None:
    add_video(ctx, platform="tiktok")
    add_video(ctx, duration_ms=None)
    add_video(ctx, series=None)  # no owner subject
    assert select_downloads(session, ctx.config, _settings(ctx), today=TODAY) == []


def test_select_downloads_skips_a_series_without_account_in_the_rotation(
    ctx: Context, config_dir: Path, edit_yaml: Callable[..., None], session: Session
) -> None:
    add_video(ctx, series=SERIES)
    oggy = add_video(ctx, series=OTHER_SERIES)
    _only_oggy(config_dir, edit_yaml)
    config = with_config(ctx, config_dir).config
    assert select_downloads(session, config, _settings(ctx), today=TODAY) == [oggy]


def test_select_downloads_filters_by_series(ctx: Context, session: Session) -> None:
    tom = add_video(ctx, series=SERIES)
    oggy = add_video(ctx, series=OTHER_SERIES)
    s = _settings(ctx)
    assert select_downloads(session, ctx.config, s, today=TODAY, series=OTHER_SERIES) == [oggy]
    assert select_downloads(session, ctx.config, s, today=TODAY, series=SERIES) == [tom]
    assert select_downloads(session, ctx.config, s, today=TODAY, creator="squeezie") == []


def test_ingest_settings_defaults() -> None:
    s = IngestSettings()
    assert (s.max_downloads_per_run, s.stock_days_target, s.est_post_s) == (6, 14, 90)
    assert (s.community_max_per_series, s.max_height) == (5, 1080)


# --- owner balance within one run -------------------------------------------------------------


def test_owner_balances_within_one_run(yt: tuple[Context, CassetteYtDlp], synth: Synth) -> None:
    ctx, cassettes = yt
    for tone in (400, 500, 600, 700):  # 4 distinct files of the same length: 2 accounts, 2 each
        video = add_video(ctx, score=1.0 - tone / 10_000, duration_ms=3000)
        cassettes.downloads[_url(ctx, video)] = synth(_tiny(tone))
    report = _ingest(ctx)
    assert (report.done, report.failed) == (4, 0)
    owners = [e.owner_account_id for e in _episodes(ctx)]
    assert sorted(owners) == sorted([ACCOUNT, ACCOUNT, ACCOUNT_SAME_BRAND, ACCOUNT_SAME_BRAND])


# --- download options and cookies (R18 extended, L32) -----------------------------------------


class _Ydl:
    """Scripted YoutubeDL: `outcomes` are consumed at each `extract_info(download=True)`."""

    def __init__(self, script: _Script, opts: dict[str, Any]) -> None:
        self.script, self.opts = script, opts

    def __enter__(self) -> _Ydl:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        assert download is True
        outcome = self.script.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        out = Path(self.opts["outtmpl"]["default"]).parent
        out.mkdir(parents=True, exist_ok=True)
        (out / "abc123.mp4").write_bytes(outcome)
        return {"id": "abc123"}


@dataclasses.dataclass
class _Script:
    outcomes: list[bytes | Exception]
    options: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    cookie_modes: list[int | None] = dataclasses.field(default_factory=list)

    def factory(self, opts: dict[str, Any]) -> _Ydl:
        self.options.append(dict(opts))
        cookiefile = opts.get("cookiefile")
        self.cookie_modes.append(
            None if cookiefile is None else stat.S_IMODE(Path(cookiefile).stat().st_mode)
        )
        return _Ydl(self, opts)


@dataclasses.dataclass
class _Rig:
    client: YtDlpClient
    script: _Script
    paths: Paths
    stream: io.StringIO
    out_dir: Path

    def alerts(self) -> list[str]:
        lines = [json.loads(x) for x in self.stream.getvalue().splitlines() if x.strip()]
        return [m["alert"] for m in lines if m.get("event") == "message" and m.get("alert")]


@pytest.fixture
def make_rig(tmp_path: Path, config_dir: Path) -> Iterator[Callable[..., _Rig]]:
    streams: list[Any] = []

    def build(
        outcomes: list[bytes | Exception], *, cookies: str = "fallback", has_file: bool = True
    ) -> _Rig:
        settings: Settings = load_config(config_dir).settings.model_copy(
            update={"youtube": YoutubeSettings(cookies=cookies)}  # type: ignore[arg-type]
        )
        paths = Paths(config_dir=config_dir, data_dir=tmp_path / "data")
        if has_file:
            paths.cookies_youtube.parent.mkdir(parents=True)
            paths.cookies_youtube.write_text(
                "# Netscape HTTP Cookie File\n"
                ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tSECRETVALUE9\n",
                encoding="utf-8",
            )
        stream = io.StringIO()
        ui = make_emitter(plain=False, json=True, verbose=True, stream=stream)
        streams.append(ui)
        script = _Script(outcomes)
        limiter = RateLimiter(settings.rate_limit_s, monotonic=lambda: 0.0, sleep=lambda s: None)
        client = YtDlpClient(settings, paths, ui, ydl_factory=script.factory, limiter=limiter)
        return _Rig(client, script, paths, stream, paths.tmp / "dl-test")

    yield build


URL = "https://www.youtube.com/watch?v=abc123"


def test_download_uses_separate_options_from_collection(
    make_rig: Callable[..., _Rig],
) -> None:
    rig = make_rig([b"video"], has_file=False)
    path = rig.client.download(URL, rig.out_dir, max_height=720)
    assert path == rig.out_dir / "abc123.mp4" and path.read_bytes() == b"video"
    (o,) = rig.script.options
    h = 720
    assert o["format"] == (
        f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]"
    )
    assert o["merge_output_format"] == "mp4"
    assert o["outtmpl"] == {"default": str(rig.out_dir / "%(id)s.%(ext)s")}
    assert o["noplaylist"] is True and o["concurrent_fragment_downloads"] == 1
    assert o["sleep_interval_requests"] == 2  # as the collection
    assert not o.get("skip_download")  # unlike the collection, the media is fetched
    assert "extract_flat" not in o and "cookiefile" not in o and "getcomments" not in o


def test_r18_download_falls_back_to_cookies_with_alert(make_rig: Callable[..., _Rig]) -> None:
    rig = make_rig([DownloadError(SIGNIN), b"video"])
    stale = rig.out_dir / "abc123.mp4.part"  # what a refused first attempt may leave behind
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    path = rig.client.download(URL, rig.out_dir, max_height=1080)
    assert path.read_bytes() == b"video"
    assert not stale.exists()
    first, second = rig.script.options
    assert "cookiefile" not in first
    assert Path(second["cookiefile"]) != rig.paths.cookies_youtube
    assert rig.script.cookie_modes == [None, 0o600]
    assert not Path(second["cookiefile"]).exists()  # the working copy is gone
    assert rig.alerts() == ["youtube_signin"]


def test_r18_download_in_always_mode_uses_cookies_from_the_start(
    make_rig: Callable[..., _Rig],
) -> None:
    rig = make_rig([b"video"], cookies="always")
    rig.client.download(URL, rig.out_dir, max_height=1080)
    assert rig.script.cookie_modes == [0o600] and rig.alerts() == ["youtube_signin"]


def test_r18_download_mode_never_fails_without_cookies(make_rig: Callable[..., _Rig]) -> None:
    rig = make_rig([DownloadError(SIGNIN), b"video"], cookies="never")
    with pytest.raises(ExtractError) as info:
        rig.client.download(URL, rig.out_dir, max_height=1080)
    assert info.value.signin is True
    assert rig.script.cookie_modes == [None] and rig.alerts() == []


def test_r18_download_cookies_refused_alerts_cookies_expired(make_rig: Callable[..., _Rig]) -> None:
    rig = make_rig([DownloadError(SIGNIN), DownloadError(SIGNIN)])
    with pytest.raises(ExtractError):
        rig.client.download(URL, rig.out_dir, max_height=1080)
    assert len(rig.script.options) == 2  # one retry, never a third
    assert rig.alerts().count("cookies_expired") == 1


def test_r47_download_error_is_not_retried_and_a_registered_secret_is_redacted(
    make_rig: Callable[..., _Rig],
) -> None:
    register_secret("SECRETVALUE9")
    rig = make_rig([DownloadError("HTTP 403 SECRETVALUE9 forbidden")])
    with pytest.raises(ExtractError) as info:
        rig.client.download(URL, rig.out_dir, max_height=1080)
    assert info.value.signin is False and len(rig.script.options) == 1
    assert "403" in str(info.value) and "SECRETVALUE9" not in str(info.value)


def test_r18_refused_cookies_are_not_tried_again_by_the_same_client(
    make_rig: Callable[..., _Rig],
) -> None:
    wall = DownloadError(SIGNIN)
    rig = make_rig([wall, wall, wall, wall])
    for _ in range(2):  # two downloads of one run, the cookies are expired
        with pytest.raises(ExtractError) as info:
            rig.client.download(URL, rig.out_dir, max_height=1080)
        assert info.value.signin is True
    # download 1: bare, then cookies (refused); download 2: bare only
    assert rig.script.cookie_modes == [None, 0o600, None]
    assert rig.alerts().count("cookies_expired") == 1


def test_r18_always_mode_stops_sending_cookies_once_they_are_refused(
    make_rig: Callable[..., _Rig],
) -> None:
    wall = DownloadError(SIGNIN)
    rig = make_rig([wall, wall], cookies="always")
    for _ in range(2):
        with pytest.raises(ExtractError):
            rig.client.download(URL, rig.out_dir, max_height=1080)
    assert rig.script.cookie_modes == [0o600, None]
    assert rig.alerts().count("cookies_expired") == 1


def test_r18_missing_cookie_file_is_remembered_too(make_rig: Callable[..., _Rig]) -> None:
    rig = make_rig([DownloadError(SIGNIN), DownloadError(SIGNIN)], has_file=False)
    for _ in range(2):
        with pytest.raises(ExtractError):
            rig.client.download(URL, rig.out_dir, max_height=1080)
    assert len(rig.script.options) == 2 and rig.alerts() == ["cookies_expired"]


def test_download_without_a_file_is_an_error(make_rig: Callable[..., _Rig]) -> None:
    rig = make_rig([b"video"])
    rig.script.factory = lambda opts: _Empty()  # type: ignore[method-assign]
    rig.client._factory = rig.script.factory
    with pytest.raises(ExtractError):
        rig.client.download(URL, rig.out_dir, max_height=1080)


class _Empty:
    def __enter__(self) -> _Empty:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        return {"id": "abc123"}  # yt-dlp said nothing and wrote nothing


# --- IngestStep: download and episode ---------------------------------------------------------


def test_ingest_youtube_downloads_and_creates_episode(
    yt: tuple[Context, CassetteYtDlp], synth: Synth
) -> None:
    ctx, cassettes = yt
    video = add_video(ctx, kind="official", duration_ms=3000)
    cassettes.downloads[_url(ctx, video)] = synth(_tiny())
    report = _ingest(ctx)
    assert (report.done, report.skipped, report.warned, report.failed) == (1, 0, 0, 0)

    (episode,) = _episodes(ctx)
    assert episode.provenance == "official" and episode.status == "imported"
    assert episode.source_video_id == video
    assert (episode.series_id, episode.creator_id) == (SERIES, None)
    assert episode.file_path == f"sources/{source_key(video)}.mp4" == "sources/v_000001.mp4"
    assert (ctx.paths.data_dir / episode.file_path).is_file()
    assert abs(episode.duration_ms - 3000) <= 60
    assert episode.owner_account_id == ACCOUNT_SAME_BRAND  # both at 0: the smallest id
    assert _status(ctx, video) == "downloaded"
    assert cassettes.calls[-1][1] == {"download": True, "max_height": 1080}
    assert not (ctx.paths.tmp / f"dl-{os.getpid()}").exists()  # temporary folder gone
    events = [e for e in ctx_events(ctx) if e.get("event") == "step"]
    assert events[0]["item"] == "v_000001" and events[0]["status"] == "done"
    assert episode_key(episode.id) in events[0]["message"]


def test_ingest_youtube_community_video_makes_a_community_episode(
    yt: tuple[Context, CassetteYtDlp], synth: Synth
) -> None:
    ctx, cassettes = yt
    video = add_video(ctx, kind="community", duration_ms=3000)
    cassettes.downloads[_url(ctx, video)] = synth(_tiny())
    assert _ingest(ctx).done == 1
    (episode,) = _episodes(ctx)
    assert episode.provenance == "community" and episode.source_video_id == video


def test_failed_download_leaves_no_file_and_marks_failed(
    yt: tuple[Context, CassetteYtDlp], synth: Synth
) -> None:
    ctx, cassettes = yt
    video = add_video(ctx)
    cassettes.downloads[_url(ctx, video)] = ExtractError("YouTube exige une connexion", signin=True)
    report = _ingest(ctx)
    assert (report.done, report.failed, report.unavailable) == (0, 1, 1)
    assert _status(ctx, video) == "failed"
    assert _episodes(ctx) == []
    assert not ctx.paths.sources.exists() or not list(ctx.paths.sources.iterdir())
    assert not ctx.paths.tmp.exists() or not list(ctx.paths.tmp.iterdir())
    # the next run tries again (mode never, a transient error...) and succeeds this time
    cassettes.downloads[_url(ctx, video)] = synth(_tiny())
    assert _ingest(ctx).done == 1
    assert _status(ctx, video) == "downloaded"


def test_download_that_is_not_a_video_is_removed_and_marked_failed(
    yt: tuple[Context, CassetteYtDlp], tmp_path: Path
) -> None:
    ctx, cassettes = yt
    video = add_video(ctx)
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    cassettes.downloads[_url(ctx, video)] = junk
    report = _ingest(ctx)
    assert (report.done, report.failed) == (0, 1)
    assert _status(ctx, video) == "failed" and _episodes(ctx) == []
    assert not ctx.paths.sources.exists() or not list(ctx.paths.sources.iterdir())
    assert not ctx.paths.tmp.exists() or not list(ctx.paths.tmp.iterdir())


def test_a_download_of_known_content_is_dropped_not_duplicated(
    yt: tuple[Context, CassetteYtDlp], synth: Synth
) -> None:
    ctx, cassettes = yt
    drop_import(ctx, SERIES, synth(_tiny()))
    assert _ingest(ctx).done == 1  # the same file, imported by hand
    video = add_video(ctx)
    cassettes.downloads[_url(ctx, video)] = synth(_tiny())
    report = _ingest(ctx)
    assert (report.done, report.skipped, report.failed) == (0, 1, 0)
    assert len(_episodes(ctx)) == 1 and _status(ctx, video) == "rejected"
    assert not (ctx.paths.sources / "v_000001.mp4").exists()


def test_a_file_left_by_a_crash_before_the_database_is_reused(
    yt: tuple[Context, CassetteYtDlp], synth: Synth
) -> None:
    ctx, cassettes = yt
    video = add_video(ctx)
    ctx.paths.sources.mkdir(parents=True)
    leftover = ctx.paths.sources / f"{source_key(video)}.mp4"
    leftover.write_bytes(synth(_tiny()).read_bytes())  # renamed, then the process died
    assert _ingest(ctx).done == 1  # no download registered: none attempted
    assert cassettes.calls == []
    assert _status(ctx, video) == "downloaded" and len(_episodes(ctx)) == 1


def test_r16_ingest_youtube_rerun_changes_nothing(
    yt: tuple[Context, CassetteYtDlp], synth: Synth
) -> None:
    ctx, cassettes = yt
    video = add_video(ctx, duration_ms=3000)
    cassettes.downloads[_url(ctx, video)] = synth(_tiny())
    assert _ingest(ctx).done == 1
    before = snapshot(ctx)
    calls = len(cassettes.calls)
    report = _ingest(ctx)
    assert (report.done, report.skipped, report.warned, report.failed) == (0, 0, 0, 0)
    assert snapshot(ctx) == before and len(cassettes.calls) == calls


def test_ingest_youtube_filters(yt: tuple[Context, CassetteYtDlp], synth: Synth) -> None:
    ctx, cassettes = yt
    tom = add_video(ctx, series=SERIES, duration_ms=3000)
    oggy = add_video(ctx, series=OTHER_SERIES, duration_ms=3000)
    cassettes.downloads[_url(ctx, tom)] = synth(_tiny(450))
    cassettes.downloads[_url(ctx, oggy)] = synth(_tiny(550))
    assert _ingest(ctx, episode=1).done == 0  # not ingested yet: no episode, no owner
    assert _ingest(ctx, account=ACCOUNT).done == 0
    assert _ingest(ctx, creator="squeezie").done == 0
    assert _ingest(ctx, series=OTHER_SERIES).done == 1
    assert (_status(ctx, tom), _status(ctx, oggy)) == ("retained", "downloaded")
    assert _ingest(ctx).done == 1 and _status(ctx, tom) == "downloaded"


def test_ingest_imports_files_before_downloads(
    yt: tuple[Context, CassetteYtDlp], synth: Synth
) -> None:
    ctx, cassettes = yt
    video = add_video(ctx, duration_ms=3000)
    cassettes.downloads[_url(ctx, video)] = synth(_tiny(450))
    drop_import(ctx, SERIES, synth(_tiny(550)))
    assert _ingest(ctx).done == 2
    items = [e["item"] for e in ctx_events(ctx) if e.get("event") == "step"]
    assert items == ["tom_and_jerry/video.mp4", "v_000001"]


def test_ingest_youtube_without_owner_downloads_nothing(
    yt: tuple[Context, CassetteYtDlp], config_dir: Path, edit_yaml: Callable[..., None]
) -> None:
    ctx, cassettes = yt
    add_video(ctx, series=SERIES)
    _only_oggy(config_dir, edit_yaml)
    ctx = with_config(ctx, config_dir)
    assert _ingest(ctx).done == 0 and cassettes.calls == []


# --- review fixes (I1, M2, M3, M4) --------------------------------------------------------------


def _without_instagram_rate(config_dir: Path, edit_yaml: Callable[..., None]) -> None:
    edit_yaml(config_dir / "accounts.yaml", lambda d: d["posts_per_day"].pop("instagram"))


def test_account_on_a_platform_without_posts_per_day_does_not_break_ingestion(
    ctx: Context,
    config_dir: Path,
    edit_yaml: Callable[..., None],
    synth: Synth,
    session: Session,
) -> None:
    _without_instagram_rate(config_dir, edit_yaml)
    context = with_config(ctx, config_dir)
    assert stock_days(session, context.config, ACCOUNT_SAME_BRAND) == float("inf")
    assert stock_days(session, context.config, ACCOUNT) == 0
    # owner choice does not crash and avoids the account that posts nothing
    drop_import(context, SERIES, synth(_tiny()))
    assert _ingest(context).done == 1
    (episode,) = _episodes(context)
    assert episode.owner_account_id == ACCOUNT
    # no need on that platform: only the tiktok account asks for posts (14 of them)
    add_video(context, series=SERIES, duration_ms=3_600_000)  # 40 posts
    add_video(context, series=SERIES, duration_ms=3_600_000)
    s = _settings(context)
    assert len(select_downloads(session, context.config, s, today=TODAY)) == 1


def test_stock_days_takes_est_post_s_from_the_caller(ctx: Context, session: Session) -> None:
    make_episode(session, owner=ACCOUNT)  # 600 s, not cut
    assert stock_days(session, ctx.config, ACCOUNT) == pytest.approx(600 / 90)
    assert stock_days(session, ctx.config, ACCOUNT, est_post_s=30) == pytest.approx(600 / 30)


def test_select_downloads_uses_est_post_s_of_its_settings_for_the_stock(
    ctx: Context, session: Session
) -> None:
    _plan_posts(session, ACCOUNT, 8)  # tom_and_jerry needs 6 + 3.4 posts
    _plan_posts(session, ACCOUNT_SAME_BRAND, 5)
    uncut = make_episode(session, owner="tt_tt", series_id=OTHER_SERIES)  # 600 s, not cut
    uncut.duration_ms = 600_000
    session.commit()
    tom = add_video(ctx, series=SERIES, duration_ms=3_600_000)
    oggy = add_video(ctx, series=OTHER_SERIES, duration_ms=3_600_000)
    one = {"max_downloads_per_run": 1}
    # at 90 s a post the uncut episode is 6.7 posts: oggy needs 15.7, more than tom (9.4)
    assert select_downloads(session, ctx.config, _settings(ctx, **one), today=TODAY) == [oggy]
    # at 10 s it is 60 posts: oggy only needs 8.4, tom comes first
    s = _settings(ctx, est_post_s=10, **one)
    assert select_downloads(session, ctx.config, s, today=TODAY) == [tom]


def test_select_downloads_counts_a_split_community_video_once(
    ctx: Context, session: Session
) -> None:
    _plan_posts(session, ACCOUNT, 14)
    _plan_posts(session, ACCOUNT_SAME_BRAND, 9)  # no official need
    done = add_video(ctx, kind="community", status="downloaded")
    parent = make_episode(session, owner=ACCOUNT)
    parent.status = "split"
    children = [make_episode(session, owner=ACCOUNT) for _ in range(2)]
    for episode in (parent, *children):
        episode.provenance, episode.source_video_id = "community", done
    for child in children:
        child.parent_episode_id = parent.id
    session.commit()
    best = add_video(ctx, kind="community", score=0.9)
    s = _settings(ctx, community_max_per_series=2)  # 1 video held (3 episodes): 1 more allowed
    assert select_downloads(session, ctx.config, s, today=TODAY) == [best]
    s = _settings(ctx, community_max_per_series=1)
    assert select_downloads(session, ctx.config, s, today=TODAY) == []


def test_ingest_sweeps_stale_download_folders_of_other_processes(
    yt: tuple[Context, CassetteYtDlp], synth: Synth
) -> None:
    ctx, cassettes = yt
    stale = ctx.paths.tmp / "dl-1"  # a killed run, another pid
    stale.mkdir(parents=True)
    (stale / "abc.mp4.part").write_bytes(b"x" * 10)
    keep = [ctx.paths.tmp / "cookies-xyz", ctx.paths.tmp / "dl-note", ctx.paths.tmp / "dl-12a"]
    for folder in keep:  # other folders: only `dl-<digits>` is ours
        folder.mkdir()
        (folder / "keep.txt").write_text("keep")
    note = ctx.paths.tmp / "dl-99.txt"  # a file, not a download folder
    note.write_text("keep")
    video = add_video(ctx, duration_ms=3000)
    cassettes.downloads[_url(ctx, video)] = synth(_tiny())
    assert _ingest(ctx).done == 1
    assert not stale.exists()
    assert all((folder / "keep.txt").read_text() == "keep" for folder in keep)
    assert note.read_text() == "keep"


def test_ingest_sweeps_even_when_there_is_nothing_to_download(
    yt: tuple[Context, CassetteYtDlp],
) -> None:
    ctx, cassettes = yt
    stale = ctx.paths.tmp / "dl-1"
    stale.mkdir(parents=True)
    assert _ingest(ctx).done == 0
    assert not stale.exists() and cassettes.calls == []


def test_ingest_without_youtube_part_does_not_sweep(
    yt: tuple[Context, CassetteYtDlp],
) -> None:
    ctx, _ = yt
    stale = ctx.paths.tmp / "dl-1"
    stale.mkdir(parents=True)
    assert _ingest(ctx, episode=1).done == 0  # filters that exclude every new item
    assert stale.exists()


def _hold(ctx: Context) -> int:
    """Take the YouTube-download lock as another process would."""
    ctx.paths.locks.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(ctx.paths.locks / "ingest-youtube.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return descriptor


def test_ingest_downloads_nothing_while_another_ingest_holds_the_youtube_lock(
    yt: tuple[Context, CassetteYtDlp], synth: Synth
) -> None:
    ctx, cassettes = yt
    stale = ctx.paths.tmp / "dl-1"  # the other ingest may be using it: not ours to sweep
    stale.mkdir(parents=True)
    video = add_video(ctx, duration_ms=3000)
    cassettes.downloads[_url(ctx, video)] = synth(_tiny(450))
    drop_import(ctx, SERIES, synth(_tiny(550)))
    descriptor = _hold(ctx)
    try:
        report = _ingest(ctx)
    finally:
        os.close(descriptor)
    assert (report.done, report.warned, report.failed) == (1, 1, 0)  # the local file is imported
    assert cassettes.calls == [] and stale.exists()
    assert _status(ctx, video) == "retained"
    warnings = [e for e in ctx_events(ctx) if e.get("status") == "warned"]
    assert "un autre ingest télécharge déjà" in warnings[0]["message"]
    # released: the next run downloads
    assert _ingest(ctx).done == 1
    assert _status(ctx, video) == "downloaded" and not stale.exists()


def test_ingest_releases_the_youtube_lock_when_select_fails(
    yt: tuple[Context, CassetteYtDlp], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _ = yt

    def broken(*args: Any, **kwargs: Any) -> list[int]:
        raise RuntimeError("selection failed")

    monkeypatch.setattr("rushes.ingest.ingest.select_downloads", broken)
    with pytest.raises(RuntimeError):
        IngestStep().select(ctx, Filters())
    os.close(_hold(ctx))  # the lock is free: no BlockingIOError


def test_ingest_creator_filter_skips_the_youtube_part(
    yt: tuple[Context, CassetteYtDlp],
) -> None:
    ctx, cassettes = yt
    stale = ctx.paths.tmp / "dl-1"
    stale.mkdir(parents=True)
    descriptor = _hold(ctx)
    try:
        report = _ingest(ctx, creator="squeezie")  # a discovery belongs to a series
    finally:
        os.close(descriptor)
    assert (report.done, report.warned) == (0, 0) and stale.exists() and cassettes.calls == []
    _ingest(ctx, creator="squeezie")  # and it takes no lock either
    os.close(_hold(ctx))


def test_sweep_keeps_the_folder_of_the_current_process(yt: tuple[Context, CassetteYtDlp]) -> None:
    ctx, _ = yt
    mine, other = ctx.paths.tmp / f"dl-{os.getpid()}", ctx.paths.tmp / "dl-2"
    mine.mkdir(parents=True)
    other.mkdir()
    _ingest(ctx)
    assert mine.exists() and not other.exists()


def test_ingest_releases_the_youtube_lock_at_the_end_of_the_run(
    yt: tuple[Context, CassetteYtDlp],
) -> None:
    ctx, _ = yt
    _ingest(ctx)
    descriptor = _hold(ctx)  # would raise BlockingIOError if the step kept it
    os.close(descriptor)


# --- R15: a community episode is never cut -----------------------------------------------------


def test_r15_community_episode_never_cut(ctx: Context, session: Session) -> None:
    community = make_episode(session, owner=ACCOUNT)
    community.provenance = "community"
    control = make_episode(session, owner=ACCOUNT)  # a local analyzed episode is selected
    session.commit()
    step = CutStep()
    assert list(step.select(ctx, Filters())) == [control.id]
    assert list(step.select(ctx, Filters(force=True))) == [control.id]
    assert list(step.select(ctx, Filters(episode=community.id))) == []
