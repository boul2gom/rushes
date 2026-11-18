"""YtDlpClient (R18, R47), RateLimiter, cassette key and sanitize. No network: a scripted yt-dlp."""

import contextlib
import io
import json
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from yt_dlp.cookies import CookieLoadError
from yt_dlp.utils import DownloadError

from fakes.clock import FakeClock
from helpers import START
from rushes.config import Settings, YoutubeSettings, load_config
from rushes.errors import SourceUnavailable
from rushes.net.ratelimit import RateLimiter
from rushes.net.ytdlp import (
    SIGNIN_MARKERS,
    ExtractError,
    YtDlpClient,
    cassette_key,
    sanitize,
)
from rushes.paths import Paths
from rushes.ui import Emitter, Message, make_emitter

SIGNIN = "Sign in to confirm you're not a bot"
COOKIE_VALUE = "SECRETCOOKIEVALUE123"
COOKIES = (
    "# Netscape HTTP Cookie File\n"
    f".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\t{COOKIE_VALUE}\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tHSID\tOTHERCOOKIEVALUE456\n"
)


@dataclass
class Script:
    """What the fake yt-dlp does at each call, and what it was given."""

    outcomes: list[dict[str, Any] | Exception]
    options: list[dict[str, Any]] = field(default_factory=list)
    cookie_modes: list[int | None] = field(default_factory=list)
    cookie_texts: list[str | None] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)

    def factory(self, opts: dict[str, Any]) -> FakeYdl:
        self.options.append(dict(opts))
        cookiefile = opts.get("cookiefile")
        if cookiefile is None:
            self.cookie_modes.append(None)
            self.cookie_texts.append(None)
        else:
            path = Path(cookiefile)
            self.cookie_modes.append(stat.S_IMODE(path.stat().st_mode))
            self.cookie_texts.append(path.read_text(encoding="utf-8"))
        return FakeYdl(self)


class FakeYdl:
    def __init__(self, script: Script) -> None:
        self.script = script

    def __enter__(self) -> FakeYdl:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        assert download is False
        self.script.urls.append(url)
        outcome = self.script.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass
class Rig:
    client: YtDlpClient
    script: Script
    paths: Paths
    stream: io.StringIO
    ui: Emitter
    log: Path

    def messages(self) -> list[dict[str, Any]]:
        lines = [json.loads(x) for x in self.stream.getvalue().splitlines() if x.strip()]
        return [e for e in lines if e.get("event") == "message"]


@pytest.fixture
def make_rig(tmp_path: Path, config_dir: Path) -> Iterator[Any]:
    stream = io.StringIO()
    log = tmp_path / "logs" / "journal.jsonl"
    ui = make_emitter(plain=False, json=True, verbose=True, stream=stream, log_path=log)

    def build(
        outcomes: list[dict[str, Any] | Exception], *, cookies: str = "fallback", has_file: bool
    ) -> Rig:
        settings: Settings = load_config(config_dir).settings.model_copy(
            update={"youtube": YoutubeSettings(cookies=cookies)}  # type: ignore[arg-type]
        )
        paths = Paths(config_dir=config_dir, data_dir=tmp_path / "data")
        if has_file:
            paths.cookies_youtube.parent.mkdir(parents=True)
            paths.cookies_youtube.write_text(COOKIES, encoding="utf-8")
        script = Script(outcomes)
        sleeps: list[float] = []
        limiter = RateLimiter(settings.rate_limit_s, monotonic=lambda: 0.0, sleep=sleeps.append)
        client = YtDlpClient(settings, paths, ui, ydl_factory=script.factory, limiter=limiter)
        return Rig(client, script, paths, stream, ui, log)

    yield build
    with contextlib.suppress(ValueError):  # a test may have closed the emitter to read the journal
        ui.close()


def _extract(rig: Rig, *, allowed: bool, url: str = "https://www.youtube.com/@x/videos") -> Any:
    return rig.client.extract(url, flat=True, platform="youtube", cookies_allowed=allowed)


def test_r18_ytdlp_params_metadata_only(make_rig: Any) -> None:
    rig = make_rig([], has_file=False)
    o = rig.client.options(
        flat=True, platform="youtube", comments=0, playlist_items=None, cookies=None
    )
    assert o["skip_download"] is True
    assert o["check_formats"] is False
    assert o["ignore_no_formats_error"] is True
    assert o["sleep_interval_requests"] == 2
    assert "cookiefile" not in o
    assert o["extract_flat"] == "in_playlist"
    assert o["extractor_retries"] == 2
    assert o["js_runtimes"] == {"deno": {}}
    assert "getcomments" not in o and "playlist_items" not in o and "impersonate" not in o


