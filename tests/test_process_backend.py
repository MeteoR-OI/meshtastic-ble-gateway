# SPDX-License-Identifier: AGPL-3.0-or-later
import queue

from fakes import FakeContext, FakeCounter, FakeProcess
from mbg.config import Config
from mbg.process_backend import WorkerHandle, spawn_worker
from mbg.worker import run_worker


class FakeCmdQ:
    def __init__(self):
        self.put_items = []

    def put(self, item):
        self.put_items.append(item)


class FakeResQ:
    def __init__(self, result=None, stale=None):
        self._stale = list(stale or [])
        self._result = result

    def get_nowait(self):
        if self._stale:
            return self._stale.pop(0)
        raise queue.Empty

    def get(self, timeout=None):
        if self._result is None:
            raise queue.Empty
        return self._result


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


def test_submit_success_tags_id():
    cmd_q = FakeCmdQ()
    res_q = FakeResQ(result={"ok": True})
    h = WorkerHandle(FakeProcess(), FakeCounter(), cmd_q, res_q)
    r = h.submit({"type": "text"}, timeout=1)
    assert r == {"ok": True}
    assert cmd_q.put_items[0] == {"type": "text", "id": 1}


def test_submit_purges_stale_result():
    cmd_q = FakeCmdQ()
    res_q = FakeResQ(result={"ok": True, "fresh": 1}, stale=[{"ok": False, "old": 1}])
    h = WorkerHandle(FakeProcess(), FakeCounter(), cmd_q, res_q)
    r = h.submit({"type": "x"}, 1)
    assert r == {"ok": True, "fresh": 1}  # le périmé a été purgé


def test_submit_timeout():
    cmd_q = FakeCmdQ()
    res_q = FakeResQ(result=None)  # get() -> Empty
    h = WorkerHandle(FakeProcess(), FakeCounter(), cmd_q, res_q)
    r = h.submit({"type": "x"}, 0.01)
    assert r["ok"] is False and "timeout" in r["error"]
