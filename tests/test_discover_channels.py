"""Channel search, classification and TikTok handles (spec, Sources; R15), on recorded data."""

from dataclasses import replace
from typing import Any

import pytest

from fakes.net import CassetteYtDlp
from rushes.config import DiscoverSettings
from rushes.discover.channels import (
    ChannelCandidate,
    channel_search_url,
    channel_videos_url,
    classify,
    popular_playlist_url,
    rank_channels,
    tiktok_handles,
)

OGGY = "UCNEKMkg_DG8eAyR1BNWsSvw"  # OGGY, verified, 14 M
OGGY_ODD = "UCA6x1g6ORETTC5grlIYL2ag"
OGGY_TV = "UC1ghHlhem7676hJfsyoON5Q"
TOM_BEST = "UCjcDfiG5ViPeTVCEXvlxryw"  # "Tom And Jerry Best Episodes HD", not verified
TOM_CHANNELS = (
    TOM_BEST,
    "UCX-7MDPrCppQEHaOVof3hRw",
    "UCp4XbtbSqh0CUgINdVIoVqA",
    "UCVQ19vZmwyAFoGkgyIZWJmw",
)
SETTINGS = DiscoverSettings()


def _search(cassettes: CassetteYtDlp, query: str) -> dict[str, Any]:
    return cassettes.extract(
        channel_search_url(query), flat=True, platform="youtube", playlist_items="1:10"
    )


def _titles(cassettes: CassetteYtDlp, channel_id: str) -> list[str]:
    info = cassettes.extract(
        popular_playlist_url(channel_id), flat=True, platform="youtube", playlist_items="1:30"
    )
    return [str(entry["title"]) for entry in info["entries"]]


def _candidate(**overrides: Any) -> ChannelCandidate:
    base = ChannelCandidate("UCx", "@x", "X", True, 500_000, 100.0, 1.0)
    return replace(base, **overrides)


def test_urls() -> None:
    assert channel_search_url("Tom and Jerry") == (
        "https://www.youtube.com/results?search_query=Tom+and+Jerry&sp=EgIQAg%3D%3D"
    )
    assert channel_search_url("Oggy & Co") == (
        "https://www.youtube.com/results?search_query=Oggy+%26+Co&sp=EgIQAg%3D%3D"
    )
    assert popular_playlist_url(OGGY) == (
        "https://www.youtube.com/playlist?list=UULPNEKMkg_DG8eAyR1BNWsSvw"
    )
    assert channel_videos_url(OGGY) == f"https://www.youtube.com/channel/{OGGY}/videos"


def test_real_title_keeps_the_look_alikes_out_of_the_official_list() -> None:
    """The recorded "Oggy" search replayed under the title of the config (R15)."""
    cassettes = CassetteYtDlp()
    search = _search(cassettes, "Oggy")
    titles = {c["channel_id"]: _titles(cassettes, c["channel_id"]) for c in search["entries"][:5]}
    ranked = rank_channels("Oggy and the Cockroaches", search, titles)
    kinds = {c.channel_id: classify(c, SETTINGS)[0] for c in ranked[:5]}
    assert kinds[OGGY] == "official"
    assert kinds[OGGY_ODD] != "official"  # "Oggy Oggy": a name that fits, no content
    assert kinds["UCGnJ9rpBoEaRQXxhAbDi4Xw"] != "official"  # "Ogy", a vlogger
    assert {c.channel_id: c for c in ranked}[OGGY_ODD].content_share < 0.5


def test_official_channel_identified_from_recorded_search() -> None:
    cassettes = CassetteYtDlp()
    titles = {cid: _titles(cassettes, cid) for cid in (OGGY, OGGY_ODD, OGGY_TV)}
    ranked = rank_channels("Oggy", _search(cassettes, "Oggy"), titles)
    first = ranked[0]
    assert (first.channel_id, first.handle, first.verified) == (OGGY, "@oggy", True)
    assert first.followers == 14_000_000 and first.name_sim == 100.0
    assert first.content_share > 0.9
    kind, confidence = classify(first, SETTINGS)
    assert kind == "official" and confidence >= 0.7
    # candidates whose titles were not read have no content share, never a made-up one
    assert all(c.content_share == 0.0 for c in ranked if c.channel_id not in titles)


