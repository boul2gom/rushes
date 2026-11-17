"""yt-dlp client: metadata only, rate-limited, cookies only as a last resort (R18, R47).

Cookies never leave `data/cookies/youtube.txt`: a 0600 copy in a temporary folder is given to
yt-dlp (it rewrites the file when it closes) and deleted afterwards. Only a call that passes
`cookies_allowed=True` (the YouTube discovery step) may use them, as the settings allow.
"""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

import yt_dlp
from yt_dlp.cookies import CookieLoadError
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import YoutubeDLError

from rushes.config import Settings
from rushes.errors import SourceUnavailable
from rushes.net.ratelimit import RateLimiter
from rushes.paths import Paths
from rushes.ui import Emitter, Message, redact, register_secret

SIGNIN_MARKERS = ("Sign in to confirm", "confirm you're not a bot")
Platform = Literal["youtube", "tiktok"]

# Keys that carry media or session data: never kept in a cassette nor handed to the steps.
_DROPPED = frozenset(
    {
        "formats",
        "requested_formats",
        "thumbnails",
        "http_headers",
        "automatic_captions",
        "subtitles",
        "cookies",
    }
)
# Top-level bookkeeping of yt-dlp (paths, timestamps): not content, and it would make a cassette
# differ at every recording.
_PRIVATE = frozenset(
    {
        "requested_downloads",
        "requested_subtitles",
        "requested_entries",
        "filepath",
        "_filename",
        "filename",
        "infojson_filename",
        "epoch",
        "_version",
    }
)
_MESSAGE_LIMIT = 200


