"""EKOS shared-kernel API gateway (skeleton).

Mounts the three contract services. Run with:

    uvicorn apps.api.main:app --port ${API_HOST:-18000}

No port is hardcoded in code (TR-7); the port is supplied on the command line
or via the API_HOST environment variable.
"""

from __future__ import annotations

from fastapi import FastAPI
from packages.common.log import get_logger
from packages.document.api import router as document_router
from packages.retrieval.api import router as retrieval_router
from packages.vault.api import router as vault_router

logger = get_logger("gateway")
app = FastAPI(title="EKOS Shared Kernel (skeleton)", version="0.1.0")
app.include_router(vault_router)
app.include_router(retrieval_router)
app.include_router(document_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "ekos-core", "status": "ok"}
