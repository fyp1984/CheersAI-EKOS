"""In-memory Policy adapter — E0 stub 环境使用，零外部依赖。

满足：
  - 用于 E0 开发 / 单测 / 契约测试
  - 支持层级继承（doc -> space -> org）
  - 支持非层级（协作者 collaborator、分享链接 share-viewer、通配符 user:*）
  - 不用自己写一致性：单进程内存模型天然一致
  - 权限表达力对齐 SpiceDB 子集（换后端时测试代码零改动）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .adapter import PolicyAdapter
from .types import (
    AccessAction,
    AccessCheck,
    AccessDecision,
    Actor,
    FilteredResources,
    Resource,
)

_SUBJECT_WILDCARD = "*"


@dataclass(frozen=True)
class _RelationTuple:
    resource_kind: str
    resource_id: str
    relation: str
    subject_kind: str
    subject_id: str


@dataclass
class InMemoryPolicyAdapter(PolicyAdapter):
    _relations: set[_RelationTuple] = field(default_factory=set)

    # ----------------------------------------------------------- 对外 API
    async def health(self) -> dict[str, object]:
        return {"status": "ok", "backend": "in-memory", "relations": len(self._relations)}

    async def check(self, check: AccessCheck) -> AccessDecision:
        start = time.perf_counter()
        allowed = self._is_allowed(check.actor, check.action, check.resource)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return AccessDecision(
            check=check,
            allowed=allowed,
            reason=None if allowed else "no matching relation",
            latency_ms=round(elapsed_ms, 3),
        )

    async def list_allowed(
        self,
        actor: Actor,
        action: AccessAction,
        resource_kind: str,
        *,
        trace_id: str | None = None,
    ) -> FilteredResources:
        resource_ids = {r.resource_id for r in self._relations if r.resource_kind == resource_kind}
        allowed_ids: list[str] = []
        for rid in sorted(resource_ids):
            res = Resource(kind=resource_kind, id=rid, tenant_id=actor.tenant_id)
            if self._is_allowed(actor, action, res):
                allowed_ids.append(rid)
        return FilteredResources(
            actor=actor,
            action=action,
            resource_kind=resource_kind,
            allowed_ids=allowed_ids,
            trace_id=trace_id,
        )

    async def add_relation(
        self,
        resource: Resource,
        relation: str,
        subject: Actor | Resource,
        *,
        trace_id: str | None = None,
    ) -> None:
        subj_kind, subj_id = (
            (subject.type, subject.id) if isinstance(subject, Actor) else (subject.kind, subject.id)
        )
        self._relations.add(
            _RelationTuple(resource.kind, resource.id, relation, subj_kind, subj_id)
        )

    async def remove_relation(
        self,
        resource: Resource,
        relation: str,
        subject: Actor | Resource,
        *,
        trace_id: str | None = None,
    ) -> None:
        subj_kind, subj_id = (
            (subject.type, subject.id) if isinstance(subject, Actor) else (subject.kind, subject.id)
        )
        self._relations.discard(
            _RelationTuple(resource.kind, resource.id, relation, subj_kind, subj_id)
        )

    # ----------------------------------------------------------- 权限表达
    # role -> action 的蕴含关系（对齐 SpiceDB schema）：
    #   viewer / collaborator / share-viewer -> view, retrieve
    #   editor / share-owner                  -> + edit, share
    #   admin                                 -> + admin
    _ACTION_ROLES: dict[AccessAction, tuple[str, ...]] = field(
        default_factory=lambda: {
            "view": (
                "viewer",
                "editor",
                "admin",
                "collaborator",
                "share-viewer",
                "share-owner",
            ),
            "retrieve": (
                "viewer",
                "editor",
                "admin",
                "collaborator",
                "share-viewer",
                "share-owner",
            ),
            "edit": ("editor", "admin", "share-owner"),
            "admin": ("admin",),
            "share": ("editor", "admin", "share-owner"),
        }
    )

    def _role_implies(self, role: str, action: AccessAction) -> bool:
        return role in self._ACTION_ROLES.get(action, ())

    def _is_allowed(
        self,
        actor: Actor,
        action: AccessAction,
        resource: Resource,
        *,
        _stack: tuple[str, ...] = (),
    ) -> bool:
        key = f"{resource.kind}:{resource.id}"
        if key in _stack:
            return False
        stack = (*_stack, key)

        for rel in self._relations:
            if rel.resource_kind != resource.kind or rel.resource_id != resource.id:
                continue

            # 1. 通配符分享链接（任何 user 都可看）
            if (
                rel.subject_kind == actor.type
                and rel.subject_id == _SUBJECT_WILDCARD
                and self._role_implies(rel.relation, action)
            ):
                return True
            # 2. 直接赋权给 actor
            if (
                rel.subject_kind == actor.type
                and rel.subject_id == actor.id
                and self._role_implies(rel.relation, action)
            ):
                return True
            # 3. group 成员（递归：group 还可以嵌套 group）
            if (
                rel.subject_kind == "group"
                and self._actor_in_group(actor, rel.subject_id, stack)
                and self._role_implies(rel.relation, action)
            ):
                return True
            # 4. 层级继承（resource#parent@parent_resource）
            if rel.relation == "parent":
                parent = Resource(
                    kind=rel.subject_kind,
                    id=rel.subject_id,
                    tenant_id=resource.tenant_id,
                )
                if self._is_allowed(actor, action, parent, _stack=stack):
                    return True
        return False

    def _actor_in_group(self, actor: Actor, group_id: str, stack: tuple[str, ...]) -> bool:
        key = f"group:{group_id}"
        if key in stack:
            return False
        stack = (*stack, key)
        for rel in self._relations:
            if rel.resource_kind != "group" or rel.resource_id != group_id:
                continue
            if rel.relation not in {"member", "viewer", "editor", "admin"}:
                continue
            if rel.subject_kind == actor.type and rel.subject_id == actor.id:
                return True
            if rel.subject_kind == "group" and self._actor_in_group(actor, rel.subject_id, stack):
                return True
        return False


__all__ = ["InMemoryPolicyAdapter"]
