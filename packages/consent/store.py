"""事件溯源存储（append-only，不可篡改）。

MVP 使用内存实现（E0 验证合同形状即可），切换到持久化只换适配器，
上层代码不动——与 policy 包的 in_memory / spicedb 双实现设计一致。
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from typing import Protocol

from packages.common.log import get_logger

from .types import ConsentEvent

logger = get_logger("ekos.consent.store")


class AppendError(RuntimeError):
    """试图修改或重复写入事件时抛出（事件溯源绝不允许）。"""


class EventStore(Protocol):
    """事件溯源存储的适配器契约。"""

    def append(self, event: ConsentEvent) -> None: ...
    def stream(self, consent_id: str | None = None) -> list[ConsentEvent]: ...
    def verify_chain(self) -> tuple[bool, int, str]: ...


class InMemoryEventStore:
    """纯内存 append-only 事件日志，带链式哈希校验不可篡改性。

    设计要点（红线上的约束）：
    1. ``_events`` 只能通过 ``append`` 增长，不暴露修改入口；
    2. 每次写入后将当前 event 的 hash 链入下一条，回放时可验证整条链；
    3. 写锁保证并发下顺序一致。
    """

    def __init__(
        self,
        hash_factory: Callable[[], hashlib._Hash] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._events: list[ConsentEvent] = []
        self._chain: list[str] = []  # chain[i] = hash( chain[i-1] | events[i].frozen_key() )
        self._hasher = hash_factory or hashlib.sha256
        self._seen_ids: set[str] = set()

    # -------- 写入侧：只允许追加 --------
    def append(self, event: ConsentEvent) -> None:
        with self._lock:
            if event.event_id in self._seen_ids:
                raise AppendError(f"duplicate event_id: {event.event_id}")
            prev = self._chain[-1] if self._chain else "GENESIS"
            h = self._hasher()
            h.update((prev + "|" + event.frozen_key()).encode("utf-8"))
            digest = h.hexdigest()
            self._events.append(event)
            self._chain.append(digest)
            self._seen_ids.add(event.event_id)
            logger.info(
                "consent_event_appended event_id=%s action=%s consent_id=%s chain_tip=%s",
                event.event_id,
                event.action.value,
                event.consent_id,
                digest[:12],
            )

    # -------- 读取侧：事件流回放 --------
    def stream(self, consent_id: str | None = None) -> list[ConsentEvent]:
        with self._lock:
            if consent_id is None:
                return list(self._events)
            return [e for e in self._events if e.consent_id == consent_id]

    # -------- 防篡改：验证整条链 --------
    def verify_chain(self) -> tuple[bool, int, str]:
        """返回 (是否完整, 校验事件数, 首个失败的 event_id 或 'ok')。"""
        with self._lock:
            prev = "GENESIS"
            for idx, ev in enumerate(self._events):
                h = self._hasher()
                h.update((prev + "|" + ev.frozen_key()).encode("utf-8"))
                expected = h.hexdigest()
                if expected != self._chain[idx]:
                    return False, idx, ev.event_id
                prev = expected
            return True, len(self._events), "ok"

    # -------- 便于测试的只读视图 --------
    def __len__(self) -> int:
        return len(self._events)


__all__ = ["AppendError", "EventStore", "InMemoryEventStore"]
