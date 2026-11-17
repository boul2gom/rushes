"""`discover youtube` (R16, R18): recorded metadata, scripted failures, no network."""

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from fakes.net import CassetteYtDlp, FakeHttp
from helpers import ctx_events, make_context, snapshot, with_config
from rushes.context import Context, Filters
from rushes.db.derived import resolve
from rushes.db.models import SourceVideo, VideoSignal
from rushes.discover.channels import (
    channel_search_url,
    channel_videos_url,
    popular_playlist_url,
)
from rushes.discover.discover import DiscoverYoutubeStep
from rushes.net.base import NetClients
from rushes.net.ytdlp import ExtractError
from rushes.runner import StepReport, exit_code_for, run_step

EditYaml = Callable[[Path, Callable[[Any], Any]], None]

OGGY = "UCNEKMkg_DG8eAyR1BNWsSvw"  # OGGY (official), recorded
OGGY_ODD = "UCA6x1g6ORETTC5grlIYL2ag"  # recorded
OGGY_TV = "UC1ghHlhem7676hJfsyoON5Q"
OGGY_SEARCH_IDS = [OGGY, OGGY_ODD, OGGY_TV, "UCwEuCjq11jl47RS_EjqzTMw", "UCGnJ9rpBoEaRQXxhAbDi4Xw"]
# FIXTURE ONLY: "Ogy" is a Czech vlogger, not an Oggy channel. It is the one recorded channel whose
# most viewed video has a heatmap, so the heatmap and TikTok tests force it as the series' channel.
OGY = "UCGnJ9rpBoEaRQXxhAbDi4Xw"
REAL_TITLE = "Oggy and the Cockroaches"  # the title of config/series/oggy.yaml
TOM = "UCjcDfiG5ViPeTVCEXvlxryw"
MAGIC_PEN = "https://www.youtube.com/watch?v=4auOwokj2qg"  # OGGY, 188 M views, recorded in full
HEATMAP_VIDEO = "https://www.youtube.com/watch?v=XtLljgz-Cw0"  # OGY, recorded in full


def _series(ids: list[str], kind: str = "official") -> dict[str, Any]:
    return {"youtube_channels": [{"id": cid, "kind": kind} for cid in ids]}


class Rig:
    """A context over the recorded metadata; every edit of the config comes before the context."""

    def __init__(
        self,
        tmp_path: Path,
        config_dir: Path,
        edit_yaml: EditYaml,
        *,
        discover: dict[str, Any] | None = None,
        series: dict[str, dict[str, Any]] | None = None,
        overrides: dict[Any, dict[str, Any] | Exception] | None = None,
    ) -> None:
        edit_yaml(
            config_dir / "settings.yaml",
            lambda d: d.update(
                discover={"popular_limit": 30, "full_extract_per_run": 2, **(discover or {})}
            ),
        )
        for series_id, changes in (series or {}).items():
            edit_yaml(config_dir / "series" / f"{series_id}.yaml", lambda d, c=changes: d.update(c))
        # the channel search was recorded for "Oggy": replayed under the real title of the series
        recorded = CassetteYtDlp().extract(
            channel_search_url("Oggy"), flat=True, platform="youtube", playlist_items="1:10"
        )
        replay: dict[Any, dict[str, Any] | Exception] = {
            (channel_search_url(REAL_TITLE), "1:10"): recorded,
            **(overrides or {}),
        }
        self.config_dir = config_dir
        self.cassettes = CassetteYtDlp(overrides=replay)
        self.ctx: Context = make_context(
            tmp_path, config_dir, net=NetClients(ytdlp=self.cassettes, http=FakeHttp())
        )

    def reload_config(self) -> None:
        """The config files were edited: read them again (same database)."""
        self.ctx = with_config(self.ctx, self.config_dir)

    def run(self, **filters: Any) -> StepReport:
        return run_step(self.ctx, DiscoverYoutubeStep(), Filters(**filters))

    def videos(self) -> list[SourceVideo]:
        with Session(self.ctx.engine) as session:
            rows = list(session.scalars(select(SourceVideo).order_by(SourceVideo.id)))
            session.expunge_all()
        return rows

    def derived(self, key: str, scope: str) -> Any:
        with Session(self.ctx.engine) as session:
            return resolve(session, key, scope, config_value=None, default=None, parse=lambda v: v)

    def urls(self) -> list[str]:
        return [url for url, _ in self.cassettes.calls]


@pytest.fixture
def make_rig(tmp_path: Path, config_dir: Path, edit_yaml: EditYaml) -> Callable[..., Rig]:
    rigs: list[Rig] = []

    def build(**kwargs: Any) -> Rig:
        rig = Rig(tmp_path, config_dir, edit_yaml, **kwargs)
        rigs.append(rig)
        return rig

    yield_ = build
    yield yield_
    for rig in rigs:
        rig.ctx.close()


OGGY_CONFIG = {"title": REAL_TITLE, "sources": {"search_queries": ["oggy"]}}


