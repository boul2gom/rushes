"""Network doubles: record calls, return what the test configured, never touch the network."""

import copy
import hashlib
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from rushes.net.ytdlp import cassette_key

CASSETTES = Path(__file__).resolve().parent.parent / "fixtures" / "cassettes"


class FakeYtDlp:
    def __init__(self, responses: Mapping[str, dict[str, Any]] | None = None) -> None:
        self.responses = dict(responses or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def extract(
        self,
        url: str,
        *,
        flat: bool,
        platform: Literal["youtube", "tiktok"],
        comments: int = 0,
        playlist_items: str | None = None,
        cookies_allowed: bool = False,
    ) -> dict[str, Any]:
        self.calls.append((url, {"flat": flat, "platform": platform, "comments": comments}))
        return self.responses.get(url, {})

    def download(self, url: str, out_dir: Path, *, max_height: int) -> Path:
        self.calls.append((url, {"download": True, "max_height": max_height}))
        raise NotImplementedError("FakeYtDlp.download: configure a subclass in the test")


class CassetteMissing(LookupError):
    """No recording answers this request: a test never guesses a response (R20)."""


class CassetteYtDlp:
    """Replays the metadata recorded by `scripts/record_cassette.py`.

    `overrides` answer first, by `(url, playlist_items)` or by `url` (a dict, or an exception to
    raise): for the cases no recording can show (a failing channel, a short video).
    `downloads` maps a video url to a synthetic file that `download` copies (or an exception).
    """

    def __init__(
        self,
        directory: Path = CASSETTES,
        overrides: Mapping[Any, dict[str, Any] | Exception] | None = None,
        downloads: Mapping[str, Path | Exception] | None = None,
    ) -> None:
        self.directory = directory
        self.overrides = dict(overrides or {})
        self.downloads: dict[str, Path | Exception] = dict(downloads or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def extract(
        self,
        url: str,
        *,
        flat: bool,
        platform: Literal["youtube", "tiktok"],
        comments: int = 0,
        playlist_items: str | None = None,
        cookies_allowed: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                url,
                {
                    "flat": flat,
                    "platform": platform,
                    "comments": comments,
                    "playlist_items": playlist_items,
                    "cookies_allowed": cookies_allowed,
                },
            )
        )
        for key in ((url, playlist_items), url):
            if key in self.overrides:
                answer = self.overrides[key]
                if isinstance(answer, Exception):
                    raise answer
                return copy.deepcopy(answer)
        name = cassette_key(
            url, flat=flat, platform=platform, comments=comments, playlist_items=playlist_items
        )
        path = self.directory / f"{name}.json"
        if not path.is_file():
            raise CassetteMissing(
                f"cassette absente {name}.json pour {url} "
                f"(flat={flat}, comments={comments}, playlist_items={playlist_items}) : "
                "enregistrer avec scripts/record_cassette.py"
            )
        info: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))["info"]
        return info

    def download(self, url: str, out_dir: Path, *, max_height: int) -> Path:
        self.calls.append((url, {"download": True, "max_height": max_height}))
        answer = self.downloads.get(url)
        if answer is None:
            raise CassetteMissing(f"aucun fichier enregistré pour le téléchargement de {url}")
        if isinstance(answer, Exception):
            raise answer
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{hashlib.sha1(url.encode()).hexdigest()[:11]}.mp4"
        shutil.copy2(answer, target)
        return target


class FakeHttp:
    def __init__(
        self, json: Mapping[str, Any] | None = None, blobs: Mapping[str, bytes] | None = None
    ) -> None:
        self.json = dict(json or {})
        self.blobs = dict(blobs or {})  # url -> bytes written by `download`
        self.posts: list[tuple[str, bytes, Mapping[str, str]]] = []
        self.downloads: list[tuple[str, Path]] = []
        self.max_bytes: list[int | None] = []  # the bound asked for each download

    def get_json(self, url: str, *, params: Mapping[str, str] | None = None) -> Any:
        return self.json.get(url)

    def get_text(self, url: str) -> str:
        return ""

    def post(self, url: str, *, body: bytes, headers: Mapping[str, str]) -> int:
        self.posts.append((url, body, headers))
        return 200

    def download(self, url: str, dest: Path, *, max_bytes: int | None = None) -> None:
        self.downloads.append((url, dest))
        self.max_bytes.append(max_bytes)
        if url in self.blobs:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self.blobs[url])
