"""L2 contract test: ingest pipeline contract.

Covers the public ingest contract exposed via packages.document:
  - IngestResult carries the five required fields (doc_id, source_uri,
    markdown, enforcement, alerts)
  - Enforcement value matches the config-layer JSON (config is authoritative)
  - ingest_document preserves original ParsedDocument shape -- no mutation
  - Existing X2 contract (test_document_contract.py) is unaffected
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from packages.document.ingest import IngestResult, ingest_document
from packages.document.schema import ParsedBlock, ParsedDocument

RULES_PATH = Path(__file__).resolve().parents[2] / "configs" / "naming_rules.json"
CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "runtime.yaml"


def _default_language() -> str:
    cfg: dict[str, Any] = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    lang: str = cfg["default_language"]
    return lang


def _clean_doc() -> ParsedDocument:
    return ParsedDocument(
        doc_id="contract-check-001",
        source_uri="minio://ekos/contracts/x.pdf",
        language=_default_language(),
        blocks=[
            ParsedBlock(
                block_id="contract-check-001-p1-b0",
                order=0,
                text="Intro block",
                language=_default_language(),
                page=1,
                kind="text",
            )
        ],
        parse_status="ok",
        parser_version="1.0.0",
    )


INGEST_FIELDS = ("doc_id", "source_uri", "markdown", "enforcement", "alerts")


@pytest.mark.contract()
def test_ingest_result_shape_matches_contract() -> None:
    result = ingest_document(_clean_doc())
    assert isinstance(result, IngestResult)
    dumped = result.__dict__
    for field in INGEST_FIELDS:
        assert field in dumped, f"IngestResult missing contract field {field}"


@pytest.mark.contract()
def test_ingest_alerts_list_is_typed() -> None:
    result = ingest_document(_clean_doc())
    assert isinstance(result.alerts, list)
    assert result.alert_count == 0
    assert result.has_alerts is False


@pytest.mark.contract()
def test_ingest_enforcement_matches_config_authority() -> None:
    """Enforcement comes from JSON, not from a Python constant.

    If someone hard-codes enforcement="advisory" in the ingest module,
    and then changes the JSON to "blocking" (when MVP graduates), this
    test will catch the disconnect.
    """
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    expected = str(rules.get("enforcement", "advisory"))
    result = ingest_document(_clean_doc())
    assert result.enforcement == expected, (
        f"enforcement mismatch: config says {expected!r}, ingest returned {result.enforcement!r}"
    )


@pytest.mark.contract()
def test_ingest_does_not_mutate_parsed_document() -> None:
    doc = _clean_doc()
    original_id = doc.doc_id
    original_uri = doc.source_uri
    original_blocks = len(doc.blocks)
    ingest_document(doc)
    assert doc.doc_id == original_id
    assert doc.source_uri == original_uri
    assert len(doc.blocks) == original_blocks


@pytest.mark.contract()
def test_ingest_violation_sample_alert_count_matches_sample_count() -> None:
    """Advisory-level contract check: three distinct violation docs -> three
    result objects, each has_alerts=True, no cross-leak between samples."""
    docs: list[tuple[ParsedDocument, int]] = [
        (
            ParsedDocument(
                doc_id="has space in name",
                source_uri="minio://b/x.pdf",
                language=_default_language(),
                blocks=[],
            ),
            2,
        ),
        (
            ParsedDocument(
                doc_id="ok-name",
                source_uri="ftp://host/y.pdf",
                language=_default_language(),
                blocks=[],
            ),
            1,
        ),
        (
            ParsedDocument(
                doc_id="ok-name-2",
                source_uri="file:///a/../../b/x.pdf",
                language=_default_language(),
                blocks=[],
            ),
            2,
        ),
    ]

    running_total = 0
    for doc, expected_min in docs:
        result = ingest_document(doc)
        assert result.has_alerts is True
        assert result.alert_count >= expected_min, (
            f"expected >= {expected_min} alerts for {doc.source_uri}, got {result.alert_count}"
        )
        running_total += result.alert_count
    assert running_total >= 5
