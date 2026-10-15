"""知情同意门服务层（T-D3 · 准出⑧）。

对外暴露三项契约：
1. list_in_scopes(actor_id)   → 返回被纳入的知识范围列表（「可见」判据）
2. grant(actor_id, scopes)    → 同意一组范围（用于首次授权，MVP 默认在 config 中预置）
3. revoke(actor_id, reason)   → 一键关闭同步（「关闭生效」判据 + webhook 通知）

状态由事件流投影得出；写入侧永远 append，不修改历史（事件溯源不可篡改）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from packages.common.audit import get_audit
from packages.common.log import get_logger

from .store import EventStore, InMemoryEventStore
from .types import (
    ConsentAction,
    ConsentEvent,
    ConsentScope,
    ConsentState,
    KnowledgeScope,
    RevocationNotice,
)
from .webhook import LogOnlyNotifier, RevocationNotifier

logger = get_logger("ekos.consent.service")
audit = get_audit()

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "consent.yaml"


class ConsentConfig(BaseModel):
    """configs/consent.yaml 的形状。MVP 预置一组默认 scope 给 E0 验证用。"""

    version: int = 1
    default_scopes: list[KnowledgeScope] = Field(default_factory=list)
    webhook_targets: list[str] = Field(default_factory=list)
    default_consent_id: str = "consent-default"


def _load_config(path: Path | None = None) -> ConsentConfig:
    target = path or _CONFIG_PATH
    if not target.exists():
        logger.info("consent config not found at %s, using empty defaults", target)
        return ConsentConfig()
    with open(target, encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    return ConsentConfig(**raw)


class GrantResult(BaseModel):
    consent_id: str
    state: ConsentState
    scope_ids: list[str]
    event_id: str


class RevokeResult(BaseModel):
    consent_id: str
    state: ConsentState
    scope_ids: list[str]
    event_id: str
    webhook_ok: bool
    webhook_note: str
    downstream_notified: list[str]


class ConsentService:
    """知情同意门主服务。

    依赖注入设计（便于测试）：
    - store: 默认内存事件溯源，可换持久化适配器
    - notifier: 默认只打日志，生产可注入 HttpNotifier
    - config_path: 默认 configs/consent.yaml，测试可覆盖指向 fixture
    """

    def __init__(
        self,
        store: EventStore | None = None,
        notifier: RevocationNotifier | None = None,
        config_path: Path | None = None,
    ) -> None:
        self._config = _load_config(config_path)
        self._store: EventStore = store or InMemoryEventStore()
        self._notifier: RevocationNotifier = notifier or LogOnlyNotifier()
        # knowledge scope 注册表：scope_id -> KnowledgeScope
        self._scope_registry: dict[str, KnowledgeScope] = {
            s.scope_id: s for s in self._config.default_scopes
        }
        # 懒加载：首次调用时从 config 写一条 grant 事件（模拟「默认开启」）
        self._bootstrapped = False

    # ------------ 内部工具：bootstrap + 投影 ------------
    def _ensure_bootstrap(self) -> None:
        """首次进入时把 config 里的默认 scope 写成一条 GRANT 事件。

        这样「查看纳入范围」的接口直接走事件流投影，和真实用户授予的路径完全一致，
        不出现两套实现的分叉。
        """
        if self._bootstrapped:
            return
        default_ids = [s.scope_id for s in self._config.default_scopes]
        if default_ids:
            ev = ConsentEvent.build(
                action=ConsentAction.GRANT,
                actor_id="system-bootstrap",
                consent_id=self._config.default_consent_id,
                scope_ids=default_ids,
                payload={"source": "configs/consent.yaml", "version": self._config.version},
            )
            self._store.append(ev)
        self._bootstrapped = True

    def _consent_ids_for(self, actor_id: str) -> list[str]:
        """按 actor_id 取所有相关 consent_id（MVP 先按「actor 或系统 bootstrap 的」取）。"""
        self._ensure_bootstrap()
        seen: set[str] = set()
        out: list[str] = []
        for ev in self._store.stream():
            cond = ev.actor_id == actor_id or ev.actor_id == "system-bootstrap"
            if cond and ev.consent_id not in seen:
                seen.add(ev.consent_id)
                out.append(ev.consent_id)
        # 若系统没 bootstrap 任何 default，且 actor 无事件，兜底返回默认 consent_id
        if not out:
            out.append(self._config.default_consent_id)
        return out

    def _project(self, consent_id: str) -> ConsentScope:
        """把事件流投影成当前 consent 状态（Fold）。"""
        state = ConsentState.PENDING
        scope_ids: list[str] = []
        actor_id = "unknown"
        granted_at = None
        revoked_at = None
        last_eid = None
        for ev in self._store.stream(consent_id=consent_id):
            actor_id = ev.actor_id
            last_eid = ev.event_id
            if ev.action == ConsentAction.GRANT:
                state = ConsentState.ACTIVE
                for sid in ev.scope_ids:
                    if sid not in scope_ids:
                        scope_ids.append(sid)
                granted_at = granted_at or ev.timestamp
            elif ev.action == ConsentAction.EXTEND:
                if state == ConsentState.ACTIVE:
                    for sid in ev.scope_ids:
                        if sid not in scope_ids:
                            scope_ids.append(sid)
                else:
                    logger.warning(
                        "consent_extend_ignored state=%s consent_id=%s event=%s",
                        state.value,
                        consent_id,
                        ev.event_id,
                    )
            elif ev.action == ConsentAction.REVOKE:
                state = ConsentState.REVOKED
                revoked_at = ev.timestamp
                scope_ids = []  # 一键关闭：清空纳入范围
        active_scopes = [
            self._scope_registry[sid] for sid in scope_ids if sid in self._scope_registry
        ]
        return ConsentScope(
            consent_id=consent_id,
            actor_id=actor_id,
            state=state,
            scopes=active_scopes,
            granted_at=granted_at,
            revoked_at=revoked_at,
            last_event_id=last_eid,
        )

    def _register_scope(self, scope: KnowledgeScope) -> None:
        """允许 grant 时动态登记新 scope（MVP 用；不持久化，和 E0 内存存储一致）。"""
        self._scope_registry.setdefault(scope.scope_id, scope)

    # ------------ 对外契约 ①：查看哪些知识被纳入（可见性） ------------
    def list_in_scopes(self, actor_id: str) -> list[ConsentScope]:
        """返回 actor 所有 consent 的投影结果。

        准出⑧ 判据 1：用户可查看「哪些知识被纳入」。
        调用方只需看返回值里 state=ACTIVE 的条目即可得知当前生效范围。
        """
        out: list[ConsentScope] = []
        for cid in self._consent_ids_for(actor_id):
            out.append(self._project(cid))
        return out

    # ------------ 对外契约 ②：同意纳入（用于首次授权或扩展） ------------
    def grant(
        self,
        actor_id: str,
        scopes: list[KnowledgeScope],
        consent_id: str | None = None,
        trace_id: str | None = None,
    ) -> GrantResult:
        """同意一组知识范围被纳入。返回写入的事件元信息。"""
        if not scopes:
            raise ValueError("grant requires at least one scope")
        for s in scopes:
            self._register_scope(s)
        cid = consent_id or self._config.default_consent_id
        ev = ConsentEvent.build(
            action=ConsentAction.GRANT,
            actor_id=actor_id,
            consent_id=cid,
            scope_ids=[s.scope_id for s in scopes],
            trace_id=trace_id,
            payload={"display_names": [s.display_name for s in scopes]},
        )
        self._store.append(ev)
        audit.record_raw(
            "consent.grant",
            actor_id,
            trace_id=trace_id,
            consent_id=cid,
            scope_count=len(scopes),
        )
        proj = self._project(cid)
        return GrantResult(
            consent_id=cid,
            state=proj.state,
            scope_ids=[s.scope_id for s in proj.scopes],
            event_id=ev.event_id,
        )

    # ------------ 对外契约 ③：一键关闭同步（关闭生效 + webhook） ------------
    def revoke(
        self,
        actor_id: str,
        reason: str = "",
        consent_id: str | None = None,
        trace_id: str | None = None,
    ) -> RevokeResult:
        """一键关闭同步（撤回同意）。

        准出⑧ 判据 2：可一键关闭同步，关闭后同步停止生效。
        撤回后触发 webhook 通知下游（尽力而为）。
        """
        self._ensure_bootstrap()
        cid = consent_id or self._config.default_consent_id
        before = self._project(cid)
        affected_scope_ids = [s.scope_id for s in before.scopes]
        ev = ConsentEvent.build(
            action=ConsentAction.REVOKE,
            actor_id=actor_id,
            consent_id=cid,
            scope_ids=affected_scope_ids,
            trace_id=trace_id,
            payload={"reason": reason},
        )
        self._store.append(ev)
        audit.record_raw(
            "consent.revoke",
            actor_id,
            trace_id=trace_id,
            consent_id=cid,
            reason=reason or "unspecified",
        )

        # 下游 webhook 通知（撤回通知 = 谁 + 何时 + 哪些范围）
        notice = RevocationNotice(
            notice_id="NOT-" + uuid.uuid4().hex,
            consent_id=cid,
            actor_id=actor_id,
            revoked_at=ev.timestamp,
            scope_ids=affected_scope_ids,
            trace_id=trace_id,
            downstream_target=str(type(self._notifier).__name__),
        )
        ok, note = self._notifier.send(notice)

        proj = self._project(cid)
        return RevokeResult(
            consent_id=cid,
            state=proj.state,
            scope_ids=[s.scope_id for s in proj.scopes],
            event_id=ev.event_id,
            webhook_ok=ok,
            webhook_note=note,
            downstream_notified=(
                list(self._config.webhook_targets)
                if isinstance(self._notifier, type) or hasattr(self._notifier, "targets")
                else ["log-only"]
            ),
        )

    # ------------ 辅助：同意记录溯源 & 防篡改校验 ------------
    def list_events(
        self,
        actor_id: str | None = None,
        consent_id: str | None = None,
    ) -> list[ConsentEvent]:
        """列出事件（用于「同意记录可溯源」验证）。"""
        self._ensure_bootstrap()
        events = self._store.stream(consent_id=consent_id)
        if actor_id is not None:
            events = [e for e in events if e.actor_id in (actor_id, "system-bootstrap")]
        return events

    def verify_integrity(self) -> tuple[bool, int, str]:
        """校验事件链未被篡改（事件溯源的红线上约束）。"""
        return self._store.verify_chain()

    # ------------ 便于测试：读取内部注册表 ------------
    def known_scope_ids(self) -> list[str]:
        self._ensure_bootstrap()
        return sorted(self._scope_registry.keys())


__all__ = [
    "ConsentConfig",
    "ConsentService",
    "GrantResult",
    "RevokeResult",
]