def test_options_not_flat_with_comments_and_playlist_items(make_rig: Any) -> None:
    rig = make_rig([], has_file=False)
    o = rig.client.options(
        flat=False, platform="youtube", comments=40, playlist_items="1:30", cookies=None
    )
    assert "extract_flat" not in o
    assert o["playlist_items"] == "1:30"
    assert o["getcomments"] is True
    assert o["extractor_args"] == {"youtube": {"max_comments": ["40"], "comment_sort": ["top"]}}


def test_tiktok_options_impersonate_chrome(make_rig: Any) -> None:
    rig = make_rig([], has_file=False)
    o = rig.client.options(
        flat=True, platform="tiktok", comments=0, playlist_items=None, cookies=None
    )
    assert str(o["impersonate"]) == "chrome"
    assert "js_runtimes" not in o
    assert o["skip_download"] is True and o["sleep_interval_requests"] == 2


def test_r18_cookies_only_after_signin_error(make_rig: Any) -> None:
    rig = make_rig([DownloadError(SIGNIN), {"id": "ok"}], has_file=True)
    assert _extract(rig, allowed=True) == {"id": "ok"}
    first, second = rig.script.options
    assert "cookiefile" not in first
    assert Path(second["cookiefile"]) != rig.paths.cookies_youtube
    assert rig.script.cookie_modes == [None, 0o600]
    assert rig.script.cookie_texts[1] == COOKIES
    assert not Path(second["cookiefile"]).exists()  # the working copy is deleted afterwards
    warns = [m for m in rig.messages() if m["alert"] == "youtube_signin"]
    assert len(warns) == 1
    assert warns[0]["level"] == "warn" and "cookies de secours" in warns[0]["text"]
    assert rig.paths.cookies_youtube.read_text(encoding="utf-8") == COOKIES  # original untouched


def test_r18_other_error_is_not_retried(make_rig: Any) -> None:
    rig = make_rig([DownloadError("Video unavailable")], has_file=True)
    with pytest.raises(ExtractError) as info:
        _extract(rig, allowed=True)
    assert info.value.signin is False
    assert isinstance(info.value, SourceUnavailable)
    assert len(rig.script.options) == 1
    assert not [m for m in rig.messages() if m["alert"]]


def test_r18_no_cookies_unless_allowed(make_rig: Any) -> None:
    rig = make_rig([DownloadError(SIGNIN)], cookies="always", has_file=True)
    with pytest.raises(ExtractError) as info:
        _extract(rig, allowed=False)
    assert info.value.signin is True
    assert len(rig.script.options) == 1
    assert "cookiefile" not in rig.script.options[0]
    assert not [m for m in rig.messages() if m["alert"]]


def test_r18_no_retry_without_allowance_in_fallback_mode(make_rig: Any) -> None:
    rig = make_rig([DownloadError(SIGNIN), {"id": "never"}], cookies="fallback", has_file=True)
    with pytest.raises(ExtractError) as info:
        _extract(rig, allowed=False)
    assert info.value.signin is True and len(rig.script.options) == 1


def test_r18_cookies_retry_failure_alerts_cookies_expired(make_rig: Any) -> None:
    rig = make_rig([DownloadError(SIGNIN), DownloadError(SIGNIN)], has_file=True)
    with pytest.raises(ExtractError) as info:
        _extract(rig, allowed=True)
    assert info.value.signin is True
    assert len(rig.script.options) == 2  # a single retry, never a third call
    assert [m["alert"] for m in rig.messages()].count("cookies_expired") == 1


def test_r18_cookies_file_missing_alerts_cookies_expired(make_rig: Any) -> None:
    rig = make_rig([], cookies="always", has_file=False)
    with pytest.raises(ExtractError) as info:
        _extract(rig, allowed=True)
    assert info.value.signin is True
    assert rig.script.options == []  # no request at all
    assert [m["alert"] for m in rig.messages()] == ["cookies_expired"]


def test_r18_fallback_without_cookie_file_alerts_cookies_expired(make_rig: Any) -> None:
    rig = make_rig([DownloadError(SIGNIN)], has_file=False)
    with pytest.raises(ExtractError) as info:
        _extract(rig, allowed=True)
    assert info.value.signin is True and len(rig.script.options) == 1
    assert [m["alert"] for m in rig.messages()] == ["cookies_expired"]


def test_r18_always_mode_sends_cookies_from_the_first_request(make_rig: Any) -> None:
    rig = make_rig([{"id": "ok"}], cookies="always", has_file=True)
    assert _extract(rig, allowed=True) == {"id": "ok"}
    assert rig.script.cookie_modes == [0o600]
    assert "youtube_signin" in [m["alert"] for m in rig.messages()]


