"""YAML configuration validated by Pydantic. Errors are aggregated and name file + field."""

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

from rushes.core.text import hashtag_from_name, normalize_hashtag
from rushes.ui import register_secret

PLACEHOLDER = re.compile(r"<[^>]*>")
MIN_MONETIZABLE_S = 60  # R6: every family lasts more than one minute
MAX_HASHTAGS = 5  # R13

Originality = Literal["base", "advanced"]
Layout = Literal["square", "vertical", "portrait", "full"]
Vertical = Literal["cartoons", "creators"]
Platform = Literal["tiktok", "instagram"]

HASHTAG_TOKENS: frozenset[str] = frozenset({"{series}", "{creator}", "{nostalgia}"})
FORBIDDEN_HASHTAGS: frozenset[str] = frozenset({"#viral", "#foryou"})
HASHTAG = re.compile(r"^#[a-z0-9_]+$")
CHANNEL_ID: re.Pattern[str] = re.compile(r"^UC[\w-]{22}$")
TIKTOK_HANDLE: re.Pattern[str] = re.compile(r"^@(?=[\w.]*\w)[\w.]+$")

# Starting values of the spec, used while a recalibrable key is absent (a written value wins).
DEFAULT_SLOTS: dict[Platform, list[str]] = {"tiktok": ["15:00", "18:00"], "instagram": ["17:00"]}
DEFAULT_SLOTS_PARIS: dict[Platform, list[str]] = {  # evening slots for Europe/Paris brands
    "tiktok": ["18:00", "20:00"],
    "instagram": ["19:00"],
}
DEFAULT_SHARE: dict[str, dict[Platform, float]] = {
    "monetizable": {"tiktok": 1.0, "instagram": 1.0},
    "long": {"tiktok": 0.0, "instagram": 0.0},
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Issue:
    file: str
    location: str
    message: str

    def __str__(self) -> str:
        where = f" ({self.location})" if self.location else ""
        return f"{self.file}{where}: {self.message}"


class ConfigError(Exception):
    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__("\n".join(f"  - {i}" for i in issues))


def _env_secret(name: str) -> SecretStr:
    """Read a secret on use, never at load time (F7)."""
    value = os.environ.get(name)
    if not value:
        raise ConfigError([Issue("environnement", name, "variable absente ou vide")])
    register_secret(value)  # R47: no output may ever show it
    return SecretStr(value)


# --- settings.yaml -------------------------------------------------------------------------


class Threshold(_Model):
    cartoons: float = Field(ge=0, le=1)
    creators: float = Field(ge=0, le=1)


class RegistrySettings(_Model):
    overlap_tolerance_s: float = Field(ge=0)
    phash_max_distance: int = Field(ge=0)


class LlmSettings(_Model):
    enabled: bool
    model: str
    fallback: str
    threads: int = Field(gt=0)

    @model_validator(mode="after")
    def _enabled_needs_real_model(self) -> LlmSettings:
        if self.enabled:
            for name in ("model", "fallback"):
                if PLACEHOLDER.search(getattr(self, name)):
                    raise ValueError(f"{name}: valeur à renseigner (placeholder) car llm.enabled")
        return self


class VoiceSettings(_Model):
    engine: Literal["supertonic", "kokoro"] = "supertonic"
    duck_db: float = Field(-12, lt=0)


class OriginalitySettings(_Model):
    default: Originality = "base"  # overridden per account (R50)
    voice: VoiceSettings = VoiceSettings()


class YoutubeSettings(_Model):
    cookies: Literal["never", "fallback", "always"] = "fallback"


class ShowcaseSettings(_Model):
    max_episodes_per_week: int = Field(4, ge=0)


class ServeSettings(_Model):
    host: str = "0.0.0.0"  # behind the reverse proxy
    port: int = Field(8080, gt=0, lt=65536)
    public_origin: str  # free text: a placeholder is tolerated until `rushes serve` starts


class ModelSettings(_Model):
    whisper: Literal["base", "small"] = "small"
    clip_pretrained: str = "datacomp_xl_s13b_b90k"


class NtfySettings(_Model):
    url: str
    topic_env: str
    token_env: str | None = None

    def topic(self) -> SecretStr:
        return _env_secret(self.topic_env)

    def token(self) -> SecretStr | None:
        return _env_secret(self.token_env) if self.token_env else None


class Settings(_Model):
    rate_limit_s: float = Field(gt=0)
    approval_threshold: Threshold | None = None  # None: 0.7 per vertical, recalibrated
    registry: RegistrySettings
    llm: LlmSettings
    originality: OriginalitySettings = OriginalitySettings()
    youtube: YoutubeSettings = YoutubeSettings()
    showcase: ShowcaseSettings = ShowcaseSettings()
    serve: ServeSettings | None = None
    models: ModelSettings = ModelSettings()
    ntfy: NtfySettings | None = None


# --- accounts.yaml -------------------------------------------------------------------------


class Brand(_Model):
    id: str
    vertical: Vertical
    language: str
    timezone: str


class Account(_Model):
    id: str
    brand: str
    platform: Platform
    handle: str
    originality: Originality | None = None  # None: settings.originality.default (R50)


class RotationPeriod(BaseModel):
    """`from`/`to` plus one key per brand listing the series it receives."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    from_: date = Field(alias="from")
    to: date

    @model_validator(mode="after")
    def _check(self) -> RotationPeriod:
        if self.to < self.from_:
            raise ValueError("to: la fin précède le début")
        for brand, series in self.series_by_brand.items():
            if not (isinstance(series, list) and all(isinstance(s, str) for s in series)):
                raise ValueError(f"{brand}: liste d'identifiants de séries attendue")
        return self

    @property
    def series_by_brand(self) -> dict[str, list[str]]:
        return dict(self.model_extra or {})


class Share(_Model):
    tiktok: float = Field(ge=0, le=1)
    instagram: float = Field(ge=0, le=1)


class Family(_Model):
    min_s: float
    max_s: float
    share: Share | None = None  # None: starting share of the spec, recalibrated

    @model_validator(mode="after")
    def _bounds(self) -> Family:
        if self.min_s <= MIN_MONETIZABLE_S:
            raise ValueError(
                f"min_s: doit dépasser {MIN_MONETIZABLE_S} s pour rester monétisable (R6)"
            )
        if self.min_s >= self.max_s:
            raise ValueError("min_s: doit être inférieur à max_s (R6)")
        return self


class LayoutSettings(_Model):
    preference: list[Literal["square", "portrait", "full"]] = ["square", "portrait", "full"]
    vertical_share: float | None = Field(
        None, ge=0, le=1
    )  # None: rushes decides (0.15, recalibrated)

    @field_validator("preference", mode="before")
    @classmethod
    def _no_vertical(cls, value: Any) -> Any:
        if isinstance(value, list) and "vertical" in value:
            raise ValueError("vertical se règle par vertical_share, pas par preference")
        return value

    @field_validator("preference")
    @classmethod
    def _no_duplicate(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("doublon dans preference")
        return value


def _hashtag_problem(tag: str, platform: str) -> str | None:
    if tag in HASHTAG_TOKENS:
        return None
    if tag.startswith("{"):
        return f"jeton inconnu : {tag} (attendus : {', '.join(sorted(HASHTAG_TOKENS))})"
    if tag in FORBIDDEN_HASHTAGS:
        return f"{tag} interdit (R13)"
    if platform == "instagram" and tag == "#fyp":
        return "#fyp interdit sur instagram (R13)"
    if not HASHTAG.fullmatch(tag):
        return f"hashtag invalide : {tag} (minuscules, chiffres et _ après #)"
    return None


def _line_error(loc: tuple[str | int, ...], message: str, value: Any) -> InitErrorDetails:
    """One validation issue at its own location (pydantic prefixes the field name)."""
    return InitErrorDetails(type=PydanticCustomError("config", message), loc=loc, input=value)


class AccountsConfig(_Model):
    brands: list[Brand]
    accounts: list[Account]
    rotation: list[RotationPeriod] = []  # optional: rushes generates the plan
    creators_by_brand: dict[str, list[str]] = {}
    families: dict[str, Family]
    required_hashtags: dict[Vertical, dict[Platform, list[str]]]  # R13, Louis's decision
    layouts: LayoutSettings = LayoutSettings()
    posts_per_day: dict[Platform, float]
    slots: dict[Platform, list[str]] = {}  # absent platform: default for the brand timezone
    upload_defaults: dict[Platform, dict[str, Any]] = {}

    @field_validator("required_hashtags")
    @classmethod
    def _check_required_hashtags(
        cls, value: dict[Vertical, dict[Platform, list[str]]]
    ) -> dict[Vertical, dict[Platform, list[str]]]:
        errors: list[InitErrorDetails] = []
        for vertical, per_platform in value.items():
            for platform, tags in per_platform.items():
                if not tags:
                    errors.append(
                        _line_error(
                            (vertical, platform),
                            "au moins un hashtag obligatoire est requis (R13)",
                            tags,
                        )
                    )
                if len(tags) > MAX_HASHTAGS:
                    errors.append(
                        _line_error(
                            (vertical, platform), f"{MAX_HASHTAGS} hashtags au maximum (R13)", tags
                        )
                    )
                seen: set[str] = set()
                for i, tag in enumerate(tags):
                    problem = _hashtag_problem(tag, platform)
                    if problem is None:  # duplicates only among well-formed tags
                        if tag in seen:
                            problem = f"doublon : {tag}"
                        seen.add(tag)
                    if problem is not None:
                        errors.append(_line_error((vertical, platform, i), problem, tag))
        if errors:
            raise ValidationError.from_exception_data("required_hashtags", errors)
        return value

    @model_validator(mode="after")
    def _cover_verticals(self) -> AccountsConfig:
        verticals: set[Vertical] = {b.vertical for b in self.brands}
        errors = [
            _line_error(
                ("required_hashtags", vertical, platform),
                f"hashtags obligatoires manquants pour {vertical} sur {platform} : "
                "verticale portée par une marque (R13)",
                None,
            )
            for vertical in sorted(verticals)
            for platform in get_args(Platform)
            if platform not in self.required_hashtags.get(vertical, {})
        ]
        if errors:
            raise ValidationError.from_exception_data("AccountsConfig", errors)
        return self


# --- series/*.yaml, creators/*.yaml ------------------------------------------------------


class Rights(_Model):
    holder: str | None = None
    status: Literal["to_request", "requested", "granted", "refused"] = "to_request"
    requested_at: date | None = None
    notes: str = ""


def _channel_id(value: str) -> str:
    if not CHANNEL_ID.fullmatch(value):
        raise ValueError(f"identifiant de chaîne YouTube invalide (UC + 22 caractères) : {value}")
    return value


def _tiktok_handle(value: str) -> str:
    if not TIKTOK_HANDLE.fullmatch(value):
        raise ValueError(f"compte TikTok invalide (@nom) : {value}")
    return value


class YoutubeChannel(_Model):
    id: Annotated[str, AfterValidator(_channel_id)]
    kind: Literal["official", "community"]


class SeriesSources(_Model):
    youtube_channels: list[YoutubeChannel] = []
    tiktok_accounts: list[Annotated[str, AfterValidator(_tiktok_handle)]] = []
    search_queries: list[str] = []


class IntroOutro(_Model):
    reference_frames: list[str] = []
    theme_audio: str | None = None


class Character(_Model):
    id: str
    refs: list[str] = []


class Branding(_Model):
    """Optional hints: rushes derives hashtags from trends and from the sector's videos."""

    display_name: str | None = None
    hashtags_pool: list[str] = []


class SeriesConfig(_Model):
    id: str
    title: str
    audio: Literal["nonverbal", "dialogue_en"]
    licensed: bool  # Louis's decision (R14): never defaulted
    rights: Rights | None = None
    sources: SeriesSources = SeriesSources()  # seeds only: rushes finds the official channels
    intro_outro: IntroOutro = IntroOutro()
    characters: list[Character] = []
    branding: Branding = Branding()


class Campaign(_Model):
    url: str
    required_mentions: list[str] = []
    required_hashtags: list[str] = []


class CreatorConfig(_Model):
    id: str
    language: str
    youtube_channel: str
    authorized: bool  # Louis's decision (R22): never defaulted
    rights: Rights | None = None
    campaign: Campaign | None = None
    credit_template: str
    hashtag: str | None = None  # default: the creator's name without spaces (R13)

    @field_validator("hashtag")
    @classmethod
    def _normalize(cls, value: str | None) -> str | None:
        if value is None:
            return None
        tag = normalize_hashtag(value)
        if tag == "#":
            raise ValueError("hashtag vide")
        return tag

    @property
    def effective_hashtag(self) -> str:
        return self.hashtag or hashtag_from_name(self.id.replace("_", " "))


# --- loading ---------------------------------------------------------------------------------


@dataclass
class Config:
    settings: Settings
    accounts: AccountsConfig
    series: list[SeriesConfig]
    creators: list[CreatorConfig]
    warnings: list[str] = field(default_factory=list)

    def __repr__(self) -> str:  # never dump anything that could carry a secret (R47)
        return (
            f"Config(brands={len(self.accounts.brands)}, accounts={len(self.accounts.accounts)}, "
            f"series={len(self.series)}, creators={len(self.creators)})"
        )

    def account(self, account_id: str) -> Account:
        for acc in self.accounts.accounts:
            if acc.id == account_id:
                return acc
        raise ConfigError([Issue("accounts.yaml", "accounts", f"compte inconnu : {account_id}")])

    def brand(self, brand_id: str) -> Brand:
        for brand in self.accounts.brands:
            if brand.id == brand_id:
                return brand
        raise ConfigError([Issue("accounts.yaml", "brands", f"marque inconnue : {brand_id}")])

    def brand_of(self, account_id: str) -> Brand:
        return self.brand(self.account(account_id).brand)

    def series_by_id(self, series_id: str) -> SeriesConfig | None:
        return next((s for s in self.series if s.id == series_id), None)

    def creator_by_id(self, creator_id: str) -> CreatorConfig | None:
        return next((c for c in self.creators if c.id == creator_id), None)

    def effective_originality(self, account_id: str) -> Originality:
        """R50: the account's module, else the default of settings.yaml."""
        return self.account(account_id).originality or self.settings.originality.default

    def slots_for(self, account_id: str) -> list[str]:
        """Written slots, else the starting slots for the platform and the brand timezone."""
        acc = self.account(account_id)
        written = self.accounts.slots.get(acc.platform)
        if written is not None:
            return list(written)
        paris = self.brand(acc.brand).timezone == "Europe/Paris"
        return list((DEFAULT_SLOTS_PARIS if paris else DEFAULT_SLOTS)[acc.platform])

    def family_share(self, family: str, platform: Platform) -> float:
        """Written share, else the starting share of the spec."""
        fam = self.accounts.families.get(family)
        if fam is None:
            raise ConfigError([Issue("accounts.yaml", "families", f"famille inconnue : {family}")])
        if fam.share is not None:
            return fam.share.tiktok if platform == "tiktok" else fam.share.instagram
        if family not in DEFAULT_SHARE:
            raise ConfigError(
                [Issue("accounts.yaml", f"families.{family}", "share: part de départ inconnue")]
            )
        return DEFAULT_SHARE[family][platform]


# --- legacy keys -----------------------------------------------------------------------------

_D1_MESSAGE = "Postiz a été abandonné (décision D1 du 2026-11-01) : retirer cette clé"
_ANCHOR_MESSAGE = "remplacé par required_hashtags (R13) et hashtag du créateur : retirer cette clé"

# (file prefix, path of the key — "*" matches every list index, message)
_LEGACY_KEYS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("settings.yaml", ("postiz",), _D1_MESSAGE),
    ("settings.yaml", ("manual_posts_notice",), _D1_MESSAGE),
    ("accounts.yaml", ("accounts", "*", "publish_mode"), _D1_MESSAGE),
    ("accounts.yaml", ("accounts", "*", "postiz_integration"), _D1_MESSAGE),
    ("accounts.yaml", ("platform_hashtags",), _D1_MESSAGE),
    ("series/", ("branding", "hashtags_anchor"), _ANCHOR_MESSAGE),
    ("creators/", ("hashtags_anchor",), _ANCHOR_MESSAGE),
)


