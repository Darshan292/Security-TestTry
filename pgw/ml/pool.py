"""Bounded pool of model worker PROCESSES that fails closed (the pattern of pgw.detect.ner_pool, model-agnostic).

Each worker process builds its model once from a factory named in CODE (``"package.module:function"`` plus
arguments; never from configuration or a request), warms it, and serves one call at a time. ONNX Runtime threads per
worker come from the model's SessionOptions (`intra_op_threads`); BLAS/OpenMP threads in the worker are pinned to 1 so
that `workers x intra_op_threads` is the CPU budget.

Contract: ``call(method, texts)`` returns the worker's answer or raises ``MlUnavailable``: queue full (immediately),
per-call timeout, worker crash, closed pool, protocol error or inference failure. Nothing is ever silently skipped. A
worker that times out or dies is killed and replaced in the background.
"""
from __future__ import annotations

import atexit
import importlib
import logging
import multiprocessing as mp
import os
import queue
import threading
import time

from .base import MlUnavailable

_log = logging.getLogger("pgw.ml.pool")
_PIN = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS")
_spawn_lock = threading.Lock()
_METHODS = frozenset({"detect_many", "classify_many"})


def _resolve(factory: str):
    mod, _, fn = factory.partition(":")
    if not mod or not fn:
        raise ValueError("factory must be 'module:function'")
    return getattr(importlib.import_module(mod), fn)


def _worker_main(conn, factory: str, args: tuple) -> None:     # runs in the child process
    try:
        obj = _resolve(factory)(*args)
        obj.warm()
        conn.send(("ready", None))
    except BaseException as e:  # noqa: BLE001
        try:
            conn.send(("fatal", f"{type(e).__name__}: {e}"))
        except Exception as e2:  # noqa: BLE001 - parent never sees "ready" and fails closed
            _log.debug("ml worker could not report start failure: %s", type(e2).__name__)
        return
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        if msg is None:
            return
        tid, method, texts = msg
        try:
            if method not in _METHODS:
                raise ValueError("unknown method")
            conn.send((tid, "ok", getattr(obj, method)(texts)))
        except (EOFError, OSError):
            return
        except BaseException as e:  # noqa: BLE001
            try:
                conn.send((tid, "err", f"{type(e).__name__}: {e}"))
            except Exception:  # noqa: BLE001
                return


class _Worker:
    def __init__(self, proc, conn):
        self.proc, self.conn = proc, conn

    def kill(self) -> None:
        for step in (self.proc.kill, self.conn.close, lambda: self.proc.join(2)):
            try:
                step()
            except Exception as e:  # noqa: BLE001 - best effort teardown of a worker already being discarded
                _log.debug("ml worker teardown step failed: %s", type(e).__name__)


