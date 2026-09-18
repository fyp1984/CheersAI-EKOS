"""HTTP surface for the retrieval layer (X3 contract)."""

from __future__ import annotations

from fastapi import APIRouter

from .service import RetrievalService, RetrieveRequest, RetrieveResult

router = APIRouter(prefix="/retrieval", tags=["retrieval"])
_service = RetrievalService()


@router.post("/retrieve")
def retrieve(req: RetrieveRequest) -> RetrieveResult:
    return _service.retrieve(req)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "retrieval"}
