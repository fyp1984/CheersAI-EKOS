"""HTTP surface for the parsing layer (X2 contract)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .parser import parse
from .schema import ParsedDocument

router = APIRouter(prefix="/document", tags=["document"])


class ParseRequest(BaseModel):
    request_id: str
    source_uri: str
    content_type: str
    policy_id: str
    trace_id: str


class ParseResponse(BaseModel):
    """X2.4 response envelope.

    `request_id` is echoed back so a caller can pair a reply with its request.
    Without it the trace chain breaks for any concurrent or retrying client --
    the response carries no other field that identifies which request produced it.
    """

    request_id: str
    document: ParsedDocument


@router.post("/parse")
def parse_endpoint(req: ParseRequest) -> ParseResponse:
    return ParseResponse(
        request_id=req.request_id,
        document=parse(req.source_uri, req.content_type, req.policy_id, req.trace_id),
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "document"}
