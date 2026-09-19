"""Ingestion pipeline: naming/path validation plus Markdown conversion.

MVP enforcement is advisory (warnings only) per technical plan section 4.8.
All rules live in configs/naming_rules.json; none are hard-coded in kernel
code (the kernel stays rule-agnostic so a config change updates behaviour
without a deploy).

Why advisory instead of blocking
--------------------------------
§4.8 decided MVP uses advisory mode so ingest never silently drops data --
the operator sees every violation in logs and can fix the source, while
the content still flows through the pipeline. Blocking can be turned on
later by flipping enforcement in the JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .parser import to_markdown
from .schema import ParsedDocument

_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "naming_rules.json"


@dataclass
class ValidationAlert:
    """One naming or path violation. Pure data -- no formatting here."""

    scope: str
    rule_id: str
    message: str
    value: str


@dataclass
class IngestResult:
    """Result of running a ParsedDocument through the ingest pipeline."""

    doc_id: str
    source_uri: str
    markdown: str
    enforcement: str
    alerts: list[ValidationAlert] = field(default_factory=list)

    @property
    def has_alerts(self) -> bool:
        return len(self.alerts) > 0

    @property
    def alert_count(self) -> int:
        return len(self.alerts)


def _load_rules() -> dict[str, Any]:
    """Load naming rules from the config layer.

    Raises RuntimeError if the JSON is missing, so a broken deploy fails fast
    instead of silently running with no rules -- the latter is the classic
    way validation gates silently rot.
    """
    try:
        with open(_RULES_PATH, encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
    except FileNotFoundError as exc:
        raise RuntimeError(f"ingest rules missing at {_RULES_PATH}; validation cannot run") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"ingest rules {_RULES_PATH} must be a JSON object")
    return data


def _validate_naming(doc_id: str, rules: dict[str, Any]) -> list[ValidationAlert]:
    naming: dict[str, Any] = rules.get("naming", {}) or {}
    alerts: list[ValidationAlert] = []

    min_len = int(naming.get("doc_id_min_length", 0) or 0)
    if len(doc_id) < min_len:
        alerts.append(
            ValidationAlert(
                scope="naming",
                rule_id="naming.min_length",
                message=f"doc_id length {len(doc_id)} below minimum {min_len}",
                value=doc_id,
            )
        )

    max_len = int(naming.get("doc_id_max_length", 0) or 0)
    if max_len and len(doc_id) > max_len:
        alerts.append(
            ValidationAlert(
                scope="naming",
                rule_id="naming.max_length",
                message=f"doc_id length {len(doc_id)} exceeds maximum {max_len}",
                value=doc_id,
            )
        )

    pattern = naming.get("doc_id_pattern")
    if isinstance(pattern, str) and pattern and not re.fullmatch(pattern, doc_id):
        alerts.append(
            ValidationAlert(
                scope="naming",
                rule_id="naming.pattern",
                message=f"doc_id does not match required pattern {pattern!r}",
                value=doc_id,
            )
        )

    forbidden = naming.get("forbidden_substrings") or []
    for token in forbidden:
        if not isinstance(token, str):
            continue
        if token in doc_id:
            alerts.append(
                ValidationAlert(
                    scope="naming",
                    rule_id="naming.forbidden_substring",
                    message=f"doc_id contains forbidden substring {token!r}",
                    value=doc_id,
                )
            )

    return alerts


def _validate_path(source_uri: str, rules: dict[str, Any]) -> list[ValidationAlert]:
    path_cfg: dict[str, Any] = rules.get("path", {}) or {}
    alerts: list[ValidationAlert] = []

    min_len = int(path_cfg.get("source_uri_min_length", 0) or 0)
    if len(source_uri) < min_len:
        alerts.append(
            ValidationAlert(
                scope="path",
                rule_id="path.min_length",
                message=f"source_uri length {len(source_uri)} below minimum {min_len}",
                value=source_uri,
            )
        )

    max_len = int(path_cfg.get("source_uri_max_length", 0) or 0)
    if max_len and len(source_uri) > max_len:
        alerts.append(
            ValidationAlert(
                scope="path",
                rule_id="path.max_length",
                message=f"source_uri length {len(source_uri)} exceeds maximum {max_len}",
                value=source_uri,
            )
        )

    scheme_required = bool(path_cfg.get("scheme_required", False))
    allowed_schemes = path_cfg.get("allowed_schemes") or []
    if "://" in source_uri:
        scheme = source_uri.split("://", 1)[0].lower()
        if (
            isinstance(allowed_schemes, list)
            and allowed_schemes
            and scheme not in [str(s).lower() for s in allowed_schemes]
        ):
            alerts.append(
                ValidationAlert(
                    scope="path",
                    rule_id="path.scheme_not_allowed",
                    message=(
                        f"source_uri scheme {scheme!r} not in allowed list: {allowed_schemes!r}"
                    ),
                    value=source_uri,
                )
            )
    elif scheme_required:
        alerts.append(
            ValidationAlert(
                scope="path",
                rule_id="path.scheme_missing",
                message="source_uri is missing a scheme (e.g. file://, minio://)",
                value=source_uri,
            )
        )

    forbidden = path_cfg.get("forbidden_substrings") or []
    for token in forbidden:
        if not isinstance(token, str):
            continue
        if token in source_uri:
            alerts.append(
                ValidationAlert(
                    scope="path",
                    rule_id="path.forbidden_substring",
                    message=f"source_uri contains forbidden substring {token!r}",
                    value=source_uri,
                )
            )

    traversal_check = bool(path_cfg.get("path_traversal_check", False))
    if traversal_check:
        normalized = source_uri.replace("\\", "/")
        segments = normalized.split("/")
        if ".." in segments:
            alerts.append(
                ValidationAlert(
                    scope="path",
                    rule_id="path.path_traversal",
                    message="source_uri contains path-traversal segment '..'",
                    value=source_uri,
                )
            )

    return alerts


def ingest_document(doc: ParsedDocument) -> IngestResult:
    """Run naming + path validation and convert to Markdown.

    Rules are read from the config layer (configs/naming_rules.json).
    Enforcement mode is taken from the same JSON and defaults to "advisory"
    so MVP does not block ingestion. Callers that want blocking behaviour
    can check result.has_alerts and raise themselves.
    """
    rules = _load_rules()
    enforcement = str(rules.get("enforcement", "advisory"))

    alerts: list[ValidationAlert] = []
    alerts.extend(_validate_naming(doc.doc_id, rules))
    alerts.extend(_validate_path(doc.source_uri, rules))

    markdown = to_markdown(doc)

    return IngestResult(
        doc_id=doc.doc_id,
        source_uri=doc.source_uri,
        markdown=markdown,
        enforcement=enforcement,
        alerts=alerts,
    )
