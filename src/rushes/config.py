"""YAML configuration validated by Pydantic. Errors are aggregated and name file + field."""

import os
import re
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)

PLACEHOLDER = re.compile(r"<[^>]*>")
MIN_MONETIZABLE_S = 60  # R6: every family lasts more than one minute


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


class PostizSettings(_Model):
    enabled: bool
    base_url: str
    api_key_env: str
    max_requests_per_hour: int = Field(gt=0)
    tiktok_mode: Literal["upload_only", "direct"]

    @model_validator(mode="after")
    def _enabled_needs_real_url(self) -> PostizSettings:
        if self.enabled and (
            PLACEHOLDER.search(self.base_url) or not self.base_url.startswith("https://")
        ):
            raise ValueError("base_url: URL https:// réelle requise car postiz.enabled")
        return self

    def api_key(self) -> SecretStr:
        """Read the secret on use, never at load time (F7)."""
        value = os.environ.get(self.api_key_env)
        if not value:
            raise ConfigError(
                [Issue("environnement", self.api_key_env, "variable absente ou vide")]
            )
        return SecretStr(value)


class NtfySettings(_Model):
    url: str
    topic_env: str


class Settings(_Model):
    rate_limit_s: float = Field(gt=0)
    approval_threshold: Threshold
    registry: RegistrySettings
    llm: LlmSettings
    manual_posts_notice: int = Field(gt=0)
    postiz: PostizSettings
    ntfy: NtfySettings


# --- accounts.yaml -------------------------------------------------------------------------

Platform = Literal["tiktok", "instagram"]


class Brand(_Model):
    id: str
    vertical: Literal["cartoons", "creators"]
    language: str
    timezone: str


class Account(_Model):
    id: str
    brand: str
    platform: Platform
    handle: str
    publish_mode: Literal["manual", "postiz"] = "manual"  # R48
    postiz_integration: str | None = None

    @model_validator(mode="after")
    def _postiz_needs_integration(self) -> Account:
        if self.publish_mode == "postiz" and not self.postiz_integration:
            raise ValueError("postiz_integration: requis quand publish_mode vaut postiz (R48)")
        return self


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
    share: Share

    @model_validator(mode="after")
    def _bounds(self) -> Family:
        if self.min_s <= MIN_MONETIZABLE_S:
            raise ValueError(
                f"min_s: doit dépasser {MIN_MONETIZABLE_S} s pour rester monétisable (R6)"
            )
        if self.min_s >= self.max_s:
            raise ValueError("min_s: doit être inférieur à max_s (R6)")
        return self


class AccountsConfig(_Model):
    brands: list[Brand]
    accounts: list[Account]
    rotation: list[RotationPeriod] = []
    creators_by_brand: dict[str, list[str]] = {}
    families: dict[str, Family]
    platform_hashtags: dict[Platform, list[str]] = {}
    posts_per_day: dict[Platform, float]
    slots: dict[Platform, list[str]]
    upload_defaults: dict[Platform, dict[str, Any]] = {}


# --- series/*.yaml, creators/*.yaml ------------------------------------------------------


class YoutubeChannel(_Model):
    id: str
    kind: Literal["official", "community"]


class SeriesSources(_Model):
    youtube_channels: list[YoutubeChannel] = []
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
    hashtags_anchor: list[str] = []
    hashtags_pool: list[str] = []


class SeriesConfig(_Model):
    id: str
    title: str
    audio: Literal["nonverbal", "dialogue_en"]
    licensed: bool  # Louis's decision (R14): never defaulted
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
    campaign: Campaign | None = None
    credit_template: str
    hashtags_anchor: list[str] = []  # seeds only: derived from trends and the sector


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
    for brand, platform in sorted({p for p in pairs if pairs.count(p) > 1}):
        issues.append(Issue(f, "accounts", f"deux comptes {platform} pour la marque {brand}"))

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
    series = [m for _, m in _load_dir(config_dir, "series", SeriesConfig, issues)]
    creators = [m for _, m in _load_dir(config_dir, "creators", CreatorConfig, issues)]

    if accounts is not None:
        _cross_checks(accounts, series, creators, issues, warnings)

    if issues or settings is None or accounts is None:
        raise ConfigError(issues)
    return Config(settings, accounts, series, creators, warnings)
