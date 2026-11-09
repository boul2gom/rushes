import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from fakes.models import FakeModelHub
from helpers import FakeStep, ctx_events, ctx_output, make_context, snapshot
from rushes.context import Context, Filters
from rushes.errors import LockBusy, MediaError, RushesError, SourceUnavailable
from rushes.lock import express_intent, heavy_lock
from rushes.paths import Paths
from rushes.runner import (
    BaseStep,
    Outcome,
    StepReport,
    combine_exit_codes,
    exit_code_for,
    run_pipeline,
    run_step,
)


def bounded[R](call: Callable[[], R], timeout: float = 10.0) -> R:
    """Run `call` in a daemon thread; fail (instead of hanging the suite) after `timeout`."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = call()
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), f"still running after {timeout} s: probable deadlock"
    if "error" in box:
        raise box["error"]
    return box["value"]  # type: ignore[no-any-return]


def assert_locks_free(paths: Paths) -> None:
    def take_both() -> None:
        with heavy_lock(paths, wait=True, timeout_s=2), express_intent(paths):
            pass

    bounded(take_both, timeout=5)


def rep(**kwargs: int) -> StepReport:
    base = {"step": "s", "done": 0, "skipped": 0, "warned": 0, "failed": 0, "duration_ms": 1}
    return StepReport(**{**base, **kwargs})  # type: ignore[arg-type]


def last_run(ctx: Context) -> dict[str, Any]:
    with ctx.engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM runs ORDER BY id DESC LIMIT 1")).mappings().one()
    return dict(row)


def test_run_step_counts_done_skipped_failed(ctx: Context) -> None:
    step = FakeStep(fail={2})
    report = run_step(ctx, step, Filters())
    assert (report.done, report.skipped, report.failed) == (2, 0, 1)
    assert report.step == "fake"
    code = run_pipeline(ctx, [FakeStep(fail={2})], Filters(), command="t")
    assert code == 4
    assert last_run(ctx)["status"] == "partial"


def test_run_step_counts_every_status_and_unavailable_sources(ctx: Context) -> None:
    step = FakeStep(
        items=(1, 2, 3, 4),
        outcomes={
            1: Outcome("skipped", "déjà attribué"),
            2: Outcome("warned", "source perdue", source_unavailable=True),
            3: Outcome("warned", "avertissement"),
            4: Outcome("done"),
        },
    )
    report = run_step(ctx, step, Filters())
    assert (report.done, report.skipped, report.warned, report.failed) == (1, 1, 2, 0)
    assert report.unavailable == 1
    assert exit_code_for([report], []) == 5


def test_failed_item_is_reported_and_the_next_ones_continue(ctx: Context) -> None:
    run_step(ctx, FakeStep(fail={2}), Filters())
    events = [e for e in ctx_events(ctx) if e["event"] == "step"]
    assert [(e["item"], e["status"]) for e in events] == [
        ("i_1", "done"),
        ("i_2", "failed"),
        ("i_3", "done"),
    ]
    assert "boom 2" in events[1]["message"]


def test_finish_is_called_once_after_the_last_item(ctx: Context) -> None:
    step = FakeStep()
    run_step(ctx, step, Filters())
    assert step.finished == 1


def test_a_raising_select_is_a_failed_step_not_a_crash(ctx: Context) -> None:
    class BadSelect(BaseStep):
        name = "bad"

        def key(self, item: int) -> str:
            return str(item)

        def select(self, ctx: Context, filters: Filters) -> list[int]:
            raise RuntimeError("select exploded")

        def run(self, ctx: Context, item: int, *, force: bool) -> Outcome:
            raise AssertionError("never reached")

    report = run_step(ctx, BadSelect(), Filters())
    assert report.failed == 1
    assert run_pipeline(ctx, [BadSelect()], Filters(), command="t") == 4


def test_source_unavailable_outside_an_item_gives_exit_5(ctx: Context) -> None:
    class Offline(BaseStep):
        name = "offline"

        def key(self, item: int) -> str:
            return str(item)

        def select(self, ctx: Context, filters: Filters) -> list[int]:
            raise SourceUnavailable("YouTube ne répond pas", hint="Réessaie plus tard.")

        def run(self, ctx: Context, item: int, *, force: bool) -> Outcome:
            raise AssertionError("never reached")

    code = run_pipeline(ctx, [Offline(), FakeStep()], Filters(), command="t")
    assert code == 5
    assert last_run(ctx)["exit_code"] == 5
    messages = [e for e in ctx_events(ctx) if e["event"] == "message"]
    assert messages and messages[0]["hint"] == "Réessaie plus tard."
    assert (ctx.paths.data_dir / "out" / "1.txt").exists()  # the next step still ran


def test_r16_step_rerun_changes_nothing(ctx: Context) -> None:
    step = FakeStep()
    run_step(ctx, step, Filters())
    before = snapshot(ctx)
    assert step.select(ctx, Filters()) == []
    report = run_step(ctx, step, Filters())
    assert (report.done, report.skipped, report.failed) == (0, 0, 0)
    assert snapshot(ctx) == before
    assert before["files"] != {}  # the snapshot really saw the written files


def test_r16_pipeline_rerun_changes_nothing(ctx: Context) -> None:
    """Through run_pipeline: the run row and the log are bookkeeping, not work."""
    assert run_pipeline(ctx, [FakeStep()], Filters(), command="t") == 0
    before = snapshot(ctx)
    assert run_pipeline(ctx, [FakeStep()], Filters(), command="t") == 0
    assert snapshot(ctx) == before
    assert snapshot(ctx, include_runs=True) != snapshot(ctx)  # the second run row exists


def test_force_reruns_everything(ctx: Context) -> None:
    step = FakeStep()
    run_step(ctx, step, Filters())
    step.ran.clear()
    report = run_step(ctx, step, Filters(force=True))
    assert step.ran == [1, 2, 3]
    assert report.done == 3


def test_limit_caps_the_number_of_items(ctx: Context) -> None:
    step = FakeStep()
    assert run_step(ctx, step, Filters(limit=2)).done == 2
    assert step.ran == [1, 2]


def test_interrupted_step_leaves_no_partial_output(ctx: Context) -> None:
    step = FakeStep(crash_during_write={2})
    run_step(ctx, step, Filters())
    out = ctx.paths.data_dir / "out"
    names = sorted(p.name for p in out.iterdir())
    assert names == ["1.txt", "3.txt"]  # no 2.txt, no .tmp leftovers
    assert step.select(ctx, Filters()) == [2]  # the next run picks it up again


@pytest.mark.parametrize(
    ("reports", "errors", "code"),
    [
        ([rep(failed=1)], [SourceUnavailable("x")], 4),
        ([rep()], [SourceUnavailable("x")], 5),
        ([], [LockBusy("x")], 3),
        ([rep()], [], 0),
        ([rep(failed=1)], [], 4),
        ([rep(failed=1)], [MediaError("x")], 1),
        ([rep(unavailable=1, warned=1)], [], 5),
        ([rep(failed=1), rep(unavailable=2)], [], 4),
        ([], [], 0),
    ],
)
def test_exit_code_priority(
    reports: list[StepReport], errors: list[RushesError], code: int
) -> None:
    assert exit_code_for(reports, errors) == code


def test_combine_exit_codes() -> None:
    assert combine_exit_codes(0, 5) == 5
    assert combine_exit_codes(4, 5) == 4
    assert combine_exit_codes(1, 2) == 2
    assert combine_exit_codes(0, 0) == 0
    assert combine_exit_codes(3, 1, 4) == 3
    assert combine_exit_codes() == 0


def test_runs_row_records_exit_code_and_summary(ctx: Context) -> None:
    code = run_pipeline(ctx, [FakeStep(fail={2})], Filters(series="tom_and_jerry"), command="cut")
    assert code == 4
    row = last_run(ctx)
    assert (row["command"], row["status"], row["exit_code"]) == ("cut", "partial", 4)
    assert row["started_at"] == "2026-11-16T12:00:00+00:00" and row["ended_at"]
    summary = json.loads(row["summary_json"])
    assert summary["reports"][0]["step"] == "fake" and summary["reports"][0]["failed"] == 1
    header, *_, final = ctx_events(ctx)
    assert header["event"] == "header" and header["command"] == "cut"
    assert header["scope"] == "série tom_and_jerry"
    assert final["event"] == "summary" and final["warnings"] == 0


def test_runs_row_ok_and_failed_statuses(ctx: Context) -> None:
    assert run_pipeline(ctx, [FakeStep()], Filters(), command="ok") == 0
    assert last_run(ctx)["status"] == "ok"
    assert run_pipeline(ctx, [FakeStep(items=(9,), fail={9})], Filters(), command="bad") == 4
    assert last_run(ctx)["exit_code"] == 4


def test_summary_counts_ready_posts_per_account(ctx: Context) -> None:
    with ctx.engine.begin() as conn:
        account = conn.execute(text("SELECT id FROM accounts LIMIT 1")).scalar_one()
        for status, is_test in (("ready", 0), ("ready", 0), ("ready", 1), ("planned", 0)):
            conn.execute(
                text("INSERT INTO posts(account_id, status, is_test) VALUES (:a, :s, :t)"),
                {"a": account, "s": status, "t": is_test},
            )
    run_pipeline(ctx, [], Filters(), command="t")
    summary = ctx_events(ctx)[-1]
    assert summary["ready_by_account"] == {account: 2}
    assert summary["showcase_by_account"] == {account: 1}  # the `ready` test post, apart


def test_pipeline_event_stream_has_no_secret(ctx: Context) -> None:
    from rushes.ui import register_secret

    register_secret("tok-123-SECRET")
    run_pipeline(ctx, [FakeStep(fail={2})], Filters(), command="t")
    assert "tok-123-SECRET" not in ctx_output(ctx)


class HeavyFake(FakeStep):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(items=(1, 2), heavy=True, **kwargs)


def test_heavy_step_takes_and_frees_the_heavy_lock(ctx: Context) -> None:
    seen: list[bool] = []

    def probe(item: int) -> None:
        try:
            with heavy_lock(ctx.paths, wait=False):
                seen.append(False)
        except LockBusy:
            seen.append(True)

    assert run_pipeline(ctx, [HeavyFake(on_run=probe)], Filters(), command="t") == 0
    assert seen == [True, True]  # held during the items
    with heavy_lock(ctx.paths, wait=False):  # free afterwards
        pass


def test_heavy_lock_busy_with_no_wait_exits_3(ctx: Context) -> None:
    with heavy_lock(ctx.paths, wait=True):
        code = run_pipeline(ctx, [HeavyFake()], Filters(), command="t", wait=False)
    assert code == 3
    assert last_run(ctx)["status"] == "failed"
    assert not (ctx.paths.data_dir / "out").exists()


def test_r19_models_released_before_yielding_lock(tmp_path: Path, config_dir: Path) -> None:
    hub = FakeModelHub(loaded={"clip": 1})
    ctx = make_context(tmp_path, config_dir, hub=hub)
    order: list[str] = []
    announced = threading.Event()

    def express() -> None:
        with express_intent(ctx.paths):
            announced.set()
            with heavy_lock(ctx.paths, wait=True):
                order.append("express")

    thread = threading.Thread(target=express)

    def on_run(item: int) -> None:
        if item == 1:
            thread.start()
            assert announced.wait(5)
        order.append(f"item{item}")

    code = bounded(lambda: run_pipeline(ctx, [HeavyFake(on_run=on_run)], Filters(), command="t"))
    thread.join(5)
    assert not thread.is_alive()
    assert code == 0
    assert order == ["item1", "express", "item2"]
    assert hub.release_calls == 1
    ctx.close()


def test_heavy_lock_is_held_once_for_the_whole_pipeline(tmp_path: Path, config_dir: Path) -> None:
    """Two heavy steps and a light one: an express lane waiting during the last item of the first
    heavy step gets in at an item boundary of the next step, never between two steps; the light
    step still runs under the held lock."""
    hub = FakeModelHub(loaded={"clip": 1})
    ctx = make_context(tmp_path, config_dir, hub=hub)
    order: list[str] = []
    announced = threading.Event()

    def express() -> None:
        with express_intent(ctx.paths):
            announced.set()
            with heavy_lock(ctx.paths, wait=True):
                order.append(f"express(release={hub.release_calls})")

    thread = threading.Thread(target=express, daemon=True)

    def first(item: int) -> None:
        if item == 2:  # last item of the first heavy step
            thread.start()
            assert announced.wait(5)
        order.append(f"a{item}")

    held_by_light: list[bool] = []

    def light(item: int) -> None:
        try:
            with heavy_lock(ctx.paths, wait=False):
                held_by_light.append(False)
        except LockBusy:
            held_by_light.append(True)
        order.append(f"l{item}")

    steps_ = [
        FakeStep(items=(1, 2), heavy=True, on_run=first),
        FakeStep(items=(3, 4), heavy=True, on_run=lambda i: order.append(f"b{i}")),
        FakeStep(items=(5,), on_run=light),
    ]
    code = bounded(lambda: run_pipeline(ctx, steps_, Filters(), command="t"))
    thread.join(5)
    assert code == 0
    # no hand-over between a2 and b3 (two steps) nor before the light step: only between b3 and b4
    assert order == ["a1", "a2", "b3", "express(release=1)", "b4", "l5"]
    assert hub.release_calls == 1
    assert held_by_light == [True]
    ctx.close()


class Bogus(FakeStep):
    def run(self, ctx: Context, item: int, *, force: bool) -> Outcome:
        return Outcome("bogus")  # type: ignore[arg-type]


def test_exception_escaping_the_pipeline_closes_the_runs_row(ctx: Context) -> None:
    with pytest.raises(KeyError):
        run_pipeline(ctx, [Bogus()], Filters(), command="boom")
    row = last_run(ctx)
    assert (row["command"], row["status"], row["exit_code"]) == ("boom", "failed", 1)
    assert row["ended_at"]


def test_keyboard_interrupt_closes_the_runs_row_and_frees_the_lock(ctx: Context) -> None:
    def interrupt(item: int) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_pipeline(ctx, [FakeStep(heavy=True, on_run=interrupt)], Filters(), command="ctrl-c")
    assert last_run(ctx)["status"] == "failed"
    assert_locks_free(ctx.paths)


def test_failure_while_yielding_closes_the_runs_row(tmp_path: Path, config_dir: Path) -> None:
    class BrokenHub(FakeModelHub):
        def release(self) -> None:
            raise RuntimeError("release exploded")

    ctx = make_context(tmp_path, config_dir, hub=BrokenHub())
    announced = threading.Event()

    def express() -> None:
        with express_intent(ctx.paths):
            announced.set()
            with heavy_lock(ctx.paths, wait=True):
                pass

    thread = threading.Thread(target=express, daemon=True)

    def on_run(item: int) -> None:
        if item == 1:
            thread.start()
            assert announced.wait(5)

    with pytest.raises(RuntimeError, match="release exploded"):
        bounded(lambda: run_pipeline(ctx, [HeavyFake(on_run=on_run)], Filters(), command="y"))
    assert last_run(ctx)["status"] == "failed"
    ctx.close()


def test_light_step_never_releases_models_nor_yields(tmp_path: Path, config_dir: Path) -> None:
    hub = FakeModelHub()
    ctx = make_context(tmp_path, config_dir, hub=hub)
    with express_intent(ctx.paths):  # an express lane waiting does not matter to a light step
        assert run_pipeline(ctx, [FakeStep()], Filters(), command="t") == 0
    assert hub.release_calls == 0
    ctx.close()


def test_run_pipeline_with_held_lock_never_waits_on_itself(
    tmp_path: Path, config_dir: Path
) -> None:
    hub = FakeModelHub()
    ctx = make_context(tmp_path, config_dir, hub=hub)
    result: dict[str, int] = {}

    def express_lane() -> None:
        with express_intent(ctx.paths), heavy_lock(ctx.paths, wait=True) as held:
            result["code"] = run_pipeline(
                ctx, [HeavyFake()], Filters(), command="t", lock=held, yield_to_express=False
            )

    bounded(express_lane, timeout=10)
    assert result == {"code": 0}
    assert hub.release_calls == 0  # no yield happened
    assert_locks_free(ctx.paths)
    ctx.close()
