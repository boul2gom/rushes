"""Network doubles: record calls, return what the test configured, never touch the network."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal


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
