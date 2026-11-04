"""Single source of file-system paths (no hard-coded path anywhere else)."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_CONFIG_DIR = "RUSHES_CONFIG_DIR"
ENV_DATA_DIR = "RUSHES_DATA_DIR"

_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class PathError(ValueError):
    pass


def _ident(value: str) -> str:
    if not _IDENT.fullmatch(value) or ".." in value:
        raise PathError(f"identifiant invalide pour un chemin : {value!r}")
    return value


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    data_dir: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Paths:
        env = os.environ if environ is None else environ
        return cls(
            config_dir=Path(env.get(ENV_CONFIG_DIR, "config")).resolve(),
            data_dir=Path(env.get(ENV_DATA_DIR, "data")).resolve(),
        )

    @property
    def db(self) -> Path:
        return self.data_dir / "rushes.db"

    @property
    def cache_models(self) -> Path:
        return self.data_dir / "cache" / "models"

    @property
    def cookies_youtube(self) -> Path:
        return self.data_dir / "cookies" / "youtube.txt"

    @property
    def sources(self) -> Path:
        return self.data_dir / "sources"

    @property
    def episodes(self) -> Path:
        return self.data_dir / "episodes"

    @property
    def renders(self) -> Path:
        return self.data_dir / "renders"

    @property
    def inbox(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def outbox(self) -> Path:
        return self.data_dir / "outbox"

    @property
    def outbox_test(self) -> Path:
        return self.data_dir / "outbox_test"

    @property
    def reports(self) -> Path:
        return self.data_dir / "reports"

    def episode_dir(self, episode_id: str) -> Path:
        return self.episodes / _ident(episode_id)

    def post_dir(self, account_id: str, post_id: str, *, test: bool = False) -> Path:
        root = self.outbox_test if test else self.outbox
        return root / _ident(account_id) / _ident(post_id)
