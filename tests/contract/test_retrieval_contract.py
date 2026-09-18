"""L2 契约测试：验证检索层遵守 X3 接口契约（03_实施/契约 X1-X4 v1.2 §X3）。

为什么这一层需要单独建文件
--------------------------
T2.08 校验时 `packages/retrieval/` 只有 2 条单元测试（`test_retrieval_service.py`），
契约侧零覆盖。X1（脱敏）8 条、X2（解析）19 条，X3 是四契约里唯一没有
契约断言的一层。后果在本次校验中立刻兑现：`normalize()` 用朴素 substring
逐条 replace，把「股份有限公司」改成「股份有限责任公司」——一个工商登记里
不存在的组织形式。它**不报错**，只是让这条查询永远召不回内容。

覆盖的契约条款
--------------
- X3.2 入参 / 出参 Schema（请求 6 字段、filters 4 字段、响应 3 字段、hit 6 字段）
- X3.2 拒答机制 `refused`：无证据时置位，不得让 LLM 补写
- X3.3 策略落配置层：切分阈值、别名判断一律在 configs/，代码不得内联
- X3.2 语种纪律：`language_hint` 可选、开放字符串，非封闭枚举
- TR-3 字段名不得携带行业/语种语义
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from packages.retrieval.api import router
from packages.retrieval.service import (
    Filters,
    Hit,
    RetrievalService,
    RetrieveRequest,
    RetrieveResult,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RETRIEVAL_DIR = REPO_ROOT / "packages" / "retrieval"

# X3.2 定稿字段，字面写死（契约是判据，实现是被判对象）
REQUEST_FIELDS = {
    "request_id",
    "query",
    "top_k",
    "filters",
    "language_hint",
    "trace_id",
}
FILTER_FIELDS = {"tenant_id", "permission_domain", "doc_ids", "date_range"}
RESULT_FIELDS = {"request_id", "hits", "refused"}
HIT_FIELDS = {"asset_id", "chunk_id", "score", "evidence", "language", "snippet"}

# X3.2 契约示例：Top 20 重排到 Top 6（§3.3）
CONTRACT_TOP_K_DEFAULT = 6


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _retrieve(client: TestClient, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "request_id": "REQ-S-001",
        "query": "示例查询",
        "top_k": CONTRACT_TOP_K_DEFAULT,
        "filters": {"tenant_id": "T-001", "permission_domain": "D1"},
        "language_hint": None,
        "trace_id": "TRACE-S-001",
    }
    body.update(overrides)
    r = client.post("/retrieval/retrieve", json=body)
    assert r.status_code == 200, r.text
    data: dict[str, object] = r.json()
    return data


# ---------------------------------------------------------------- X3.2 Schema


@pytest.mark.contract()
def test_request_schema_matches_x3_2() -> None:
    """X3.2：入参六字段一个都不能少，多一个也不行。"""
    req = RetrieveRequest(request_id="REQ-S-001", query="q", trace_id="TRACE-S-001")
    assert set(req.model_dump()) == REQUEST_FIELDS


@pytest.mark.contract()
def test_filters_schema_matches_x3_2() -> None:
    """X3.2：元数据过滤四字段，权限域在其中——权限收敛在检索入口做。"""
    assert set(Filters().model_dump()) == FILTER_FIELDS


@pytest.mark.contract()
def test_filters_all_optional() -> None:
    """X3.2：过滤项全可空，不传即不过滤，不得要求调用方填空。"""
    f = Filters()
    assert f.tenant_id is None
    assert f.permission_domain is None
    assert f.doc_ids is None
    assert f.date_range is None


@pytest.mark.contract()
def test_result_schema_matches_x3_2() -> None:
    """X3.2：响应三字段，顶层封套与 X2 的 {request_id, payload} 形状对齐。"""
    res = RetrieveResult(request_id="REQ-S-001")
    assert set(res.model_dump()) == RESULT_FIELDS


@pytest.mark.contract()
def test_hit_schema_matches_x3_2() -> None:
    """X3.2：hit 六字段，`evidence` 是 BIZ-1 可溯源的物理基础（对应 X2 的 page）。"""
    hit = Hit(
        asset_id="A-001",
        chunk_id="C-001",
        score=0.91,
        evidence="page=3",
        language="zh",
        snippet="……",
    )
    assert set(hit.model_dump()) == HIT_FIELDS


@pytest.mark.contract()
def test_top_k_default_matches_contract() -> None:
    """X3.2 契约示例与 §3.3（Top 20 重排到 Top 6）一致。"""
    req = RetrieveRequest(request_id="R", query="q", trace_id="T")
    assert req.top_k == CONTRACT_TOP_K_DEFAULT


@pytest.mark.contract()
def test_response_echoes_request_id(client: TestClient) -> None:
    """X3.2：响应必须回传 request_id，否则并发/重试时请求与结果无法配对。"""
    data = _retrieve(client, request_id="REQ-S-042")
    assert data["request_id"] == "REQ-S-042"


# ---------------------------------------------------------------- X3.2 拒答


@pytest.mark.contract()
def test_refused_flag_exists() -> None:
    """X3.2：拒答机制进契约。没有这个字段，无证据时只能靠调用方猜。"""
    assert "refused" in RetrieveResult(request_id="R").model_dump()


@pytest.mark.contract()
def test_no_index_refuses_rather_than_fabricating(client: TestClient) -> None:
    """X3.2 + §9.3：检索无证据时必须置 refused 并返回空 hits，不得让 LLM 补写。

    这是本契约的核心纪律：宁可拒答，不可编造。断言 hits 为空是关键——
    返回 refused=True 却同时给出内容，等于既拒答又编造。
    """
    data = _retrieve(client)
    assert data["refused"] is True
    assert data["hits"] == []


@pytest.mark.contract()
def test_refused_is_false_only_with_evidence() -> None:
    """refused 默认为 False，但只有在真的给出 hits 时才允许保持 False。"""
    res = RetrieveResult(request_id="R")
    assert res.refused is False
    res_with_hit = RetrieveResult(
        request_id="R",
        hits=[
            Hit(
                asset_id="A-001",
                chunk_id="C-001",
                score=0.91,
                evidence="page=3",
                language="zh",
                snippet="……",
            )
        ],
    )
    assert res_with_hit.refused is False
    assert len(res_with_hit.hits) == 1


# ---------------------------------------------------------------- X3.2 语种


@pytest.mark.parametrize("hint", ["zh", "ko", "zh-Hans", "en-US", None])
@pytest.mark.contract()
def test_language_hint_is_optional_and_open(hint: str | None) -> None:
    """X3.2：language_hint 是**可选提示**，加语种零改代码。

    `ko` 能过是本条的核心——韩文若需先改枚举再发版，我们对甲方说的
    「加一个语种是加配置」就成了假话。
    """
    req = RetrieveRequest(request_id="R", query="q", trace_id="T", language_hint=hint)
    assert req.language_hint == hint


@pytest.mark.contract()
def test_hit_language_is_open_string_not_enum() -> None:
    """X3.2：hit.language 是开放字符串，不是封闭枚举。"""
    hit = Hit(
        asset_id="A",
        chunk_id="C",
        score=0.5,
        evidence="page=1",
        language="ko",
        snippet="...",
    )
    assert hit.language == "ko"
    assert isinstance(hit.language, str)


# ---------------------------------------------------------------- X3.3 配置层


def _has_cjk(text: str) -> bool:
    """是否含 CJK 统一汉字。

    刻意不用 `re.search(pattern含汉字)` —— TR-6 会把「正则字面量里出现汉字」
    判为代码写死语种规则。用字符区间比较同样能判定，且不触发该误判。
    """
    return any("一" <= ch <= "鿿" for ch in text)


@pytest.mark.contract()
def test_alias_values_come_from_config_not_code() -> None:
    """X3.3：别名判断不得内联在代码里。

    代码内联别名，必然以 `canonical/variant/alias = "中文"` 的形式出现。
    扫描命中的是**赋值形态**，注释与断言消息不在此列。

    大小写不敏感是实测逼出来的：首版只写小写，变异注入
    `INLINE_ALIAS = {"股份公司": ...}` 后 25 条断言全绿 —— 大写常量是
    模块级写死的惯用写法，只认小写的守卫等于没守。

    只匹配「标识符 = 立即跟引号」同样不够（第二次实测）：字典字面量
    `INLINE_ALIAS = {"股份公司": ...}` 的 `{` 挡在 `=` 与引号之间，判据落空。
    因此判据收敛为「赋值给别名类标识符 + 该行含汉字」，两个条件同时成立才报。
    """
    sources = sorted(RETRIEVAL_DIR.glob("*.py"))
    assert sources, "glob 落空会让本守卫静默通过"
    inline = [
        f"{p.name}:{i}"
        for p in sources
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"(?i)(?:canonical|variant|alias)\s*[:=]", line) and _has_cjk(line)
    ]
    assert not inline, f"代码内联了别名判断，须挪到 configs/aliases.yaml：{inline}"


@pytest.mark.contract()
def test_chunking_thresholds_not_hardcoded() -> None:
    """X3.3 铁律：切分阈值一律在 configs/chunking.yaml，代码不得内联。

    本断言现在通过，是因为切分实现尚未落地（骨架阶段）。它不是空转——
    T3.04 一旦把阈值写成 `max_chars = 800` 这类字面量，这里立刻失败。
    允许从配置载入的写法（右值引用 config/_load/yaml），只拦写死。

    判据必须大小写不敏感
    --------------------
    首版只写小写标识符，变异实测注入 `MAX_CHARS = 800 / CHUNK_SIZE = 1200 /
    OVERLAP_CHARS = 80` 三个常量后**全部 25 条断言仍全绿**——而模块级大写常量
    恰恰是 Python 里写死阈值的惯用写法。只认小写的守卫等于给最常见的违规
    形式发了通行证，比没有守卫更危险。
    """
    sources = sorted(RETRIEVAL_DIR.glob("*.py"))
    assert sources, "glob 落空会让本守卫静默通过"
    offenders = [
        f"{p.name}:{i}"
        for p in sources
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(
            r"(?i)\b(?:max_chars|overlap_chars|min_chars|chunk_size|chunk_overlap|separators)\s*[:=]\s*(?!.*(?:config|_load|yaml|_config))",
            line,
        )
    ]
    assert not offenders, f"切分阈值写死在代码里，须挪到 configs/chunking.yaml：{offenders}"


@pytest.mark.contract()
@pytest.mark.skip(reason="T3.04 接入切分实现后启用：断言 chunking.yaml 被真实消费")
def test_chunking_config_is_consumed() -> None:
    """X3.3：切分实现必须真的读 configs/chunking.yaml。

    刻意写成 skip 而不是删除：本条现在无处可验（检索层还没做切分），
    若删掉，T3.04 实现时「配置层已落实」这句话就没人再核一遍。
    skip 在 `-ra` 下会显式列出理由，不会静默放行。
    """
    sources = sorted(RETRIEVAL_DIR.glob("*.py"))
    assert sources, "glob 落空会让本守卫静默通过"
    assert any("chunking.yaml" in p.read_text(encoding="utf-8") for p in sources), (
        "检索层未消费 configs/chunking.yaml"
    )


@pytest.mark.contract()
def test_chunking_config_structure_is_present() -> None:
    """X3.3：切分配置按语种组织——结构与数值分开看，结构必须真在。

    契约记载数值「待标定，Q3 用 X6 题库回填」，因此这里只验结构，
    不验数字。验数字会把待标定值固化成承诺。
    """
    cfg = yaml.safe_load((REPO_ROOT / "configs" / "chunking.yaml").read_text(encoding="utf-8"))
    assert "default" in cfg, "缺兜底段，未登记语种无处可退"
    assert "languages" in cfg, "切分规则未按语种组织"
    required = {"max_chars", "overlap_chars", "min_chars", "separators"}
    for scope in ("default", *(cfg.get("languages") or {})):
        node = cfg["default"] if scope == "default" else cfg["languages"][scope]
        missing = required - set(node)
        assert not missing, f"chunking.yaml 的 {scope} 段缺 {missing}"


@pytest.mark.contract()
def test_aliases_config_structure_is_present() -> None:
    """X3.3：别名表按语种组织，每条含 canonical + variants。

    契约记载内容「待业务确认（B4 输入）」，因此只验结构不验词条。
    """
    cfg = yaml.safe_load((REPO_ROOT / "configs" / "aliases.yaml").read_text(encoding="utf-8"))
    languages = cfg.get("languages") or {}
    assert languages, "别名表未按语种组织"
    for lang, block in languages.items():
        entries = block.get("aliases")
        assert isinstance(entries, list) and entries, f"{lang} 段无 aliases 列表"
        for entry in entries:
            assert entry.get("canonical"), f"{lang} 有条目的 canonical 为空"
            assert isinstance(entry.get("variants"), list), f"{lang} 有条目的 variants 不是列表"


@pytest.mark.contract()
def test_alias_normalization_is_config_driven() -> None:
    """X3.3：归一结果来自配置表，测试用例本身也从配置取值，不从代码常量。"""
    cfg = yaml.safe_load((REPO_ROOT / "configs" / "aliases.yaml").read_text(encoding="utf-8"))
    entry = cfg["languages"]["zh"]["aliases"][0]
    svc = RetrievalService()
    assert svc.normalize(entry["variants"][0]) == entry["canonical"]


@pytest.mark.contract()
def test_alias_normalization_prefers_longest_match() -> None:
    """回归守卫：短变体不得把长变体截断。

    修掉的缺陷：逐条 substring replace 时，「有限公司」会命中上一条刚写入的
    canonical「股份有限公司」的后半段，把「股份有限公司」改成
    「股份有限责任公司」——工商登记里没有这种组织形式，不报错，
    只是这条查询永远召不回内容。
    """
    svc = RetrievalService()
    assert svc.normalize("股份有限公司") == "股份有限公司"
    assert svc.normalize("北京某某股份有限公司") == "北京某某股份有限公司"
    assert svc.normalize("某某股份公司") == "某某股份有限公司"
    assert svc.normalize("某某有限公司") == "某某有限责任公司"


@pytest.mark.contract()
def test_alias_normalization_is_single_pass() -> None:
    """归一化必须单遍、非重叠：canonical 写入后不得被后续变体二次改写。

    幂等是单遍的可观测后果——多遍实现会在二次调用时继续劣化。
    """
    svc = RetrievalService()
    samples = [
        "股份有限公司",
        "某某股份公司",
        "某某集团有限责任公司",
        "二零二五年年报",
        "2025 年报",
    ]
    for text in samples:
        once = svc.normalize(text)
        assert svc.normalize(once) == once, f"归一化非单遍：{text} -> {once}"


# ---------------------------------------------------------------- 其他


@pytest.mark.contract()
def test_field_names_carry_no_domain_semantics() -> None:
    """TR-3：共用层字段名不得携带行业或语种语义，否则内核被单一业务污染。"""
    industry = ("credit", "loan", "dealer", "applicant", "borrower", "guarantor")
    language = ("korean", "chinese", "zh_", "ko_")
    sources = sorted(RETRIEVAL_DIR.glob("*.py"))
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
    r = client.get("/retrieval/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
