import re
from pathlib import Path

PUBLICATION_MARKERS = (
    "postiz",
    "open.tiktokapis.com",
    "graph.facebook.com",
    "graph.instagram.com",
    "content_publishing",
    "video.publish",
    "/publish/video",
)

SRC = Path(__file__).resolve().parent.parent / "src" / "rushes"
MIGRATIONS = SRC / "db" / "migrations" / "versions"
CONFIG = SRC / "config.py"

# The name of the abandoned tool may only appear in past migrations (history of the dropped
# columns) and on the legacy-key lines of config.py (decision D1). Network markers: never.
LEGACY_LINE = re.compile(r"^\s*(_D1_MESSAGE = |\(\".*\", \(.*\), _D1_MESSAGE\),$)")


def _hits(path: Path, source: str) -> list[str]:
    if MIGRATIONS in path.parents:
        source = source.replace("postiz", "").replace("Postiz", "")
    elif path == CONFIG:
        source = "\n".join(ln for ln in source.splitlines() if not LEGACY_LINE.match(ln))
    return [m for m in PUBLICATION_MARKERS if m in source.lower()]


def test_r48_no_publication_client_in_code() -> None:
    hits = [(p, m) for p in SRC.rglob("*.py") for m in _hits(p, p.read_text(encoding="utf-8"))]
    assert hits == []


def test_r48_waiver_does_not_cover_a_client_in_config() -> None:
    client = 'POSTIZ_URL = "https://postiz.example/api"\n'
    assert _hits(CONFIG, client) == ["postiz"]
    assert _hits(CONFIG, 'x = "https://graph.facebook.com/v1"\n') == ["graph.facebook.com"]


def test_r48_waiver_keeps_the_legacy_key_table_in_config() -> None:
    legacy = (
        '_D1_MESSAGE = "Postiz a été abandonné (décision D1 du 2026-11-01) : retirer cette clé"\n'
        '    ("settings.yaml", ("postiz",), _D1_MESSAGE),\n'
        '    ("accounts.yaml", ("accounts", "*", "postiz_integration"), _D1_MESSAGE),\n'
    )
    assert _hits(CONFIG, legacy) == []


def test_r48_waiver_still_covers_past_migrations() -> None:
    assert _hits(MIGRATIONS / "0001_initial.py", 'sa.Column("postiz_status", sa.Text())') == []
    assert _hits(SRC / "db" / "sync.py", "postiz") == ["postiz"]