def test_r18_always_mode_stops_sending_cookies_once_they_are_refused(make_rig: Any) -> None:
    rig = make_rig([DownloadError(SIGNIN), DownloadError(SIGNIN)], cookies="always", has_file=True)
    for _ in range(2):  # two requests of one client: the cookies are dead after the first
        with pytest.raises(ExtractError) as info:
            _extract(rig, allowed=True)
        assert info.value.signin is True
    assert rig.script.cookie_modes == [0o600, None]
    assert [m["alert"] for m in rig.messages()].count("cookies_expired") == 1


def test_r18_cookies_never_mode_raises_source_unavailable(make_rig: Any) -> None:
    rig = make_rig([DownloadError(SIGNIN), {"id": "never"}], cookies="never", has_file=True)
    with pytest.raises(SourceUnavailable) as info:
        _extract(rig, allowed=True)
    assert isinstance(info.value, ExtractError) and info.value.signin is True
    assert len(rig.script.options) == 1
    assert "cookiefile" not in rig.script.options[0]


def test_r47_cookie_values_never_logged(make_rig: Any) -> None:
    leak = f"HTTP 429 with SID={COOKIE_VALUE} then {COOKIE_VALUE} and OTHERCOOKIEVALUE456"
    rig = make_rig([DownloadError(SIGNIN), DownloadError(leak)], has_file=True)
    with pytest.raises(ExtractError) as info:
        _extract(rig, allowed=True)
    # what the runner does with the error: it is shown, and written to the journal
    rig.ui.emit(Message("warn", str(info.value), hint=info.value.hint))
    rig.ui.close()
    journal = rig.log.read_text(encoding="utf-8")
    assert "youtube_signin" in journal  # the journal really holds the events
    for value in (COOKIE_VALUE, "OTHERCOOKIEVALUE456"):
        assert value not in rig.stream.getvalue()
        assert value not in journal
        assert value not in str(info.value) and value not in (info.value.hint or "")


def test_r47_cookie_values_in_a_raw_error_text_are_masked(make_rig: Any) -> None:
    """A text that holds a cookie value (SID, a value alone) is masked wherever it is emitted."""
    rig = make_rig([DownloadError(SIGNIN), {"id": "ok"}], has_file=True)
    _extract(rig, allowed=True)  # the cookie file is read: its values are secrets now
    rig.ui.emit(Message("error", f"boom {COOKIE_VALUE} / OTHERCOOKIEVALUE456"))
    rig.ui.close()
    for text in (rig.stream.getvalue(), rig.log.read_text(encoding="utf-8")):
        assert COOKIE_VALUE not in text and "OTHERCOOKIEVALUE456" not in text
        assert "boom" in text


def test_fallback_alert_is_sent_once_per_client(make_rig: Any) -> None:
    """Under a lasting login wall, each request still tries without cookies first, one alert."""
    rig = make_rig(
        [DownloadError(SIGNIN), {"id": "a"}, DownloadError(SIGNIN), {"id": "b"}], has_file=True
    )
    assert _extract(rig, allowed=True) == {"id": "a"}
    assert _extract(rig, allowed=True) == {"id": "b"}
    assert rig.script.cookie_modes == [None, 0o600, None, 0o600]
    assert [m["alert"] for m in rig.messages()].count("youtube_signin") == 1


def test_a_corrupt_cookie_file_is_an_expired_cookie_alert(make_rig: Any) -> None:
    rig = make_rig(
        [DownloadError(SIGNIN), CookieLoadError("invalid Netscape format")], has_file=True
    )
    with pytest.raises(ExtractError) as info:
        _extract(rig, allowed=True)
    assert info.value.signin is True
    expired = [m for m in rig.messages() if m["alert"] == "cookies_expired"]
    assert len(expired) == 1 and "refusé par yt-dlp" in expired[0]["hint"]
    assert "toujours exigée" not in expired[0]["hint"]  # the cause is the file, not the wall
    assert len(rig.script.options) == 2


def test_a_cookie_file_that_is_not_text_is_an_expired_cookie_alert(make_rig: Any) -> None:
    rig = make_rig([DownloadError(SIGNIN)], has_file=True)
    rig.paths.cookies_youtube.write_bytes(b"\xff\xfe\x00 not utf-8 \xc3\x28")
    with pytest.raises(ExtractError) as info:
        _extract(rig, allowed=True)
    assert info.value.signin is True
    assert len(rig.script.options) == 1  # no request with an unreadable file
    assert [m["alert"] for m in rig.messages()].count("cookies_expired") == 1


def test_unreadable_answer_is_an_extract_error(make_rig: Any) -> None:
    rig = make_rig([None], has_file=False)  # type: ignore[list-item]
    with pytest.raises(ExtractError) as info:
        _extract(rig, allowed=False)
    assert info.value.signin is False