class _SilentLogger:
    """yt-dlp prints nothing itself: every error comes back as an ExtractError, redacted (R47)."""

    def debug(self, msg: str) -> None: ...

    def info(self, msg: str) -> None: ...

    def warning(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


class ExtractError(SourceUnavailable):
    """An extraction that failed; `signin` is True when YouTube asks for a login."""

    def __init__(
        self,
        message: str = "",
        *,
        signin: bool = False,
        hint: str | None = None,
        alerted: bool = False,
    ) -> None:
        super().__init__(message, hint=hint)
        self.signin = signin
        self.alerted = alerted  # the `cookies_expired` alert was already sent for this error


def cassette_key(
    url: str, *, flat: bool, platform: str, comments: int, playlist_items: str | None
) -> str:
    """Name (without extension) of the cassette that answers this request."""
    request = {
        "url": url,
        "flat": flat,
        "platform": platform,
        "comments": comments,
        "playlist_items": playlist_items,
    }
    return hashlib.sha1(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        format_like = "format_id" in value or "protocol" in value
        return {
            key: _strip(item)
            for key, item in value.items()
            if item is not None
            and key not in _DROPPED
            and key not in _PRIVATE
            and not key.startswith("__")
            and not (key == "url" and format_like)
        }
    if isinstance(value, list | tuple):
        return [_strip(item) for item in value]
    return value


def sanitize(info: dict[str, Any]) -> dict[str, Any]:
    """A copy of `info` without media URLs, headers, captions or cookies (nothing signed)."""
    plain = yt_dlp.YoutubeDL.sanitize_info(dict(info))  # JSON-able; setdefault on the copy only
    if "_type" not in info:
        plain.pop("_type", None)
    cleaned: dict[str, Any] = _strip(plain)
    return cleaned


def _is_signin(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in SIGNIN_MARKERS)


def _register_cookie_secrets(text: str) -> None:
    """R47: every cookie line, and its value, becomes a secret no output may show."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        register_secret(line)
        register_secret(line.split("\t")[-1])


def _short(text: str) -> str:
    one_line = re.sub(r"\s+", " ", redact(text)).strip()
    return one_line[:_MESSAGE_LIMIT]


class YtDlpClient:
    def __init__(
        self,
        settings: Settings,
        paths: Paths,
        ui: Emitter,
        *,
        ydl_factory: Callable[[dict[str, Any]], Any] = yt_dlp.YoutubeDL,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._ui = ui
        self._factory = ydl_factory
        self._limiter = limiter or RateLimiter(settings.rate_limit_s)
        self._signin_announced = False  # one `youtube_signin` alert per client (per command)

    def options(
        self,
        *,
        flat: bool,
        platform: Platform,
        comments: int,
        playlist_items: str | None,
        cookies: Path | None,
    ) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "logger": _SilentLogger(),
            "skip_download": True,
            "check_formats": False,
            "ignore_no_formats_error": True,
            "sleep_interval_requests": self._settings.rate_limit_s,
            "extractor_retries": 2,
        }
        if platform == "youtube":
            opts["js_runtimes"] = {"deno": {}}
        else:
            opts["impersonate"] = ImpersonateTarget("chrome")
        if flat:
            opts["extract_flat"] = "in_playlist"
        if playlist_items:
            opts["playlist_items"] = playlist_items
        if comments > 0:
            opts["getcomments"] = True
            opts["extractor_args"] = {
                "youtube": {"max_comments": [str(comments)], "comment_sort": ["top"]}
            }
        if cookies is not None:
            opts["cookiefile"] = str(cookies)
        return opts

    def extract(
        self,
        url: str,
        *,
        flat: bool,
        platform: Platform,
        comments: int = 0,
        playlist_items: str | None = None,
        cookies_allowed: bool = False,  # True: DiscoverYoutubeStep only (R18)
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "flat": flat,
            "platform": platform,
            "comments": comments,
            "playlist_items": playlist_items,
        }
        mode = self._settings.youtube.cookies
        may_use_cookies = cookies_allowed and platform == "youtube" and mode != "never"
        if may_use_cookies and mode == "always":
            return self._with_cookies(url, request, reason="always")
        try:
            return self._request(url, cookies=None, **request)
        except ExtractError as exc:
            if not (exc.signin and may_use_cookies):
                raise  # no allowance, mode "never" or another error: no retry, no cookies
        return self._with_cookies(url, request, reason="fallback")

    def download(self, url: str, out_dir: Path, *, max_height: int) -> Path:
        raise NotImplementedError("YtDlpClient.download arrive avec la tâche 10b")

    # --- internals -----------------------------------------------------------------------------

    def _request(self, url: str, *, cookies: Path | None, **request: Any) -> dict[str, Any]:
        self._limiter.wait()
        try:
            with self._factory(self.options(cookies=cookies, **request)) as ydl:
                info = ydl.extract_info(url, download=False)
                # a flat playlist is lazy: read it (and its pages) before the session closes
                cleaned = sanitize(info) if isinstance(info, dict) and info else None
        except CookieLoadError:
            self._cookies_expired("fichier refusé par yt-dlp")
            raise ExtractError(
                "Cookies YouTube illisibles", signin=True, hint=self._renew_hint(), alerted=True
            ) from None
        except YoutubeDLError as exc:
            text = str(exc)
            if _is_signin(text):
                raise ExtractError(
                    "YouTube exige une connexion",
                    signin=True,
                    hint="Les cookies ne servent qu'en secours (réglage youtube.cookies).",
                ) from None
            raise ExtractError(f"Extraction impossible : {_short(text)}") from None
        if cleaned is None:
            raise ExtractError("Réponse vide de YouTube")
        return cleaned

    def _with_cookies(self, url: str, request: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
        source = self._paths.cookies_youtube
        if not source.is_file():
            self._cookies_expired("fichier absent")
            raise ExtractError("Cookies YouTube absents", signin=True, hint=self._renew_hint())
        try:
            text = source.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            self._cookies_expired("fichier illisible")
            raise ExtractError(
                "Cookies YouTube illisibles", signin=True, hint=self._renew_hint()
            ) from None
        _register_cookie_secrets(text)
        self._announce_cookies(reason)
        self._paths.tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._paths.tmp) as folder:
            copy = Path(folder) / "cookies.txt"
            descriptor = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
            try:
                return self._request(url, cookies=copy, **request)
            except ExtractError as exc:
                if exc.signin and not exc.alerted:
                    self._cookies_expired("connexion toujours exigée")
                raise

    def _announce_cookies(self, reason: str) -> None:
        if self._signin_announced:
            return  # a lasting login wall must not flood the alerts
        self._signin_announced = True
        if reason == "always":
            text = "Cookies YouTube utilisés (réglage youtube.cookies: always)"
        else:
            text = "YouTube exige une connexion : cookies de secours utilisés"
        self._ui.emit(
            Message(
                "warn",
                text,
                hint="Utiliser un compte Google dédié : yt-dlp expose le compte au blocage.",
                alert="youtube_signin",
            )
        )

    def _cookies_expired(self, detail: str) -> None:
        self._ui.emit(
            Message(
                "warn",
                "Cookies YouTube expirés ou absents",
                hint=f"{detail} ; {self._renew_hint()}",
                alert="cookies_expired",
            )
        )

    def _renew_hint(self) -> str:
        return f"exporter à nouveau les cookies du compte dédié vers {self._paths.cookies_youtube}"
