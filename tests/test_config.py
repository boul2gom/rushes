from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from rushes.config import Config, ConfigError, load_config

Edit = Callable[[Path, Callable[[Any], Any]], None]


def test_repository_example_config_is_valid(config_dir: Path) -> None:
    config = load_config(config_dir)
    assert isinstance(config, Config)
    assert {b.id for b in config.accounts.brands} == {
        "saturday_toons",
        "toon_tape",
        "instant_culte",
        "peak_moments",
    }
    assert len(config.accounts.accounts) == 8
    assert {s.id for s in config.series} == {"tom_and_jerry", "oggy"}
    assert {c.id for c in config.creators} == {
        "squeezie",
        "byilhan",
        "beta_squad",
        "caseoh",
        "dude_perfect",
    }


def test_empty_creators_for_a_brand_is_a_warning_not_an_error(
    config_dir: Path, edit_yaml: Edit
) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["creators_by_brand"].update(peak_moments=[]),
    )
    config = load_config(config_dir)
    assert any("peak_moments" in w for w in config.warnings)


def test_malformed_yaml_names_file_and_line(config_dir: Path) -> None:
    (config_dir / "accounts.yaml").write_text("brands: [\n  - {id: x\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    message = str(exc.value)
    assert "accounts.yaml" in message
    assert "line" in message


def test_unknown_field_is_named(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(rate_limt_s=3))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "rate_limt_s" in str(exc.value)
    assert "settings.yaml" in str(exc.value)


def test_missing_config_dir_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path / "nope")
    assert "nope" in str(exc.value)


def test_errors_are_aggregated(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(rate_limit_s="vite"))
    edit_yaml(config_dir / "series" / "oggy.yaml", lambda d: d.update(audio="loud"))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    message = str(exc.value)
    assert "settings.yaml" in message
    assert "oggy.yaml" in message
    assert len(exc.value.issues) >= 2


def test_r21_creator_language_must_match_brand(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / "creators" / "squeezie.yaml", lambda d: d.update(language="en"))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "R21" in str(exc.value)


def test_r21_brand_without_language_is_refused(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / "accounts.yaml", lambda d: d["brands"][0].pop("language"))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "language" in str(exc.value)


def test_r6_family_min_above_60s_rejected(config_dir: Path, edit_yaml: Edit) -> None:
    def shorten(d: Any) -> None:
        d["families"]["monetizable"]["min_s"] = 30
        d["families"]["monetizable"]["max_s"] = 50

    edit_yaml(config_dir / "accounts.yaml", shorten)
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "60" in str(exc.value)


def test_r6_family_min_not_above_max_rejected(config_dir: Path, edit_yaml: Edit) -> None:
    def invert(d: Any) -> None:
        d["families"]["monetizable"]["min_s"] = 100
        d["families"]["monetizable"]["max_s"] = 70

    edit_yaml(config_dir / "accounts.yaml", invert)
    with pytest.raises(ConfigError):
        load_config(config_dir)


