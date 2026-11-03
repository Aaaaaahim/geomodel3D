"""对话框后台任务助手测试。"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from geomodel3d.ui.async_runner import AsyncRunner


class _FakeHandle:
    run_id = "job-1"

    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _FakeTasks(QObject):
    taskFinished = pyqtSignal(str, object)
    taskFailed = pyqtSignal(str, object)
    taskCancelled = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.submitted = []
        self.cancelled = []

    def submit(self, fn, *, name="task"):
        self.submitted.append((fn, name))
        return _FakeHandle()

    def cancel(self, run_id):
        self.cancelled.append(run_id)


def test_async_runner_finished(qapp):
    tasks = _FakeTasks()
    runner = AsyncRunner(tasks)
    received = []

    def fn(ctx):
        return 42

    run_id = runner.submit(fn, name="demo", on_finished=received.append)
    assert run_id == "job-1"
    assert runner.busy is True
    assert tasks.submitted == [(fn, "demo")]

    tasks.taskFinished.emit("job-1", 42)
    assert received == [42]
    assert runner.busy is False


def test_async_runner_failed_and_cancelled(qapp):
    tasks = _FakeTasks()
    runner = AsyncRunner(tasks)
    failures = []
    cancels = []

    runner.submit(lambda ctx: None, name="a", on_finished=lambda _: None, on_failed=failures.append)
    error = RuntimeError("boom")
    tasks.taskFailed.emit("job-1", error)
    assert failures == [error]

    runner.submit(
        lambda ctx: None,
        name="b",
        on_finished=lambda _: None,
        on_cancelled=lambda: cancels.append(True),
    )
    tasks.taskCancelled.emit("job-1")
    assert cancels == [True]


def test_async_runner_ignores_unrelated_run_id(qapp):
    tasks = _FakeTasks()
    runner = AsyncRunner(tasks)
    received = []
    runner.submit(lambda ctx: None, name="demo", on_finished=received.append)

    tasks.taskFinished.emit("modeling-xxxx", "other")
    assert received == []
    assert runner.busy is True


def test_async_runner_cancel_and_detach(qapp):
    tasks = _FakeTasks()
    runner = AsyncRunner(tasks)
    run_id = runner.submit(lambda ctx: None, name="demo", on_finished=lambda _: None)
    runner.cancel(run_id)
    assert tasks.cancelled == [run_id]

    runner.detach()
    assert runner.busy is False
