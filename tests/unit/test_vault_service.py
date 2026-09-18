"""L1 unit test: Vault masking adapter (X1 contract) backed by CheersAI Vault.

Synthetic input only. The masking itself is performed by the CheersAI Vault
product; this suite exercises the **adapter** -- contract shape, rule mapping,
failure behaviour and red lines -- against a test double that reproduces the
response shapes Vault actually returns (E0 verified).

Two red lines are asserted here because a silent failure in either direction
leaks data:

* If Vault fails, ``mask`` must raise -- never fall back to returning the
  original text, which would send it straight to the cloud model.
* ``/health`` must report every rule Vault cannot serve rather than answering
  "ok" while one entity type passes through in the clear.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from packages.vault.client import (
    BatchStatus,
    FileStatus,
    SubmittedFile,
    VaultApiError,
    VaultUnavailableError,
)
from packages.vault.service import VaultService

# Synthetic, format-valid but obviously fake identifiers.
SYNTHETIC = "id 11010119900307001X phone 13800138000 card 6222021234567890123"

# Placeholder shapes Vault emits. The test double reproduces them; it is a
# stub, not a masking engine.
_STUB_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("IDCARD", re.compile(r"\b\d{17}[\dXx]\b")),
    ("PHONE", re.compile(r"\b1[3-9]\d{9}\b")),
    ("BANKCARD", re.compile(r"\b\d{16,19}\b")),
]


def _stub_mask(text: str) -> tuple[str, int]:
    counter = 0
    out = text
    for token, pattern in _STUB_PATTERNS:

        def repl(_m: re.Match[str], _token: str = token) -> str:
            nonlocal counter
            counter += 1
            return f"***{_token}***{counter}"

        out = pattern.sub(repl, out)
    return out, counter


@dataclass
class FakeVaultClient:
    """Stand-in for Vault Runtime API. Unit tests never need a live service."""

    fail_batch: bool = False
    unreachable: bool = False

    def __post_init__(self) -> None:
        self._artifacts: dict[str, str] = {}
        self._originals: dict[str, str] = {}
        self._batches: dict[str, list[str]] = {}
        self.rule_ids_seen: list[list[str]] = []

    def health(self) -> dict[str, Any]:
        if self.unreachable:
            raise VaultUnavailableError("stub: unreachable")
        return {"status": "ready", "version": "0.1.0-stub"}

    def submit(
        self, files: list[tuple[str, bytes]], rule_ids: list[str]
    ) -> tuple[str, list[SubmittedFile]]:
        self.rule_ids_seen.append(list(rule_ids))
        batch_id = f"batch-{len(self._batches) + 1}"
        submitted: list[SubmittedFile] = []
        artifact_ids: list[str] = []
        for name, data in files:
            original = data.decode("utf-8")
            masked, _count = _stub_mask(original)
            artifact_id = f"artifact-{len(self._artifacts) + 1}"
            self._artifacts[artifact_id] = masked
            self._originals[artifact_id] = original
            artifact_ids.append(artifact_id)
            submitted.append(SubmittedFile(file_id=artifact_id, display_name=name))
        self._batches[batch_id] = artifact_ids
        return batch_id, submitted

    def wait(self, batch_id: str) -> BatchStatus:
        ids = self._batches[batch_id]
        if self.fail_batch:
            files = [
                FileStatus(
                    file_id=aid,
                    display_name="x.txt",
                    status="Failed",
                    masked_entity_count=None,
                    artifact_id=None,
                    error_code="MASKING_FAILED",
                    error_message="stub: masking failed",
                )
                for aid in ids
            ]
            return BatchStatus(batch_id, "Failed", 0, files)
        files = [
            FileStatus(
                file_id=aid,
                display_name="x.txt",
                status="Completed",
                masked_entity_count=3,
                artifact_id=aid,
                error_code=None,
                error_message=None,
            )
            for aid in ids
        ]
        return BatchStatus(batch_id, "Completed", 3, files)

    def download(self, artifact_id: str) -> str:
        return self._artifacts[artifact_id]

    def restore(self, artifact_id: str) -> tuple[str, int]:
        return self._originals[artifact_id], 3


@pytest.mark.unit()
def test_mask_returns_ref_not_plaintext() -> None:
    """Red line: only a reference comes back. The mapping never leaves Vault."""
    client = FakeVaultClient()
    svc = VaultService(client)
    out = svc.mask("REQ-1", "MS-1", SYNTHETIC, "default")
    assert out.mapping_ref.startswith("vault://artifact/")
    assert SYNTHETIC not in out.masked_content
    assert out.entity_count >= 1


@pytest.mark.unit()
def test_restore_roundtrip() -> None:
    client = FakeVaultClient()
    svc = VaultService(client)
    out = svc.mask("REQ-2", "MS-2", SYNTHETIC, "default")
    back = svc.restore("REQ-2", out.mapping_ref, "default", "TRACE-2")
    assert back.restored_content == SYNTHETIC


@pytest.mark.unit()
def test_detect_returns_entities_from_vault_placeholders() -> None:
    """Vault has no detect call: detection is derived from the masked text."""
    client = FakeVaultClient()
    svc = VaultService(client)
    res = svc.detect("REQ-3", SYNTHETIC, "default")
    assert [e.type for e in res.entities] == ["ID_CARD", "PHONE", "BANK_CARD"]
    # Offsets point into the masked text -- that is the only coordinate Vault gives.
    masked = svc.mask("REQ-3b", "MS-3", SYNTHETIC, "default").masked_content
    for entity in res.entities:
        assert masked[entity.start : entity.end].startswith("***")


@pytest.mark.unit()
def test_rule_mapping_is_sent_to_vault() -> None:
    """The adapter must request Vault's rule ids, not invent its own."""
    client = FakeVaultClient()
    svc = VaultService(client)
    ids = svc.rule_ids()
    assert "id_card" in ids and "phone" in ids and "email" in ids
    assert "bank_card" in ids and "use_sensitive_terms" in ids
    svc.mask("REQ-4", "MS-4", SYNTHETIC, "default")
    assert client.rule_ids_seen[-1] == ids


