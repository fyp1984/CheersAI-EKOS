"""L1 unit test: retrieval skeleton (X3 contract)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from packages.retrieval.service import RetrievalService, RetrieveRequest

ALIASES = Path(__file__).resolve().parents[2] / "configs" / "aliases.yaml"


@pytest.mark.unit()
def test_retrieve_refuses_without_index() -> None:
    svc = RetrievalService()
    req = RetrieveRequest(request_id="R1", query="foo", top_k=6, trace_id="T1")
    res = svc.retrieve(req)
    assert res.refused is True
    assert res.hits == []


@pytest.mark.unit()
def test_alias_normalization_config_driven() -> None:
    cfg = yaml.safe_load(ALIASES.read_text(encoding="utf-8"))
    variant = cfg["languages"]["zh"]["aliases"][0]["variants"][0]
    canonical = cfg["languages"]["zh"]["aliases"][0]["canonical"]
    out = RetrievalService().normalize(variant)
    assert out == canonical
