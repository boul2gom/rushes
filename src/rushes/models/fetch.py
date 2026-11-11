"""Model files: the pinned manifest, `rushes models fetch` and `rushes models check` (task 9).

`manifest.toml` fixes every file (URL, size, sha256, license). A file that is already present with
the right sha256 is skipped. A downloaded file is verified before it takes its final name (an
atomic rename); a wrong sha256 deletes it and raises. Models shipped as archives are downloaded
once per archive, the archive sha256 is checked before anything is extracted, and only the listed
members are written (never a path from the archive itself, never a link).
"""

import fcntl
import hashlib
import shutil
import tarfile
import tempfile
import tomllib
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import IO, Literal

from rushes.errors import RushesError
from rushes.fsutil import atomic_path
from rushes.net.base import Http
from rushes.paths import Paths

Status = Literal["ok", "missing", "bad_sha256"]
CHUNK = 1 << 20
LOCK_NAME = ".fetch.lock"


@dataclass(frozen=True)
class ModelFile:
    name: str  # the model (`whisper_small`): several files share it
    file: str  # path under paths.cache_models, e.g. `whisper_small/model.bin`
    url: str  # the file, or the archive that holds it
    sha256: str  # of the file as stored in the cache
    bytes: int
    license: str
    archive_member: str = ""  # path inside the archive; empty for a direct download
    archive_sha256: str = ""  # of the whole archive, checked before extracting
    archive_bytes: int = 0  # size of the whole archive: bound of its download (0: unknown)


def _safe_relative(value: str) -> PurePosixPath | None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        return None
    return path


def load_manifest() -> list[ModelFile]:
    """The packaged `manifest.toml`: one `[[model]]` table per file, in file order."""
    text = resources.files("rushes.models").joinpath("manifest.toml").read_text("utf-8")
    entries = []
    for name, tables in tomllib.loads(text).items():
        for table in tables:
            entries.append(ModelFile(name=name, **table))
    return entries


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _target(paths: Paths, entry: ModelFile) -> Path:
    if _safe_relative(entry.file) is None:
        raise RushesError(
            f"chemin de modèle invalide pour {entry.name} : {entry.file!r}",
            hint="Le manifeste ne doit contenir que des chemins relatifs sans « .. ».",
        )
    return paths.cache_models / entry.file


def _state(paths: Paths, entry: ModelFile) -> Status:
    target = _target(paths, entry)
    if not target.is_file():
        return "missing"
    return "ok" if _sha256(target) == entry.sha256 else "bad_sha256"


def check(paths: Paths, *, manifest: Sequence[ModelFile] | None = None) -> dict[str, Status]:
    """State of every manifest file (keyed by `file`), read from disk: no network."""
    return {e.file: _state(paths, e) for e in (load_manifest() if manifest is None else manifest)}


def _bad_hash(entry: ModelFile, *, what: str = "") -> RushesError:
    return RushesError(
        f"empreinte incorrecte pour {entry.name}",
        hint=(
            f"{what or entry.file} ne correspond pas au manifeste (corrompu ou modifié en amont) : "
            "relance `rushes models fetch`."
        ),
    )


def _check_size(path: Path, expected: int, entry: ModelFile) -> None:
    """A cut or endless download is told apart from a wrong hash (same size, other content)."""
    size = path.stat().st_size
    if expected and size != expected:
        raise RushesError(
            f"téléchargement incomplet ou trop long pour {entry.name}",
            hint=f"{size} octets reçus, {expected} attendus : relance `rushes models fetch`.",
        )


def _fetch_direct(http: Http, paths: Paths, entry: ModelFile) -> None:
    target = _target(paths, entry)
    target.unlink(missing_ok=True)  # a wrong file never survives a refetch
    with atomic_path(target) as tmp:
        http.download(entry.url, tmp, max_bytes=entry.bytes or None)
        _check_size(tmp, entry.bytes, entry)
        if _sha256(tmp) != entry.sha256:
            raise _bad_hash(entry)


