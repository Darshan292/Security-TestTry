"""Bounded pool of NER worker PROCESSES (fail closed).

Why: spaCy inference is pure-Python/numpy work that holds the GIL and oversubscribes BLAS threads when several
gateway threads call it at once (see eval/results/ner_profile.md). Each worker process loads the model once, with
BLAS/OMP/torch threads pinned to 1, and serves one task at a time.

Contract (same as ``NerDetector``): ``detect``/``detect_many`` return PERSON detections with offsets into the exact
strings passed in, or raise ``NerUnavailable`` -- queue full (immediately), per-call timeout, worker crash, closed
pool, or inference failure. Nothing is ever silently skipped. A worker that times out or dies is killed and
replaced in the background; until the replacement is ready the pool has fewer workers (callers may time out).
"""
from __future__ import annotations

import atexit
import logging
import multiprocessing as mp
import os
import queue
import threading
import time

from pgw.detect.ner import NerDetector, NerUnavailable
from pgw.types import Detection

_log = logging.getLogger("pgw.ner_pool")
_PIN = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS")
_spawn_lock = threading.Lock()


def default_workers() -> int:
    return max(1, min(4, os.cpu_count() or 1))


def _worker_main(conn, model: str, min_score: float) -> None:     # runs in the child process
    try:
        # torch is not a pinned dependency; if present it honours OMP_NUM_THREADS=1 set by the parent at spawn
        det = NerDetector(model, min_score)
        det.warm()
        conn.send(("ready", None))
    except BaseException as e:  # noqa: BLE001
        # Report and exit. If even the report fails, the parent never sees "ready" and fails closed (startup error).
        try:
            conn.send(("fatal", f"{type(e).__name__}: {e}"))
        except Exception:  # noqa: BLE001
            pass
        return
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return                                   # parent gone
        if msg is None:
            return
        tid, texts = msg
        try:
            conn.send((tid, "ok", det.detect_many(texts)))
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
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.join(2)
        except Exception:  # noqa: BLE001
            pass


class NerPool:
    """Drop-in for ``NerDetector`` (``detect`` / ``detect_many`` / ``warm``) backed by worker processes."""

    def __init__(self, model: str, workers: int | None = None, max_queued: int = 8, timeout_s: float = 8.0,
                 min_score: float = 0.0, start_timeout_s: float = 180.0):
        self.model, self.min_score = model, min_score
        self.workers = workers or default_workers()
        self.max_queued, self.timeout_s, self.start_timeout_s = max_queued, timeout_s, start_timeout_s
        self._ctx = mp.get_context("spawn")           # no fork of a multi-threaded process
        self._idle: queue.Queue[_Worker] = queue.Queue()
        self._admit = threading.BoundedSemaphore(self.workers + max(0, max_queued))
        self._closed = threading.Event()
        self._live: set[_Worker] = set()
        self._live_lock = threading.Lock()
        self._tid = 0
        self.replacements = 0
        starters = [threading.Thread(target=self._start_one, args=(True,)) for _ in range(self.workers)]
        for t in starters:
            t.start()
        for t in starters:
            t.join()
        if self._idle.qsize() < self.workers:
            self.close()
            raise NerUnavailable(f"NER pool failed to start {self.workers} worker(s) for {model!r}")
        atexit.register(self.close)

    # -- worker lifecycle ----------------------------------------------------------------------------------
    def _spawn(self) -> _Worker:
        parent, child = self._ctx.Pipe()
        with _spawn_lock:                              # spawn inherits env: pin threads before numpy loads
            saved = {k: os.environ.get(k) for k in _PIN}
            os.environ.update({k: "1" for k in _PIN})
            try:
                proc = self._ctx.Process(target=_worker_main, args=(child, self.model, self.min_score), daemon=True)
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
            raise NerUnavailable("NER worker start timed out")
        try:
            kind, info = parent.recv()
        except (EOFError, OSError):
            w.kill()
            raise NerUnavailable("NER worker died while starting") from None
        if kind != "ready":
            w.kill()
            raise NerUnavailable(f"NER worker failed to start: {info}")
        return w

    def _start_one(self, initial: bool = False) -> None:
        delay = 0.5
        while not self._closed.is_set():
            try:
                w = self._spawn()
            except NerUnavailable as e:
                if initial:
                    _log.error("ner worker start failed: %s", e)
                    return
                _log.error("ner worker replacement failed: %s; retrying", e)
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
        """Kill a worker that timed out or died and start a replacement in the background."""
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
            except Exception:  # noqa: BLE001
                pass
            w.kill()

    # -- API -----------------------------------------------------------------------------------------------
    def warm(self) -> None:          # workers are warmed at start
        return None

    def detect(self, text: str) -> list[Detection]:
        return self.detect_many([text])[0]

    def detect_many(self, texts: list[str]) -> list[list[Detection]]:
        for t in texts:
            if not isinstance(t, str):
                raise NerUnavailable(f"expected str, got {type(t).__name__}")
        uniq = list(dict.fromkeys(texts))              # dedupe identical view texts, keep order
        if not uniq:
            return []
        if self._closed.is_set():
            raise NerUnavailable("NER pool is closed")
        if not self._admit.acquire(blocking=False):
            raise NerUnavailable("NER queue full")
        try:
            by_text = dict(zip(uniq, self._run(uniq)))
        finally:
            self._admit.release()
        return [list(by_text[t]) for t in texts]

    def _run(self, texts: list[str]) -> list[list[Detection]]:
        deadline = time.monotonic() + self.timeout_s
        try:
            w = self._idle.get(timeout=max(0.0, self.timeout_s))
        except queue.Empty:
            raise NerUnavailable("NER timed out waiting for a worker") from None
        if self._closed.is_set() or not w.proc.is_alive():
            self._retire(w)
            raise NerUnavailable("NER worker unavailable")
        self._tid += 1
        tid = self._tid
        try:
            w.conn.send((tid, texts))
            if not w.conn.poll(max(0.0, deadline - time.monotonic())):
                self._retire(w)
                raise NerUnavailable("NER call timed out")
            msg = w.conn.recv()
        except NerUnavailable:
            raise
        except (EOFError, OSError, BrokenPipeError):
            self._retire(w)
            raise NerUnavailable("NER worker crashed") from None
        except BaseException:
            self._retire(w)                            # unknown state (e.g. cancelled mid-call): never reuse
            raise
        if msg[0] != tid:
            self._retire(w)
            raise NerUnavailable("NER protocol error")
        self._idle.put(w)
        if msg[1] != "ok":
            raise NerUnavailable(f"NER inference failed: {msg[2]}")
        return msg[2]
