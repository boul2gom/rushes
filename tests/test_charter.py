"""Brand charter (task 7b, L35): loading, validation and the shipped fonts."""

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from rushes.config import ConfigError, load_config
from rushes.render.charter import BrandCharter, charters_for, fonts_dir, load_charter

Edit = Callable[[Path, Callable[[Any], Any]], None]
CHARTER = "charter.yaml"


def _message(error: ConfigError) -> str:
    return str(error)


def test_charter_of_the_repository_covers_every_brand(config_dir: Path) -> None:
    charters = load_charter(config_dir)
    assert set(charters) == {"saturday_toons", "toon_tape", "instant_culte", "peak_moments"}
    colours = {  # L35: banner_bg, banner_fg, highlight, progress
        "saturday_toons": ("#3B1F0E", "#FFF4DC", "#FFB627", "#FF6B35"),
        "toon_tape": ("#1E1B2E", "#FFFFFF", "#FF4FA3", "#FFD23F"),
        "instant_culte": ("#121316", "#F5F1E8", "#E63946", "#E63946"),
        "peak_moments": ("#0E1A2B", "#F5F1E8", "#F2A541", "#F2A541"),
    }
    for brand, expected in colours.items():
        c = charters[brand]
        assert (c.banner_bg, c.banner_fg, c.highlight, c.progress) == expected
        assert (c.safe.top, c.safe.bottom, c.safe.left, c.safe.right) == (150, 480, 60, 140)
        assert c.font_path().is_file() and c.emoji_font_path().is_file()
    assert charters["saturday_toons"].font == "LuckiestGuy-Regular.ttf"  # cartoons (L35)
    assert charters["toon_tape"].font == "LuckiestGuy-Regular.ttf"
    assert charters["instant_culte"].font == "BebasNeue-Regular.ttf"  # creators (L35)
    assert charters["peak_moments"].font == "BebasNeue-Regular.ttf"


def test_charter_copy_is_the_text_of_the_brief(config_dir: Path) -> None:
    charters = load_charter(config_dir)
    en = charters["saturday_toons"].texts["en"]
    assert (en.banner, en.next_part, en.last_part, en.follow, en.subscribe) == (
        "{title} — Part {n}",
        "Part {n} next",
        "Watch {other} next",
        "Follow for more",
        "Follow",
    )
    fr = charters["instant_culte"].texts["fr"]
    assert (fr.banner, fr.next_part, fr.last_part, fr.follow, fr.subscribe) == (
        "{title} — Partie {n}",
        "Partie {n} ensuite",
        "À voir ensuite : {other}",
        "Abonne-toi pour la suite",
        "Abonne-toi",
    )


def test_charter_requires_every_brand(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / CHARTER, lambda d: d.pop("peak_moments"))
    with pytest.raises(ConfigError) as caught:
        load_charter(config_dir)
    assert "peak_moments" in _message(caught.value) and CHARTER in _message(caught.value)


def test_charter_requires_avatar_file(config_dir: Path) -> None:
    (config_dir / "avatars" / "toon_tape.png").unlink()
    with pytest.raises(ConfigError) as caught:
        load_charter(config_dir)
    assert "toon_tape" in _message(caught.value) and "avatars/toon_tape.png" in _message(
        caught.value
    )


def test_charter_requires_the_copy_of_the_brand_language(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / CHARTER, lambda d: d["instant_culte"]["copy"].pop("fr"))
    with pytest.raises(ConfigError) as caught:
        load_charter(config_dir)
    assert "instant_culte" in _message(caught.value) and "fr" in _message(caught.value)


def test_charter_refuses_a_brand_unknown_to_accounts(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / CHARTER, lambda d: d.update(ghost=d["toon_tape"]))
    with pytest.raises(ConfigError) as caught:
        load_charter(config_dir)
    assert "ghost" in _message(caught.value)


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        (lambda d: d["toon_tape"].update(banner_bg="red"), "toon_tape.banner_bg"),
        (lambda d: d["toon_tape"].update(font="Nope.ttf"), "Nope.ttf"),
        (lambda d: d["toon_tape"].update(font="../../x.ttf"), "sans dossier"),
        (lambda d: d["toon_tape"].update(emoji_font="sub/NotoEmoji-Regular.ttf"), "sans dossier"),
        (lambda d: d["toon_tape"].update(font="LuckiestGuy-Regular.txt"), ".ttf ou .otf"),
        (lambda d: d["toon_tape"].update(unknown=1), "champ inconnu"),
        (
            lambda d: d["toon_tape"]["copy"]["en"].update(banner="{title} {n}/{total}"),
            "total",
        ),
        (lambda d: d["toon_tape"]["copy"]["en"].update(banner="{title"), "gabarit"),
        (lambda d: d["toon_tape"]["copy"]["en"].update(follow="{n}"), "follow"),
    ],
)
def test_charter_rejects_a_bad_entry_naming_it(
    config_dir: Path, edit_yaml: Edit, change: Callable[[Any], Any], needle: str
) -> None:
    edit_yaml(config_dir / CHARTER, change)
    with pytest.raises(ConfigError) as caught:
        load_charter(config_dir)
    assert needle in _message(caught.value)


def test_charter_reports_every_problem_at_once(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / CHARTER, lambda d: d.pop("toon_tape"))
    (config_dir / "avatars" / "peak_moments.png").unlink()
    with pytest.raises(ConfigError) as caught:
        load_charter(config_dir)
    assert len(caught.value.issues) == 2