def _member(tar: tarfile.TarFile, entry: ModelFile) -> tarfile.TarInfo:
    member = _safe_relative(entry.archive_member)
    if member is None:
        raise RushesError(
            f"membre d'archive invalide pour {entry.name} : {entry.archive_member!r}",
            hint="Le manifeste ne doit contenir que des chemins relatifs sans « .. ».",
        )
    try:
        info = tar.getmember(str(member))
    except KeyError:
        raise RushesError(
            f"fichier absent de l'archive pour {entry.name} : {entry.archive_member}",
            hint="L'archive a changé en amont : vérifie le manifeste.",
        ) from None
    if not info.isreg():  # no symlink, hard link, device, folder
        raise RushesError(
            f"membre d'archive refusé pour {entry.name} : {entry.archive_member}",
            hint="Seuls les fichiers ordinaires sont extraits.",
        )
    return info


def _extract(tar: tarfile.TarFile, info: tarfile.TarInfo, tmp: Path) -> str:
    source: IO[bytes] | None = tar.extractfile(info)
    if source is None:  # pragma: no cover - isreg() members always have a file object
        raise RushesError("membre d'archive illisible")
    digest = hashlib.sha256()
    with source, tmp.open("wb") as out:
        while chunk := source.read(CHUNK):
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def _fetch_archive(http: Http, paths: Paths, members: Sequence[ModelFile]) -> None:
    """Download one archive, verify it, then extract exactly `members`."""
    first = members[0]
    paths.cache_models.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".fetch-", dir=paths.cache_models))
    try:
        archive = work / "archive"
        http.download(first.url, archive, max_bytes=first.archive_bytes or None)
        _check_size(archive, first.archive_bytes, first)
        if _sha256(archive) != first.archive_sha256:
            raise _bad_hash(first, what="l'archive")
        with tarfile.open(archive, "r:*") as tar:
            for entry in members:
                info = _member(tar, entry)
                with atomic_path(_target(paths, entry)) as tmp:
                    if _extract(tar, info, tmp) != entry.sha256:
                        raise _bad_hash(entry)
    finally:
        shutil.rmtree(work, ignore_errors=True)


@contextmanager
def _exclusive(root: Path) -> Iterator[None]:
    """One `models fetch` at a time: a flock on `.fetch.lock`, released when the process dies."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / LOCK_NAME).open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RushesError(
                "un autre `models fetch` est en cours",
                hint="Attends sa fin (il télécharge dans le même dossier) puis relance.",
            ) from None
        yield  # closing the file releases the lock


def _remove_orphans(root: Path) -> None:
    """Delete what a killed run left behind: `.fetch-*` folders, `.*.tmp-*` and `*.part` files."""
    if not root.is_dir():
        return
    for path in list(root.rglob("*")):
        name = path.name
        if path.is_symlink():
            continue
        if path.is_dir() and name.startswith(".fetch-"):
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file() and (name.endswith(".part") or (name[:1] == "." and ".tmp-" in name)):
            path.unlink(missing_ok=True)


def fetch(
    http: Http,
    paths: Paths,
    *,
    only: Sequence[str] = (),
    manifest: Sequence[ModelFile] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[str]:
    """Download what is missing or wrong; returns the names of the models that were fetched.

    `only` limits the work to those model names (RushesError for an unknown one). `progress` is
    called with a model name just before its first download.
    """
    entries = list(load_manifest() if manifest is None else manifest)
    names = list(dict.fromkeys(entry.name for entry in entries))
    unknown = [name for name in only if name not in names]
    if unknown:
        raise RushesError(
            f"modèle inconnu : {', '.join(unknown)}",
            hint=f"Noms valides : {', '.join(names)}.",
        )
    with _exclusive(paths.cache_models):
        _remove_orphans(paths.cache_models)  # only under the lock: no other fetch is writing
        todo = [
            entry
            for entry in entries
            if (not only or entry.name in only) and _state(paths, entry) != "ok"
        ]
        announced: set[str] = set()

        def announce(name: str) -> None:
            if name not in announced:
                announced.add(name)
                if progress is not None:
                    progress(name)

        groups: dict[tuple[str, str], list[ModelFile]] = {}
        for entry in todo:
            if entry.archive_member:  # one download per archive, whatever the tables that cite it
                groups.setdefault((entry.url, entry.archive_sha256), []).append(entry)
            else:
                announce(entry.name)
                _fetch_direct(http, paths, entry)
        for members in groups.values():
            announce(members[0].name)
            _fetch_archive(http, paths, members)
        return [name for name in names if name in {entry.name for entry in todo}]
