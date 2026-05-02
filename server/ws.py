"""WebSocket connection manager for broadcasting job logs."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from contextvars import ContextVar
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)

_ACTIVE_JOB_ID: ContextVar[str | None] = ContextVar(
    "job_log_active_job_id", default=None
)


def set_log_job_context(job_id: str | None):
    return _ACTIVE_JOB_ID.set(job_id)


def reset_log_job_context(token: object) -> None:
    _ACTIVE_JOB_ID.reset(token)


class JobLogHandler(logging.Handler):
    """Custom logging handler that broadcasts log records to WebSocket subscribers.

    Includes a per-job log buffer so that early log messages emitted before
    any WebSocket subscriber connects are not lost.  When a new subscriber
    calls :meth:`subscribe`, all buffered messages are returned so the caller
    can replay them before entering the live queue loop.
    """

    _MAX_BUFFER_SIZE = 2000

    def __init__(self) -> None:
        super().__init__()
        # Single lock protects both _subscribers and _log_buffers to ensure
        # atomicity: a message is either buffered-only (subscriber joins
        # later and replays it) or delivered via the queue (subscriber was
        # already registered), but never both for the same subscriber.
        self._lock = threading.Lock()
        # job_id -> list[(event loop, asyncio.Queue)]
        self._subscribers: dict[
            str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]]
        ] = {}
        # job_id -> bounded deque of log messages
        self._log_buffers: dict[str, deque[dict[str, Any]]] = {}

    # -- buffer lifecycle ----------------------------------------------------

    def start_buffering(self, job_id: str) -> None:
        """Begin buffering log messages for *job_id*.

        Called when the pipeline task starts so that messages emitted before
        any WebSocket subscriber connects are preserved.
        """
        with self._lock:
            if job_id not in self._log_buffers:
                self._log_buffers[job_id] = deque(maxlen=self._MAX_BUFFER_SIZE)

    def stop_buffering(self, job_id: str) -> None:
        """Discard the log buffer for *job_id*.

        Called when the pipeline completes or the job is purged.
        """
        with self._lock:
            self._log_buffers.pop(job_id, None)

    # -- subscriber management -----------------------------------------------

    def subscribe(
        self, job_id: str
    ) -> tuple[asyncio.Queue[dict[str, Any]], list[dict[str, Any]]]:
        """Register a subscriber and return ``(queue, buffered_logs)``.

        The caller should replay *buffered_logs* first, then consume from
        *queue* for live messages.  The single-lock design guarantees that
        no message is lost or duplicated across the buffer and the queue.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers.setdefault(job_id, []).append((loop, queue))
            buffered = list(self._log_buffers.get(job_id, []))
        return queue, buffered

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            subs = self._subscribers.get(job_id, [])
            filtered = [
                (loop, existing_queue)
                for loop, existing_queue in subs
                if existing_queue is not queue
            ]
            if filtered:
                self._subscribers[job_id] = filtered
            else:
                self._subscribers.pop(job_id, None)

    # -- internal helpers ----------------------------------------------------

    def _drop_oldest(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return

    def _enqueue_message(
        self, job_id: str, queue: asyncio.Queue[dict[str, Any]], message: dict[str, Any]
    ) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            if message.get("type") in {"state", "done"}:
                while True:
                    self._drop_oldest(queue)
                    try:
                        queue.put_nowait(message)
                        break
                    except asyncio.QueueFull:
                        continue
            else:
                self._drop_oldest(queue)
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    log.warning("Dropped overflowing log message for job %s", job_id)

    # -- broadcast -----------------------------------------------------------

    def _broadcast(self, job_id: str, message: dict[str, Any]) -> None:
        with self._lock:
            # Buffer log-type messages so that late-connecting subscribers
            # can replay them.
            if message.get("type") == "log":
                buf = self._log_buffers.get(job_id)
                if buf is not None:
                    buf.append(message)
            subscribers = list(self._subscribers.get(job_id, []))

        if not subscribers:
            return

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        for loop, queue in subscribers:
            if current_loop is loop:
                self._enqueue_message(job_id, queue, message)
                continue
            try:
                loop.call_soon_threadsafe(self._enqueue_message, job_id, queue, message)
            except RuntimeError:
                self.unsubscribe(job_id, queue)

    def publish_state(self, job_id: str, job: dict[str, Any]) -> None:
        self._broadcast(job_id, {"type": "state", "job": job})

    def publish_done(self, job_id: str, job: dict[str, Any] | None) -> None:
        self._broadcast(job_id, {"type": "done", "job": job})
        # No more messages expected — discard buffer to free memory.
        self.stop_buffering(job_id)

    def emit(self, record: logging.LogRecord) -> None:
        job_id = getattr(record, "job_id", None) or _ACTIVE_JOB_ID.get()
        if not job_id:
            return

        message = {
            "type": "log",
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
            "timestamp": record.created,
        }
        self._broadcast(job_id, message)


# Singleton handler — attached to root logger
_handler: JobLogHandler | None = None


def get_log_handler() -> JobLogHandler:
    global _handler
    if _handler is None:
        _handler = JobLogHandler()
        _handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logging.getLogger().addHandler(_handler)
    return _handler
