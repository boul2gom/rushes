"""Brand charter (L35): fonts, colours, safe margins and the copy of the overlays.

`config/charter.yaml` maps each brand to its charter; the avatar is `config/avatars/<brand>.png`.
Errors are aggregated in a `ConfigError` that names the file and the brand. `load_config` reads
the charter too (lazy import: this module imports `rushes.config`), so a bad charter or avatar
fails at startup like any config error and `Config.charters` holds the result.
"""

import re
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from string import Formatter
from typing import Any

import yaml
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from rushes.config import Config, ConfigError, Issue, load_config

CHARTER_FILE = "charter.yaml"
AVATARS_DIR = "avatars"
COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# fields a copy template may use
_FIELDS: dict[str, frozenset[str]] = {
    "banner": frozenset({"title", "n"}),
    "next_part": frozenset({"n"}),
    "last_part": frozenset({"other"}),
    "follow": frozenset(),
    "subscribe": frozenset(),
}


def fonts_dir() -> Path:
    """The fonts shipped in the package (found through importlib.resources, never a fixed path)."""
    return Path(str(files("rushes").joinpath("assets", "fonts")))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SafeMargins(_Model):
    top: int = 150
    bottom: int = 480
    left: int = 60
    right: int = 140


class Copy(_Model):
    """Templates (str.format) of one language."""

    banner: str
    next_part: str
    last_part: str
    follow: str
    subscribe: str

    @field_validator("*")
    @classmethod
    def _known_fields(cls, value: str, info: Any) -> str:
        if not value.strip():
            raise ValueError("gabarit vide")
        allowed = _FIELDS[info.field_name]
        try:
            used = {name for _, name, _, _ in Formatter().parse(value) if name is not None}
        except ValueError as exc:
            raise ValueError(f"gabarit invalide : {exc}") from exc
        if used - allowed:
            names = ", ".join(sorted(used - allowed))
            expected = ", ".join(sorted(allowed)) or "aucun"
            raise ValueError(f"champ inconnu {{{names}}} (attendus : {expected})")
        return value


class BrandCharter(_Model):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    font: str
    emoji_font: str = "NotoEmoji-Regular.ttf"
    banner_bg: str
    banner_fg: str
    highlight: str
    progress: str
    safe: SafeMargins = SafeMargins()
    texts: dict[str, Copy] = Field(alias="copy")  # YAML key `copy` (BaseModel.copy exists)

    @field_validator("font", "emoji_font")
    @classmethod
    def _file_name(cls, value: str) -> str:
        """A bare file name of the packaged fonts: no folder, so no way out of `assets/fonts`."""
        if Path(value).name != value or Path(value).suffix.lower() not in (".ttf", ".otf"):
            raise ValueError(f"nom de fichier .ttf ou .otf sans dossier attendu : {value}")
        return value

    @field_validator("banner_bg", "banner_fg", "highlight", "progress")
    @classmethod
    def _color(cls, value: str) -> str:
        if not COLOR.fullmatch(value):
            raise ValueError(f"couleur #RRGGBB attendue : {value}")
        return value

    def font_path(self) -> Path:
        return fonts_dir() / self.font

    def emoji_font_path(self) -> Path:
        return fonts_dir() / self.emoji_font


def _avatar_problem(path: Path) -> str | None:
    """None when the picture opens and decodes, else why not (French)."""
    try:
        with Image.open(path) as picture:
            picture.load()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        return f"photo de profil illisible : {exc}"
    return None


def charters_for(config_dir: Path, brands: Mapping[str, str]) -> dict[str, BrandCharter]:
    """Charter of every brand (`{id: language}`); ConfigError (every problem listed) otherwise.

    A brand needs its entry, its fonts in the package, its copy in the brand's language and a
    readable `config/avatars/<brand>.png`."""
    issues: list[Issue] = []
    path = config_dir / CHARTER_FILE
    raw: Any = None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        issues.append(Issue(CHARTER_FILE, "", f"lecture impossible : {exc.strerror}"))
    except yaml.YAMLError as exc:
        issues.append(Issue(CHARTER_FILE, "", f"YAML invalide : {exc}"))
    if raw is not None and not isinstance(raw, dict):
        issues.append(Issue(CHARTER_FILE, "", "un dictionnaire marque -> charte est attendu"))
        raw = None
    charters: dict[str, BrandCharter] = {}
    for brand, language in brands.items() if raw is not None else ():
        if brand not in raw:
            issues.append(Issue(CHARTER_FILE, brand, f"charte absente pour la marque {brand}"))
            continue
        try:
            charter = BrandCharter.model_validate(raw[brand])
        except ValidationError as exc:
            for err in exc.errors():
                where = ".".join([brand, *(str(p) for p in err["loc"])])
                message = "champ inconnu" if err["type"] == "extra_forbidden" else err["msg"]
                issues.append(Issue(CHARTER_FILE, where, message.removeprefix("Value error, ")))
            continue
        problems = len(issues)
        for field_name, name in (("font", charter.font), ("emoji_font", charter.emoji_font)):
            if not (fonts_dir() / name).is_file():
                issues.append(
                    Issue(CHARTER_FILE, f"{brand}.{field_name}", f"police absente : {name}")
                )
        if language not in charter.texts:
            issues.append(
                Issue(CHARTER_FILE, f"{brand}.copy", f"textes absents pour la langue {language}")
            )
        avatar = avatar_path(config_dir, brand)
        where = f"{AVATARS_DIR}/{brand}.png"
        if not avatar.is_file():
            issues.append(Issue(where, brand, f"photo de profil absente pour la marque {brand}"))
        elif (why := _avatar_problem(avatar)) is not None:
            issues.append(Issue(where, brand, f"marque {brand} : {why}"))
        if len(issues) == problems:
            charters[brand] = charter
    if isinstance(raw, dict):
        for extra in sorted(set(raw) - set(brands)):
            issues.append(Issue(CHARTER_FILE, str(extra), "marque inconnue de accounts.yaml"))
    if issues:
        raise ConfigError(issues)
    return charters


def load_charter(config_dir: Path, config: Config | None = None) -> dict[str, BrandCharter]:
    """Charter of every brand of the config (read from `config_dir` when not given).

    `config.charters` already holds it when the config comes from `load_config`."""
    loaded = config or load_config(config_dir, charters=False)
    return charters_for(config_dir, {b.id: b.language for b in loaded.accounts.brands})


def avatar_path(config_dir: Path, brand: str) -> Path:
    return config_dir / AVATARS_DIR / f"{brand}.png"
