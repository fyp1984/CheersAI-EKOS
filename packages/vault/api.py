"""HTTP surface for the Vault masking layer (X1 contract)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .service import DetectResult, HealthResult, MaskResult, RestoreResult, VaultService

router = APIRouter(tags=["vault"])
_service = VaultService()


class DetectBody(BaseModel):
    request_id: str
    tenant_id: str
    policy_id: str
    content_type: str
    content: str
    trace_id: str


class MaskBody(BaseModel):
    request_id: str
    mapping_session_id: str
    content: str
    content_type: str
    policy_id: str


class RestoreBody(BaseModel):
    request_id: str
    mapping_ref: str
    policy_id: str
    trace_id: str


@router.post("/detect")
def detect(body: DetectBody) -> DetectResult:
    return _service.detect(body.request_id, body.content, body.policy_id)


@router.post("/mask")
def mask(body: MaskBody) -> MaskResult:
    return _service.mask(body.request_id, body.mapping_session_id, body.content, body.policy_id)


@router.post("/restore")
def restore(body: RestoreBody) -> RestoreResult:
    return _service.restore(body.request_id, body.mapping_ref, body.policy_id, body.trace_id)


@router.get("/health")
def health() -> HealthResult:
    return _service.health()
