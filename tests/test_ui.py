import io
import json
from pathlib import Path

import pytest

from rushes import ui
from rushes.ui import (
    Header,
    Message,
    Progress,
    StepEvent,
    Summary,
    Table,
    make_emitter,
    redact,
    register_secret,
)


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def lines(buf: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def test_json_mode_one_valid_line_per_event() -> None:
    buf = io.StringIO()
    em = make_emitter(plain=False, json=True, verbose=False, stream=buf)
    em.emit(Header("render", None, False, "0.1.0", None))
    em.emit(StepEvent("render", "p_000123", "done", 61234))
    em.close()
    records = lines(buf)
    assert len(records) == 2
    line = records[1]
    assert line["event"] == "step" and line["status"] == "done" and line["item"] == "p_000123"
    assert line["command"] == "render" and line["duration_ms"] == 61234
    assert isinstance(line["ts"], str) and line["ts"].endswith("Z")


def test_non_tty_defaults_to_json() -> None:
    buf = io.StringIO()
    em = make_emitter(plain=False, json=False, verbose=False, stream=buf)
    em.emit(Message("info", "bonjour"))
    assert json.loads(buf.getvalue())["text"] == "bonjour"


def test_plain_flag_forces_plain_text_even_on_a_tty() -> None:
    buf = Tty()
    em = make_emitter(plain=True, json=False, verbose=False, stream=buf)
    em.emit(StepEvent("cut", "e_000001", "done", 1500))
    out = buf.getvalue()
    assert "\x1b[" not in out and "cut" in out and "1,5 s" in out


def test_rich_mode_shows_symbols_and_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    buf = Tty()
    em = make_emitter(plain=False, json=False, verbose=False, stream=buf)
    for status in ("done", "skipped", "warned", "failed"):
        em.emit(StepEvent("render", "p_000123", status, 61234))  # type: ignore[arg-type]
    out = buf.getvalue()
    for symbol in ("✓", "↷", "⚠", "✗"):
        assert symbol in out
    assert "61,2 s" in out
    assert "\x1b[" in out  # colours are on by default in a terminal


def test_no_color_disables_ansi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("NO_COLOR", "1")
    buf = Tty()
    em = make_emitter(plain=False, json=False, verbose=False, stream=buf)
    em.emit(Header("run", "series=tom_and_jerry", True, "0.1.0", "abc1234"))
    em.emit(StepEvent("render", "p_000123", "failed", 10))
    em.emit(Message("error", "boum", hint="essaie encore"))
    em.emit(Table("T", ["a", "b"], [["1", "2"]]))
    assert "\x1b[" not in buf.getvalue()


@pytest.mark.parametrize("mode", ["rich", "plain", "json"])
def test_test_mode_header_says_ne_pas_publier(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    buf = Tty()
    em = make_emitter(plain=mode == "plain", json=mode == "json", verbose=False, stream=buf)
    em.emit(Header("run", None, True, "0.1.0", None))
    assert "NE PAS PUBLIER" in buf.getvalue() or '"test_mode": true' in buf.getvalue()
    if mode != "json":
        assert "NE PAS PUBLIER" in buf.getvalue()


def test_header_without_test_mode_does_not_say_ne_pas_publier() -> None:
    buf = Tty()
    em = make_emitter(plain=True, json=False, verbose=False, stream=buf)
    em.emit(Header("run", None, False, "0.1.0", "abc1234"))
    assert "NE PAS PUBLIER" not in buf.getvalue() and "abc1234" in buf.getvalue()


def test_errors_are_french_with_hint_and_trace_only_when_verbose() -> None:
    quiet, loud = Tty(), Tty()
    for buf, verbose in ((quiet, False), (loud, True)):
        em = make_emitter(plain=True, json=False, verbose=verbose, stream=buf)
        em.emit(Message("error", "configuration invalide", hint="Corrige le fichier."))
        ui.emit_trace(em, "Traceback (most recent call last): boom")
    assert (
        "configuration invalide" in quiet.getvalue() and "Corrige le fichier." in quiet.getvalue()
    )
    assert "Traceback" not in quiet.getvalue()
    assert "Traceback" in loud.getvalue()


def test_trace_always_reaches_the_log_file(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "2026-11-16.jsonl"
    em = make_emitter(plain=True, json=False, verbose=False, stream=io.StringIO(), log_path=log)
    ui.emit_trace(em, "Traceback: boom")
    em.close()
    assert "Traceback: boom" in log.read_text(encoding="utf-8")


def test_r47_no_secret_in_any_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    register_secret("tok-123-SECRET")
    leaky = "erreur avec tok-123-SECRET et cookie=abcdef"
    for mode in ("rich", "plain", "json"):
        buf = Tty()
        log = tmp_path / f"{mode}.jsonl"
        em = make_emitter(
            plain=mode == "plain", json=mode == "json", verbose=True, stream=buf, log_path=log
        )
        em.emit(Header("run", leaky, False, "0.1.0", None))
        em.emit(Message("error", leaky, hint=leaky))
        em.emit(StepEvent("render", leaky, "failed", 5, message=leaky))
        em.emit(Table("T", ["c"], [[leaky]]))
        ui.emit_trace(em, f"Traceback {leaky}")
        em.close()
        for text in (buf.getvalue(), log.read_text(encoding="utf-8")):
            assert "tok-123-SECRET" not in text, mode
            assert "abcdef" not in text, mode
            assert "***" in text, mode


@pytest.mark.parametrize(
    ("raw", "gone"),
    [
        ("Authorization: Bearer abc123def", ["abc123def"]),
        ("api_key=sk-live-999", ["sk-live-999"]),
        ("apikey=sk-live-999", ["sk-live-999"]),
        ("API-KEY: sk-live-999", ["sk-live-999"]),
        ('{"token": "xyz789"}', ["xyz789"]),
        ("PASSWORD = hunter2", ["hunter2"]),
        ('password="a b c" ensuite', ["a b c"]),
        ("secret='deux mots' ensuite", ["deux mots"]),
        ("Cookie: A=1; B=2; SID=zzz9", ["A=1", "B=2", "SID=zzz9"]),
        ("Set-Cookie: sid=abc; Path=/", ["sid=abc"]),
        ('{"cookie": "A=1; B=2"}', ["A=1", "B=2"]),
        ('password="unterminated', ["unterminated"]),
    ],
)
def test_redact_cookie_like_tokens(raw: str, gone: list[str]) -> None:
    out = redact(raw)
    assert not any(secret in out for secret in gone), out
    assert "«***»" in out


def test_redact_keeps_the_text_around_a_quoted_value() -> None:
    assert redact('password="a b c" ensuite') == f"password={ui.MASK} ensuite"


def test_redact_is_linear_on_hostile_input() -> None:
    import time

    for hostile in ("cookie=" + "a; " * 30000, "key=" + "x" * 200000, "token=" + '"' * 100000):
        started = time.perf_counter()
        redact(hostile)
        assert time.perf_counter() - started < 2.0


def test_redact_leaves_harmless_text_alone() -> None:
    assert redact("monkey=1 et clé de voûte") == "monkey=1 et clé de voûte"


def test_log_file_receives_every_event_even_in_rich_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    log = tmp_path / "logs" / "2026-11-16.jsonl"
    buf = Tty()
    em = make_emitter(plain=False, json=False, verbose=False, stream=buf, log_path=log)
    em.emit(Header("run", None, False, "0.1.0", None))
    em.emit(StepEvent("cut", "e_000001", "done", 10))
    em.emit(Progress("cut", 1, 3, "ép."))
    em.emit(Message("warn", "attention", hint="vérifie"))
    em.emit(Table("T", ["a"], [["1"]]))
    em.emit(Summary({"tiktok_a": 2}, 1, 1234, None))
    em.close()
    written = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in written] == [
        "header",
        "step",
        "progress",
        "message",
        "table",
        "summary",
    ]
    assert "\x1b[" in buf.getvalue()  # the console was rich while the log stayed JSON
    assert written[-1]["ready_by_account"] == {"tiktok_a": 2}


def test_message_alert_key_is_carried_in_json() -> None:
    buf = io.StringIO()
    em = make_emitter(plain=False, json=True, verbose=False, stream=buf)
    em.emit(Message("error", "disque plein", alert="disk_full"))
    assert lines(buf)[0]["alert"] == "disk_full"


def test_log_file_keeps_every_progress_while_the_stream_is_throttled(tmp_path: Path) -> None:
    buf = io.StringIO()
    log = tmp_path / "logs" / "d.jsonl"
    em = make_emitter(plain=False, json=True, verbose=False, stream=buf, log_path=log)
    for done in range(1, 6):
        em.emit(Progress("ffmpeg", done, 10))
    em.close()
    assert len(lines(buf)) == 1
    written = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["done"] for r in written] == [1, 2, 3, 4, 5]


def test_json_progress_is_limited_to_one_per_second_per_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(ui, "_monotonic", lambda: now[0])
    buf = io.StringIO()
    em = make_emitter(plain=False, json=True, verbose=False, stream=buf)
    em.emit(Progress("ffmpeg", 1, 10))
    em.emit(Progress("ffmpeg", 2, 10))  # same second: dropped
    em.emit(Progress("other", 1, 10))  # another task: kept
    now[0] += 1.1
    em.emit(Progress("ffmpeg", 3, 10))  # a second later: kept
    em.emit(Progress("ffmpeg", 10, 10))  # completion is never dropped
    assert [(r["task"], r["done"]) for r in lines(buf)] == [
        ("ffmpeg", 1),
        ("other", 1),
        ("ffmpeg", 3),
        ("ffmpeg", 10),
    ]


def test_table_and_summary_render_in_every_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    for mode in ("rich", "plain", "json"):
        buf = Tty()
        em = make_emitter(plain=mode == "plain", json=mode == "json", verbose=False, stream=buf)
        em.emit(Table("File par compte", ["Compte", "Prêts"], [["tiktok_a", "3"], ["ig_a"]]))
        em.emit(Summary({"tiktok_a": 3}, 2, 61234, "/data/outbox/INDEX.html"))
        out = buf.getvalue()
        assert "tiktok_a" in out and "INDEX.html" in out, mode


def test_r47_reading_a_secret_from_the_config_registers_it(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rushes.config import load_config

    monkeypatch.setenv("NTFY_TOPIC", "topic-very-private")
    cfg = load_config(config_dir)
    assert cfg.settings.ntfy is not None
    assert "topic-very-private" in cfg.settings.ntfy.topic().get_secret_value()
    assert redact("posting to topic-very-private") == f"posting to {ui.MASK}"


def test_ansi_only_on_the_stream_that_is_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    out, err = Tty(), io.StringIO()  # stderr redirected to a file or a pipe
    em = ui.RichEmitter(out, err)
    em.emit(StepEvent("render", "p_000123", "done", 10))
    em.emit(Message("error", "boum", hint="essaie encore"))
    assert "\x1b[" in out.getvalue()
    assert "boum" in err.getvalue() and "\x1b[" not in err.getvalue()
