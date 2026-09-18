"""Deep parsing layer (shared kernel).

Reads a raw material, splits it into blocks, tags language. The real parsing is
wired to an external engine on E1; this skeleton returns a structurally-correct
empty document so downstream code can be built and tested now.

Language comes from config, never from a code constant (TR-6).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .schema import ParsedDocument

PARSER_VERSION = "1.0.0"
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "runtime.yaml"


def _load_default_language() -> str:
    """Default language is read from the config layer, not hardcoded (TR-6)."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError as exc:
        raise RuntimeError("configs/runtime.yaml missing: default language undefined") from exc
    value = cfg.get("default_language")
    if not isinstance(value, str) or not value:
        raise RuntimeError("default_language not set in configs/runtime.yaml")
    return value


def parse(source_uri: str, content_type: str, policy_id: str, trace_id: str) -> ParsedDocument:
    """Skeleton parse: returns an empty but valid ParsedDocument.

    Real block extraction is filled in on E1. The language field is taken from
    config, not from a module-level constant.
    """
    language = _load_default_language()
    return ParsedDocument(
        doc_id=source_uri,
        source_uri=source_uri,
        language=language,
        blocks=[],
        parse_status="ok",
        parser_version=PARSER_VERSION,
    )