@pytest.mark.unit()
def test_gaps_are_surfaced_not_dropped() -> None:
    """Rules Vault cannot serve must be reported, never silently skipped.

    Guards the regression where a declared rule quietly never runs: one entity
    type stays in the clear while /health still answers "ok".
    """
    client = FakeVaultClient()
    svc = VaultService(client)
    gaps = {str(g["entity"]): str(g["status"]) for g in svc.gaps()}
    assert gaps.get("PERSON_NAME") == "config_dependent"
    assert gaps.get("USCI") == "missing"

    health = svc.health()
    # USCI is a hard gap -> degraded, not "ok": coverage is incomplete.
    assert health.status == "degraded"
    assert health.dependencies["rule:USCI"] == "missing"
    assert health.dependencies["cheersai_vault"].startswith("ok:")


@pytest.mark.unit()
def test_mask_fails_loud_when_vault_fails() -> None:
    """Red line: a Vault failure must raise, never return the original text.

    Returning the input on failure would push unmasked data straight to the
    cloud model -- a data-leak, not a degraded feature.
    """
    svc = VaultService(FakeVaultClient(fail_batch=True))
    with pytest.raises(VaultApiError) as exc:
        svc.mask("REQ-5", "MS-5", SYNTHETIC, "default")
    assert SYNTHETIC not in str(exc.value)  # error body must not carry the original


@pytest.mark.unit()
def test_health_degrades_when_vault_unreachable() -> None:
    """60.90.03 §651: an unavailable masking API blocks the cloud path."""
    svc = VaultService(FakeVaultClient(unreachable=True))
    health = svc.health()
    assert health.status == "degraded"
    assert health.dependencies["cheersai_vault"].startswith("unavailable")


@pytest.mark.unit()
def test_adapter_holds_no_masking_patterns() -> None:
    """Architecture guard: EKOS must not re-implement detection.

    Masking belongs to CheersAI Vault. We build the adapter -- not an engine.
    Someone adding sensitive-entity regexes here is rebuilding the wheel, which
    is exactly how this module drifted off-plan once already (2026-09-05).

    Two traps this guard has to survive, both of which made an earlier version
    of it pass while checking nothing:

    * The patterns are matched as **literal source text**, not compiled. These
      are regex *source* strings sitting in a file, so ``re.search(r"\\d{17}",
      text)`` looks for seventeen digits in the source and never matches the
      seven characters ``\\d{17}``. That version reported PASS on a file that
      literally contained the pattern -- caught by mutation testing, not by
      reading it.
    * The file list must be non-empty. A glob that misses every file makes the
      loop body never run, and an assertion nobody executes is a green light.
    """
    forbidden = [r"\d{17}[\dXx]", r"1[3-9]\d{9}", r"\d{16,19}"]
    files = sorted(Path("packages/vault").glob("*.py"))
    # A guard that scans zero files proves nothing -- fail loudly instead.
    assert files, "no files scanned: packages/vault glob matched nothing"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in text, (
                f"{path} carries a sensitive-entity pattern ({pattern}); "
                "detection must come from Vault, not from here"
            )
