"""Vault masking adapter -- CheersAI Vault integration.

This module is an **adapter**, not a masking engine.

Masking is performed by the CheersAI Vault product
(``subproducts/CheersAI-Vault``) over HTTP. Per 60.90.03 §4 our scope is the
adapter, call orchestration, error handling and tests -- we do not build a
masking engine of our own, and the EKOS product plan names Vault as the
security differentiator kernel (F26).

Contract shape (X1) is unchanged: detect / mask / restore / health.

Red line, and it is stronger than the previous local-table design: the mapping
table never leaves Vault. ``/mask`` returns only a ``mapping_ref`` (the Vault
``artifact_id``); the reverse operation is served by Vault's server-side
restore. EKOS never holds plaintext mapping data at all.

Two consequences worth knowing before reading further:

* Vault is **file-batch and asynchronous**; the contract is text-synchronous.
  The adapter bridges that by staging the text through a temporary file.
  E0 measured a batch round-trip at well under one second.
* Vault has **no standalone detect endpoint**, so ``detect`` is derived from a
  mask round-trip. Its ``start``/``end`` offsets are therefore positions in
  the *masked* text, not in the original -- see ``detect``.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from .client import VaultApiError, VaultClient, VaultUnavailableError

_POLICY_PATH = Path(__file__).resolve().parent / "masking_policy.json"

# Vault emits placeholders shaped `***IDCARD***1` for built-in rules and
# `[category]` for sensitive-term hits.
_TOKEN_RE = re.compile(r"\*\*\*([A-Z][A-Z0-9_]*)\*\*\*\d+")
_CATEGORY_RE = re.compile(r"\[([^\[\]]+)\]")


class Entity(BaseModel):
    type: str
    start: int
    end: int
    score: float = 1.0
    rule_version: str = "1.0.0"


class DetectResult(BaseModel):
    request_id: str
    entities: list[Entity]
    policy_id: str
    engine_version: str = "1.0.0"


class MaskResult(BaseModel):
    request_id: str
    masked_content: str
    mapping_ref: str
    entity_count: int


class RestoreResult(BaseModel):
    request_id: str
    restored_content: str
    audit_id: str


class HealthResult(BaseModel):
    status: str = "ok"
    engine_version: str = "1.0.0"
    rule_version: str = "1.0.0"
    dependencies: dict[str, str] = {}


class MaskingClient(Protocol):
    """The slice of Vault's API the adapter needs. Keeps tests service-free."""

    def health(self) -> dict[str, Any]: ...
    def submit(
        self, files: list[tuple[str, bytes]], rule_ids: list[str]
    ) -> tuple[str, list[Any]]: ...
    def wait(self, batch_id: str) -> Any: ...
    def download(self, artifact_id: str) -> str: ...
    def restore(self, artifact_id: str) -> tuple[str, int]: ...


