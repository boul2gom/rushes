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
    def __init__(self, json: Mapping[str, Any] | None = None) -> None:
        self.json = dict(json or {})
        self.posts: list[tuple[str, bytes, Mapping[str, str]]] = []
        self.downloads: list[tuple[str, Path]] = []

    def get_json(self, url: str, *, params: Mapping[str, str] | None = None) -> Any:
        return self.json.get(url)

    def get_text(self, url: str) -> str:
        return ""

    def post(self, url: str, *, body: bytes, headers: Mapping[str, str]) -> int:
        self.posts.append((url, body, headers))
        return 200

    def download(self, url: str, dest: Path) -> None:
        self.downloads.append((url, dest))
