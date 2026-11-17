"""Record one yt-dlp answer as a test cassette (development only, never in the pipeline).

    uv run python -I scripts/record_cassette.py URL [--flat] [--platform youtube|tiktok]
        [--comments N] [--playlist-items 1:30]

Calls the real client WITHOUT cookies (`cookies_allowed` stays False), metadata only, 2 s between
requests, then applies `sanitize` and writes `tests/fixtures/cassettes/<cassette_key>.json`.
Run it from the repository root, with network access, and read the file before committing it: no
`googlevideo.com` URL, no cookie, no token.
"""

import argparse
import json
import sys
from pathlib import Path

from rushes.config import load_config
from rushes.net.ytdlp import ExtractError, YtDlpClient, cassette_key, sanitize
from rushes.paths import Paths
from rushes.ui import make_emitter

CASSETTES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "cassettes"


def record(
    url: str, *, flat: bool, platform: str, comments: int, playlist_items: str | None
) -> Path:
    paths = Paths.from_env()
    ui = make_emitter(plain=True, json=False, verbose=False)
    client = YtDlpClient(load_config(paths.config_dir).settings, paths, ui)
    info = client.extract(
        url,
        flat=flat,
        platform="tiktok" if platform == "tiktok" else "youtube",
        comments=comments,
        playlist_items=playlist_items,
        cookies_allowed=False,
    )
    name = cassette_key(
        url, flat=flat, platform=platform, comments=comments, playlist_items=playlist_items
    )
    request = {
        "url": url,
        "flat": flat,
        "platform": platform,
        "comments": comments,
        "playlist_items": playlist_items,
    }
    CASSETTES.mkdir(parents=True, exist_ok=True)
    target = CASSETTES / f"{name}.json"
    document = {"request": request, "info": sanitize(info)}
    target.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("url")
    parser.add_argument("--flat", action="store_true")
    parser.add_argument("--platform", choices=("youtube", "tiktok"), default="youtube")
    parser.add_argument("--comments", type=int, default=0)
    parser.add_argument("--playlist-items", default=None)
    args = parser.parse_args()
    try:
        target = record(
            args.url,
            flat=args.flat,
            platform=args.platform,
            comments=args.comments,
            playlist_items=args.playlist_items,
        )
    except ExtractError as exc:
        sys.stderr.write(f"échec : {exc}\n")
        return 1 if not exc.signin else 5
    sys.stdout.write(f"{target}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
