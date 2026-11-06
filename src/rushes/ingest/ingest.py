"""Local ingestion: data/import/<series or creator id>/<video> -> sources/ + an `imported` episode.

The file is copied (hard link when the file system allows it) through `atomic_path`, the episode is
inserted in one short transaction, and only then does the original move to import/_done/. A crash
between two of these leaves nothing to undo: the next run finds the same sha256 and finishes.
"""

import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rushes.config import Config
from rushes.context import Context, Filters
from rushes.db.models import Episode
from rushes.fsutil import atomic_path
from rushes.ids import episode_key
from rushes.ingest.owner import choose_owner
from rushes.media.probe import probe, sha256_file
from rushes.runner import BaseStep, Outcome

VIDEO_SUFFIXES = frozenset({".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"})
# A file modified more recently may still be copied into import/: leave it for a later run. Limits
# (decision C39): an mtime in the future is skipped until the clock catches up, and a copy stalled
# for more than SETTLE_S seconds is taken as it is (its partial bytes get a sha256 of their own).
SETTLE_S = 60


@dataclass(frozen=True)
class ImportItem:
    folder: str  # "" for a file dropped directly in import/
    path: Path


def _subject(config: Config, folder: str) -> tuple[str | None, str | None] | None:
    """(series_id, creator_id) of an import folder, None when the folder is not in the config."""
    if config.series_by_id(folder) is not None:
        return folder, None
    if config.creator_by_id(folder) is not None:
        return None, folder
    return None


def _entries(folder: Path) -> list[Path]:
    """Files and sub-folders (reported, never entered) of an import folder, minus `.`/`_` ones."""
    return sorted(
        p
        for p in folder.iterdir()
        if not p.name.startswith(".") and (p.is_file() or (p.is_dir() and p.name[0] != "_"))
    )


def _link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:  # another device, no hard links, permissions: a plain copy does it
        shutil.copy2(source, target)


def archive(ctx: Context, path: Path) -> None:
    """Move an ingested original to import/_done/, never overwriting an earlier file."""
    done = ctx.paths.import_done
    done.mkdir(parents=True, exist_ok=True)
    target, number = done / path.name, 1
    while target.exists():
        target = done / f"{path.stem}-{number}{path.suffix}"
        number += 1
    try:
        os.replace(path, target)
    except OSError:
        shutil.move(path, target)


class IngestStep(BaseStep):
    name: ClassVar[str] = "ingest"

    def key(self, item: ImportItem) -> str:
        return f"{item.folder}/{item.path.name}" if item.folder else item.path.name

    def select(self, ctx: Context, filters: Filters) -> Iterable[ImportItem]:
        root = ctx.paths.imports
        if not root.is_dir():
            return []
        if filters.episode is not None or filters.account is not None:
            return []  # a file not ingested yet has neither an episode nor an owner
        wanted = {f for f in (filters.series, filters.creator) if f is not None}
        items: list[ImportItem] = []
        for entry in sorted(root.iterdir()):
            if entry.name.startswith(("_", ".")):
                continue  # _done/ and other private folders: ignored without a warning
            if entry.is_dir():
                if not wanted or entry.name in wanted:
                    items.extend(ImportItem(entry.name, p) for p in _entries(entry))
            elif entry.is_file() and not wanted:
                items.append(ImportItem("", entry))
        return items

    def run(self, ctx: Context, item: ImportItem, *, force: bool) -> Outcome:
        subject = _subject(ctx.config, item.folder)
        if subject is None:
            where = f"dossier inconnu : {item.folder}" if item.folder else "fichier hors dossier"
            return Outcome("warned", f"{where} (attendu import/<série ou créateur>/)")
        if item.path.is_dir():  # warned again at every run until the folder is emptied (on purpose)
            return Outcome(
                "warned",
                f"sous-dossier ignoré : {item.path.name} "
                f"(déposer les fichiers directement dans import/{item.folder}/)",
            )
        suffix = item.path.suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            return Outcome("warned", f"extension non prise en charge : {item.path.suffix or '-'}")
        age_s = ctx.clock.now().timestamp() - item.path.stat().st_mtime
        if age_s < SETTLE_S:  # also true for an mtime in the future
            return Outcome("skipped", "copie en cours")

        digest = sha256_file(item.path)
        if self._known(ctx, digest):
            archive(ctx, item.path)
            return Outcome("skipped", "déjà importé")
        info = probe(item.path)  # MediaError: the file stays where it is
        target = ctx.paths.sources / f"local_{digest[:16]}{suffix}"
        if not target.exists():  # left by a run that stopped before the database
            with atomic_path(target) as tmp:
                _link_or_copy(item.path, tmp)

        series_id, creator_id = subject
        try:
            owner, episode_id = self._insert(
                ctx, series_id, creator_id, digest, target, info.duration_ms
            )
        except IntegrityError:
            if not self._known(ctx, digest):  # a CHECK or FK failure is not "already imported"
                raise
            archive(ctx, item.path)  # the same file was inserted meanwhile
            return Outcome("skipped", "déjà importé")
        archive(ctx, item.path)
        return Outcome("done", f"{episode_key(episode_id)} → {owner}")

    @staticmethod
    def _known(ctx: Context, digest: str) -> bool:
        with Session(ctx.engine) as session:
            return session.scalar(select(Episode.id).where(Episode.sha256 == digest)) is not None

    @staticmethod
    def _insert(
        ctx: Context,
        series_id: str | None,
        creator_id: str | None,
        digest: str,
        target: Path,
        duration_ms: int,
    ) -> tuple[str, int]:
        """One short transaction: the owner is chosen on the stock as it is right now."""
        with Session(ctx.engine) as session, session.begin():
            owner = choose_owner(
                session,
                ctx.config,
                series_id=series_id,
                creator_id=creator_id,
                today=ctx.clock.now().date(),
            )
            episode = Episode(
                series_id=series_id,
                creator_id=creator_id,
                source_video_id=None,
                provenance="local",
                owner_account_id=owner,
                file_path=ctx.paths.relative(target),
                sha256=digest,
                duration_ms=duration_ms,
                status="imported",
            )
            session.add(episode)
            session.flush()
            return owner, episode.id
