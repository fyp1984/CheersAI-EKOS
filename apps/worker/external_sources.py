"""⑩ M14 外部知识源接入（L2 ConnectorAdapter 的第二个实现）

PRD §6 F-M14（MVP 最小版）三条判据：
  ① ≥1 个外部法规 / 标准可信源接入，并可作为检索源被召回；
  ② 外部知识 **100% 标注来源与生效日期**；
  ③ 过期内容 **100% 被标记**，且在**检索结果中不可见**。

设计要点
--------
- 走与内部数据源**完全同一套 SPI**（`ConnectorAdapter`：name / health / capabilities / execute），
  新增一个外部源**只加一个适配器，内核零改动**（承 T-C1「换引擎只改配置」）。
- 时效信息（来源 / 生效日期 / 失效日期）来自文档 front matter，落进 `IndexChunk`
  的专用字段；**缺失即报错** —— 这是判据②「100% 标注」的可执行保障，不靠人工检查。
- 判据③拆成两步：**标记**在同步侧（记录里带 `expired` 字段 + health 计数），
  **不可见**在检索侧（`RetrievalService.retrieve()` 按 `expiry_date` 过滤，见 packages/retrieval）。

纪律：内核零行业词（TR-1）｜端口零硬编码（TR-7）｜样本零真实数据（TR-8）。
"""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from packages.retrieval.service import IndexChunk

from apps.worker.connectors import ConnectorAdapter

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
EXTERNAL_SOURCES_DEFAULT = REPO_ROOT / "deploy" / "fixtures" / "external-sources"

# 每条 chunk 的目标长度（字符）。E1 接切分服务后由 configs/chunking.yaml 接管。
CHUNK_TARGET_CHARS = 240


def _parse_front_matter(text: str) -> dict[str, str]:
    """解析 `---\\n<yaml>\\n---` 头部；无头部或解析失败返回空 dict。"""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        raw = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): ("" if v is None else str(v)) for k, v in raw.items()}


def _split_body(body: str) -> list[str]:
    """按空行切段落，过滤标题行与过短段落。"""
    out: list[str] = []
    for block in body.split("\n\n"):
        para = " ".join(line.strip() for line in block.strip().splitlines()).strip()
        if not para or para.startswith("#") or len(para) < 8:
            continue
        out.append(para)
    return out


class ExternalSourceConnector(ConnectorAdapter):
    """外部法规 / 标准可信源连接器：遍历本地只读镜像，产出带时效元数据的同步记录。"""

    name = "external-source"

    def __init__(
        self,
        source_id: str,
        source_name: str,
        root: str | os.PathLike[str] | None = None,
        kind: str = "standard",
    ) -> None:
        self.source_id = source_id
        self.source_name = source_name
        self.kind = kind
        self.root = Path(root) if root else EXTERNAL_SOURCES_DEFAULT

    def health(self) -> dict[str, Any]:
        ok = self.root.is_dir()
        return {
            "name": self.name,
            "status": "ok" if ok else "failed",
            "source_id": self.source_id,
            "source_name": self.source_name,
            "root": str(self.root),
            "exists": ok,
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "source": "external",
            "kind": self.kind,
            "external": True,
            "formats": ["md"],
            "auto_sync": True,
            "requires_effective_date": True,
        }

    def execute(
        self, policy_id: str = "default", trace_id: str | None = None
    ) -> list[dict[str, Any]]:
        """产出同步记录。缺 `source_name` / `effective_date` 直接报错（判据②的硬保障）。"""
        if not self.root.is_dir():
            return []
        today = date.today().isoformat()
        records: list[dict[str, Any]] = []
        for full in sorted(self.root.rglob("*.md")):
            if full.name.startswith("."):
                continue
            text = full.read_text(encoding="utf-8")
            meta = _parse_front_matter(text)
            effective_date = meta.get("effective_date", "").strip()
            source_name = (meta.get("source_name") or self.source_name).strip()
            expiry_date = meta.get("expiry_date", "").strip()
            if not source_name or not effective_date:
                raise ValueError(
                    f"外部知识源缺时效元数据（source_name/effective_date）：{full.name}；"
                    "判据②要求外部知识 100% 标注来源与生效日期。"
                )
            rel = full.relative_to(self.root).as_posix()
            digest = hashlib.sha256(full.read_bytes()).hexdigest()[:16]
            expired = bool(expiry_date) and expiry_date < today
            records.append(
                {
                    "doc_id": f"EXT-{digest}",
                    "source_id": self.source_id,
                    "source_name": source_name,
                    "source_kind": self.kind,
                    "source_uri": f"external://{self.source_id}/{rel}",
                    "title": full.stem,
                    "effective_date": effective_date,
                    "expiry_date": expiry_date,
                    "expired": expired,
                    "permission_domain": "D-external",
                    "sha256_16": digest,
                    "body": text.split("---", 2)[-1] if text.startswith("---") else text,
                }
            )
        return records

    def mark_expired(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """标记过期项（判据③的「标记」环节）：返回过期记录子集，便于统计与留痕。"""
        return [r for r in records if r.get("expired")]


def to_index_chunks(
    records: list[dict[str, Any]], *, language: str, chunk_target: int = CHUNK_TARGET_CHARS
) -> list[IndexChunk]:
    """把同步记录转成可检索单元。

    - **证据链**：`evidence` 恒为「来源｜生效日期｜URI」——判据②在检索结果侧的落地，
      用户看到的每条外部知识都自带出处与时效。
    - `language` 由调用方从配置注入（不在本模块写死语种字面量，守 TR-6）。
    """
    chunks: list[IndexChunk] = []
    for rec in records:
        source_name = str(rec.get("source_name") or "")
        effective_date = str(rec.get("effective_date") or "")
        expiry_date = str(rec.get("expiry_date") or "")
        uri = str(rec.get("source_uri") or "")
        if not source_name or not effective_date:
            raise ValueError(f"外部记录缺时效元数据，拒绝入库：{rec.get('doc_id')}")
        evidence = f"{source_name}｜生效 {effective_date}｜{uri}"
        doc_id = str(rec.get("doc_id") or "")
        paragraphs = _split_body(str(rec.get("body") or ""))
        buf = ""
        idx = 0
        for para in [*paragraphs, ""]:
            if buf and (len(buf) + len(para) > chunk_target or not para):
                chunks.append(
                    IndexChunk(
                        asset_id=doc_id,
                        chunk_id=f"{doc_id}-c{idx:03d}",
                        language=language,
                        snippet=buf,
                        evidence=evidence,
                        is_external=True,
                        source_name=source_name,
                        effective_date=effective_date,
                        expiry_date=expiry_date,
                    )
                )
                idx += 1
                buf = ""
            if para:
                buf = f"{buf} {para}".strip() if buf else para
    return chunks
