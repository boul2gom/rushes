from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from rushes.config import Account, ConfigError, Issue, load_config
from rushes.core.text import hashtag_from_name, normalize_hashtag

Edit = Callable[[Path, Callable[[Any], Any]], None]


def _issues(exc: pytest.ExceptionInfo[ConfigError]) -> list[Issue]:
    return exc.value.issues


@pytest.mark.parametrize(
    ("file", "mutate", "loc"),
    [
        ("settings.yaml", lambda d: d.update(postiz={"enabled": False}), "postiz"),
        ("settings.yaml", lambda d: d.update(manual_posts_notice=10), "manual_posts_notice"),
        (
            "accounts.yaml",
            lambda d: d["accounts"][0].update(publish_mode="manual"),
            "accounts.0.publish_mode",
        ),
        (
            "accounts.yaml",
            lambda d: d["accounts"][0].update(postiz_integration="x"),
            "accounts.0.postiz_integration",
        ),
        ("accounts.yaml", lambda d: d.update(platform_hashtags={}), "platform_hashtags"),
    ],
)
def test_legacy_postiz_key_refused_with_d1_message(
    config_dir: Path,
    edit_yaml: Edit,
    file: str,
    mutate: Callable[[Any], Any],
    loc: str,
) -> None:
    edit_yaml(config_dir / file, mutate)
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    issue = next(i for i in _issues(exc) if i.location == loc)
    assert "Postiz a été abandonné (décision D1 du 2026-11-01)" in issue.message
    assert not any(i.message == "champ inconnu" for i in _issues(exc))


@pytest.mark.parametrize(
    ("rel", "mutate", "loc"),
    [
        ("creators/squeezie.yaml", lambda d: d.update(hashtags_anchor=["#x"]), "hashtags_anchor"),
        (
            "series/oggy.yaml",
            lambda d: d.update(branding={"hashtags_anchor": ["#x"]}),
            "branding.hashtags_anchor",
        ),
    ],
)
def test_legacy_hashtags_anchor_refused(
    config_dir: Path, edit_yaml: Edit, rel: str, mutate: Callable[[Any], Any], loc: str
) -> None:
    edit_yaml(config_dir / rel, mutate)
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    issue = next(i for i in _issues(exc) if i.location == loc)
    assert issue.file == rel
    assert "remplacé par required_hashtags (R13) et hashtag du créateur" in issue.message
    assert not any(i.message == "champ inconnu" for i in _issues(exc))


def test_r13_config_rejects_6_hashtags(config_dir: Path, edit_yaml: Edit) -> None:
    six = ["#a", "#b", "#c", "#d", "#e", "#f"]
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["required_hashtags"]["cartoons"].update(tiktok=six),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert [(i.file, i.location, i.message) for i in _issues(exc)] == [
        ("accounts.yaml", "required_hashtags.cartoons.tiktok", "5 hashtags au maximum (R13)")
    ]


@pytest.mark.parametrize("tag", ["#viral", "#foryou"])
def test_r13_never_viral_foryou_in_config(config_dir: Path, edit_yaml: Edit, tag: str) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["required_hashtags"]["cartoons"]["tiktok"].append(tag),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    issue = next(i for i in _issues(exc) if i.location == "required_hashtags.cartoons.tiktok.4")
    assert tag in issue.message
    assert "R13" in issue.message


