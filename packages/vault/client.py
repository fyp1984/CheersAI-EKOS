"""CheersAI Vault Runtime API client -- transport only.

No masking logic lives here, and none belongs here. Masking is a capability of
the CheersAI Vault product (``subproducts/CheersAI-Vault``). Per 60.90.03 §4
our scope is the adapter, orchestration, error handling and tests -- we do not
build a masking engine of our own.

This client talks to the four first-edition APIs documented in
``CheersAI-Vault/docs/enterprise/API_REFERENCE.md``:

    POST /api/v1/batches                 submit one or more files
    GET  /api/v1/batches/{id}            poll progress and failures
    GET  /api/v1/artifacts/{id}          download masked Markdown
    GET  /api/v1/health                  readiness

Reverse masking uses ``POST /api/v1/artifacts/{id}/restore``. It works (E0
verified) but sits outside Vault's documented first-edition promise -- tracked
as gap (5) in ``记录/Vault集成方案-260905.md``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

# TR-7: no default host:port is defined here on purpose. A hardcoded address
# only fails at environment switch-over, and it fails as "connection refused"
# rather than "the port was hardcoded" -- sending the investigation the wrong
# way from the start. E0 and E1 must resolve Vault through configuration.
BASE_URL_ENV = "VAULT_API_BASE_URL"

# Vault reaches one of these before results are readable (API_REFERENCE §3).
TERMINAL_BATCH_STATES = frozenset({"Completed", "CompletedWithErrors", "Failed"})


class VaultApiError(RuntimeError):
    """Vault answered with an error body (already sanitised on Vault's side)."""

    def __init__(self, code: str, message: str, retryable: bool, status: int) -> None:
        super().__init__(f"HTTP {status} {code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status


class VaultUnavailableError(RuntimeError):
    """Vault is unreachable or did not finish in time.

    60.90.03 §651: when the masking API times out the cloud call must not go
    out -- fall back to the local model or stop. Callers must treat this as a
    hard stop for the cloud path, not as a soft warning.
    """


@dataclass(frozen=True)
class SubmittedFile:
    file_id: str
    display_name: str


@dataclass(frozen=True)
class FileStatus:
    file_id: str
    display_name: str
    status: str
    masked_entity_count: int | None
    artifact_id: str | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class BatchStatus:
    batch_id: str
    status: str
    masked_entity_count: int
    files: list[FileStatus]


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class VaultClient:
    """HTTP transport for the Vault Runtime API."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_ms: int | None = None,
        poll_interval_ms: int | None = None,
        poll_max_attempts: int | None = None,
    ) -> None:
        resolved = base_url or os.getenv(BASE_URL_ENV)
        if resolved is None or not resolved.strip():
            raise ValueError(
                f"{BASE_URL_ENV} is required (e.g. http://vault.internal:8787/api/v1). "
                "The Vault address is injected per environment, never hardcoded (TR-7)."
            )
        self._base_url = resolved.strip().rstrip("/")
        self._timeout_ms = (
            timeout_ms if timeout_ms is not None else _env_int("VAULT_TIMEOUT_MS", 10_000)
        )
        # API_REFERENCE §3 asks for a polling interval of at least 1s.
        self._poll_interval_ms = (
            poll_interval_ms
            if poll_interval_ms is not None
            else _env_int("VAULT_POLL_INTERVAL_MS", 1_000)
        )
        self._poll_max_attempts = (
            poll_max_attempts
            if poll_max_attempts is not None
            else _env_int("VAULT_POLL_MAX_ATTEMPTS", 30)
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def _timeout_s(self) -> float:
        return self._timeout_ms / 1000

    def _raise_for(self, response: httpx.Response) -> None:
        try:
            payload: dict[str, Any] = response.json()
        except ValueError:
            payload = {}
        raise VaultApiError(
            code=str(payload.get("code", "UNKNOWN")),
            message=str(payload.get("message", response.text or "no body")),
            retryable=bool(payload.get("retryable", False)),
            status=response.status_code,
        )

    def health(self) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self._base_url}/health", timeout=self._timeout_s())
        except httpx.HTTPError as exc:
            raise VaultUnavailableError(f"vault health unreachable: {exc}") from exc
        if response.status_code != 200:
            self._raise_for(response)
        payload: dict[str, Any] = response.json()
        return payload

    def submit(
        self, files: list[tuple[str, bytes]], rule_ids: list[str]
    ) -> tuple[str, list[SubmittedFile]]:
        """Submit a masking batch. Returns (batch_id, submitted files)."""
        if not files:
            raise ValueError("at least one file is required")
        if not rule_ids:
            raise ValueError("at least one rule id is required")
        parts: list[tuple[str, tuple[str, bytes]]] = [
            ("files", (name, data)) for name, data in files
        ]
        try:
            response = httpx.post(
                f"{self._base_url}/batches",
                files=parts,
                data={"rule_ids": json.dumps(rule_ids, ensure_ascii=False)},
                timeout=self._timeout_s(),
            )
        except httpx.HTTPError as exc:
            raise VaultUnavailableError(f"vault submit unreachable: {exc}") from exc
        if response.status_code not in (200, 202):
            self._raise_for(response)
        payload: dict[str, Any] = response.json()
        submitted = [
            SubmittedFile(
                file_id=str(item["file_id"]),
                display_name=str(item.get("display_name", "")),
            )
            for item in payload.get("files", [])
        ]
        return str(payload["batch_id"]), submitted

    def batch_status(self, batch_id: str) -> BatchStatus:
        try:
            response = httpx.get(f"{self._base_url}/batches/{batch_id}", timeout=self._timeout_s())
        except httpx.HTTPError as exc:
            raise VaultUnavailableError(f"vault batch status unreachable: {exc}") from exc
        if response.status_code != 200:
            self._raise_for(response)
        payload: dict[str, Any] = response.json()
        batch: dict[str, Any] = payload.get("batch", {})
        files = [
            FileStatus(
                file_id=str(item.get("file_id", "")),
                display_name=str(item.get("display_name", "")),
                status=str(item.get("status", "")),
                masked_entity_count=(
                    int(item["masked_entity_count"])
                    if item.get("masked_entity_count") is not None
                    else None
                ),
                artifact_id=(
                    str(item["artifact_id"]) if item.get("artifact_id") is not None else None
                ),
                error_code=(
                    str(item["error_code"]) if item.get("error_code") is not None else None
                ),
                error_message=(
                    str(item["error_message"]) if item.get("error_message") is not None else None
                ),
            )
            for item in payload.get("files", [])
        ]
        return BatchStatus(
            batch_id=str(batch.get("batch_id", batch_id)),
            status=str(batch.get("status", "")),
            masked_entity_count=int(batch.get("masked_entity_count", 0) or 0),
            files=files,
        )

    def wait(self, batch_id: str) -> BatchStatus:
        """Poll until the batch reaches a terminal state."""
        for _ in range(self._poll_max_attempts):
            status = self.batch_status(batch_id)
            if status.status in TERMINAL_BATCH_STATES:
                return status
            time.sleep(self._poll_interval_ms / 1000)
        raise VaultUnavailableError(f"batch {batch_id} did not reach a terminal state in time")

    def download(self, artifact_id: str) -> str:
        """Download one masked artifact (Markdown text)."""
        try:
            response = httpx.get(
                f"{self._base_url}/artifacts/{artifact_id}", timeout=self._timeout_s()
            )
        except httpx.HTTPError as exc:
            raise VaultUnavailableError(f"vault artifact unreachable: {exc}") from exc
        if response.status_code != 200:
            self._raise_for(response)
        return response.text

    def restore(self, artifact_id: str) -> tuple[str, int]:
        """Reverse-mask one artifact. Returns (restored_text, entity_count)."""
        try:
            response = httpx.post(
                f"{self._base_url}/artifacts/{artifact_id}/restore",
                timeout=self._timeout_s(),
            )
        except httpx.HTTPError as exc:
            raise VaultUnavailableError(f"vault restore unreachable: {exc}") from exc
        if response.status_code != 200:
            self._raise_for(response)
        raw_count = response.headers.get("x-restored-entity-count", "0")
        try:
            count = int(raw_count)
        except ValueError:
            count = 0
        return response.text, count
