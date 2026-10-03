"""SSE bus with monotonic ids, a replay buffer, and overflow resync."""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, replay_limit: int = 200, queue_size: int = 32) -> None:
        self.replay_limit = replay_limit
        self.queue_size = queue_size
        self._replay: deque[dict[str, Any]] = deque(maxlen=replay_limit)
        self._subscribers: dict[int, asyncio.Queue[str]] = {}
        self._next_subscriber = 0
        self._sequence = 0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish_threadsafe(self, event: str, data: dict) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self.publish, event, data)
            return
        self.publish(event, data)

    def publish(self, event: str, data: dict) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            record = {"id": self._sequence, "event": event, "data": data}
            self._replay.append(record)
            payload = format_sse(record)
            resync = format_sse(
                {
                    "id": self._sequence,
                    "event": "resync",
                    "data": {"reason": "queue_overflow", "after": self._sequence},
                }
            )
            for queue in list(self._subscribers.values()):
                _offer(queue, payload, resync)
        return record

    def subscribe(self) -> tuple[int, asyncio.Queue[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self.queue_size)
        with self._lock:
            self._next_subscriber += 1
            ident = self._next_subscriber
            self._subscribers[ident] = queue
        return ident, queue

    def unsubscribe(self, ident: int) -> None:
        with self._lock:
            self._subscribers.pop(ident, None)

    def replay_after(self, last_id: int | None) -> tuple[list[dict[str, Any]], bool]:
        with self._lock:
            records = list(self._replay)
        if last_id is None:
            return [], False
        if not records:
            return [], True
        if last_id < records[0]["id"] - 1 and last_id not in {item["id"] for item in records}:
            if last_id < records[0]["id"]:
                return [], True
        return [item for item in records if item["id"] > last_id], False


def format_sse(record: dict[str, Any]) -> str:
    return f"id: {record['id']}\nevent: {record['event']}\ndata: {json.dumps(record['data'], default=str)}\n\n"


def _offer(queue: asyncio.Queue[str], payload: str, resync: str) -> None:
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        _drain(queue)
        try:
            queue.put_nowait(resync)
        except asyncio.QueueFull:
            return


def _drain(queue: asyncio.Queue[str]) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


bus = EventBus()
