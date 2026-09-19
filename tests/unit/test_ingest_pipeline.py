"""L1 unit test: ingest pipeline -- naming/path validation + markdown conversion.

Strict requirement from QA-1b / exit-criteria 9: every naming or path
violation must produce exactly one alert (100% correspondence, no silent
drops and no false duplicates).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from packages.document.ingest import (
    IngestResult,
    ValidationAlert,
    ingest_document,
)
from packages.document.parser import to_markdown
from packages.document.schema import ParsedDocument

RULES_PATH = Path(__file__).resolve().parents[2] / "configs" / "naming_rules.json"
CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "runtime.yaml"


def _default_language() -> str:
    cfg: dict[str, Any] = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    lang: str = cfg["default_language"]
    return lang


def _rules() -> dict[str, Any]:
    rules: dict[str, Any] = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return rules


def _make_doc(doc_id: str, source_uri: str) -> ParsedDocument:
    return ParsedDocument(
        doc_id=doc_id,
        source_uri=source_uri,
        language=_default_language(),
        blocks=[],
    )


@pytest.mark.unit()
def test_rules_live_in_config_layer() -> None:
    """No rule literals are hard-coded; the JSON on disk is the authority.

    If this test fails because someone moved rules into Python constants,
    we have silently broken the promise that "updating rules means editing
    one file, not redeploying".
    """
    rules = _rules()
    assert rules["version"] == "1.0"
    assert rules["enforcement"] == "advisory"
    assert "naming" in rules and "path" in rules


@pytest.mark.unit()
def test_clean_sample_zero_alerts() -> None:
    doc = _make_doc("annual-report-2025-v1", "minio://knowledge-bucket/reports/2025.pdf")
    result = ingest_document(doc)
    assert isinstance(result, IngestResult)
    assert result.doc_id == doc.doc_id
    assert result.source_uri == doc.source_uri
    assert result.enforcement == "advisory"
    assert result.has_alerts is False
    assert result.alert_count == 0
    assert result.alerts == []


@pytest.mark.unit()
def test_markdown_reuses_existing_to_markdown() -> None:
    """Requirement 2.2: reuse parser.py to_markdown, do not rebuild."""
    doc = _make_doc("reuse-check", "file:///data/in/x.pdf")
    result = ingest_document(doc)
    expected = to_markdown(doc)
    assert result.markdown == expected
    assert result.markdown.startswith(f"# {doc.doc_id}\n")


# ---------------------------------------------------------------------------
# Naming violations: one sample -> exact expected alerts
# ---------------------------------------------------------------------------


@pytest.mark.unit()
def test_naming_pattern_violation() -> None:
    doc = _make_doc("annual report 2025.pdf", "minio://b/x.pdf")
    result = ingest_document(doc)
    pattern_alerts = [a for a in result.alerts if a.rule_id == "naming.pattern"]
    assert len(pattern_alerts) == 1
    assert pattern_alerts[0].scope == "naming"
    assert "does not match required pattern" in pattern_alerts[0].message


@pytest.mark.unit()
def test_naming_forbidden_substring_slash() -> None:
    doc = _make_doc("reports/2025/annual", "minio://b/x.pdf")
    result = ingest_document(doc)
    hits = [a for a in result.alerts if a.rule_id == "naming.forbidden_substring"]
    assert len(hits) == 1
    assert "contains forbidden substring" in hits[0].message


@pytest.mark.unit()
def test_naming_forbidden_substring_space() -> None:
    doc = _make_doc("annual report", "minio://b/x.pdf")
    result = ingest_document(doc)
    hits = [a for a in result.alerts if a.rule_id == "naming.forbidden_substring"]
    assert len(hits) == 1


@pytest.mark.unit()
def test_naming_max_length_exceeded() -> None:
    rules = _rules()
    max_len = int(rules["naming"]["doc_id_max_length"])
    long_id = "a" * (max_len + 5)
    doc = _make_doc(long_id, "minio://b/x.pdf")
    result = ingest_document(doc)
    hits = [a for a in result.alerts if a.rule_id == "naming.max_length"]
    assert len(hits) == 1
    assert f"exceeds maximum {max_len}" in hits[0].message


# ---------------------------------------------------------------------------
# Path violations
# ---------------------------------------------------------------------------


@pytest.mark.unit()
def test_path_scheme_not_allowed() -> None:
    doc = _make_doc("x", "ftp://secret-server/internal.pdf")
    result = ingest_document(doc)
    hits = [a for a in result.alerts if a.rule_id == "path.scheme_not_allowed"]
    assert len(hits) == 1
    assert "ftp" in hits[0].message.lower()


@pytest.mark.unit()
def test_path_scheme_missing() -> None:
    doc = _make_doc("x", "local-disk-only.pdf")
    result = ingest_document(doc)
    hits = [a for a in result.alerts if a.rule_id == "path.scheme_missing"]
    assert len(hits) == 1


@pytest.mark.unit()
def test_path_forbidden_space() -> None:
    doc = _make_doc("x", "file:///my docs/report.pdf")
    result = ingest_document(doc)
    hits = [a for a in result.alerts if a.rule_id == "path.forbidden_substring"]
    assert len(hits) == 1
    assert " " in hits[0].message or "space" in hits[0].message.lower()


@pytest.mark.unit()
def test_path_traversal_dotdot() -> None:
    doc = _make_doc("x", "file:///data/../etc/passwd")
    result = ingest_document(doc)
    substring_hits = [a for a in result.alerts if a.rule_id == "path.forbidden_substring"]
    traversal_hits = [a for a in result.alerts if a.rule_id == "path.path_traversal"]
    assert len(substring_hits) == 1
    assert len(traversal_hits) == 1


@pytest.mark.unit()
def test_path_max_length_exceeded() -> None:
    rules = _rules()
    max_len = int(rules["path"]["source_uri_max_length"])
    long_uri = "file:///" + ("x" * (max_len + 10))
    doc = _make_doc("x", long_uri)
    result = ingest_document(doc)
    hits = [a for a in result.alerts if a.rule_id == "path.max_length"]
    assert len(hits) == 1


# ---------------------------------------------------------------------------
# QA-1b core check: violation sample count == alert count, 100%
# ---------------------------------------------------------------------------


@pytest.mark.unit()
def test_exit_criteria_9_one_to_one_violation_alerts() -> None:
    """QA-1b: naming/path violations trigger 100% of expected alerts.

    We construct a fixed table of (sample doc_id, source_uri, expected_alert_rules).
    The sum of (expected rule count per sample) across all violation samples
    must equal the actual alert count exactly -- no missed violations and no
    phantom alerts.
    """

    samples: list[tuple[str, str, list[str]]] = [
        (
            "bad name with spaces",
            "minio://bucket/ok.pdf",
            ["naming.pattern", "naming.forbidden_substring"],
        ),
        (
            "too long bad id " + "x" * 200,
            "minio://bucket/ok.pdf",
            ["naming.pattern", "naming.forbidden_substring", "naming.max_length"],
        ),
        (
            "ok_name-1.0",
            "ftp://host/internal/file.pdf",
            ["path.scheme_not_allowed"],
        ),
        (
            "bad#id",
            "noscheme-local.pdf",
            ["path.scheme_missing", "naming.pattern"],
        ),
        (
            "traverse",
            "file:///data/../../secret/path 1/x.pdf",
            [
                "path.forbidden_substring",
                "path.forbidden_substring",
                "path.path_traversal",
            ],
        ),
        (
            "slash/in/id",
            "file:///data/../etc/x.pdf",
            [
                "naming.pattern",
                "naming.forbidden_substring",
                "path.forbidden_substring",
                "path.path_traversal",
            ],
        ),
    ]

    total_expected = sum(len(rules) for _, _, rules in samples)
    total_actual = 0
    for idx, (doc_id, source_uri, expected_rules) in enumerate(samples):
        result = ingest_document(_make_doc(doc_id, source_uri))
        actual_rules = [a.rule_id for a in result.alerts]
        assert len(actual_rules) == len(expected_rules), (
            f"sample #{idx}: expected {len(expected_rules)} alerts "
            f"({expected_rules}), got {len(actual_rules)} ({actual_rules})"
        )
        for rule_id in expected_rules:
            assert rule_id in actual_rules, (
                f"sample #{idx}: expected rule {rule_id!r} missing; actual={actual_rules}"
            )
        total_actual += len(result.alerts)

    assert total_actual == total_expected, (
        f"QA-1b 100% correspondence check: {total_expected} violations expected, "
        f"{total_actual} alerts fired"
    )


@pytest.mark.unit()
def test_validation_alert_dataclass_shape() -> None:
    alert = ValidationAlert(
        scope="path",
        rule_id="path.scheme_missing",
        message="missing scheme",
        value="local-file.pdf",
    )
    assert alert.scope == "path"
    assert alert.rule_id == "path.scheme_missing"