def test_search_entries_that_are_not_channels_are_skipped() -> None:
    cassettes = CassetteYtDlp()
    search = _search(cassettes, "Tom and Jerry")
    assert any(not str(e.get("id", "")).startswith("UC") for e in search["entries"])  # a shelf
    ranked = rank_channels("Tom and Jerry", search, {})
    assert ranked and all(c.channel_id.startswith("UC") for c in ranked)
    assert len({c.channel_id for c in ranked}) == len(ranked)


def test_unverified_lookalike_stays_community() -> None:
    cassettes = CassetteYtDlp()
    titles = {cid: _titles(cassettes, cid) for cid in TOM_CHANNELS}
    ranked = {
        c.channel_id: c
        for c in rank_channels("Tom and Jerry", _search(cassettes, "Tom and Jerry"), titles)
    }
    best = ranked[TOM_BEST]
    assert best.verified is False and best.name_sim >= SETTINGS.official_min_name_sim
    assert best.content_share >= 0.5  # its videos are about the series ...
    assert (
        classify(best, SETTINGS)[0] == "community"
    )  # ... but nothing proves it is the owner (R15)
    # even a huge audience does not make an unverified channel official
    assert classify(replace(best, followers=5_000_000), SETTINGS)[0] == "community"


@pytest.mark.parametrize(
    ("candidate", "kind"),
    [
        (_candidate(), "official"),
        (_candidate(verified=False), "community"),  # content matches, no badge: doubt
        (_candidate(followers=99_999), "community"),
        (_candidate(followers=None), "community"),
        (_candidate(name_sim=79.9, content_share=0.59), "community"),  # neither name nor share
        (_candidate(name_sim=79.9, content_share=0.6), "official"),
        (_candidate(name_sim=80.0, content_share=0.5), "official"),  # the floor of the content
        (_candidate(name_sim=80.0, content_share=0.49), "ignore"),  # Louis: content is required
        (_candidate(name_sim=100.0, content_share=0.0), "ignore"),  # a look-alike: same name only
        (_candidate(verified=False, name_sim=20.0, content_share=0.49), "ignore"),
        (_candidate(name_sim=20.0, content_share=0.1), "ignore"),
    ],
)
def test_classify_rules(candidate: ChannelCandidate, kind: str) -> None:
    assert classify(candidate, SETTINGS)[0] == kind


def test_r15_official_channel_requires_content() -> None:
    """Louis (2026-11-17): badge, audience and content (>= 0.5), plus a name or enough content."""
    lookalike = _candidate(name_sim=100.0, content_share=0.49)
    assert classify(lookalike, SETTINGS) == ("ignore", 0.0)
    assert classify(replace(lookalike, content_share=0.5), SETTINGS)[0] == "official"
    # the content floor holds even when the thresholds of the config are loosened
    loose = DiscoverSettings(official_min_name_sim=0, official_min_content_share=0)
    assert classify(replace(lookalike, name_sim=0.0, content_share=0.4), loose)[0] == "ignore"


def test_classify_confidence_formula() -> None:
    assert classify(_candidate(name_sim=100.0, content_share=1.0), SETTINGS)[1] == pytest.approx(
        1.0
    )
    kind, confidence = classify(_candidate(name_sim=80.0, content_share=0.5), SETTINGS)
    assert kind == "official" and confidence == pytest.approx(0.4 + 0.3 * 0.8 + 0.3 * 0.5)
    assert classify(_candidate(name_sim=20.0, content_share=0.1), SETTINGS) == ("ignore", 0.0)
    _, community = classify(_candidate(verified=False, content_share=0.5), SETTINGS)
    assert 0 < community < 0.7  # a community channel is never as sure as an official one


def test_tiktok_handles_from_descriptions() -> None:
    descriptions = [
        "Follow us: https://www.tiktok.com/@Oggy.Official. And TikTok : @oggy.official too",
        "TikTok\uff1a@Second_Account (fullwidth colon) https://tiktok.com/@second_account",
        "no handle here, but an @mention and instagram.com/@insta",
        "tiktok.com/@",
    ]
    assert tiktok_handles(descriptions) == ["@oggy.official", "@second_account"]
    assert tiktok_handles([]) == []


def test_tiktok_handles_from_recorded_descriptions() -> None:
    import json
    from pathlib import Path

    folder = Path(__file__).parent / "fixtures" / "cassettes"
    found: list[str] = []
    for path in folder.glob("*.json"):
        info = json.loads(path.read_text(encoding="utf-8"))["info"]
        if "watch?v=" in info.get("webpage_url", ""):
            found.extend(tiktok_handles([str(info.get("description", ""))]))
    assert "@oggyandthecockroaches" in found and "@theogycz" in found
