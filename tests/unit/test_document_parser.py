"""L1 unit test: parsing skeleton (X2 contract)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from packages.document.parser import parse
from packages.document.schema import ParsedDocument

CONFIG = Path(__file__).resolve().parents[2] / "configs" / "runtime.yaml"


@pytest.mark.unit()
def test_parse_returns_valid_document() -> None:
    doc = parse("minio://x/doc.pdf", "application/pdf", "default", "TRACE-1")
    assert isinstance(doc, ParsedDocument)
    assert doc.parse_status == "ok"
    assert doc.parser_version == "1.0.0"
    assert doc.blocks == []


@pytest.mark.unit()
def test_parse_language_from_config() -> None:
    expected = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["default_language"]
    doc = parse("u", "t", "p", "tr")
    assert doc.language == expected