def test_charter_missing_file_is_a_config_error(config_dir: Path) -> None:
    (config_dir / CHARTER).unlink()
    with pytest.raises(ConfigError, match=CHARTER):
        load_charter(config_dir)


def test_charter_model_accepts_the_copy_key() -> None:
    charter = BrandCharter.model_validate(
        {
            "font": "BebasNeue-Regular.ttf",
            "banner_bg": "#000000",
            "banner_fg": "#FFFFFF",
            "highlight": "#FF0000",
            "progress": "#00FF00",
            "copy": {
                "en": {
                    "banner": "{title} — Part {n}",
                    "next_part": "Part {n} next",
                    "last_part": "Watch {other} next",
                    "follow": "Follow for more",
                    "subscribe": "Follow",
                }
            },
        }
    )
    assert charter.emoji_font == "NotoEmoji-Regular.ttf" and "en" in charter.texts


# --- the fonts shipped in the package ------------------------------------------------------


def test_fonts_are_shipped_in_the_package_with_their_licences() -> None:
    names = {p.name for p in fonts_dir().iterdir()}
    assert {
        "LuckiestGuy-Regular.ttf",
        "BebasNeue-Regular.ttf",
        "NotoEmoji-Regular.ttf",
        "LICENSE-Apache-2.0.txt",
        "SOURCES.md",
    } <= names
    assert any(n.startswith("OFL") for n in names)
    assert "src/rushes" in fonts_dir().as_posix()  # found next to the code, not at a fixed path


def test_sources_md_records_the_sha256_of_every_file() -> None:
    sources = (fonts_dir() / "SOURCES.md").read_text(encoding="utf-8")
    recorded = dict(re.findall(r"`([\w.\-]+\.(?:ttf|txt))`.*?`([0-9a-f]{64})`", sources))
    shipped = {p.name for p in fonts_dir().iterdir() if p.suffix in (".ttf", ".txt")}
    assert set(recorded) == shipped
    for name, digest in recorded.items():
        assert hashlib.sha256((fonts_dir() / name).read_bytes()).hexdigest() == digest, name
    assert "raw.githubusercontent.com/google/fonts/86aced91" in sources


# --- the charter is part of the config (startup), the avatars are really read -------------------


def test_load_config_holds_the_charters(config_dir: Path) -> None:
    config = load_config(config_dir)
    assert set(config.charters) == {b.id for b in config.accounts.brands}
    assert load_charter(config_dir, config) == config.charters
    assert load_charter(config_dir) == config.charters  # reads the brands from the config itself


@pytest.mark.parametrize("damage", ["corrupt", "truncated", "empty"])
def test_a_damaged_avatar_fails_at_startup_naming_the_brand(config_dir: Path, damage: str) -> None:
    avatar = config_dir / "avatars" / "toon_tape.png"
    data = avatar.read_bytes()
    avatar.write_bytes(
        {"corrupt": b"not a png at all", "truncated": data[: len(data) // 2], "empty": b""}[damage]
    )
    for load in (lambda: load_config(config_dir), lambda: load_charter(config_dir)):
        with pytest.raises(ConfigError) as caught:
            load()
        text = _message(caught.value)
        assert "toon_tape" in text and "illisible" in text, text


def test_a_missing_avatar_or_charter_fails_at_startup(config_dir: Path) -> None:
    (config_dir / "avatars" / "peak_moments.png").unlink()
    with pytest.raises(ConfigError, match="peak_moments"):
        load_config(config_dir)
    (config_dir / "avatars" / "peak_moments.png").write_bytes(b"")
    (config_dir / CHARTER).unlink()
    with pytest.raises(ConfigError, match=CHARTER):
        load_config(config_dir)


def test_charter_problems_come_after_the_other_config_errors(
    config_dir: Path, edit_yaml: Edit
) -> None:
    """The charter is read once the rest is valid: no second error list on a broken config."""
    (config_dir / CHARTER).unlink()
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(rate_limit_s=-1))
    with pytest.raises(ConfigError) as caught:
        load_config(config_dir)
    assert CHARTER not in _message(caught.value) and "rate_limit_s" in _message(caught.value)


def test_charters_for_takes_the_brands_it_is_given(config_dir: Path) -> None:
    brands = {
        "saturday_toons": "en",
        "toon_tape": "en",
        "instant_culte": "fr",
        "peak_moments": "en",
    }
    assert set(charters_for(config_dir, brands)) == set(brands)
    with pytest.raises(ConfigError, match="instant_culte"):  # a charter entry no brand owns
        charters_for(config_dir, {k: v for k, v in brands.items() if k != "instant_culte"})
    with pytest.raises(ConfigError, match=r"instant_culte\.copy"):  # the config's language
        charters_for(config_dir, {**brands, "instant_culte": "de"})


@pytest.mark.parametrize("field", ["banner", "next_part", "last_part", "follow", "subscribe"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_an_empty_copy_template_is_refused_naming_brand_language_and_field(
    config_dir: Path, edit_yaml: Edit, field: str, blank: str
) -> None:
    edit_yaml(
        config_dir / CHARTER, lambda d: d["instant_culte"]["copy"]["fr"].update({field: blank})
    )
    for load in (lambda: load_config(config_dir), lambda: load_charter(config_dir)):
        with pytest.raises(ConfigError) as caught:
            load()
        text = _message(caught.value)
        assert f"instant_culte.copy.fr.{field}" in text and "gabarit vide" in text, text