def _find_key(
    node: Any, path: tuple[str, ...], loc: tuple[str, ...]
) -> Iterator[tuple[dict[str, Any], str, str]]:
    head, *rest = path
    if head == "*":
        if isinstance(node, list):
            for i, item in enumerate(node):
                yield from _find_key(item, tuple(rest), (*loc, str(i)))
    elif isinstance(node, dict) and head in node:
        if rest:
            yield from _find_key(node[head], tuple(rest), (*loc, head))
        else:
            yield node, head, ".".join((*loc, head))


def _legacy_keys(rel: str, data: Any, issues: list[Issue]) -> None:
    """Report removed keys with a dedicated message, then drop them (no second 'champ inconnu')."""
    for prefix, path, message in _LEGACY_KEYS:
        if not rel.startswith(prefix):
            continue
        for node, key, location in list(_find_key(data, path, ())):
            issues.append(Issue(rel, location, message))
            del node[key]


# --- reading and validating ------------------------------------------------------------------


def _pretty_loc(loc: tuple[Any, ...]) -> str:
    return ".".join(str(p) for p in loc)


def _read_yaml(path: Path, rel: str, issues: list[Issue]) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        problem = getattr(exc, "problem", None) or "YAML invalide"
        issues.append(Issue(rel, where, f"YAML invalide : {problem}"))
    except OSError as exc:
        issues.append(Issue(rel, "", f"lecture impossible : {exc.strerror}"))
    return None