def _load_rules(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or _POLICY_PATH
    with open(target, encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    rules: list[dict[str, Any]] = data.get("rules", [])
    return rules


def _token_map(rules: list[dict[str, Any]]) -> dict[str, str]:
    """Vault placeholder name -> EKOS entity (e.g. ``IDCARD`` -> ``ID_CARD``)."""
    return {str(rule["token"]).upper(): str(rule["entity"]) for rule in rules if rule.get("token")}


def _category_map(rules: list[dict[str, Any]]) -> dict[str, str]:
    """Sensitive-term category -> EKOS entity (e.g. ``人名`` -> ``PERSON_NAME``)."""
    return {str(rule["category"]): str(rule["entity"]) for rule in rules if rule.get("category")}


def _parse_tokens(
    masked: str,
    token_map: dict[str, str],
    category_map: dict[str, str],
) -> list[Entity]:
    """Recover entity positions from Vault placeholders.

    Offsets are positions in the masked text: that is the only coordinate
    Vault exposes. Callers needing original-text spans must re-locate them.
    """
    entities: list[Entity] = []
    for match in _TOKEN_RE.finditer(masked):
        entity = token_map.get(match.group(1))
        if entity:
            entities.append(Entity(type=entity, start=match.start(), end=match.end()))
    for match in _CATEGORY_RE.finditer(masked):
        entity = category_map.get(match.group(1))
        if entity:
            entities.append(Entity(type=entity, start=match.start(), end=match.end()))
    entities.sort(key=lambda e: e.start)
    return entities


class VaultService:
    """X1 contract implementation backed by CheersAI Vault."""

    def __init__(
        self,
        client: MaskingClient | None = None,
        *,
        policy_path: Path | None = None,
    ) -> None:
        # Lazy on purpose: building a VaultService must not require a
        # configured -- let alone reachable -- Vault. api.py constructs one at
        # import time, and TR-7 turns an unconfigured address into a hard
        # error, so resolving the client eagerly would break the import.
        self._injected: MaskingClient | None = client
        self._rules = _load_rules(policy_path)
        self._tokens = _token_map(self._rules)
        self._categories = _category_map(self._rules)

    @property
    def client(self) -> MaskingClient:
        """The Vault transport, resolved on first use."""
        if self._injected is None:
            self._injected = VaultClient()
        return self._injected

    # -- policy ---------------------------------------------------------

    def rule_ids(self) -> list[str]:
        """Vault rule ids to request for a mask call."""
        return [str(rule["vault_rule_id"]) for rule in self._rules if rule.get("vault_rule_id")]

    def gaps(self) -> list[dict[str, Any]]:
        """Rules Vault cannot serve, surfaced rather than silently skipped."""
        return [rule for rule in self._rules if rule.get("status") != "active"]

    # -- contract operations --------------------------------------------

    def detect(self, request_id: str, content: str, policy_id: str) -> DetectResult:
        """Detect entities. Derived from ``mask`` -- Vault has no detect call.

        Offsets are positions in the *masked* text, because that is what
        Vault returns. Do not feed them back into the original string.
        """
        masked = self.mask(request_id, "detect-" + request_id, content, policy_id)
        entities = _parse_tokens(masked.masked_content, self._tokens, self._categories)
        return DetectResult(request_id=request_id, entities=entities, policy_id=policy_id)

    def mask(
        self,
        request_id: str,
        mapping_session_id: str,
        content: str,
        policy_id: str,
    ) -> MaskResult:
        """Mask one text through Vault's batch API.

        The text is staged as a temporary file because Vault accepts files,
        not strings. The temp directory is removed before we return.
        """
        rule_ids = self.rule_ids()
        if not rule_ids:
            raise VaultApiError(
                code="NO_ACTIVE_RULES",
                message="masking policy declares no usable Vault rule ids",
                retryable=False,
                status=500,
            )
        name = f"{mapping_session_id}.txt"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / name
            path.write_text(content, encoding="utf-8")
            payload = path.read_bytes()
        _batch_id, _submitted = self.client.submit([(name, payload)], rule_ids)

        status = self.client.wait(_batch_id)
        if not status.files:
            raise VaultApiError(
                code="EMPTY_BATCH",
                message="vault returned a batch with no files",
                retryable=False,
                status=502,
            )
        file_status = status.files[0]
        if file_status.status != "Completed" or file_status.artifact_id is None:
            raise VaultApiError(
                code=file_status.error_code or "MASKING_FAILED",
                message=file_status.error_message or "vault did not produce an artifact",
                retryable=False,
                status=502,
            )
        masked = self.client.download(file_status.artifact_id)
        return MaskResult(
            request_id=request_id,
            masked_content=masked,
            # Red line: a reference only. The mapping never leaves Vault.
            mapping_ref="vault://artifact/" + file_status.artifact_id,
            entity_count=file_status.masked_entity_count or 0,
        )

    def restore(
        self,
        request_id: str,
        mapping_ref: str,
        policy_id: str,
        trace_id: str,
    ) -> RestoreResult:
        """Reverse-mask via Vault's server-side restore.

        Restoration depends on a Vault endpoint that is implemented but sits
        outside the documented first-edition promise (gap 5). Verified working
        on E0; treat as a dependency to re-confirm before E1 sign-off.
        """
        artifact_id = mapping_ref.rsplit("artifact/", 1)[-1]
        restored, _count = self.client.restore(artifact_id)
        return RestoreResult(
            request_id=request_id,
            restored_content=restored,
            audit_id="AUD-" + request_id,
        )

    def health(self) -> HealthResult:
        """Report Vault reachability plus every rule Vault cannot serve.

        A rule Vault cannot serve is reported rather than dropped: a gap that
        leaves one entity type in the clear while the service answers healthy
        is a red-line failure, not a cosmetic one.
        """
        dependencies: dict[str, str] = {}
        status = "ok"
        try:
            info = self.client.health()
            dependencies["cheersai_vault"] = "ok:" + str(info.get("version", "unknown"))
        # VaultUnavailableError must be caught too: an unreachable Vault once
        # escaped as an unhandled exception, so /health crashed instead of
        # reporting `degraded` -- the opposite of a silent pass, but just as
        # useless to the caller watching it.
        except (VaultApiError, VaultUnavailableError, OSError) as exc:
            dependencies["cheersai_vault"] = "unavailable:" + type(exc).__name__
            status = "degraded"

        for gap in self.gaps():
            entity = str(gap.get("entity"))
            gap_status = str(gap.get("status"))
            dependencies["rule:" + entity] = gap_status
            if gap_status == "missing":
                status = "degraded"
        return HealthResult(status=status, dependencies=dependencies)