class WorkerPool:
    def __init__(self, factory: str, args: tuple, workers: int, max_queued: int = 8, timeout_s: float = 5.0,
                 start_timeout_s: float = 180.0, name: str = "ml"):
        if workers < 1:
            raise ValueError("a worker pool needs at least one worker")
        self.factory, self.args, self.name = factory, tuple(args), name
        self.workers, self.max_queued = workers, max_queued
        self.timeout_s, self.start_timeout_s = timeout_s, start_timeout_s
        self._ctx = mp.get_context("spawn")           # no fork of a multi-threaded process
        self._idle: queue.Queue[_Worker] = queue.Queue()
        self._admit = threading.BoundedSemaphore(self.workers + max(0, max_queued))
        self._closed = threading.Event()
        self._live: set[_Worker] = set()
        self._live_lock = threading.Lock()
        self._tid_lock = threading.Lock()
        self._tid = 0
        self.replacements = 0
        self.start_errors: list[str] = []
        starters = [threading.Thread(target=self._start_one, args=(True,)) for _ in range(self.workers)]
        for t in starters:
            t.start()
        for t in starters:
            t.join()
        if self._idle.qsize() < self.workers:
            self.close()
            detail = f": {self.start_errors[0]}" if self.start_errors else ""
            raise MlUnavailable(f"{name} pool failed to start {self.workers} worker(s){detail}")
        atexit.register(self.close)

    # -- worker lifecycle ----------------------------------------------------------------------------------
    def _spawn(self) -> _Worker:
        parent, child = self._ctx.Pipe()
        with _spawn_lock:                              # spawn inherits env: pin BLAS threads before numpy loads
            saved = {k: os.environ.get(k) for k in _PIN}
            os.environ.update({k: "1" for k in _PIN})
            try:
                proc = self._ctx.Process(target=_worker_main, args=(child, self.factory, self.args), daemon=True)
                proc.start()
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        child.close()
        w = _Worker(proc, parent)
        if not parent.poll(self.start_timeout_s):
            w.kill()
            raise MlUnavailable(f"{self.name} worker start timed out")
        try:
            kind, info = parent.recv()
        except (EOFError, OSError):
            w.kill()
            raise MlUnavailable(f"{self.name} worker died while starting") from None
        if kind != "ready":
            w.kill()
            raise MlUnavailable(f"{self.name} worker failed to start: {info}")
        return w

    def _start_one(self, initial: bool = False) -> None:
        delay = 0.5
        while not self._closed.is_set():
            try:
                w = self._spawn()
            except MlUnavailable as e:
                if initial:
                    self.start_errors.append(str(e))
                    _log.error("%s worker start failed: %s", self.name, e)
                    return
                _log.error("%s worker replacement failed: %s; retrying", self.name, e)
                self._closed.wait(delay)
                delay = min(delay * 2, 10)
                continue
            with self._live_lock:
                if self._closed.is_set():
                    w.kill()
                    return
                self._live.add(w)
            self._idle.put(w)
            return

    def _retire(self, w: _Worker) -> None:
        with self._live_lock:
            self._live.discard(w)
        w.kill()
        if not self._closed.is_set():
            self.replacements += 1
            threading.Thread(target=self._start_one, daemon=True).start()

    def close(self) -> None:
        self._closed.set()
        with self._live_lock:
            ws, self._live = list(self._live), set()
        for w in ws:
            try:
                w.conn.send(None)
            except Exception as e:  # noqa: BLE001 - the worker is killed next either way
                _log.debug("ml worker stop message failed: %s", type(e).__name__)
            w.kill()

    # -- API -----------------------------------------------------------------------------------------------
    def warm(self) -> None:          # workers are warmed at start
        return None

    def call(self, method: str, texts: list[str]):
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise MlUnavailable("expected a list of str")
        if not texts:
            return []
        if self._closed.is_set():
            raise MlUnavailable(f"{self.name} pool is closed")
        if not self._admit.acquire(blocking=False):
            raise MlUnavailable(f"{self.name} queue full")
        try:
            res = self._run(method, texts)
        finally:
            self._admit.release()
        if not isinstance(res, list) or len(res) != len(texts):
            raise MlUnavailable(f"{self.name} protocol error")
        return res

    def _run(self, method: str, texts: list[str]):
        deadline = time.monotonic() + self.timeout_s
        try:
            w = self._idle.get(timeout=max(0.0, self.timeout_s))
        except queue.Empty:
            raise MlUnavailable(f"{self.name} timed out waiting for a worker") from None
        if self._closed.is_set() or not w.proc.is_alive():
            self._retire(w)
            raise MlUnavailable(f"{self.name} worker unavailable")
        with self._tid_lock:
            self._tid += 1
            tid = self._tid
        try:
            w.conn.send((tid, method, texts))
            if not w.conn.poll(max(0.0, deadline - time.monotonic())):
                self._retire(w)
                raise MlUnavailable(f"{self.name} call timed out")
            msg = w.conn.recv()
        except MlUnavailable:
            raise
        except (EOFError, OSError):
            self._retire(w)
            raise MlUnavailable(f"{self.name} worker crashed") from None
        except BaseException:
            self._retire(w)                            # unknown state (e.g. cancelled mid-call): never reuse
            raise
        if not isinstance(msg, tuple) or len(msg) != 3 or msg[0] != tid:
            self._retire(w)
            raise MlUnavailable(f"{self.name} protocol error")
        self._idle.put(w)
        if msg[1] != "ok":
            raise MlUnavailable(f"{self.name} inference failed: {msg[2]}")
        return msg[2]


class DetectorPool(WorkerPool):
    """Drop-in for an in-process detector stage (``detect`` / ``detect_many`` / ``warm`` / ``close``)."""

    def detect_many(self, texts: list[str]):
        uniq = list(dict.fromkeys(texts))
        by_text = dict(zip(uniq, self.call("detect_many", uniq), strict=True))
        return [list(by_text[t]) for t in texts]

    def detect(self, text: str):
        return self.detect_many([text])[0]


class GatePool(WorkerPool):
    """Drop-in for an in-process content gate (``classify`` / ``classify_many``)."""

    def classify_many(self, texts: list[str]) -> list[dict[str, float]]:
        uniq = list(dict.fromkeys(texts))
        by_text = dict(zip(uniq, self.call("classify_many", uniq), strict=True))
        return [dict(by_text[t]) for t in texts]

    def classify(self, text: str) -> dict[str, float]:
        return self.classify_many([text])[0]