def _validate[M: BaseModel](model: type[M], data: Any, rel: str, issues: list[Issue]) -> M | None:
    if data is None:
        issues.append(Issue(rel, "", "fichier vide"))
        return None
    _legacy_keys(rel, data, issues)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            loc = _pretty_loc(err["loc"])
            msg = err["msg"].removeprefix("Value error, ")
            if err["type"] == "extra_forbidden":
                msg = "champ inconnu"
            issues.append(Issue(rel, loc, msg))
    return None


def _load_dir[M: BaseModel](
    root: Path, sub: str, model: type[M], issues: list[Issue]
) -> list[tuple[str, M]]:
    out: list[tuple[str, M]] = []
    directory = root / sub
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.yaml")):
        rel = f"{sub}/{path.name}"
        item = _validate(model, _read_yaml(path, rel, issues), rel, issues)
        if item is None:
            continue
        if getattr(item, "id", None) != path.stem:
            issues.append(
                Issue(rel, "id", f"l'identifiant doit être le nom du fichier ({path.stem})")
            )
            continue
        out.append((rel, item))
    return out


def _dupes(values: list[str]) -> set[str]:
    return {v for v in values if values.count(v) > 1}


def _check_seed_files(root: Path, rel: str, series: SeriesConfig, issues: list[Issue]) -> None:
    """Reference images and theme audio must exist under the config folder."""
    refs: list[tuple[str, str]] = [
        (f"intro_outro.reference_frames.{i}", ref)
        for i, ref in enumerate(series.intro_outro.reference_frames)
    ]
    if series.intro_outro.theme_audio is not None:
        refs.append(("intro_outro.theme_audio", series.intro_outro.theme_audio))
    for ci, character in enumerate(series.characters):
        refs.extend((f"characters.{ci}.refs.{ri}", ref) for ri, ref in enumerate(character.refs))

    base = root.resolve()
    for location, ref in refs:
        target = (base / ref).resolve()
        if not target.is_relative_to(base):
            issues.append(Issue(rel, location, f"chemin hors de la config : {ref}"))
        elif not target.is_file():
            issues.append(Issue(rel, location, f"fichier absent : {ref}"))