def test_signin_markers_cover_both_texts() -> None:
    assert SIGNIN_MARKERS == ("Sign in to confirm", "confirm you're not a bot")


def test_r18_every_request_goes_through_the_rate_limiter(tmp_path: Path, config_dir: Path) -> None:
    sleeps: list[float] = []
    limiter = RateLimiter(2.0, monotonic=lambda: 0.0, sleep=sleeps.append)
    script = Script([{"id": "a"}, {"id": "b"}, {"id": "c"}])
    settings = load_config(config_dir).settings
    ui = make_emitter(plain=False, json=True, verbose=False, stream=io.StringIO())
    client = YtDlpClient(
        settings,
        Paths(config_dir=config_dir, data_dir=tmp_path / "data"),
        ui,
        ydl_factory=script.factory,
        limiter=limiter,
    )
    for _ in range(3):
        client.extract("https://www.youtube.com/@x", flat=True, platform="youtube")
    assert sleeps == [2.0, 2.0]


def test_rate_limiter_spaces_calls() -> None:
    sleeps: list[float] = []
    limiter = RateLimiter(2.0, monotonic=lambda: 0.0, sleep=sleeps.append)
    for _ in range(3):
        limiter.wait()
    assert sleeps == [2.0, 2.0]


def test_rate_limiter_only_waits_for_the_remaining_time() -> None:
    clock = FakeClock(START)
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds=seconds)

    limiter = RateLimiter(2.0, monotonic=lambda: clock.now().timestamp(), sleep=sleep)
    limiter.wait()
    clock.advance(seconds=0.5)
    limiter.wait()
    clock.advance(seconds=5)
    limiter.wait()  # already 5 s since the last call: no wait
    assert sleeps == [1.5]


def test_cassette_key_stable_and_sanitize_drops_media_urls() -> None:
    args: dict[str, Any] = {"flat": True, "platform": "youtube", "comments": 0}
    key = cassette_key("https://www.youtube.com/@oggy", playlist_items="1:30", **args)
    assert key == cassette_key("https://www.youtube.com/@oggy", playlist_items="1:30", **args)
    assert len(key) == 40 and int(key, 16) >= 0  # sha1 hex
    other_options = [
        cassette_key("https://www.youtube.com/@oggy", playlist_items="1:50", **args),
        cassette_key("https://www.youtube.com/@oggy", playlist_items=None, **args),
        cassette_key(
            "https://www.youtube.com/@oggy", **{**args, "flat": False}, playlist_items="1:30"
        ),
        cassette_key(
            "https://www.youtube.com/@oggy", **{**args, "comments": 5}, playlist_items="1:30"
        ),
        cassette_key(
            "https://www.youtube.com/@oggy", **{**args, "platform": "tiktok"}, playlist_items="1:30"
        ),
        cassette_key("https://www.youtube.com/@other", playlist_items="1:30", **args),
    ]
    assert len({key, *other_options}) == 7

    media = "https://rr1---sn-x.googlevideo.com/videoplayback?expire=1&sig=abc"
    info = {
        "id": "abc",
        "title": "T",
        "url": media,
        "format_id": "18",
        "formats": [{"format_id": "18", "url": media, "http_headers": {"Cookie": "SID=1"}}],
        "requested_formats": [{"url": media}],
        "thumbnails": [{"url": "https://i.ytimg.com/vi/abc/hq.jpg?sqp=token"}],
        "http_headers": {"Cookie": "SID=1"},
        "automatic_captions": {"en": [{"url": "https://www.youtube.com/api/timedtext?sig=x"}]},
        "subtitles": {"fr": [{"url": "https://www.youtube.com/api/timedtext?sig=y"}]},
        "cookies": "SID=1",
        "heatmap": [{"start_time": 0.0, "end_time": 5.0, "value": 0.5}],
        "entries": [
            {
                "id": "e1",
                "url": "https://www.youtube.com/watch?v=e1",
                "formats": [{"url": media}],
                "__x_forwarded_for_ip": None,
            }
        ],
    }
    clean = sanitize(info)
    text = json.dumps(clean)
    for forbidden in ("googlevideo", "timedtext", "Cookie", "SID=1", "sqp=token"):
        assert forbidden not in text
    assert clean["heatmap"] == info["heatmap"]  # what the tests need is kept
    assert clean["entries"][0]["url"] == "https://www.youtube.com/watch?v=e1"
    assert "formats" not in clean["entries"][0] and "url" not in clean
    assert "__x_forwarded_for_ip" not in clean["entries"][0]
    assert "formats" in info and "url" in info  # the input is not modified
