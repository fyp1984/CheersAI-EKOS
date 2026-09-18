"""L2 契约测试：验证解析层遵守 X2 接口契约（03_实施/契约 X1-X4 v1.2 §X2）。

为什么这一层以前是零覆盖
------------------------
T2.07 校验时发现 `packages/document/` 在整个 tests/ 下**零命中**——
X1（脱敏）有 8 条断言，X2（解析）一条都没有。后果是：X2.4 响应结构
与契约不一致（返回裸 `ParsedDocument`，缺 `request_id` 与 `document` 包装）
这件事，在逐字段肉眼比对时是全绿的，只有实跑才暴露。

覆盖的契约条款
--------------
- X2.2 输出 Schema：`ParsedDocument` 4 字段 + `ParsedBlock` 6 字段
- X2.3 可溯源字段：`parse_status` / `parser_version`
- X2.4 调用契约：响应封套 `{request_id, document}`
- X2.5 语种纪律：开放字符串，非封闭枚举；代码不写死语种常量
- TR-3 字段名不得携带行业/语种语义
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from packages.document.api import ParseRequest, router
from packages.document.schema import ParsedBlock, ParsedDocument
from pydantic import ValidationError

# 契约 X2.2 的字面模式。
# 刻意不从 schema.LANGUAGE_PATTERN 导入：那样等于让被测代码自己给自己发通行证，
# 模式被改宽时测试同步放宽，断言永远成立。契约是判据，代码是被判对象。
CONTRACT_LANGUAGE_PATTERN = r"^[a-z]{2}(?:-[A-Za-z0-9]{1,8})*$"

# X2.2 定稿字段，字面写死
DOCUMENT_FIELDS = ("doc_id", "source_uri", "language", "blocks")
BLOCK_FIELDS = ("block_id", "order", "text", "language", "page", "kind")


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _parse(client: TestClient, request_id: str = "REQ-P-001") -> dict[str, object]:
    body = {
        "request_id": request_id,
        "source_uri": "minio://bucket/doc.pdf",
        "content_type": "application/pdf",
        "policy_id": "default",
        "trace_id": "TRACE-P-001",
    }
    r = client.post("/document/parse", json=body)
    assert r.status_code == 200
    data: dict[str, object] = r.json()
    return data


@pytest.mark.contract()
def test_response_echoes_request_id(client: TestClient) -> None:
    """X2.4：响应必须回传 request_id，否则并发或重试客户端无法配对请求。

    这是本轮修掉的缺陷：实现曾直接返回裸 ParsedDocument，响应里没有任何
    字段能标识「这一条结果对应哪一次请求」，trace 链在解析这一步断掉。
    """
    data = _parse(client, request_id="REQ-P-007")
    assert "request_id" in data, "响应缺 request_id，请求与结果无法配对"
    assert data["request_id"] == "REQ-P-007"


@pytest.mark.contract()
def test_response_wraps_document_in_envelope(client: TestClient) -> None:
    """X2.4：解析结果封装在 document 键下，为后续扩展留出位置。"""
    data = _parse(client)
    assert "document" in data, "响应缺 document 包装，与 X2.4 定稿形状不符"
    assert isinstance(data["document"], dict)


@pytest.mark.contract()
def test_document_fields_match_x2_2(client: TestClient) -> None:
    """X2.2：ParsedDocument 四个字段一个都不能少。"""
    doc = _parse(client)["document"]
    assert isinstance(doc, dict)
    for field in DOCUMENT_FIELDS:
        assert field in doc, f"ParsedDocument 缺契约字段 {field}"


@pytest.mark.contract()
def test_traceability_fields_present(client: TestClient) -> None:
    """X2.3：parse_status / parser_version 是可追溯与可重放的物理基础。

    契约曾记载这两项「待 T3.03 落」，实测实现里已存在——本断言把它们钉住，
    防止后续重构悄悄删掉。
    """
    doc = _parse(client)["document"]
    assert isinstance(doc, dict)
    assert doc["parse_status"] == "ok"
    assert doc["parser_version"]


@pytest.mark.contract()
def test_block_fields_match_x2_2() -> None:
    """X2.2：ParsedBlock 六个字段，页码可空（扫描件可能无页码）。"""
    block = ParsedBlock(
        block_id="b1",
        order=0,
        text="示例文本",
        language="zh",
        page=1,
        kind="text",
    )
    dumped = block.model_dump()
    for field in BLOCK_FIELDS:
        assert field in dumped, f"ParsedBlock 缺契约字段 {field}"
    assert dumped["page"] == 1


@pytest.mark.parametrize("language", ["zh", "ko", "zh-Hans", "en-US"])
@pytest.mark.contract()
def test_language_is_open_string_not_enum(language: str) -> None:
    """X2.5：语种是开放字符串，加语种是加配置，不是改系统。

    `ko` 能过是本条的核心——韩文若需先改枚举再发版，我们对甲方说的
    「加一个语种是加配置」就成了假话。
    """
    doc = ParsedDocument(doc_id="d1", source_uri="s1", language=language, blocks=[])
    assert doc.language == language
    assert re.match(CONTRACT_LANGUAGE_PATTERN, doc.language), (
        f"{language} 未通过契约 X2.2 的语种模式"
    )


@pytest.mark.parametrize("language", ["ZH", "z", "zh-", " chinese", "zhongwen"])
@pytest.mark.contract()
def test_malformed_language_is_rejected(language: str) -> None:
    """开放不等于放任：格式必须是合法 ISO 639-1，大小写与单字母都不接受。"""
    with pytest.raises(ValidationError):
        ParsedDocument(doc_id="d1", source_uri="s1", language=language, blocks=[])


@pytest.mark.contract()
def test_language_pattern_matches_contract_verbatim() -> None:
    """实现所用的模式必须与契约 X2.2 字面一致，私自放宽即违约。"""
    from packages.document.schema import LANGUAGE_PATTERN

    assert LANGUAGE_PATTERN == CONTRACT_LANGUAGE_PATTERN


@pytest.mark.contract()
def test_no_hardcoded_language_constant() -> None:
    """X2.5：默认值由配置层提供，代码里不得出现写死的语种常量。

    写死 `DEFAULT_LANGUAGE = "zh"` 等于把语种信息焊死在代码里——
    加韩文要先改代码发版，与「加语种是加配置」的承诺直接冲突。
    """
    sources = sorted(Path("packages/document").glob("*.py"))
    assert sources, "glob 落空会让本守卫静默通过"
    offenders = [
        f"{p.name}:{i}"
        for p in sources
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"^[A-Za-z_]*(?:LANG|LANGUAGE)[A-Za-z_]*\s*=\s*[\"']", line.strip())
    ]
    assert not offenders, f"代码里写死了语种常量：{offenders}"


@pytest.mark.contract()
def test_field_names_carry_no_domain_semantics() -> None:
    """TR-3：共用层字段名不得携带行业或语种语义，否则内核被单一业务污染。"""
    industry = ("credit", "loan", "dealer", "applicant", "borrower", "guarantor")
    language = ("korean", "chinese", "zh_", "ko_")
    sources = sorted(Path("packages/document").glob("*.py"))
    assert sources, "glob 落空会让本守卫静默通过"

    hits: list[str] = []
    for path in sources:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            matched = re.match(r"^([a-z_][a-z0-9_]*)\s*:\s*[A-Za-z_|\[]", stripped)
            if not matched:
                continue
            field = matched.group(1)
            for term in (*industry, *language):
                if term in field:
                    hits.append(f"{path.name}:{lineno} {field}")
    assert not hits, f"字段名携带行业/语种语义：{hits}"


@pytest.mark.contract()
def test_health_endpoint(client: TestClient) -> None:
    r = client.get("/document/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.contract()
def test_request_model_carries_x2_4_fields() -> None:
    """X2.4 请求侧五字段：request_id 与 trace_id 串联构成可审计链路。"""
    req = ParseRequest(
        request_id="REQ-P-001",
        source_uri="minio://bucket/doc.pdf",
        content_type="application/pdf",
        policy_id="default",
        trace_id="TRACE-P-001",
    )
    assert set(req.model_dump()) == {
        "request_id",
        "source_uri",
        "content_type",
        "policy_id",
        "trace_id",
    }