def _cross_checks(
    accounts: AccountsConfig,
    series: list[SeriesConfig],
    creators: list[CreatorConfig],
    issues: list[Issue],
    warnings: list[str],
) -> None:
    f = "accounts.yaml"
    brands = {b.id: b for b in accounts.brands}
    series_ids = {s.id for s in series}
    creator_by_id = {c.id: c for c in creators}

    for kind, ids in (
        ("marque", [b.id for b in accounts.brands]),
        ("compte", [a.id for a in accounts.accounts]),
    ):
        for dup in sorted(_dupes(ids)):
            issues.append(Issue(f, kind, f"identifiant en double : {dup}"))
    for dup in sorted(_dupes([s.id for s in series])):
        issues.append(Issue("series", "", f"identifiant en double : {dup}"))

    pairs = [(a.brand, a.platform) for a in accounts.accounts]
    for dup_brand, platform in sorted({p for p in pairs if pairs.count(p) > 1}):
        issues.append(Issue(f, "accounts", f"deux comptes {platform} pour la marque {dup_brand}"))

    for acc in accounts.accounts:
        if acc.brand not in brands:
            issues.append(Issue(f, f"accounts.{acc.id}.brand", f"marque inconnue : {acc.brand}"))

    for brand_id, creator_ids in accounts.creators_by_brand.items():
        if brand_id not in brands:
            issues.append(Issue(f, "creators_by_brand", f"marque inconnue : {brand_id}"))
            continue
        for cid in creator_ids:
            creator = creator_by_id.get(cid)
            if creator is None:
                issues.append(
                    Issue(f, f"creators_by_brand.{brand_id}", f"créateur inconnu : {cid}")
                )
            elif creator.language != brands[brand_id].language:
                issues.append(
                    Issue(
                        f"creators/{cid}.yaml",
                        "language",
                        f"langue {creator.language} ≠ langue de la marque {brand_id} "
                        f"({brands[brand_id].language}) (R21)",
                    )
                )
    for brand in accounts.brands:
        if brand.vertical == "creators" and not accounts.creators_by_brand.get(brand.id):
            warnings.append(f"marque {brand.id} : aucun créateur dans creators_by_brand")

    for i, period in enumerate(accounts.rotation):
        seen: dict[str, str] = {}
        for brand_id, ids in period.series_by_brand.items():
            if brand_id not in brands:
                issues.append(Issue(f, f"rotation.{i}", f"marque inconnue : {brand_id}"))
                continue
            for sid in ids:
                if sid not in series_ids:
                    issues.append(Issue(f, f"rotation.{i}.{brand_id}", f"série inconnue : {sid}"))
                elif sid in seen and seen[sid] != brand_id:
                    issues.append(
                        Issue(
                            f,
                            f"rotation.{i}",
                            f"la série {sid} est donnée à {seen[sid]} et {brand_id} "
                            "sur la même période",
                        )
                    )
                else:
                    seen[sid] = brand_id
    for brand_id in brands:
        spans = sorted((p.from_, p.to) for p in accounts.rotation if brand_id in p.series_by_brand)
        for (_, end), (start, _) in pairwise(spans):
            if start <= end:
                issues.append(
                    Issue(f, "rotation", f"périodes qui se chevauchent pour {brand_id} (R4)")
                )
                break


def load_config(config_dir: Path) -> Config:
    """Load and validate the whole configuration; raise ConfigError listing every problem."""
    if not config_dir.is_dir():
        raise ConfigError([Issue(str(config_dir), "", "dossier de configuration introuvable")])

    issues: list[Issue] = []
    warnings: list[str] = []

    settings = _validate(
        Settings,
        _read_yaml(config_dir / "settings.yaml", "settings.yaml", issues),
        "settings.yaml",
        issues,
    )
    accounts = _validate(
        AccountsConfig,
        _read_yaml(config_dir / "accounts.yaml", "accounts.yaml", issues),
        "accounts.yaml",
        issues,
    )
    series_files = _load_dir(config_dir, "series", SeriesConfig, issues)
    series = [m for _, m in series_files]
    creators = [m for _, m in _load_dir(config_dir, "creators", CreatorConfig, issues)]

    for rel, item in series_files:
        _check_seed_files(config_dir, rel, item, issues)
    if accounts is not None:
        _cross_checks(accounts, series, creators, issues, warnings)

    if issues or settings is None or accounts is None:
        raise ConfigError(issues)
    return Config(settings, accounts, series, creators, warnings)
