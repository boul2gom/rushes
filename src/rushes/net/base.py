"""Network client contracts (00-master.md section 5.8); real clients come in tasks 9 to 10b."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from rushes.errors import SourceUnavailable


class YtDlp(Protocol):
    def extract(
        self,
        url: str,
        *,
        flat: bool,
        platform: Literal["youtube", "tiktok"],
        comments: int = 0,
        playlist_items: str | None = None,
        cookies_allowed: bool = False,  # True: DiscoverYoutubeStep only (R18)
    ) -> dict[str, Any]: ...

    def download(self, url: str, out_dir: Path, *, max_height: int) -> Path: ...


class Http(Protocol):
    def get_json(self, url: str, *, params: Mapping[str, str] | None = None) -> Any: ...
    def get_text(self, url: str) -> str: ...
    def post(self, url: str, *, body: bytes, headers: Mapping[str, str]) -> int: ...  # ntfy only
    def download(self, url: str, dest: Path) -> None: ...  # model weights (task 9)


@dataclass(frozen=True)
class NetClients:
    ytdlp: YtDlp
    http: Http


def _unavailable() -> SourceUnavailable:
    return SourceUnavailable(
        "client réseau non disponible avant la tâche 10a",
        hint="Les accès réseau arrivent avec la découverte YouTube (tâche 10a).",
    )


class UnavailableYtDlp:
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
        raise _unavailable()

    def download(self, url: str, out_dir: Path, *, max_height: int) -> Path:
        raise _unavailable()


class UnavailableHttp:
    def get_json(self, url: str, *, params: Mapping[str, str] | None = None) -> Any:
        raise _unavailable()

    def get_text(self, url: str) -> str:
        raise _unavailable()

    def post(self, url: str, *, body: bytes, headers: Mapping[str, str]) -> int:
        raise _unavailable()

    def download(self, url: str, dest: Path) -> None:
        raise _unavailable()
