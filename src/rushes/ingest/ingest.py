"""Ingestion: data/import/<series or creator id>/<video> and selected YouTube videos -> sources/.

Each becomes an `imported` episode. A local file is copied (hard link when the file system allows
it) through `atomic_path`, the episode is inserted in one short transaction, and only then does the
original move to import/_done/. A crash between two of these leaves nothing to undo: the next run
finds the same sha256 and finishes. A YouTube video (chosen by `select_downloads`, task 10b) is
downloaded into data/tmp/, renamed atomically to sources/<v_id>.mp4, then its episode is inserted
and the video marked `downloaded` in the same transaction.
"""

import os
import re
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
from rushes.db.models import Episode, SourceVideo
from rushes.errors import MediaError, RushesError, SourceUnavailable
from rushes.fsutil import atomic_path
from rushes.ids import episode_key, source_key
from rushes.ingest.owner import choose_owner
from rushes.ingest.select import select_downloads
from rushes.lock import try_lock
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


@dataclass(frozen=True)
class DownloadsBusy:
    """Another ingest holds the YouTube-download lock: this run downloads nothing."""


Item = ImportItem | int | DownloadsBusy  # an import file, a selected source video id, or a notice

LOCK_NAME = "ingest-youtube.lock"
_STALE_DOWNLOAD = re.compile(r"dl-\d+")  # `data/tmp/dl-<pid>`: the folders of this step


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

    def __init__(self) -> None:
        self._lock_fd: int | None = None  # the YouTube-download lock, held from select to finish

    def key(self, item: Item) -> str:
        if isinstance(item, DownloadsBusy):
            return "youtube"
        if isinstance(item, int):
            return source_key(item)
        return f"{item.folder}/{item.path.name}" if item.folder else item.path.name

    def select(self, ctx: Context, filters: Filters) -> Iterable[Item]:
        if filters.episode is not None or filters.account is not None:
            return []  # nothing not ingested yet has an episode or an owner
        items: list[Item] = list(self._import_files(ctx, filters))
        if filters.creator is not None:
            return items  # a discovery belongs to a series: no YouTube part, no lock
        self._release()  # a second select on the same step must not keep two descriptors
        self._lock_fd = try_lock(ctx.paths, LOCK_NAME)  # local imports go on without it
        if self._lock_fd is None:
            return [*items, DownloadsBusy()]
        try:
            self._sweep_stale_downloads(ctx)
            with Session(ctx.engine) as session:
                items.extend(
                    select_downloads(
                        session,
                        ctx.config,
                        ctx.config.settings.ingest,
                        today=ctx.clock.now().date(),
                        series=filters.series,
                    )
                )
        except BaseException:
            self._release()  # the runner never calls `finish` after a failed select
            raise
        return items

    def _release(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)  # closing releases the flock
            self._lock_fd = None

    def finish(self, ctx: Context) -> None:
        self._release()

    @staticmethod
    def _import_files(ctx: Context, filters: Filters) -> list[ImportItem]:
        root = ctx.paths.imports
        if not root.is_dir():
            return []
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

    def run(self, ctx: Context, item: Item, *, force: bool) -> Outcome:
        if isinstance(item, DownloadsBusy):
            return Outcome(
                "warned",
                "un autre ingest télécharge déjà (les vidéos YouTube attendent le prochain)",
            )
        if isinstance(item, int):
            return self._download(ctx, item)
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

    # --- YouTube videos (task 10b) -------------------------------------------------------------

    def _download(self, ctx: Context, video_id: int) -> Outcome:
        key = source_key(video_id)
        with Session(ctx.engine) as session:
            video = session.get(SourceVideo, video_id)
            if video is None or video.status not in ("retained", "failed"):
                return Outcome("skipped", f"{key} n'est plus à télécharger")
            url, kind = video.url, video.kind
            series_id, creator_id = video.series_id, video.creator_id
            if video.platform != "youtube" or (series_id is None) == (creator_id is None):
                return Outcome("warned", f"{key} : source ou propriétaire inutilisable")
            try:  # no download for a video that no account may own (R4)
                choose_owner(
                    session,
                    ctx.config,
                    series_id=series_id,
                    creator_id=creator_id,
                    today=ctx.clock.now().date(),
                )
            except RushesError as exc:
                return Outcome("warned", f"{key} : {exc}")

        target = ctx.paths.sources / f"{key}.mp4"
        if not target.exists():  # else: left by a run that stopped before the database
            work = ctx.paths.tmp / f"dl-{os.getpid()}"
            try:
                shutil.rmtree(work, ignore_errors=True)
                work.mkdir(parents=True)
                try:
                    downloaded = ctx.net.ytdlp.download(
                        url, work, max_height=ctx.config.settings.ingest.max_height
                    )
                except SourceUnavailable as exc:
                    self._set_status(ctx, video_id, "failed")  # tried again at the next run
                    hint = f" ({exc.hint})" if exc.hint else ""
                    return Outcome("failed", f"{key} : {exc}{hint}", source_unavailable=True)
                with atomic_path(target) as tmp:
                    shutil.move(downloaded, tmp)
            finally:
                shutil.rmtree(work, ignore_errors=True)

        try:
            digest = sha256_file(target)
            info = probe(target)
        except MediaError as exc:
            target.unlink(missing_ok=True)
            self._set_status(ctx, video_id, "failed")
            return Outcome("failed", f"{key} : fichier téléchargé illisible ({exc})")
        if self._known(ctx, digest):
            return self._duplicate(ctx, video_id, target)
        try:
            owner, episode_id = self._insert_download(
                ctx, video_id, kind, series_id, creator_id, digest, target, info.duration_ms
            )
        except IntegrityError:
            if not self._known(ctx, digest):
                raise
            return self._duplicate(ctx, video_id, target)
        return Outcome("done", f"{episode_key(episode_id)} → {owner}")

    @staticmethod
    def _sweep_stale_downloads(ctx: Context) -> None:
        """Remove the `data/tmp/dl-<pid>` folders of other (killed) processes; nothing else.

        Called under the YouTube-download lock, so no other ingest is writing in them.
        """
        if not ctx.paths.tmp.is_dir():
            return
        mine = f"dl-{os.getpid()}"
        for entry in ctx.paths.tmp.iterdir():
            if (
                _STALE_DOWNLOAD.fullmatch(entry.name)
                and entry.name != mine
                and entry.is_dir()
                and not entry.is_symlink()
            ):
                shutil.rmtree(entry, ignore_errors=True)

    def _duplicate(self, ctx: Context, video_id: int, target: Path) -> Outcome:
        """The file is already an episode: drop the copy, never download this video again."""
        target.unlink(missing_ok=True)
        self._set_status(ctx, video_id, "rejected")
        return Outcome("skipped", f"{source_key(video_id)} : contenu déjà importé")

    @staticmethod
    def _set_status(ctx: Context, video_id: int, status: str) -> None:
        with Session(ctx.engine) as session, session.begin():
            video = session.get(SourceVideo, video_id)
            if video is not None:
                video.status = status

    @staticmethod
    def _insert_download(
        ctx: Context,
        video_id: int,
        kind: str,
        series_id: str | None,
        creator_id: str | None,
        digest: str,
        target: Path,
        duration_ms: int,
    ) -> tuple[str, int]:
        """One transaction: the episode and the `downloaded` status appear together."""
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
                source_video_id=video_id,
                provenance=kind,
                owner_account_id=owner,
                file_path=ctx.paths.relative(target),
                sha256=digest,
                duration_ms=duration_ms,
                status="imported",
            )
            session.add(episode)
            video = session.get(SourceVideo, video_id)
            assert video is not None
            video.status = "downloaded"
            session.flush()
            return owner, episode.id
