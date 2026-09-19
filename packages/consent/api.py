"""知情同意门 HTTP 表面（准出⑧ 三项契约对外暴露）。

挂载路径：
- GET  /consent/scopes        → 查看纳入范围（可见性）
- POST /consent/grant         → 同意纳入（服务端/测试用）
- POST /consent/revoke        → 一键关闭同步（关闭生效 + webhook 通知）
- GET  /consent/events        → 同意记录溯源（事件溯源可查）
- GET  /consent/integrity     → 防篡改校验（返回链验证结果）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .service import ConsentService, GrantResult, RevokeResult
from .types import ConsentEvent, ConsentScope, KnowledgeScope

router = APIRouter(tags=["consent"])
_service = ConsentService()


class GrantBody(BaseModel):
    actor_id: str
    scopes: list[KnowledgeScope]
    consent_id: str | None = None
    trace_id: str | None = None


class RevokeBody(BaseModel):
    actor_id: str
    reason: str = ""
    consent_id: str | None = None
    trace_id: str | None = None


class ScopesResponse(BaseModel):
    actor_id: str
    active_count: int
    active_scopes: list[KnowledgeScope]
    all_consents: list[ConsentScope]


class EventsResponse(BaseModel):
    actor_id: str | None
    consent_id: str | None
    count: int
    events: list[ConsentEvent]


class IntegrityResponse(BaseModel):
    intact: bool
    events_checked: int
    detail: str


@router.get("/consent/scopes", response_model=ScopesResponse)
def list_scopes(actor_id: str = Query(..., description="查看范围的主体标识")) -> ScopesResponse:
    """查看「哪些知识被纳入」——准出⑧ 判据 1（可见性）。"""
    all_consents = _service.list_in_scopes(actor_id)
    active_scopes: list[KnowledgeScope] = []
    for c in all_consents:
        if c.state.value == "active":
            active_scopes.extend(c.scopes)
    return ScopesResponse(
        actor_id=actor_id,
        active_count=len(active_scopes),
        active_scopes=active_scopes,
        all_consents=all_consents,
    )


@router.post("/consent/grant", response_model=GrantResult)
def grant(body: GrantBody) -> GrantResult:
    """同意将一组知识范围纳入（服务端或测试用；MVP 默认 scope 已在 config 中预置）。"""
    try:
        return _service.grant(
            body.actor_id,
            body.scopes,
            consent_id=body.consent_id,
            trace_id=body.trace_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/consent/revoke", response_model=RevokeResult)
def revoke(body: RevokeBody) -> RevokeResult:
    """一键关闭同步——准出⑧ 判据 2（关闭生效）+ 下游 webhook 撤回通知。"""
    return _service.revoke(
        body.actor_id,
        reason=body.reason,
        consent_id=body.consent_id,
        trace_id=body.trace_id,
    )


@router.get("/consent/events", response_model=EventsResponse)
def list_events(
    actor_id: str | None = Query(None),
    consent_id: str | None = Query(None),
) -> EventsResponse:
    """列出同意/撤回事件——用于「同意记录可溯源」验证。"""
    events = _service.list_events(actor_id=actor_id, consent_id=consent_id)
    return EventsResponse(
        actor_id=actor_id,
        consent_id=consent_id,
        count=len(events),
        events=events,
    )


@router.get("/consent/integrity", response_model=IntegrityResponse)
def integrity() -> dict[str, Any]:
    """校验事件溯源链的不可篡改性——红线上的防篡改验证。"""
    intact, checked, detail = _service.verify_integrity()
    return {
        "intact": intact,
        "events_checked": checked,
        "detail": detail,
    }
