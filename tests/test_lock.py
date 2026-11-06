import threading
from pathlib import Path

import pytest

from rushes.errors import LockBusy
from rushes.lock import express_intent, heavy_lock
from rushes.paths import Paths

WAIT = 5.0


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data")


def test_second_heavy_lock_waits(paths: Paths) -> None:
    acquired = threading.Event()

    def other() -> None:
        with heavy_lock(paths, wait=True):
            acquired.set()

    with heavy_lock(paths, wait=True):
        thread = threading.Thread(target=other)
        thread.start()
        assert not acquired.wait(0.3)  # blocked while we hold it
    assert acquired.wait(WAIT)
    thread.join(WAIT)


def test_no_wait_raises_lock_busy(paths: Paths) -> None:
    with (
        heavy_lock(paths, wait=True),
        pytest.raises(LockBusy) as busy,
        heavy_lock(paths, wait=False),
    ):
        pass
    assert busy.value.exit_code == 3
    assert busy.value.hint


def test_wait_with_timeout_raises_lock_busy(paths: Paths) -> None:
    with (
        heavy_lock(paths, wait=True),
        pytest.raises(LockBusy),
        heavy_lock(paths, wait=True, timeout_s=0.2),
    ):
        pass


def test_lock_released_on_exception(paths: Paths) -> None:
    with pytest.raises(ValueError, match="boom"), heavy_lock(paths, wait=True):
        raise ValueError("boom")
    with heavy_lock(paths, wait=False):  # free again, file still there
        pass
    assert (paths.locks / "heavy.lock").exists()


def test_yield_returns_false_when_nobody_waits(paths: Paths) -> None:
    released: list[bool] = []
    with heavy_lock(paths, wait=True) as held:
        assert held.yield_if_express_waiting(before_yield=lambda: released.append(True)) is False
    assert released == []
    with express_intent(paths):  # the probe left express.lock free
        pass


def test_express_yield_hands_over_and_resumes(paths: Paths) -> None:
    announced, got_heavy, release_express = threading.Event(), threading.Event(), threading.Event()
    order: list[str] = []

    def express() -> None:
        with express_intent(paths):
            announced.set()
            with heavy_lock(paths, wait=True):
                order.append("express")
                got_heavy.set()
                release_express.wait(WAIT)

    with heavy_lock(paths, wait=True) as held:
        thread = threading.Thread(target=express)
        thread.start()
        assert announced.wait(WAIT)
        done: list[bool] = []
        resumer = threading.Thread(
            target=lambda: done.append(
                held.yield_if_express_waiting(before_yield=lambda: order.append("release"))
            )
        )
        resumer.start()
        assert got_heavy.wait(WAIT)  # the express lane took the heavy lock while we yielded
        assert resumer.is_alive()  # we are parked until it is over
        release_express.set()
        resumer.join(WAIT)
        thread.join(WAIT)
        assert done == [True]
        order.append("resumed")
        with pytest.raises(LockBusy), heavy_lock(paths, wait=False):  # we hold it again
            pass
    assert order == ["release", "express", "resumed"]