def test_official_channels_derived_from_recorded_search(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(series={"oggy": OGGY_CONFIG})
    report = rig.run(series="oggy")
    assert (report.done, report.warned, report.failed) == (1, 0, 0)

    official = rig.derived("youtube.official_channels", "oggy")
    assert (official.origin, official.source) == ("derived", "channel_search")
    assert OGGY in official.value  # the verified channel whose videos are Oggy episodes
    assert OGGY_ODD not in official.value  # "Oggy Oggy": a name that fits, no content (R15)
    assert OGY not in official.value  # a vlogger whose name is close
    assert set(official.value) <= set(OGGY_SEARCH_IDS)
    with Session(rig.ctx.engine) as session:  # the confidence of the best channel is recorded
        confidence = session.scalar(
            text("SELECT confidence FROM derived_values WHERE key='youtube.official_channels'")
        )
    assert confidence is not None and confidence >= 0.7
    assert rig.derived("youtube.community_channels", "oggy").value == []
    assert rig.derived("youtube.last_discovery", "oggy").value == "2026-11-16"
    assert rig.derived("youtube.search_queries", "oggy").origin == "default"  # seeded: not derived
    assert rig.derived("tiktok.official_accounts", "oggy").value == ["@oggyandthecockroaches"]

    videos = rig.videos()
    assert {v.channel_id for v in videos if v.discovered_via == "popular"} == set(official.value)
    assert all(v.kind == "official" for v in videos if v.discovered_via == "popular")
    searched = [v for v in videos if v.discovered_via == "search"]
    assert searched and all(v.kind == "community" for v in searched)
    assert all(v.channel_id not in official.value for v in searched)
    full = {v.url: v for v in videos if v.extracted_at}
    assert MAGIC_PEN in full and len(full) == 2  # the two most viewed, recorded in full
    magic = full[MAGIC_PEN]
    assert (magic.likes, magic.comments, magic.published_at) == (786_440, None, "2015-04-14")
    assert magic.title and magic.channel_handle == "@oggy" and magic.score and magic.score > 0
    assert magic.status == "retained" and magic.duration_ms == 457_000


def test_r15_config_community_demotes_derived_official(
    make_rig: Callable[..., Rig], config_dir: Path, edit_yaml: EditYaml
) -> None:
    rig = make_rig(series={"oggy": OGGY_CONFIG})
    rig.run(series="oggy")
    assert OGGY in rig.derived("youtube.official_channels", "oggy").value
    assert {v.kind for v in rig.videos() if v.channel_id == OGGY} == {"official"}

    # Louis corrects the derived value: the config wins over it (CLAUDE.md, R15)
    sources = {"search_queries": ["oggy"], **_series([OGGY], "community")}
    edit_yaml(config_dir / "series" / "oggy.yaml", lambda d: d.update(sources=sources))
    rig.reload_config()
    # the community read of OGGY now lists 3 videos only: the others can change kind only through
    # the sync of the stored videos with the resolved lists, not through a new listing
    listing = rig.cassettes.extract(
        popular_playlist_url(OGGY), flat=True, platform="youtube", playlist_items="1:30"
    )
    short = {**listing, "entries": listing["entries"][:3]}
    rig.cassettes.overrides[(popular_playlist_url(OGGY), "1:30")] = short
    calls = len(rig.cassettes.calls)
    assert rig.run(series="oggy", force=True).done == 1

    videos = rig.videos()
    listed_again = {f"https://www.youtube.com/watch?v={e['id']}" for e in short["entries"]}
    searched = rig.cassettes.extract("ytsearch20:oggy", flat=True, platform="youtube")
    listed_again |= {e["url"] for e in searched["entries"]}
    only_synced = [v for v in videos if v.channel_id == OGGY and v.url not in listed_again]
    assert len(only_synced) >= 20  # rows no listing of this run mentions
    assert {v.kind for v in only_synced} == {"community"}  # demoted by the sync alone
    assert {v.kind for v in videos if v.channel_id == OGGY} == {"community"}
    assert {v.kind for v in videos if v.channel_id == OGGY_TV} == {"official"}
    assert channel_videos_url(OGGY) not in rig.urls()[calls:]  # no median: not an official
    retained = [v for v in videos if v.status == "retained"]
    assert retained and all(
        v.kind == ("community" if v.channel_id == OGGY else "official") for v in retained
    )


def test_r15_channel_left_out_of_the_official_list_is_demoted(
    make_rig: Callable[..., Rig], config_dir: Path, edit_yaml: EditYaml
) -> None:
    """A channel in no list any more (removed from the config) is no longer official."""
    both = {
        "title": REAL_TITLE,
        "sources": {"search_queries": ["oggy"], **_series([OGGY, OGGY_TV])},
    }
    rig = make_rig(series={"oggy": both})
    rig.run(series="oggy")
    assert {v.kind for v in rig.videos() if v.channel_id == OGGY_TV} == {"official"}

    sources = {"search_queries": ["oggy"], **_series([OGGY])}
    edit_yaml(config_dir / "series" / "oggy.yaml", lambda d: d.update(sources=sources))
    rig.reload_config()
    rig.run(series="oggy", force=True)
    tv = [v for v in rig.videos() if v.channel_id == OGGY_TV]
    assert len(tv) >= 20 and {v.kind for v in tv} == {"community"}
    assert all(v.kind == "official" for v in rig.videos() if v.channel_id == OGGY)
    assert all(v.status != "retained" or v.kind == "community" for v in tv)


def test_a_failed_channel_search_does_not_demote_the_stored_officials(
    make_rig: Callable[..., Rig], config_dir: Path
) -> None:
    rig = make_rig(series={"oggy": OGGY_CONFIG}, discover={"full_extract_per_run": 0})
    rig.run(series="oggy")
    official = [v.url for v in rig.videos() if v.kind == "official"]
    assert official
    with rig.ctx.engine.begin() as conn:  # the derived list is gone: the next run searches again
        conn.execute(text("DELETE FROM derived_values WHERE key='youtube.official_channels'"))
    rig.cassettes.overrides[(channel_search_url(REAL_TITLE), "1:10")] = ExtractError("timed out")
    report = rig.run(series="oggy", force=True)
    assert report.unavailable == 1  # warned: the search failed
    # nothing is demoted: neither the sync nor the keyword search (which lists some of them again)
    assert [v.url for v in rig.videos() if v.kind == "official"] == official


def test_r15_an_optional_timeout_on_refresh_demotes_nothing(make_rig: Callable[..., Rig]) -> None:
    """A candidate's popular playlist times out during a refresh: the derived list is kept."""
    officials = [OGGY, OGGY_TV, "UCwEuCjq11jl47RS_EjqzTMw"]
    recorded = CassetteYtDlp()
    # the official read asks 1:31 (popular_limit), the candidate check 1:30: only the latter fails
    overrides: dict[Any, dict[str, Any] | Exception] = {
        (popular_playlist_url(cid), "1:31"): recorded.extract(
            popular_playlist_url(cid), flat=True, platform="youtube", playlist_items="1:30"
        )
        for cid in officials
    }
    rig = make_rig(
        series={"oggy": OGGY_CONFIG}, discover={"popular_limit": 31}, overrides=overrides
    )
    rig.run(series="oggy")
    before = rig.derived("youtube.official_channels", "oggy").value
    assert OGGY in before
    official_rows = [v.url for v in rig.videos() if v.kind == "official"]
    assert len(official_rows) >= 30
    with rig.ctx.engine.begin() as conn:  # the derived list is old: the next run refreshes it
        conn.execute(
            text("UPDATE derived_values SET computed_at='2026-01-01T00:00:00+00:00' "
                 "WHERE key='youtube.official_channels'")
        )  # fmt: skip
    confidence = _confidence(rig, "youtube.official_channels")
    rig.cassettes.overrides[(popular_playlist_url(OGGY), "1:30")] = ExtractError("timed out")
    report = rig.run(series="oggy", force=True)
    assert (report.done, report.warned, report.failed) == (1, 0, 0)  # an optional request
    assert rig.derived("youtube.official_channels", "oggy").value == before  # nobody dropped
    assert [v.url for v in rig.videos() if v.kind == "official"] == official_rows
    # the carried candidate was the surest one: the list keeps the previous confidence
    assert _confidence(rig, "youtube.official_channels") == confidence


def _confidence(rig: Rig, key: str) -> float:
    with Session(rig.ctx.engine) as session:
        value = session.scalar(
            text("SELECT confidence FROM derived_values WHERE key=:k AND scope='oggy'"), {"k": key}
        )
    assert isinstance(value, float)
    return value


def _age(rig: Rig) -> None:
    """Every derived value is old, the last discovery too: the next run is a scheduled refresh."""
    with rig.ctx.engine.begin() as conn:
        conn.execute(text("UPDATE derived_values SET computed_at='2026-01-01T00:00:00+00:00'"))
        conn.execute(
            text("UPDATE derived_values SET value_json='\"2026-01-01\"' "
                 "WHERE key='youtube.last_discovery'")
        )  # fmt: skip


def test_r15_a_candidate_without_popular_playlist_does_not_stop_the_sweep(
    make_rig: Callable[..., Rig],
) -> None:
    """A missing popular playlist is no failure: a dropped official channel is still demoted."""
    rig = make_rig(series={"oggy": OGGY_CONFIG}, discover={"full_extract_per_run": 0})
    assert rig.run(series="oggy").done == 1
    assert OGGY_TV in rig.derived("youtube.official_channels", "oggy").value
    tv = [v for v in rig.videos() if v.channel_id == OGGY_TV]
    assert len(tv) >= 20 and {v.kind for v in tv} == {"official"}
    with rig.ctx.engine.begin() as conn:  # an ingested video is never touched
        conn.execute(
            text("UPDATE source_videos SET status='downloaded' WHERE url=:u"), {"u": tv[0].url}
        )
    # at the refresh OGGY_TV's popular titles are no longer about Oggy, and OGY has no playlist
    listing = rig.cassettes.extract(
        popular_playlist_url(OGGY_TV), flat=True, platform="youtube", playlist_items="1:30"
    )
    other = [{**e, "title": f"Minecraft vlog {i}"} for i, e in enumerate(listing["entries"])]
    rig.cassettes.overrides[(popular_playlist_url(OGGY_TV), "1:30")] = {
        **listing,
        "entries": other,
    }
    missing = ExtractError("This playlist does not exist")
    rig.cassettes.overrides[(popular_playlist_url(OGY), "1:30")] = missing
    for _ in range(2):  # two scheduled refreshes: the steady state
        _age(rig)
        report = rig.run(series="oggy")
        assert (report.done, report.warned, report.failed) == (1, 0, 0)
        assert OGGY_TV not in rig.derived("youtube.official_channels", "oggy").value
        kinds = {v.url: v.kind for v in rig.videos() if v.channel_id == OGGY_TV}
        assert kinds.pop(tv[0].url) == "official"  # downloaded
        assert len(kinds) >= 20 and set(kinds.values()) == {"community"}


def test_r15_a_listed_channel_whose_playlist_is_gone_stays_listed_and_warns(
    make_rig: Callable[..., Rig],
) -> None:
    """Known limit (C56): the channel is carried at every refresh and every run tells Louis."""
    rig = make_rig(series={"oggy": OGGY_CONFIG}, discover={"full_extract_per_run": 0})
    rig.run(series="oggy")
    before = rig.derived("youtube.official_channels", "oggy").value
    assert OGGY in before
    official_rows = [v.url for v in rig.videos() if v.kind == "official"]
    rig.cassettes.overrides[(popular_playlist_url(OGGY), "1:30")] = ExtractError(
        "This playlist does not exist"
    )
    for _ in range(2):
        _age(rig)
        report = rig.run(series="oggy")
        assert (report.warned, report.unavailable) == (1, 1)  # the official read fails
        assert rig.derived("youtube.official_channels", "oggy").value == before
        assert [v.url for v in rig.videos() if v.kind == "official"] == official_rows


def test_a_candidate_unchecked_on_the_first_search_warns_and_is_retried(
    make_rig: Callable[..., Rig],
) -> None:
    """No previous list to keep it in: the run warns and the next run searches again."""
    rig = make_rig(series={"oggy": OGGY_CONFIG}, discover={"full_extract_per_run": 0})
    rig.cassettes.overrides[(popular_playlist_url(OGGY), "1:30")] = ExtractError("timed out")
    report = rig.run(series="oggy")
    assert (report.done, report.warned, report.unavailable) == (0, 1, 0)  # an optional request
    assert OGGY not in rig.derived("youtube.official_channels", "oggy").value
    assert rig.derived("youtube.last_discovery", "oggy").value is None
    del rig.cassettes.overrides[(popular_playlist_url(OGGY), "1:30")]
    calls = len(rig.cassettes.calls)
    report = rig.run(series="oggy")  # selected again: no last discovery yet
    assert (report.done, report.warned) == (1, 0)
    assert channel_search_url(REAL_TITLE) in rig.urls()[calls:]
    assert OGGY in rig.derived("youtube.official_channels", "oggy").value
    assert {v.kind for v in rig.videos() if v.channel_id == OGGY} == {"official"}


def test_the_sweep_demotes_an_official_video_without_channel(
    make_rig: Callable[..., Rig],
) -> None:
    """A row without a channel is in no official list: the sweep makes it community."""
    sources = {"search_queries": ["oggy"], **_series([OGGY])}
    rig = make_rig(series={"oggy": {"title": REAL_TITLE, "sources": sources}})
    insert = text(
        "INSERT INTO source_videos(platform,url,kind,series_id,status) "
        "VALUES ('youtube', :u, 'official', 'oggy', 'found')"
    )
    with rig.ctx.engine.begin() as conn:
        conn.execute(insert, {"u": "https://www.youtube.com/watch?v=nochannel001"})
    rig.run(series="oggy")
    row = next(v for v in rig.videos() if v.url.endswith("nochannel001"))
    assert (row.channel_id, row.kind) == (None, "community")  # the IS NULL part of the sweep


def test_a_video_of_another_series_keeps_its_kind_and_series(ctx: Context) -> None:
    """Series B listing a video of series A (a keyword search) never flips its kind."""
    from rushes.discover.videos import upsert_source_video

    info = {"id": "abc12345678", "title": "Oggy", "channel_id": OGGY, "view_count": 10}
    with Session(ctx.engine) as session:
        first = upsert_source_video(
            session, info, kind="official", series_id="oggy", creator_id=None, via="popular"
        )
        again = upsert_source_video(
            session,
            {**info, "view_count": 20},
            kind="community",
            series_id="tom_and_jerry",
            creator_id=None,
            via="search",
        )
        session.commit()
        assert again == first
        row = session.get(SourceVideo, first)
        assert row is not None
        assert (row.series_id, row.kind, row.discovered_via, row.views) == (
            "oggy",
            "official",
            "popular",
            20,  # the counters still refresh
        )
        # and its own series still follows its latest answer
        upsert_source_video(
            session, info, kind="community", series_id="oggy", creator_id=None, via="channel"
        )
        assert row.kind == "community"


def test_a_downloaded_video_keeps_its_kind_when_its_channel_is_demoted(
    make_rig: Callable[..., Rig], config_dir: Path, edit_yaml: EditYaml
) -> None:
    rig = make_rig(series={"oggy": OGGY_CONFIG})
    rig.run(series="oggy")
    with rig.ctx.engine.begin() as conn:
        conn.execute(
            text("UPDATE source_videos SET status='downloaded' WHERE url=:u"), {"u": MAGIC_PEN}
        )
    sources = {"search_queries": ["oggy"], **_series([OGGY], "community")}
    edit_yaml(config_dir / "series" / "oggy.yaml", lambda d: d.update(sources=sources))
    rig.reload_config()
    rig.run(series="oggy", force=True)
    kinds = {v.url: (v.kind, v.status) for v in rig.videos()}
    assert kinds[MAGIC_PEN] == ("official", "downloaded")  # an ingested video is not touched


def test_every_discovery_request_asks_for_the_cookie_allowance(
    make_rig: Callable[..., Rig],
) -> None:
    rig = make_rig(series={"oggy": OGGY_CONFIG})
    rig.run(series="oggy")
    assert rig.cassettes.calls and all(o["cookies_allowed"] for _, o in rig.cassettes.calls)
    assert all(o["platform"] == "youtube" and o["comments"] == 0 for _, o in rig.cassettes.calls)


def test_config_channel_overrides_derived(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(series={"oggy": {**OGGY_CONFIG, "sources": {**_series([OGGY])}}})
    with Session(rig.ctx.engine) as session:  # an older derived value says something else
        from rushes.db.derived import record

        record(
            session,
            "youtube.official_channels",
            "oggy",
            [OGGY_ODD],
            source="old",
            confidence=0.5,
            at=rig.ctx.clock.now(),
        )
        session.commit()
    rig.run(series="oggy")
    urls = rig.urls()
    assert popular_playlist_url(OGGY) in urls and popular_playlist_url(OGGY_ODD) not in urls
    assert not any(
        "results?search_query" in url for url in urls
    )  # the config makes a search useless
    assert {v.channel_id for v in rig.videos() if v.discovered_via == "popular"} == {OGGY}
    assert rig.derived("youtube.official_channels", "oggy").source == "old"  # untouched


def test_community_channels_read_via_channel(make_rig: Callable[..., Rig]) -> None:
    sources = {
        **_series([OGGY]),
        **{
            "youtube_channels": _series([OGGY])["youtube_channels"]
            + _series([OGGY_ODD], "community")["youtube_channels"]
        },
    }
    rig = make_rig(series={"oggy": {"title": "Oggy", "sources": sources}})
    rig.run(series="oggy")
    odd_url = popular_playlist_url(OGGY_ODD)
    call = next(options for url, options in rig.cassettes.calls if url == odd_url)
    assert call["playlist_items"] == "1:30" and call["flat"] is True
    odd = [v for v in rig.videos() if v.channel_id == OGGY_ODD]
    assert len(odd) == 30
    assert all(
        (v.kind, v.discovered_via, v.series_id) == ("community", "channel", "oggy") for v in odd
    )
    official = [v for v in rig.videos() if v.channel_id == OGGY]
    assert official and all((v.kind, v.discovered_via) == ("official", "popular") for v in official)


def _listing(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"_type": "playlist", "entries": list(entries)}


def _entry(
    video_id: str, *, duration: int | None, views: int | None, channel: str = OGGY
) -> dict[str, Any]:
    return {
        "_type": "url",
        "id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": f"Oggy {video_id}",
        "duration": duration,
        "view_count": views,
        "channel_id": channel,
    }


def test_duration_filter_rejects_shorts_and_huge(make_rig: Callable[..., Rig]) -> None:
    entries = _listing(
        _entry("short000001", duration=30, views=900),
        _entry("fine0000001", duration=600, views=800),
        _entry("edge0000001", duration=120, views=700),  # the limits themselves stay
        _entry("edge0000002", duration=10_800, views=600),
        _entry("huge0000001", duration=20_000, views=500),
        _entry("live0000001", duration=None, views=400),  # unknown duration: not refused
    )
    rig = make_rig(
        series={
            "oggy": {"title": "Oggy", "sources": {**_series([OGGY]), "search_queries": ["oggy"]}}
        },
        discover={"full_extract_per_run": 0},
        overrides={
            popular_playlist_url(OGGY): entries,
            channel_videos_url(OGGY): _listing(),
            "ytsearch20:oggy": _listing(),
        },
    )
    assert rig.run(series="oggy").done == 1
    status = {v.url.rsplit("=", 1)[1]: v.status for v in rig.videos()}
    assert status == {
        "short000001": "rejected",
        "fine0000001": "found",
        "edge0000001": "found",
        "edge0000002": "found",
        "huge0000001": "rejected",
        "live0000001": "found",
    }


def test_search_queries_keep_the_three_best_by_median_views(make_rig: Callable[..., Rig]) -> None:
    def results(index: int, base: int) -> dict[str, Any]:
        other = "UCother000000000000000001"
        return _listing(
            *(
                _entry(f"q{index}v{i:08d}", duration=600, views=base * (i + 1), channel=other)
                for i in range(3)
            ),
            _entry(f"q{index}official0", duration=600, views=base * 100, channel=OGGY),
        )

    queries = ["Oggy", "Oggy cartoon", "Oggy full episode", "Oggy bob", "Oggy jack"]
    medians = [1000, 5000, 200, 9000, 7000]  # listing: base x (1, 2, 3, 100): median 2.5 x base
    overrides: dict[Any, dict[str, Any] | Exception] = {
        f"ytsearch20:{q}": results(i, base)
        for i, (q, base) in enumerate(zip(queries, medians, strict=True))
    }
    overrides[popular_playlist_url(OGGY)] = _listing()
    overrides[channel_videos_url(OGGY)] = _listing()
    rig = make_rig(
        series={
            "oggy": {
                "title": "Oggy",
                "sources": _series([OGGY]),
                "characters": [{"id": "bob"}, {"id": "jack"}, {"id": "ignored"}],
            }
        },
        discover={"full_extract_per_run": 0},
        overrides=overrides,
    )
    rig.run(series="oggy")
    best = rig.derived("youtube.search_queries", "oggy")
    assert best.origin == "derived" and best.value == ["Oggy bob", "Oggy jack", "Oggy cartoon"]
    searched = [v for v in rig.videos() if v.discovered_via == "search"]
    assert searched and all(v.channel_id != OGGY and v.kind == "community" for v in searched)
    assert len(searched) == 15  # the videos of the official channel are not read as community


def test_score_ranking_reproducible_on_recorded_metadata(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(series={"oggy": OGGY_CONFIG}, discover={"keep_top": 2})

    def retained() -> list[tuple[str, float]]:
        with Session(rig.ctx.engine) as session:
            rows = session.execute(
                select(SourceVideo.url, SourceVideo.score)
                .where(SourceVideo.status == "retained")
                .order_by(SourceVideo.score.desc(), SourceVideo.url)
            ).all()
        return [(url, float(score)) for url, score in rows]

    assert rig.run(series="oggy", force=True).done == 1
    first = retained()
    assert len(first) == 2
    assert first == sorted(first, key=lambda row: (-row[1], row[0]))
    assert rig.run(series="oggy", force=True).done == 1
    assert retained() == first  # same list, same order, same scores


def test_retention_keeps_only_keep_top_officials(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(series={"oggy": OGGY_CONFIG}, discover={"keep_top": 1, "keep_community": 0})
    rig.run(series="oggy")
    extracted = [v for v in rig.videos() if v.extracted_at]
    assert len(extracted) == 2 and [v.status for v in extracted].count("retained") == 1
    best = max(extracted, key=lambda v: v.score or 0.0)
    assert best.status == "retained"


def test_retention_needs_min_score(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(series={"oggy": OGGY_CONFIG}, discover={"keep_top": 5, "min_score": 99})
    rig.run(series="oggy")
    assert all(v.status != "retained" for v in rig.videos())  # nothing reaches the minimum
    assert [v.score for v in rig.videos() if v.extracted_at]  # but the videos are scored


def test_heatmap_extraction_stores_signals_and_tiktok_account(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(
        series={
            "oggy": {"title": "Oggy", "sources": {**_series([OGY]), "search_queries": ["oggy"]}}
        },
        discover={"full_extract_per_run": 1},
        overrides={"ytsearch20:oggy": _listing()},
    )
    rig.run(series="oggy")
    video = next(v for v in rig.videos() if v.url == HEATMAP_VIDEO)
    assert video.extracted_at and video.heatmap_json
    assert len(json.loads(video.heatmap_json)) == 100
    with Session(rig.ctx.engine) as session:
        signals = list(
            session.scalars(select(VideoSignal).where(VideoSignal.source_video_id == video.id))
        )
    assert len(signals) == 100 and {s.kind for s in signals} == {"heatmap"}
    assert min(s.t_ms for s in signals) == 6225 and all(s.collected_at for s in signals)
    assert rig.derived("tiktok.official_accounts", "oggy").value == ["@theogycz"]
    others = [v for v in rig.videos() if v.url != HEATMAP_VIDEO]
    assert others and all(v.extracted_at is None for v in others)  # only one extraction asked
    # a second pass recomputes the signals instead of piling them up
    rig.run(series="oggy", force=True)
    with Session(rig.ctx.engine) as session:
        assert session.scalar(text("SELECT count(*) FROM video_signals")) == 100


def test_video_without_heatmap_has_no_signal(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(
        series={
            "oggy": {"title": "Oggy", "sources": {**_series([OGGY]), "search_queries": ["oggy"]}}
        },
        discover={"full_extract_per_run": 1},
        overrides={"ytsearch20:oggy": _listing()},
    )
    rig.run(series="oggy")
    video = next(v for v in rig.videos() if v.url == MAGIC_PEN)
    assert video.extracted_at and video.heatmap_json is None
    with Session(rig.ctx.engine) as session:
        assert session.scalar(text("SELECT count(*) FROM video_signals")) == 0


def test_discover_survives_extractor_failure(make_rig: Callable[..., Rig]) -> None:
    broken = ExtractError("Extraction impossible : Video unavailable")
    rig = make_rig(
        series={
            "oggy": {"title": "Oggy", "sources": {**_series([OGGY]), "search_queries": ["oggy"]}},
            "tom_and_jerry": {"sources": {**_series([TOM]), "search_queries": ["tom"]}},
        },
        overrides={
            popular_playlist_url(TOM): broken,
            channel_videos_url(TOM): broken,
            "ytsearch20:tom": _listing(),
        },
    )
    report = rig.run()
    assert (report.done, report.warned, report.failed, report.unavailable) == (1, 1, 0, 1)
    assert exit_code_for([report], []) == 5
    events = {e["item"]: e for e in ctx_events(rig.ctx) if e.get("event") == "step"}
    assert events["oggy"]["status"] == "done"
    warned = events["tom_and_jerry"]
    assert warned["status"] == "warned" and "Video unavailable" in warned["message"]
    # what was obtained is kept, the failed series will be tried again
    assert any(v.url == MAGIC_PEN for v in rig.videos())
    assert rig.derived("youtube.last_discovery", "oggy").value == "2026-11-16"
    assert rig.derived("youtube.last_discovery", "tom_and_jerry").value is None
    assert list(DiscoverYoutubeStep().select(rig.ctx, Filters())) == ["tom_and_jerry"]


def test_signin_wall_stops_the_series_but_not_the_others(make_rig: Callable[..., Rig]) -> None:
    wall = ExtractError("YouTube exige une connexion", signin=True)
    rig = make_rig(
        series={
            "oggy": {"title": "Oggy", "sources": {**_series([OGGY]), "search_queries": ["oggy"]}},
            "tom_and_jerry": {"sources": {**_series([TOM]), "search_queries": ["tom"]}},
        },
        overrides={popular_playlist_url(TOM): wall},
    )
    report = rig.run()
    assert (report.done, report.warned, report.failed, report.unavailable) == (1, 1, 0, 1)
    asked = [url for url in rig.urls() if TOM[2:] in url or "tom" in url]
    assert asked == [popular_playlist_url(TOM)]  # nothing more was asked for that series


def test_a_source_that_keeps_failing_is_given_up_for_the_series(
    make_rig: Callable[..., Rig],
) -> None:
    down = ExtractError("Extraction impossible : timed out")
    rig = make_rig(
        series={
            "oggy": {"title": "Oggy", "sources": {**_series([OGGY]), "search_queries": ["a", "b"]}}
        },
        overrides={
            popular_playlist_url(OGGY): down,
            channel_videos_url(OGGY): down,
            "ytsearch20:a": down,
            "ytsearch20:b": _listing(),  # three failures in a row end the series: never asked
        },
    )
    report = rig.run(series="oggy")
    assert (report.warned, report.failed, report.unavailable) == (1, 0, 1)
    assert rig.urls() == [popular_playlist_url(OGGY), channel_videos_url(OGGY), "ytsearch20:a"]
    assert rig.derived("youtube.last_discovery", "oggy").value is None


def test_r16_discover_rerun_within_refresh_changes_nothing(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(series={"oggy": OGGY_CONFIG}, discover={"refresh_days": 7})
    assert rig.run(series="oggy").done == 1
    before = snapshot(rig.ctx)
    calls = len(rig.cassettes.calls)
    again = rig.run(series="oggy")
    assert (again.done, again.warned, again.skipped, again.failed) == (0, 0, 0, 0)
    assert snapshot(rig.ctx) == before and len(rig.cassettes.calls) == calls  # not a request
    rig.ctx.clock.advance(days=6)  # type: ignore[attr-defined]
    assert list(DiscoverYoutubeStep().select(rig.ctx, Filters(series="oggy"))) == []
    rig.ctx.clock.advance(days=1)  # type: ignore[attr-defined]
    assert list(DiscoverYoutubeStep().select(rig.ctx, Filters(series="oggy"))) == ["oggy"]


def test_select_honours_filters(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    step = DiscoverYoutubeStep()
    assert sorted(step.select(rig.ctx, Filters())) == ["oggy", "tom_and_jerry"]
    assert list(step.select(rig.ctx, Filters(series="oggy"))) == ["oggy"]
    assert list(step.select(rig.ctx, Filters(creator="squeezie"))) == []
    assert list(step.select(rig.ctx, Filters(account="st_tt"))) == []
    with rig.ctx.engine.begin() as conn:
        conn.execute(text("UPDATE series SET status='dropped' WHERE id='oggy'"))
    assert list(step.select(rig.ctx, Filters())) == ["tom_and_jerry"]


def test_discover_settings_defaults_and_validation(config_dir: Path, edit_yaml: EditYaml) -> None:
    from rushes.config import ConfigError, DiscoverSettings, load_config

    defaults = load_config(config_dir).settings.discover
    assert defaults == DiscoverSettings()
    assert (defaults.refresh_days, defaults.popular_limit, defaults.median_window) == (7, 200, 50)
    assert (defaults.full_extract_per_run, defaults.search_results) == (20, 20)
    assert (defaults.min_duration_s, defaults.max_duration_s) == (120, 10_800)
    assert (defaults.keep_top, defaults.keep_community, defaults.min_score) == (30, 5, 0.0)
    assert defaults.official_min_followers == 100_000
    assert (defaults.official_min_name_sim, defaults.official_min_content_share) == (80.0, 0.6)
    assert (defaults.weights.views_per_day, defaults.weights.engagement) == (0.5, 0.2)

    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(discover={"keep_top": 3}))
    assert load_config(config_dir).settings.discover.keep_top == 3
    edit_yaml(
        config_dir / "settings.yaml",
        lambda d: d.update(discover={"min_duration_s": 500, "max_duration_s": 100}),
    )
    with pytest.raises(ConfigError, match="min_duration_s"):
        load_config(config_dir)
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(discover={"unknown": 1}))
    with pytest.raises(ConfigError, match="discover"):
        load_config(config_dir)


def test_refresh_counts_days_not_hours(make_rig: Callable[..., Rig]) -> None:
    """Run at 12:00, next run 6 days 23 h later (the 7th calendar day): channels are refreshed."""
    rig = make_rig(series={"oggy": OGGY_CONFIG}, discover={"refresh_days": 7})
    rig.run(series="oggy")
    search = channel_search_url(REAL_TITLE)
    assert search in rig.urls()
    calls = len(rig.cassettes.calls)
    rig.ctx.clock.advance(days=6)  # type: ignore[attr-defined]  # day 6: not stale
    rig.run(series="oggy", force=True)
    assert search not in rig.urls()[calls:]
    rig.ctx.clock.advance(hours=23)  # type: ignore[attr-defined]  # 2026-11-23 11:00: day 7, 6 d 23 h
    rig.run(series="oggy", force=True)
    assert search in rig.urls()[calls:]


def test_duration_is_checked_again_after_the_full_extraction(make_rig: Callable[..., Rig]) -> None:
    """A flat entry without a duration (a premiere) is judged once its real duration is known."""
    rig = make_rig(
        series={
            "oggy": {"title": "Oggy", "sources": {**_series([OGGY]), "search_queries": ["oggy"]}}
        },
        discover={"min_duration_s": 500, "full_extract_per_run": 1},
        overrides={
            popular_playlist_url(OGGY): _listing(
                {**_entry("4auOwokj2qg", duration=None, views=188_000_000), "title": "Oggy"}
            ),
            channel_videos_url(OGGY): _listing(),
            "ytsearch20:oggy": _listing(),
        },
    )
    rig.run(series="oggy")
    (video,) = rig.videos()
    assert video.url == MAGIC_PEN and video.extracted_at and video.duration_ms == 457_000
    assert video.status == "rejected"  # 457 s < 500 s: never retained


def test_a_rejected_video_is_not_lifted_by_this_step(make_rig: Callable[..., Rig]) -> None:
    """`rejected` is a shared status: the step never turns it back into `found`."""
    entries = _listing(_entry("short000001", duration=30, views=900))
    rig = make_rig(
        series={
            "oggy": {"title": "Oggy", "sources": {**_series([OGGY]), "search_queries": ["oggy"]}}
        },
        discover={"full_extract_per_run": 0},
        overrides={
            popular_playlist_url(OGGY): entries,
            channel_videos_url(OGGY): _listing(),
            "ytsearch20:oggy": _listing(),
        },
    )
    rig.run(series="oggy")
    assert [v.status for v in rig.videos()] == ["rejected"]
    edit = rig.ctx.config.settings.discover.model_copy(update={"min_duration_s": 10})
    settings = rig.ctx.config.settings.model_copy(update={"discover": edit})
    rig.ctx = dataclasses.replace(
        rig.ctx, config=dataclasses.replace(rig.ctx.config, settings=settings)
    )
    rig.run(series="oggy", force=True)
    assert [v.status for v in rig.videos()] == ["rejected"]


def test_tiktok_accounts_of_successive_runs_are_merged(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(series={"oggy": OGGY_CONFIG})
    with Session(rig.ctx.engine) as session:
        from rushes.db.derived import record

        record(
            session, "tiktok.official_accounts", "oggy", ["@earlier", "@oggyandthecockroaches"],
            source="video_descriptions", confidence=0.6, at=rig.ctx.clock.now(),
        )  # fmt: skip
        session.commit()
    rig.run(series="oggy")
    accounts = rig.derived("tiktok.official_accounts", "oggy").value
    assert accounts == ["@earlier", "@oggyandthecockroaches"]  # kept, no duplicate


def test_a_re_extraction_without_heatmap_clears_the_stored_heatmap(
    make_rig: Callable[..., Rig],
) -> None:
    rig = make_rig(
        series={
            "oggy": {"title": "Oggy", "sources": {**_series([OGY]), "search_queries": ["oggy"]}}
        },
        discover={"full_extract_per_run": 1},
        overrides={"ytsearch20:oggy": _listing()},
    )
    rig.run(series="oggy")
    video = next(v for v in rig.videos() if v.url == HEATMAP_VIDEO)
    assert video.heatmap_json
    full = rig.cassettes.extract(HEATMAP_VIDEO, flat=False, platform="youtube")
    rig.cassettes.overrides[HEATMAP_VIDEO] = {k: v for k, v in full.items() if k != "heatmap"}
    rig.run(series="oggy", force=True)
    video = next(v for v in rig.videos() if v.url == HEATMAP_VIDEO)
    assert video.heatmap_json is None  # the column agrees with the signals
    with Session(rig.ctx.engine) as session:
        assert session.scalar(text("SELECT count(*) FROM video_signals")) == 0


def test_candidate_queries_use_words_for_snake_case_character_ids() -> None:
    from rushes.discover.videos import candidate_queries

    assert candidate_queries("Oggy", ["joey_dee", "bob", "third"]) == [
        "Oggy",
        "Oggy cartoon",
        "Oggy full episode",
        "Oggy joey dee",
        "Oggy bob",
    ]


def test_a_video_of_another_creator_keeps_its_kind(ctx: Context) -> None:
    """Without a series, the owner is the creator: creator B never changes creator A's video."""
    from rushes.discover.videos import upsert_source_video

    info = {"id": "crt12345678", "title": "Video", "channel_id": OGGY, "view_count": 10}
    with Session(ctx.engine) as session:
        first = upsert_source_video(
            session, info, kind="official", series_id=None, creator_id="squeezie", via="rss"
        )
        upsert_source_video(
            session, info, kind="community", series_id=None, creator_id="caseoh", via="search"
        )
        row = session.get(SourceVideo, first)
        assert row is not None and (row.creator_id, row.kind) == ("squeezie", "official")
        upsert_source_video(
            session, info, kind="community", series_id=None, creator_id="squeezie", via="rss"
        )
        assert row.kind == "community"  # its own creator follows its latest answer


def test_a_series_listing_a_creator_video_never_becomes_its_owner(ctx: Context) -> None:
    """The owner is the series, else the creator, and never changes: the creator keeps its say."""
    from rushes.discover.videos import upsert_source_video

    info = {"id": "mix12345678", "title": "Video", "channel_id": OGGY, "view_count": 10}
    with Session(ctx.engine) as session:
        first = upsert_source_video(
            session, info, kind="official", series_id=None, creator_id="squeezie", via="rss"
        )
        upsert_source_video(
            session, info, kind="community", series_id="oggy", creator_id=None, via="search"
        )
        row = session.get(SourceVideo, first)
        assert row is not None
        # no series is filled in: the series sweep (filtered on the series) cannot reach it
        assert (row.series_id, row.creator_id, row.kind) == (None, "squeezie", "official")
        upsert_source_video(
            session, info, kind="community", series_id=None, creator_id="squeezie", via="rss"
        )
        assert row.kind == "community"
        # and the other way round: a creator listing a series video stays out of it
        series_info = {**info, "id": "ser12345678"}
        video = upsert_source_video(
            session, series_info, kind="official", series_id="oggy", creator_id=None, via="popular"
        )
        upsert_source_video(
            session, series_info, kind="community", series_id=None, creator_id="squeezie",
            via="rss",
        )  # fmt: skip
        owned = session.get(SourceVideo, video)
        assert owned is not None
        assert (owned.series_id, owned.creator_id, owned.kind) == ("oggy", None, "official")
        upsert_source_video(
            session, series_info, kind="community", series_id="oggy", creator_id=None,
            via="channel",
        )  # fmt: skip
        assert owned.kind == "community"
        # a row with both columns belongs to its series, like for the sweep (series filter)
        both_info = {**info, "id": "bth12345678"}
        both = upsert_source_video(
            session, both_info, kind="official", series_id="oggy", creator_id="squeezie",
            via="rss",
        )  # fmt: skip
        upsert_source_video(
            session, both_info, kind="community", series_id="oggy", creator_id=None, via="channel"
        )
        row_both = session.get(SourceVideo, both)
        assert row_both is not None and row_both.kind == "community"


def test_an_ownerless_video_takes_its_first_reader_as_owner(ctx: Context) -> None:
    from rushes.discover.videos import upsert_source_video

    with ctx.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO source_videos(platform,url,kind,status) "
                 "VALUES ('youtube', 'https://www.youtube.com/watch?v=own12345678', "
                 "'official', 'found')")
        )  # fmt: skip
    info = {"id": "own12345678", "title": "Video", "channel_id": OGGY}
    with Session(ctx.engine) as session:
        video = upsert_source_video(
            session, info, kind="community", series_id="oggy", creator_id=None, via="search"
        )
        row = session.get(SourceVideo, video)
        assert row is not None and (row.series_id, row.kind) == ("oggy", "community")


def test_r15_a_series_never_extracts_a_video_of_another_series(
    make_rig: Callable[..., Rig],
) -> None:
    """Another series' official video listed by oggy: no full extraction, no TikTok account."""
    community = {
        "title": "Oggy",
        "sources": {**_series([OGY], "community"), "search_queries": ["oggy"]},
    }
    rig = make_rig(
        series={"oggy": community},
        discover={"full_extract_per_run": 1},
        # no official channel for oggy: OGY's listing (community) holds the other series' video
        overrides={"ytsearch20:oggy": _listing(), (channel_search_url("Oggy"), "1:10"): _listing()},
    )
    insert = text(
        "INSERT INTO source_videos(platform,url,kind,series_id,status) "
        "VALUES ('youtube', :u, 'official', 'tom_and_jerry', 'found')"
    )
    with rig.ctx.engine.begin() as conn:
        conn.execute(insert, {"u": HEATMAP_VIDEO})
    rig.run(series="oggy")
    assert HEATMAP_VIDEO in {v.url for v in rig.videos()}
    assert (HEATMAP_VIDEO, False) not in {(u, o["flat"]) for u, o in rig.cassettes.calls}
    row = next(v for v in rig.videos() if v.url == HEATMAP_VIDEO)
    assert (row.series_id, row.kind, row.extracted_at) == ("tom_and_jerry", "official", None)
    assert rig.derived("tiktok.official_accounts", "oggy").value is None  # no @theogycz