def test_r13_no_fyp_on_instagram_config(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["required_hashtags"]["cartoons"]["instagram"].append("#fyp"),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    issue = next(i for i in _issues(exc) if i.location == "required_hashtags.cartoons.instagram.2")
    assert "#fyp" in issue.message
    assert "R13" in issue.message


def test_r13_unknown_token_refused(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["required_hashtags"]["cartoons"]["tiktok"].append("{serie}"),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    issue = next(i for i in _issues(exc) if i.location == "required_hashtags.cartoons.tiktok.4")
    assert "jeton inconnu" in issue.message
    assert "{serie}" in issue.message


def test_r13_malformed_hashtag_refused(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["required_hashtags"]["cartoons"]["tiktok"].append("#Not Valid"),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    issue = next(i for i in _issues(exc) if i.location == "required_hashtags.cartoons.tiktok.4")
    assert "#Not Valid" in issue.message


@pytest.mark.parametrize(
    ("platform", "tag", "index"),
    [("tiktok", "#cartoons", 4), ("tiktok", "{series}", 4), ("instagram", "{nostalgia}", 2)],
)
def test_r13_duplicate_hashtag_refused(
    config_dir: Path, edit_yaml: Edit, platform: str, tag: str, index: int
) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["required_hashtags"]["cartoons"][platform].append(tag),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    issue = next(
        i for i in _issues(exc) if i.location == f"required_hashtags.cartoons.{platform}.{index}"
    )
    assert issue.message == f"doublon : {tag}"


def test_r13_invalid_tag_not_also_reported_as_duplicate(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["required_hashtags"]["cartoons"].update(tiktok=["#Cartoons", "#cartoons"]),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert [(i.location, i.message.startswith("hashtag invalide")) for i in _issues(exc)] == [
        ("required_hashtags.cartoons.tiktok.0", True)
    ]


def test_r13_empty_required_hashtags_refused(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["required_hashtags"]["creators"].update(tiktok=[]),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert [(i.file, i.location, i.message) for i in _issues(exc)] == [
        (
            "accounts.yaml",
            "required_hashtags.creators.tiktok",
            "au moins un hashtag obligatoire est requis (R13)",
        )
    ]


def test_r13_each_problem_is_its_own_issue(config_dir: Path, edit_yaml: Edit) -> None:
    def break_it(d: Any) -> None:
        d["required_hashtags"]["cartoons"]["tiktok"].append("#viral")
        d["required_hashtags"]["creators"]["instagram"].append("{creator}")

    edit_yaml(config_dir / "accounts.yaml", break_it)
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert {i.location for i in _issues(exc)} == {
        "required_hashtags.cartoons.tiktok.4",
        "required_hashtags.creators.instagram.1",
    }


def test_r13_required_hashtags_cover_every_vertical(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / "accounts.yaml", lambda d: d["required_hashtags"].pop("creators"))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    locations = {i.location for i in _issues(exc)}
    assert locations == {
        "required_hashtags.creators.tiktok",
        "required_hashtags.creators.instagram",
    }
    assert all("creators" in i.message and "R13" in i.message for i in _issues(exc))


def test_r13_required_hashtags_cover_both_platforms(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["required_hashtags"]["cartoons"].pop("instagram"),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert [i.location for i in _issues(exc)] == ["required_hashtags.cartoons.instagram"]
    assert "cartoons" in _issues(exc)[0].message
    assert "instagram" in _issues(exc)[0].message


def test_r50_account_originality_defaults_to_settings(config_dir: Path) -> None:
    cfg = load_config(config_dir)
    assert cfg.effective_originality("st_tt") == "base"
    assert cfg.effective_originality("tt_tt") == "advanced"


def test_r50_switching_default_in_settings_needs_no_code(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "settings.yaml",
        lambda d: d.setdefault("originality", {}).update(default="advanced"),
    )
    cfg = load_config(config_dir)
    assert cfg.effective_originality("st_tt") == "advanced"


def test_r49_no_business_account_requirement() -> None:
    assert set(Account.model_fields) == {"id", "brand", "platform", "handle", "originality"}


def test_recalibrable_keys_optional_with_spec_defaults(config_dir: Path, edit_yaml: Edit) -> None:
    def strip_accounts(d: Any) -> None:
        d.pop("slots", None)
        for family in d["families"].values():
            family.pop("share", None)
        d.get("layouts", {}).pop("vertical_share", None)

    edit_yaml(config_dir / "accounts.yaml", strip_accounts)
    edit_yaml(config_dir / "settings.yaml", lambda d: d.pop("approval_threshold", None))
    cfg = load_config(config_dir)
    assert cfg.settings.approval_threshold is None
    assert cfg.accounts.layouts.vertical_share is None
    assert cfg.slots_for("st_tt") == ["15:00", "18:00"]  # America/New_York
    assert cfg.slots_for("st_ig") == ["17:00"]
    assert cfg.slots_for("ic_tt") == ["18:00", "20:00"]  # Europe/Paris
    assert cfg.slots_for("ic_ig") == ["19:00"]
    assert cfg.family_share("monetizable", "tiktok") == 1.0
    assert cfg.family_share("long", "instagram") == 0.0


def test_written_slots_and_share_win_over_defaults(config_dir: Path, edit_yaml: Edit) -> None:
    def force(d: Any) -> None:
        d["slots"] = {"tiktok": ["09:00"]}
        d["families"]["long"]["share"] = {"tiktok": 0.25, "instagram": 0.5}

    edit_yaml(config_dir / "accounts.yaml", force)
    cfg = load_config(config_dir)
    assert cfg.slots_for("ic_tt") == ["09:00"]
    assert cfg.slots_for("ic_ig") == ["19:00"]  # platform absent: default for the brand timezone
    assert cfg.family_share("long", "tiktok") == 0.25
    assert cfg.family_share("long", "instagram") == 0.5


def test_unknown_account_family_and_lookups(config_dir: Path) -> None:
    cfg = load_config(config_dir)
    assert cfg.account("ic_tt").brand == "instant_culte"
    assert cfg.brand("instant_culte").timezone == "Europe/Paris"
    assert cfg.brand_of("pm_ig").id == "peak_moments"
    assert cfg.series_by_id("oggy") is not None
    assert cfg.series_by_id("nope") is None
    assert cfg.creator_by_id("nope") is None
    with pytest.raises(ConfigError):
        cfg.account("nope")
    with pytest.raises(ConfigError):
        cfg.family_share("nope", "tiktok")


def test_series_placeholder_channel_refused(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "series" / "tom_and_jerry.yaml",
        lambda d: d.update(sources={"youtube_channels": [{"id": "UC...", "kind": "official"}]}),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert any(
        i.file == "series/tom_and_jerry.yaml" and i.location == "sources.youtube_channels.0.id"
        for i in _issues(exc)
    )


def test_series_real_channel_accepted(config_dir: Path, edit_yaml: Edit) -> None:
    channel = "UC" + "a" * 22
    edit_yaml(
        config_dir / "series" / "tom_and_jerry.yaml",
        lambda d: d.update(sources={"youtube_channels": [{"id": channel, "kind": "official"}]}),
    )
    cfg = load_config(config_dir)
    tj = cfg.series_by_id("tom_and_jerry")
    assert tj is not None
    assert tj.sources.youtube_channels[0].id == channel


@pytest.mark.parametrize("handle", ["@tomandjerry", "@a.b", "@a_1"])
def test_series_real_tiktok_account_accepted(
    config_dir: Path, edit_yaml: Edit, handle: str
) -> None:
    edit_yaml(
        config_dir / "series" / "tom_and_jerry.yaml",
        lambda d: d.update(sources={"tiktok_accounts": [handle]}),
    )
    tj = load_config(config_dir).series_by_id("tom_and_jerry")
    assert tj is not None
    assert tj.sources.tiktok_accounts == [handle]


@pytest.mark.parametrize("handle", ["@...", "@.", "@", "tomandjerry", "@a b"])
def test_series_placeholder_tiktok_account_refused(
    config_dir: Path, edit_yaml: Edit, handle: str
) -> None:
    edit_yaml(
        config_dir / "series" / "tom_and_jerry.yaml",
        lambda d: d.update(sources={"tiktok_accounts": [handle]}),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert any(
        i.file == "series/tom_and_jerry.yaml" and i.location == "sources.tiktok_accounts.0"
        for i in _issues(exc)
    )


@pytest.mark.parametrize(
    ("mutate", "key"),
    [
        (
            lambda d: d.update(intro_outro={"reference_frames": ["refs/absent.png"]}),
            "intro_outro.reference_frames",
        ),
        (
            lambda d: d.update(intro_outro={"theme_audio": "refs/absent.png"}),
            "intro_outro.theme_audio",
        ),
        (
            lambda d: d.update(characters=[{"id": "tom", "refs": ["refs/absent.png"]}]),
            "characters.0.refs",
        ),
    ],
)
def test_series_missing_ref_file_refused(
    config_dir: Path, edit_yaml: Edit, mutate: Callable[[Any], Any], key: str
) -> None:
    edit_yaml(config_dir / "series" / "tom_and_jerry.yaml", mutate)
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    message = str(exc.value)
    assert key in message
    assert "refs/absent.png" in message
    assert "series/tom_and_jerry.yaml" in message


def test_series_existing_ref_file_accepted(config_dir: Path, edit_yaml: Edit) -> None:
    (config_dir / "refs").mkdir()
    (config_dir / "refs" / "tom.png").write_bytes(b"png")
    edit_yaml(
        config_dir / "series" / "tom_and_jerry.yaml",
        lambda d: d.update(characters=[{"id": "tom", "refs": ["refs/tom.png"]}]),
    )
    assert load_config(config_dir).series_by_id("tom_and_jerry") is not None


def test_layouts_preference_rejects_vertical(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["layouts"].update(preference=["vertical"]),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert any("vertical se règle par vertical_share" in i.message for i in _issues(exc))


def test_layouts_preference_rejects_duplicates(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["layouts"].update(preference=["square", "square"]),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert any("doublon" in i.message for i in _issues(exc))


@pytest.mark.parametrize(
    ("name", "tag"),
    [
        ("Tom and Jerry", "#tomandjerry"),
        ("Oggy and the Cockroaches", "#oggyandthecockroaches"),
        ("Les Zinzins de l'Espace", "#leszinzinsdelespace"),
        ("Café Crème", "#cafecreme"),
    ],
)
def test_hashtag_from_name(name: str, tag: str) -> None:
    assert hashtag_from_name(name) == tag


@pytest.mark.parametrize(
    ("raw", "tag"),
    [("#TomAndJerry", "#tomandjerry"), ("Squeezie", "#squeezie"), ("  #Été ", "#ete")],
)
def test_normalize_hashtag(raw: str, tag: str) -> None:
    assert normalize_hashtag(raw) == tag


def test_creator_hashtag_defaults_to_id(config_dir: Path) -> None:
    creator = load_config(config_dir).creator_by_id("squeezie")
    assert creator is not None
    assert creator.effective_hashtag == "#squeezie"


def test_creator_hashtag_underscore_id_and_override(config_dir: Path, edit_yaml: Edit) -> None:
    cfg = load_config(config_dir)
    beta = cfg.creator_by_id("beta_squad")
    assert beta is not None
    assert beta.effective_hashtag == "#betasquad"
    edit_yaml(config_dir / "creators" / "beta_squad.yaml", lambda d: d.update(hashtag="#BetaSq"))
    edited = load_config(config_dir).creator_by_id("beta_squad")
    assert edited is not None
    assert edited.effective_hashtag == "#betasq"


def test_peak_moments_creators_are_in_the_example_config(config_dir: Path) -> None:
    cfg = load_config(config_dir)
    assert cfg.accounts.creators_by_brand["peak_moments"] == [
        "beta_squad",
        "caseoh",
        "dude_perfect",
    ]
    for cid in ("beta_squad", "caseoh", "dude_perfect"):
        creator = cfg.creator_by_id(cid)
        assert creator is not None
        assert creator.language == "en"
        assert creator.authorized is False


def test_ntfy_topic_and_token_read_on_use(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = load_config(config_dir)  # loading never needs a secret
    assert cfg.settings.ntfy is not None
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("NTFY_TOKEN", raising=False)
    with pytest.raises(ConfigError) as exc:
        cfg.settings.ntfy.topic()
    assert "NTFY_TOPIC" in str(exc.value)
    monkeypatch.setenv("NTFY_TOPIC", "my-topic")
    monkeypatch.setenv("NTFY_TOKEN", "tk_secret")
    assert cfg.settings.ntfy.topic().get_secret_value() == "my-topic"
    token = cfg.settings.ntfy.token()
    assert token is not None
    assert token.get_secret_value() == "tk_secret"
    assert "tk_secret" not in repr(token)


def test_ntfy_token_is_optional(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / "settings.yaml", lambda d: d["ntfy"].pop("token_env", None))
    cfg = load_config(config_dir)
    assert cfg.settings.ntfy is not None
    assert cfg.settings.ntfy.token() is None


def test_settings_v2_sections_have_spec_defaults(config_dir: Path, edit_yaml: Edit) -> None:
    def strip(d: Any) -> None:
        for key in ("originality", "youtube", "showcase", "serve", "models", "ntfy"):
            d.pop(key, None)

    edit_yaml(config_dir / "settings.yaml", strip)
    s = load_config(config_dir).settings
    assert s.originality.default == "base"
    assert s.originality.voice.engine == "supertonic"
    assert s.originality.voice.duck_db == -12
    assert s.youtube.cookies == "fallback"
    assert s.showcase.max_episodes_per_week == 4
    assert s.models.whisper == "small"
    assert s.serve is None
    assert s.ntfy is None


def test_series_rights_block_is_parsed(config_dir: Path) -> None:
    tj = load_config(config_dir).series_by_id("tom_and_jerry")
    assert tj is not None
    assert tj.rights is not None
    assert tj.rights.status == "to_request"
