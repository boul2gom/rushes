"""Caption of a post (R12, R13): a Jinja template, then the limits of each platform.

Lengths are counted in characters (code points) of the text a person pastes, hashtags included.
The test prefix is not counted: a test post is never published (R14), the rules apply to the
text that would follow it.
"""

import random
import re
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from rushes.config import FORBIDDEN_HASHTAGS, MAX_HASHTAGS, Platform
from rushes.errors import RushesError

TEST_PREFIX = "NE PAS PUBLIER — "
TIKTOK_MIN, TIKTOK_MAX = 50, 150
NAME_WINDOW: dict[Platform, int] = {"tiktok": 80, "instagram": 125}  # R12

QUESTIONS: dict[str, tuple[str, ...]] = {
    "en": ("Who wins? 👇", "Did you see it coming? 👀", "Best moment? 👇"),
    "fr": ("Qui gagne ? 👇", "Tu l'avais vu venir ? 👀", "Le meilleur moment ? 👇"),
}
_PART_LABELS: dict[str, str] = {"en": "Part {n}", "fr": "Partie {n}"}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_SPACES = re.compile(r" {2,}")
_HASHTAG = re.compile(r"(?<!\S)#\w+")
_TOTAL_PARTS = re.compile(r"\b(?:part|partie)\s*\d+\s*(?:/|of|sur)\s*\d+", re.IGNORECASE)

_ENV = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)  # plain text captions


def _unsupported(language: str) -> RushesError:
    return RushesError(
        f"langue sans gabarit de légende : {language}",
        hint="Ajouter config/templates/<série ou créateur>.j2 pour cette langue.",
    )


def part_label(language: str, number: int | None) -> str | None:
    """'Part 2' (English), 'Partie 2' (French); None without a number."""
    if number is None:
        return None
    if language not in _PART_LABELS:
        raise _unsupported(language)
    return _PART_LABELS[language].format(n=number)


def pick_question(language: str, seed: int) -> str:
    if language not in QUESTIONS:
        raise _unsupported(language)
    return random.Random(seed).choice(QUESTIONS[language])


def packaged_template(language: str) -> str:
    resource = files("rushes.meta").joinpath("templates", f"caption.{language}.j2")
    if not _SAFE_ID.fullmatch(language) or not resource.is_file():
        raise _unsupported(language)
    return resource.read_text(encoding="utf-8")


def template_source(language: str, config_dir: Path, subject_id: str) -> str:
    """`config/templates/<subject id>.j2` when it exists, else the packaged template."""
    if _SAFE_ID.fullmatch(subject_id) and ".." not in subject_id:
        override = config_dir / "templates" / f"{subject_id}.j2"
        if override.is_file():
            return override.read_text(encoding="utf-8")
    return packaged_template(language)


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())


def _render(source: str, **fields: str | None) -> str:
    try:
        text = _ENV.from_string(source).render(**fields)
    except TemplateError as exc:
        raise RushesError(f"gabarit de légende invalide : {exc}") from exc
    return _SPACES.sub(" ", text).strip()


def _name_in_window(text: str, name: str, limit: int) -> bool:
    start = text.find(name)
    return start >= 0 and start + len(name) <= limit


def build_caption(
    *,
    platform: Platform,
    language: str,
    hook: str | None,
    name: str,
    part_number: int | None,
    situation: str | None,
    question: str,
    credit: str | None,
    hashtags: Sequence[str],
    test: bool,
    template: str | None = None,
) -> str:
    """Hook, name, part, situation, question, credit, hashtags (R12).

    The name must end within the platform's visible start, else the hook is removed. On TikTok
    the text is 50 to 150 characters: beyond, the situation then the question are removed (the
    credit is never removed, R23). What still breaks a rule is reported by `caption_problems`.
    `template`: source of a Jinja template (default: the packaged one of the language)."""
    source = template if template is not None else packaged_template(language)
    fields: dict[str, str | None] = {
        "hook": _flat(hook) or None,
        "name": _flat(name),
        "part_label": part_label(language, part_number),
        "situation": _flat(situation) or None,
        "question": _flat(question),
        "credit": _flat(credit) or None,
        "hashtags": " ".join(hashtags),
    }
    text = _render(source, **fields)
    if fields["hook"] and not _name_in_window(text, fields["name"] or "", NAME_WINDOW[platform]):
        fields["hook"] = None
        text = _render(source, **fields)
    if platform == "tiktok":
        for slot in ("situation", "question"):
            if len(text) <= TIKTOK_MAX:
                break
            if fields[slot]:
                fields[slot] = None if slot == "situation" else ""
                text = _render(source, **fields)
    return f"{TEST_PREFIX}{text}" if test else text


def caption_problems(caption: str, *, platform: Platform, name: str) -> list[str]:
    """What the caption breaks (French messages); empty when it respects R9, R12 and R13."""
    text = caption.removeprefix(TEST_PREFIX)
    problems: list[str] = []
    limit = NAME_WINDOW[platform]
    if text.find(name) < 0:
        problems.append(f"nom introuvable dans la légende : {name} (R12)")
    elif not _name_in_window(text, name, limit):
        problems.append(f"le nom ne tient pas dans les {limit} premiers caractères (R12)")
    if platform == "tiktok" and not TIKTOK_MIN <= len(text) <= TIKTOK_MAX:
        problems.append(
            f"{len(text)} caractères : {TIKTOK_MIN} à {TIKTOK_MAX} attendus sur TikTok (R12)"
        )
    tags = [t.lower() for t in _HASHTAG.findall(text)]
    if len(tags) > MAX_HASHTAGS:
        problems.append(f"{len(tags)} hashtags : {MAX_HASHTAGS} au maximum (R13)")
    problems += [f"{tag} interdit (R13)" for tag in sorted(set(tags) & FORBIDDEN_HASHTAGS)]
    if platform == "instagram" and "#fyp" in tags:
        problems.append("#fyp interdit sur Instagram (R13)")
    if _TOTAL_PARTS.search(text):
        problems.append("le nombre total de parties ne doit pas apparaître (R9)")
    return problems
