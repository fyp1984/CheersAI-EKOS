"""SpiceDB adapter — production first-choice backend (PRD T-D1, §4.7).

Zanzibar 模型天然满足：
  - 层级继承、组继承、非层级（协作者/分享链接）全部能表达
  - 一致性由 SpiceDB 保证（fully_consistent + zedtoken）
  - 不用自己写遍历或一致性检查（T-D1 核心判据）

当前版本：
  - E0 可用：factory 会在 SpiceDB 不可达时回退到 InMemory
  - HTTP 最小实现（authzed HTTP API）：Read / Write / Check
  - E1 升级：换 authzed-py gRPC 版，接入 LookupResources 原生 RPC
"""

from __future__ import annotations

import time

import httpx

from .adapter import PolicyAdapter
from .types import (
    AccessAction,
    AccessCheck,
    AccessDecision,
    Actor,
    FilteredResources,
    Resource,
)

V1_CHECK = "/v1/permissions/check"
V1_WRITE = "/v1/relationships/write"


class SpiceDBAdapter(PolicyAdapter):
    """HTTP 版 SpiceDB 客户端（最小可行实现）。"""

    def __init__(
        self,
        *,
        endpoint: str,
        token: str,
        scheme: str = "https",
        timeout: float = 5.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._scheme = scheme
        self._timeout = timeout
        self._client_httpx: httpx.AsyncClient | None = None

    # ----------------------------------------------------------- 内部
    def _client(self) -> httpx.AsyncClient:
        if self._client_httpx is None:
            headers: dict[str, str] = {}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client_httpx = httpx.AsyncClient(
                base_url=f"{self._scheme}://{self._endpoint}",
                headers=headers,
                timeout=self._timeout,
            )
        assert self._client_httpx is not None
        return self._client_httpx

    @staticmethod
    def _objref(kind: str, obj_id: str) -> dict[str, str]:
        return {"objectType": kind, "objectId": obj_id}

    @staticmethod
    def _subjref(subject: Actor | Resource) -> dict[str, object]:
        kind = subject.type if isinstance(subject, Actor) else subject.kind
        sid = subject.id
        return {"object": SpiceDBAdapter._objref(kind, sid)}

    # ----------------------------------------------------------- 对外 API
    async def health(self) -> dict[str, object]:
        try:
            client = self._client()
            resp = await client.get("/", timeout=1.0)
            return {"status": "ok", "backend": "spicedb", "http": resp.status_code}
        except Exception:  # noqa: BLE001
            return {"status": "down", "backend": "spicedb"}

    async def check(self, check: AccessCheck) -> AccessDecision:
        start = time.perf_counter()
        payload = {
            "consistency": {"fullyConsistent": True},
            "resource": self._objref(check.resource.kind, check.resource.id),
            "permission": self._action_to_perm(check.action),
            "subject": self._subjref(check.actor),
        }
        allowed = False
        reason: str | None = "backend unreachable"
        try:
            resp = await self._client().post(V1_CHECK, json=payload)
            data = resp.json()
            allowed = bool(data.get("permissionship") == "HAS_PERMISSION")
            reason = None if allowed else "spicedb DENY"
        except Exception as exc:  # noqa: BLE001
            reason = f"spicedb unreachable: {type(exc).__name__}"
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return AccessDecision(
            check=check,
            allowed=allowed,
            reason=reason,
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
        # HTTP 版没有原生 LookupResources。E1 接入 authzed-py gRPC 时重写此方法。
        # 这里返回空列表：调用方会 fallback 到逐条 check 的双层过滤。
        return FilteredResources(
            actor=actor,
            action=action,
            resource_kind=resource_kind,
            allowed_ids=[],
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
        payload = {
            "updates": [
                {
                    "operation": "TOUCH",
                    "relationship": {
                        "resource": self._objref(resource.kind, resource.id),
                        "relation": relation,
                        "subject": self._subjref(subject),
                    },
                }
            ]
        }
        await self._client().post(V1_WRITE, json=payload)

    async def remove_relation(
        self,
        resource: Resource,
        relation: str,
        subject: Actor | Resource,
        *,
        trace_id: str | None = None,
    ) -> None:
        payload = {
            "updates": [
                {
                    "operation": "DELETE",
                    "relationship": {
                        "resource": self._objref(resource.kind, resource.id),
                        "relation": relation,
                        "subject": self._subjref(subject),
                    },
                }
            ]
        }
        await self._client().post(V1_WRITE, json=payload)

    # ----------------------------------------------------------- Mapper
    @staticmethod
    def _action_to_perm(action: AccessAction) -> str:
        mapping: dict[AccessAction, str] = {
            "view": "viewer",
            "retrieve": "retrieve",
            "edit": "editor",
            "admin": "admin",
            "share": "share",
        }
        return mapping.get(action, action)


__all__ = ["SpiceDBAdapter"]
