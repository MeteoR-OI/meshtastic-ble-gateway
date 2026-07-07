# SPDX-License-Identifier: AGPL-3.0-or-later
from fakes import FakeContext, FakeCounter, FakeProcess
from mbg.config import Config
from mbg.process_backend import WorkerHandle, spawn_worker
from mbg.worker import run_worker


def test_spawn_worker_uses_context_and_starts():
    ctx = FakeContext()
    handle = spawn_worker(Config(), ctx, target=lambda c, k: None)
    assert ctx.last_process.started is True
    assert ctx.last_process.name == "mbg-worker"
    assert isinstance(handle, WorkerHandle)


def test_spawn_worker_default_target_is_run_worker():
    ctx = FakeContext()
    spawn_worker(Config(), ctx)
    assert ctx.last_process.target is run_worker


def test_worker_handle_delegates():
    counter = FakeCounter()
    counter.value = 7
    proc = FakeProcess()
    proc.start()
    proc.exitcode = 0
    h = WorkerHandle(proc, counter)

    assert h.beats() == 7
    assert h.is_alive() is True
    h.kill()
    assert proc.killed is True
    assert h.is_alive() is False
    h.join(2)
    assert proc.joined_with == 2
    assert h.exitcode == 0
