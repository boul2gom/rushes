"""`INDEX.html` of `outbox/` and `outbox_test/` (task 8b): a static page, readable without a server.

One section per account, posts in the order of their suggested slot, relative links to the video
and the cover, caption and hashtags in `<pre>`; no JavaScript. It holds nothing that changes from
one run to the next (no date of generation): a rerun rewrites no byte (R16).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import text

from rushes.context import Context
from rushes.core.timecode import parse_instant
from rushes.fsutil import atomic_path
from rushes.ids import post_key
from rushes.schemas import UploadFile, read_json

INDEX_NAME = "INDEX.html"
_ENV = SandboxedEnvironment(
    autoescape=True, undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True
)


@dataclass(frozen=True)
class Entry:
    key: str
    folder: str  # relative to INDEX.html, URL-quoted
    upload: UploadFile | None  # None: upload.json missing or unreadable
    hashtags: str = ""
    hooks: tuple[str, ...] = ()  # the applied hook, then the alternatives


@dataclass
class Section:
    account_id: str
    handle: str
    platform: str
    entries: list[Entry] = field(default_factory=list)


def _template_source() -> str:
    return files("rushes.package").joinpath("templates/index.html.j2").read_text(encoding="utf-8")


def _hooks(upload: UploadFile | None) -> tuple[str, ...]:
    if upload is None:
        return ()
    applied = [upload.hook.applied] if upload.hook.applied is not None else []
    return (*applied, *upload.hook.alternatives)


def _moment(value: str | None) -> datetime | None:
    """A stored slot as a UTC instant; None when there is none or it is malformed."""
    if value is None:
        return None
    try:
        return parse_instant(value)
    except ValueError:
        return None


def build_sections(ctx: Context, *, test: bool) -> list[Section]:
    """Ready posts of the folder, by account then by suggested slot (time order, not text order)."""
    query = text(
        "SELECT p.id, p.account_id, a.handle, a.platform, p.planned_at FROM posts p "
        "JOIN accounts a ON a.id = p.account_id WHERE p.status='ready' AND p.is_test=:t"
    )
    with ctx.engine.connect() as conn:
        rows = conn.execute(query, {"t": int(test)}).all()
    last = datetime.max.replace(tzinfo=UTC)  # no slot, or a malformed one: after the others
    rows = sorted(rows, key=lambda row: (row.account_id, _moment(row.planned_at) or last, row.id))
    sections: dict[str, Section] = {}
    for post_id, account_id, handle, platform, planned_at in rows:
        section = sections.setdefault(account_id, Section(account_id, handle, platform))
        key = post_key(post_id)
        folder = ctx.paths.post_dir(account_id, key, test=test)
        try:
            upload = read_json(UploadFile, folder / "upload.json")
        except OSError, ValueError:
            upload = None
        if planned_at is not None and _moment(planned_at) is None:
            upload = None  # a slot nobody can read: the entry says so, the page still builds
        section.entries.append(
            Entry(
                key=key,
                folder=f"{quote(account_id)}/{quote(key)}",
                upload=upload,
                hashtags=" ".join(upload.hashtags) if upload else "",
                hooks=_hooks(upload),
            )
        )
    return list(sections.values())


def index_path(ctx: Context, *, test: bool) -> Path:
    return (ctx.paths.outbox_test if test else ctx.paths.outbox) / INDEX_NAME


def render_index(sections: list[Section], *, test: bool) -> str:
    return _ENV.from_string(_template_source()).render(test=test, sections=sections)


def _write(target: Path, html: str) -> None:
    """Atomic write, skipped when the page already holds exactly these bytes (R16)."""
    try:
        if target.read_text(encoding="utf-8") == html:
            return
    except OSError:
        pass
    with atomic_path(target) as tmp:
        tmp.write_text(html, encoding="utf-8")


def write_index(ctx: Context, *, test: bool) -> Path:
    """Write `outbox/INDEX.html` (`outbox_test/INDEX.html` when `test`) if its content changed."""
    target = index_path(ctx, test=test)
    _write(target, render_index(build_sections(ctx, test=test), test=test))
    return target


def refresh_index(ctx: Context, *, test: bool) -> Path | None:
    """`write_index`, but only when there is something to list or an older page may list posts
    that left (no empty page on a fresh install). The sections are built once."""
    target = index_path(ctx, test=test)
    sections = build_sections(ctx, test=test)
    if not sections and not target.exists():
        return None
    _write(target, render_index(sections, test=test))
    return target
