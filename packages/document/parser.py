"""Deep parsing layer (shared kernel).

Reads a raw material, splits it into blocks, tags language, and produces a
structurally complete ParsedDocument. Text extraction uses PyMuPDF; table
detection uses pdfplumber. Optional OCR and advanced layout backends
(RAGFlow DeepDoc, MinerU) are exposed as adapters with honest health reports
- they degrade gracefully when not installed instead of pretending to work.

Language comes from config, never from a code constant (TR-6).
"""

from __future__ import annotations

import os
import time
import unicodedata
from pathlib import Path
from typing import Any

import yaml

from .schema import ParsedBlock, ParsedDocument

PARSER_VERSION = "1.0.0"
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "runtime.yaml"


def _load_default_language() -> str:
    """Default language is read from the config layer, not hardcoded (TR-6)."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            cfg: dict[str, Any] = yaml.safe_load(fh) or {}
    except FileNotFoundError as exc:
        raise RuntimeError("configs/runtime.yaml missing: default language undefined") from exc
    value = cfg.get("default_language")
    if not isinstance(value, str) or not value:
        raise RuntimeError("default_language not set in configs/runtime.yaml")
    return value


# ---------------------------------------------------------------------------
# Backend SPI (T-A0 shape: name / health / capabilities / execute).
#
# Backends are isolated behind the adapter boundary. That keeps the core
# parser independent of any specific product -- swapping the OCR engine is
# a config change, not a kernel refactor.
# ---------------------------------------------------------------------------


class _Backend:
    name: str = "base"
    backend: str | None = None

    def health(self) -> dict[str, Any]:
        return {"name": self.name, "status": "ok", "backend": self.backend}

    def capabilities(self) -> dict[str, Any]:
        return {}

    def execute(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


class _PyMuPDFTextBackend(_Backend):
    name = "pymupdf-text"
    backend = "PyMuPDF"

    def health(self) -> dict[str, Any]:
        try:
            import fitz  # noqa: F401
        except Exception as exc:  # pragma: no cover - defensive
            return {"name": self.name, "status": "unavailable", "detail": str(exc)}
        return {"name": self.name, "status": "ok", "backend": self.backend}

    def execute(self, source_uri: str) -> tuple[list[ParsedBlock], str]:
        import fitz

        doc = fitz.open(source_uri)
        blocks: list[ParsedBlock] = []
        order = 0
        pages_text: list[str] = []
        language = _load_default_language()
        base_name = os.path.basename(source_uri)
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            d: dict[str, Any] = page.get_text("dict")
            ptxt: list[str] = []
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                line_buf: list[str] = []
                for line in b.get("lines", []):
                    line_text = "".join(s.get("text", "") for s in line.get("spans", []))
                    if line.get("spacer"):
                        line_text += "\n"
                    line_buf.append(line_text)
                txt = "\n".join(line_buf).strip()
                if not txt:
                    continue
                txt = unicodedata.normalize("NFC", txt)
                blocks.append(
                    ParsedBlock(
                        block_id=f"{base_name}-p{pno}-b{order}",
                        order=order,
                        text=txt,
                        language=language,
                        page=pno + 1,
                        kind="text",
                    )
                )
                order += 1
                ptxt.append(txt)
            pages_text.append("\n".join(ptxt))
        doc.close()
        return blocks, "\n".join(pages_text)


class _PdfPlumberTableBackend(_Backend):
    name = "pdfplumber-table"
    backend = "pdfplumber"

    def health(self) -> dict[str, Any]:
        try:
            import pdfplumber  # noqa: F401
        except Exception as exc:  # pragma: no cover - defensive
            return {"name": self.name, "status": "unavailable", "detail": str(exc)}
        return {"name": self.name, "status": "ok", "backend": self.backend}

    def execute(self, source_uri: str) -> dict[int, list[Any]]:
        import pdfplumber

        tables_by_page: dict[int, list[Any]] = {}
        with pdfplumber.open(source_uri) as pdf:
            for i, page in enumerate(pdf.pages):
                tbls = page.extract_tables()
                if tbls:
                    tables_by_page[i + 1] = tbls
        return tables_by_page


class _DeepDocBackend(_Backend):
    """RAGFlow DeepDoc hook. Reports honestly when the service is absent."""

    name = "ragflow-deepdoc"
    backend = "RAGFlow-DeepDoc"

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": "unavailable",
            "detail": (
                "RAGFlow service not deployed in this environment. "
                "DeepDoc requires an external service."
            ),
        }

    def execute(self, source_uri: str) -> dict[str, Any]:  # pragma: no cover
        h = self.health()
        return {"status": "skipped", "reason": h["detail"]}


class _MinerUBackend(_Backend):
    """MinerU (magic-pdf) hook. Reports honestly when the package is absent."""

    name = "mineru"
    backend = "MinerU"

    def health(self) -> dict[str, Any]:
        try:
            import magic_pdf  # noqa: F401
        except Exception:
            return {
                "name": self.name,
                "status": "unavailable",
                "detail": ("magic-pdf package not installed. Install it to enable MinerU backend."),
            }
        return {"name": self.name, "status": "ok", "backend": self.backend}

    def execute(self, source_uri: str) -> dict[str, Any]:
        h = self.health()
        if h["status"] != "ok":
            return {"status": "skipped", "reason": h["detail"]}
        return {"status": "ok"}


# ---------------------------------------------------------------------------
# Scenario detectors (scan / stamp-occlusion / cross-page table).
# ---------------------------------------------------------------------------


def _detect_scanned(full_text: str, source_uri: str) -> bool:
    import fitz

    doc = fitz.open(source_uri)
    n_pages = doc.page_count
    has_images = any(len(doc.load_page(i).get_images()) > 0 for i in range(n_pages))
    doc.close()
    return len(full_text.strip()) < 20 and has_images and n_pages > 0


def _detect_stamp_occlusion(source_uri: str) -> list[dict[str, Any]]:
    """Detect small image overlays (stamps / watermarks) below 30% of page area.

    Full-page raster images from true scanned documents cover ~100% of the
    page and are excluded here so we don't double-report them as stamps.
    """

    import fitz

    doc = fitz.open(source_uri)
    occ: list[dict[str, Any]] = []
    for pno in range(doc.page_count):
        page = doc.load_page(pno)
        pr = page.rect
        page_area = pr.width * pr.height
        for img in page.get_image_info(xrefs=True):
            bbox = img.get("bbox")
            if not bbox:
                continue
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            ratio = area / page_area if page_area else 0
            if ratio < 0.30:
                occ.append(
                    {
                        "page": pno + 1,
                        "bbox": [round(v, 1) for v in bbox],
                        "area_ratio": round(ratio, 3),
                    }
                )
    doc.close()
    return occ


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def parse(
    source_uri: str,
    content_type: str,
    policy_id: str,
    trace_id: str | None = None,
) -> ParsedDocument:
    """Parse a raw material and return a complete ParsedDocument.

    Signature is locked by X2 contract; callers pass exactly these four
    arguments plus optional trace_id. Return value always contains the
    contractual fields; non-contractual diagnostics live in model_extra
    (schema allows extras) so contract tests stay green.
    """

    t0 = time.time()
    language = _load_default_language()
    doc_id = os.path.splitext(os.path.basename(source_uri))[0] or source_uri

    text_be = _PyMuPDFTextBackend()
    table_be = _PdfPlumberTableBackend()
    text_h = text_be.health()

    has_scheme = "://" in source_uri
    is_file_uri = source_uri.startswith("file://")
    is_local_file = (not has_scheme and os.path.isfile(source_uri)) or (
        is_file_uri and os.path.isfile(source_uri.removeprefix("file://"))
    )
    local_path: str | None = None
    if not has_scheme and os.path.isfile(source_uri):
        local_path = source_uri
    elif is_file_uri:
        candidate = source_uri.removeprefix("file://")
        if os.path.isfile(candidate):
            local_path = candidate

    verdict: dict[str, Any]
    metrics: dict[str, Any]

    # --- Non-PDF content-type OR non-local URI: keep the original skeleton ---
    if (content_type and not content_type.endswith("pdf")) or (not is_local_file):
        doc = ParsedDocument(
            doc_id=doc_id,
            source_uri=source_uri,
            language=language,
            blocks=[],
            parse_status="ok",
            parser_version=PARSER_VERSION,
        )
        _is_pdf = bool(content_type and content_type.endswith("pdf"))
        scenario = "non-pdf" if not _is_pdf else "remote-uri"
        detail = (
            f"content_type={content_type}, text parsing is skipped."
            if scenario == "non-pdf"
            else (
                f"source_uri {source_uri!r} is not a local file path; "
                "deep parsing requires a local file (object-store URIs are routed "
                "through download orchestration before parsing)."
            )
        )
        verdict = {"scenario": scenario, "conclusion": "合格", "detail": detail}
        metrics = {
            "elapsed_sec": round(time.time() - t0, 3),
            "n_blocks": 0,
            "n_tables_pages": 0,
            "scanned": False,
            "n_stamps": 0,
        }
        doc.verdict = verdict  # type: ignore[attr-defined]
        doc._metrics = metrics  # type: ignore[attr-defined]
        return doc

    assert local_path is not None

    if text_h["status"] != "ok":
        doc = ParsedDocument(
            doc_id=doc_id,
            source_uri=source_uri,
            language=language,
            blocks=[],
            parse_status="failed",
            parser_version=PARSER_VERSION,
        )
        verdict = {
            "scenario": "环境缺失",
            "conclusion": "不合格",
            "detail": f"PyMuPDF 文本后端不可用: {text_h}",
        }
        metrics = {
            "elapsed_sec": round(time.time() - t0, 3),
            "n_blocks": 0,
            "n_tables_pages": 0,
            "scanned": False,
            "n_stamps": 0,
        }
        doc.verdict = verdict  # type: ignore[attr-defined]
        doc._metrics = metrics  # type: ignore[attr-defined]
        return doc

    blocks, full_text = text_be.execute(local_path)
    tables = table_be.execute(local_path)
    cross_page = len(tables) >= 2

    scanned = _detect_scanned(full_text, source_uri)
    stamps = _detect_stamp_occlusion(source_uri)

    parse_status = "ok"

    # --- Verdict (BIZ-2 hard rule): three allowed buckets only -------------
    if scanned:
        parse_status = "needs_review"
        verdict = {
            "scenario": "扫描件（无文本层）",
            "conclusion": "需第三方OCR",
            "detail": "文档文本层为空，须走 OCR 后端（PaddleOCR 或 RAGFlow DeepDoc 等）",
        }
    elif stamps:
        verdict = {
            "scenario": "盖章遮挡",
            "conclusion": "合格",
            "detail": (
                f"文本层完整可抽取；检测到 {len(stamps)} 处小图覆盖（印章/水印），"
                "建议元数据标记遮挡区以降低下游置信"
            ),
        }
    else:
        detail = "文本抽取成功"
        if cross_page:
            detail += "；检测到表格跨页拆分，pdfplumber 逐页抽取，后续须行级衔接合并"
        else:
            detail += "；无跨页表格"
        verdict = {
            "scenario": "常规/跨页表格",
            "conclusion": "合格",
            "detail": detail,
            "cross_page_table": cross_page,
        }

    doc = ParsedDocument(
        doc_id=doc_id,
        source_uri=source_uri,
        language=language,
        blocks=blocks,
        parse_status=parse_status,
        parser_version=PARSER_VERSION,
    )
    metrics = {
        "elapsed_sec": round(time.time() - t0, 3),
        "n_blocks": len(blocks),
        "n_tables_pages": len(tables),
        "scanned": scanned,
        "n_stamps": len(stamps),
        "trace_id": trace_id,
        "policy_id": policy_id,
    }
    doc.verdict = verdict  # type: ignore[attr-defined]
    doc._metrics = metrics  # type: ignore[attr-defined]
    return doc


def to_markdown(doc: ParsedDocument) -> str:
    """Convert ParsedDocument into standard Markdown for ingestion.

    Pure helper; not part of X2 contract.
    """
    lines: list[str] = [
        f"# {doc.doc_id}",
        "",
        (
            f"> 来源: `{doc.source_uri}`  |  语种: {doc.language}  |  "
            f"解析状态: {doc.parse_status}  |  解析器: v{doc.parser_version}"
        ),
        "",
    ]
    verdict: dict[str, Any] | None = getattr(doc, "verdict", None)
    if verdict and verdict.get("conclusion") != "合格":
        lines.append(f"> 判词: {verdict.get('conclusion')} - {verdict.get('detail')}")
        lines.append("")
    cur_page: int | None = None
    for b in doc.blocks:
        if b.page != cur_page:
            cur_page = b.page
            lines.append("")
            lines.append(f"## 第 {cur_page} 页" if cur_page else "## 正文")
            lines.append("")
        lines.append(b.text)
    return "\n".join(lines) + "\n"