@pytest.mark.parametrize("share", [-0.1, 1.5])
def test_family_share_outside_unit_interval_rejected(
    config_dir: Path, edit_yaml: Edit, share: float
) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["families"]["monetizable"].update(share={"tiktok": share, "instagram": 1.0}),
    )
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_rotation_with_unknown_series_is_rejected(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["rotation"][0].update(saturday_toons=["inconnue"]),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "inconnue" in str(exc.value)


def test_rotation_with_unknown_brand_is_rejected(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["rotation"][0].update(marque_fantome=["oggy"]),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "marque_fantome" in str(exc.value)


def test_creators_by_brand_with_unknown_creator_is_rejected(
    config_dir: Path, edit_yaml: Edit
) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["creators_by_brand"]["instant_culte"].append("fantome"),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "fantome" in str(exc.value)


def test_account_with_unknown_brand_is_rejected(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["accounts"][0].update(brand="marque_fantome"),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "marque_fantome" in str(exc.value)


def test_r4_rotation_periods_overlap_rejected(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["rotation"][1].update({"from": d["rotation"][0]["to"]}),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "R4" in str(exc.value)


def test_rotation_period_end_before_start_rejected(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["rotation"][0].update({"to": d["rotation"][0]["from"].replace(year=2020)}),
    )
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_same_series_on_two_brands_in_one_period_rejected(
    config_dir: Path, edit_yaml: Edit
) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["rotation"][0].update(toon_tape=["tom_and_jerry"]),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "tom_and_jerry" in str(exc.value)


def test_duplicate_account_ids_rejected(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "accounts.yaml",
        lambda d: d["accounts"][1].update(id=d["accounts"][0]["id"]),
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "st_tt" in str(exc.value)


def test_series_id_must_match_file_name(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / "series" / "oggy.yaml", lambda d: d.update(id="autre"))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "oggy" in str(exc.value)


def test_placeholder_tolerated_when_feature_disabled(config_dir: Path) -> None:
    config = load_config(config_dir)  # example config has <...> placeholders, features disabled
    assert config.settings.llm.enabled is False


def test_placeholder_refused_when_llm_enabled(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(config_dir / "settings.yaml", lambda d: d["llm"].update(enabled=True))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "model" in str(exc.value)


def test_r47_secret_never_in_repr_or_error(
    config_dir: Path, edit_yaml: Edit, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "tok_SUPERSECRET_123"
    monkeypatch.setenv("NTFY_TOKEN", secret)
    config = load_config(config_dir)
    assert config.settings.ntfy is not None
    assert secret not in repr(config)
    assert secret not in str(config.settings)
    # secrets are read on use, from the env var named in the config
    token = config.settings.ntfy.token()
    assert token is not None
    assert token.get_secret_value() == secret
    assert secret not in repr(token)

    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(rate_limit_s="vite"))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert secret not in str(exc.value)


def test_missing_secret_is_not_a_load_error(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NTFY_TOKEN", raising=False)
    config = load_config(config_dir)  # F7: loading never requires a secret
    assert config.settings.ntfy is not None
    with pytest.raises(ConfigError) as exc:
        config.settings.ntfy.token()
    assert "NTFY_TOKEN" in str(exc.value)


def test_series_needs_only_identity_and_legal_flag(config_dir: Path, edit_yaml: Edit) -> None:
    """Autonomy: channels, queries and hashtags are derived by rushes, never required."""
    (config_dir / "series" / "oggy.yaml").write_text(
        "id: oggy\ntitle: Oggy\naudio: nonverbal\nlicensed: false\n", encoding="utf-8"
    )
    config = load_config(config_dir)
    oggy = next(s for s in config.series if s.id == "oggy")
    assert oggy.sources.youtube_channels == []
    assert oggy.branding.hashtags_pool == []
    assert oggy.branding.display_name is None


def test_creator_hashtag_is_optional(config_dir: Path) -> None:
    config = load_config(config_dir)
    assert next(c for c in config.creators if c.id == "squeezie").hashtag is None


def test_legal_flags_stay_mandatory(config_dir: Path, edit_yaml: Edit) -> None:
    """licensed / authorized are Louis's decisions (R14, R22): never defaulted."""
    edit_yaml(config_dir / "series" / "oggy.yaml", lambda d: d.pop("licensed"))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "licensed" in str(exc.value)
    edit_yaml(config_dir / "series" / "oggy.yaml", lambda d: d.update(licensed=False))
    edit_yaml(config_dir / "creators" / "squeezie.yaml", lambda d: d.pop("authorized"))
    with pytest.raises(ConfigError) as exc2:
        load_config(config_dir)
    assert "authorized" in str(exc2.value)


@pytest.mark.parametrize(
    ("key", "value", "word"),
    [
        ("target_weight", -1.0, "target_weight"),
        ("loud_weight", -0.1, "loud_weight"),
        ("motion_start_bonus", -0.5, "motion_start_bonus"),
        ("quiet_point_penalty", -1, "quiet_point_penalty"),
        ("target_weight", float("nan"), "target_weight"),
        ("loud_weight", float("inf"), "loud_weight"),
        ("start_motion_min", 1.5, "start_motion_min"),
        ("start_motion_min", -0.1, "start_motion_min"),
        ("speech_margin_ms", -1, "speech_margin_ms"),
        ("peak_guard_ms", -1, "peak_guard_ms"),
        ("start_shift_ms", -1, "start_shift_ms"),
        ("quiet_point_step_ms", 0, "quiet_point_step_ms"),
    ],
)
def test_cut_settings_out_of_range_are_refused_in_french(
    config_dir: Path, edit_yaml: Edit, key: str, value: float, word: str
) -> None:
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(cut={key: value}))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert word in str(exc.value)
    assert "doit être" in str(exc.value)


def test_cut_settings_default_to_the_brief_values(config_dir: Path) -> None:
    cut = load_config(config_dir).settings.cut
    assert cut.model_dump() == {
        "loud_weight": 1.0,
        "target_weight": 1.0,
        "motion_start_bonus": 0.5,
        "speech_margin_ms": 150,
        "peak_guard_ms": 1500,
        "quiet_point_step_ms": 2000,
        "quiet_point_penalty": 1.0,
        "start_motion_min": 0.15,
        "start_shift_ms": 2000,
    }


@pytest.mark.parametrize(
    ("key", "value", "word"),
    [("crf", 52, "crf"), ("crf", -1, "crf"), ("crf", 19.5, "crf"), ("preset", "turbo", "preset")],
)
def test_render_settings_out_of_range_are_refused_in_french(
    config_dir: Path, edit_yaml: Edit, key: str, value: object, word: str
) -> None:
    edit_yaml(config_dir / "settings.yaml", lambda d: d.update(render={key: value}))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert word in str(exc.value)
    assert "doit être" in str(exc.value)


def test_render_settings_default_to_the_brief_values(config_dir: Path) -> None:
    render = load_config(config_dir).settings.render
    assert render.model_dump() == {"preset": "medium", "crf": 19}


def test_render_settings_can_be_overridden(config_dir: Path, edit_yaml: Edit) -> None:
    edit_yaml(
        config_dir / "settings.yaml",
        lambda d: d.update(render={"preset": "ultrafast", "crf": 30}),
    )
    assert load_config(config_dir).settings.render.model_dump() == {
        "preset": "ultrafast",
        "crf": 30,
    }
