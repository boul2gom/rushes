"""SQLAlchemy models. Must stay identical to the migrations (checked by a test)."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


VERTICALS = ("cartoons", "creators")
PLATFORMS = ("tiktok", "instagram")
ORIGINALITIES = ("base", "advanced")
LAYOUTS = ("square", "vertical", "portrait", "full")
ORIGINS = ("config", "prospect")
SUBJECT_STATUSES = ("candidate", "active", "dropped")
SUBJECT_KINDS = ("series", "creator")
RIGHTS_STATUSES = ("to_request", "requested", "granted", "refused")
AUDIO = ("nonverbal", "dialogue_en")
SOURCE_KINDS = ("official", "community")
PROVENANCES = ("official", "community", "local")
EPISODE_STATUSES = ("imported", "split", "cleaned", "analyzed")
CORNERS = ("tl", "tr", "bl", "br")
SEGMENT_KINDS = ("part", "bestof_item", "compilation_item")
SEGMENT_STATUSES = ("proposed", "approved", "needs_review", "rejected")
RUN_STATUSES = ("running", "ok", "partial", "failed")
COMPILATION_KINDS = ("bestof", "theme")
HOOK_ENGINES = ("rules", "llm")
POST_STATUSES = ("planned", "rendered", "ready", "published", "discarded", "expired")


class Brand(Base):
    __tablename__ = "brands"
    __table_args__ = (CheckConstraint(_in("vertical", VERTICALS), name="vertical"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    vertical: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(_in("platform", PLATFORMS), name="platform"),
        CheckConstraint(_in("originality", ORIGINALITIES), name="originality"),
        UniqueConstraint("brand_id", "platform", name="uq_accounts_brand_id_platform"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"))
    platform: Mapped[str] = mapped_column(Text)
    handle: Mapped[str] = mapped_column(Text)
    originality: Mapped[str | None] = mapped_column(Text)  # NULL: settings default (R50)


class Series(Base):
    __tablename__ = "series"
    __table_args__ = (
        CheckConstraint(_in("audio", AUDIO), name="audio"),
        CheckConstraint(_in("origin", ORIGINS), name="origin"),
        CheckConstraint(_in("status", SUBJECT_STATUSES), name="status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    audio: Mapped[str] = mapped_column(Text)
    licensed: Mapped[bool] = mapped_column(Boolean)
    origin: Mapped[str] = mapped_column(Text, server_default="config")
    status: Mapped[str] = mapped_column(Text, server_default="active")


class Creator(Base):
    __tablename__ = "creators"
    __table_args__ = (
        CheckConstraint(_in("origin", ORIGINS), name="origin"),
        CheckConstraint(_in("status", SUBJECT_STATUSES), name="status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    language: Mapped[str] = mapped_column(Text)
    channel_id: Mapped[str] = mapped_column(Text)
    authorized: Mapped[bool] = mapped_column(Boolean)
    origin: Mapped[str] = mapped_column(Text, server_default="config")
    status: Mapped[str] = mapped_column(Text, server_default="active")


class SourceVideo(Base):
    __tablename__ = "source_videos"
    __table_args__ = (
        CheckConstraint(_in("kind", SOURCE_KINDS), name="kind"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, unique=True)
    channel_id: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    series_id: Mapped[str | None] = mapped_column(ForeignKey("series.id"))
    creator_id: Mapped[str | None] = mapped_column(ForeignKey("creators.id"))
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[str | None] = mapped_column(Text)
    heatmap_json: Mapped[str | None] = mapped_column(Text)
    sponsorblock_json: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text)


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        CheckConstraint(_in("provenance", PROVENANCES), name="provenance"),
        CheckConstraint(_in("status", EPISODE_STATUSES), name="status"),
        CheckConstraint(_in("logo_corner", CORNERS), name="logo_corner"),
        CheckConstraint("(series_id IS NULL) <> (creator_id IS NULL)", name="one_origin"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[str | None] = mapped_column(ForeignKey("series.id"))
    creator_id: Mapped[str | None] = mapped_column(ForeignKey("creators.id"))
    source_video_id: Mapped[int | None] = mapped_column(ForeignKey("source_videos.id"))
    provenance: Mapped[str] = mapped_column(Text)
    owner_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))  # R44
    file_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text, unique=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    # child of a split compilation: same file as the parent, own portion of it (task 11)
    parent_episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"))
    span_start_ms: Mapped[int | None] = mapped_column(Integer)
    span_end_ms: Mapped[int | None] = mapped_column(Integer)
    logo_corner: Mapped[str | None] = mapped_column(Text)  # burned-in logo (R34)


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (
        CheckConstraint("end_ms > start_ms", name="span"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    loudness: Mapped[float | None] = mapped_column(Float)
    motion: Mapped[float | None] = mapped_column(Float)
    speech: Mapped[float | None] = mapped_column(Float)
    tags_json: Mapped[str | None] = mapped_column(Text)
    characters_json: Mapped[str | None] = mapped_column(Text)
    sound_events_json: Mapped[str | None] = mapped_column(Text)
    popularity: Mapped[float | None] = mapped_column(Float)


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        CheckConstraint(_in("kind", SEGMENT_KINDS), name="kind"),
        CheckConstraint(_in("status", SEGMENT_STATUSES), name="status"),
        CheckConstraint("end_ms > start_ms", name="span"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    kind: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    family: Mapped[str] = mapped_column(Text)
    target_ms: Mapped[int | None] = mapped_column(Integer)
    seed: Mapped[int | None] = mapped_column(Integer)
    signal_level: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text)
    phash_json: Mapped[str | None] = mapped_column(Text)


class Assignment(Base):
    __tablename__ = "assignments"

    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    assigned_at: Mapped[str] = mapped_column(Text)


class Compilation(Base):
    __tablename__ = "compilations"
    __table_args__ = (
        CheckConstraint(_in("kind", COMPILATION_KINDS), name="kind"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"))
    theme_id: Mapped[str | None] = mapped_column(Text)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    status: Mapped[str] = mapped_column(Text)


class CompilationItem(Base):
    __tablename__ = "compilation_items"

    compilation_id: Mapped[int] = mapped_column(ForeignKey("compilations.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))


class Hook(Base):
    __tablename__ = "hooks"
    __table_args__ = (
        CheckConstraint(_in("engine", HOOK_ENGINES), name="engine"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("segments.id"))
    compilation_id: Mapped[int | None] = mapped_column(ForeignKey("compilations.id"))
    text: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    engine: Mapped[str] = mapped_column(Text)
    rank: Mapped[int | None] = mapped_column(Integer)
    chosen: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("0"))


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        CheckConstraint(_in("status", POST_STATUSES), name="status"),
        CheckConstraint(_in("layout", LAYOUTS), name="layout"),
        CheckConstraint(_in("originality", ORIGINALITIES), name="originality"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("segments.id"))
    compilation_id: Mapped[int | None] = mapped_column(ForeignKey("compilations.id"))
    part_number: Mapped[int | None] = mapped_column(Integer)
    family: Mapped[str | None] = mapped_column(Text)
    layout: Mapped[str | None] = mapped_column(Text)
    originality: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    is_test: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("0"))
    rank_score: Mapped[float | None] = mapped_column(Float)
    video_path: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[str | None] = mapped_column(Text)
    hook_id: Mapped[int | None] = mapped_column(ForeignKey("hooks.id"))
    render_version: Mapped[str | None] = mapped_column(Text)
    planned_at: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[str | None] = mapped_column(Text)
    platform_url: Mapped[str | None] = mapped_column(Text)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    command: Mapped[str] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(Text)
    ended_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    summary_json: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer)


class Right(Base):
    __tablename__ = "rights"
    __table_args__ = (
        CheckConstraint(_in("subject_kind", SUBJECT_KINDS), name="subject_kind"),
        CheckConstraint(_in("status", RIGHTS_STATUSES), name="status"),
    )

    subject_kind: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(Text, primary_key=True)
    holder: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[float | None] = mapped_column(Float)  # computed potential (task 38)
    showcase_posts: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    updated_at: Mapped[str] = mapped_column(Text)


class DerivedValue(Base):
    __tablename__ = "derived_values"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    computed_at: Mapped[str] = mapped_column(Text)


class DerivedValueHistory(Base):
    __tablename__ = "derived_values_history"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    value_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    computed_at: Mapped[str] = mapped_column(Text)
