"""L2 契约测试：验证底座分工决策记录（T2.03 / Q1 判据 TR-5）。

为什么这一层需要单独建文件
--------------------------
TR-5 原本是**纯人工签字项**（`gate_baseline.json` 里 `"type": "manual"`），判据
文本是「选型决策记录能说清 RAGFlow 承担哪几环、勤思侧自研哪几环」。它是 Q1 四项
专项判据里唯一没有自动化覆盖的一条——T2.03 之前，`记录/` 下根本不存在这份决策
记录，而门禁照样报 PASS，因为「人工项未签字」和「人工项不存在」在输出里长得一样。

更值得警惕的是**分工表可以纯纸面成立**：八环填满、集成方式写 REST、看起来与
§5.4 完全吻合，但没有任何一环被真正调用过。本轮实测发现 RAGFlow 三环在既有实例
上**能力存在但从未被本仓库调用**（`packages/` 下无 RAGFlow 集成代码，无本轮自有
实例），所以分工表里的每一环都标注了实证状态，本文件守住「标注不得为空、不得被
悄悄改成已验证」。

本文件的四条纪律
----------------
- **八环归属字面写死**（`RAGFLOW_RINGS` / `SELF_BUILT_RINGS`），不从决策记录导入：
  决策记录是被判对象，拿它的值验证它自己等于自己给自己判卷。
- **「未找到」一律 SKIP，绝不 PASS**：决策文档在仓库外，不可达时 SKIP 并写明原因。
- **glob 落空 / 解析零行即 FAIL**：静默通过比报错更危险。
- **判据按真实字符串形态写**：文档表格的列顺序、编号写法（①–⑩）都取自实际文件，
  不按想象写（T2.08 漏大写常量、T2.09 漏 `${VAR:-默认}` 自带冒号，均因此）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# 八环归属——权威口径：推进方案 v2.2 §5.2「允许的最小适配」表第一行。
# 改动此表 = 改分工决策，须走甲方变更流程，不得为通过测试而放宽。
RAGFLOW_RINGS = ["连接器", "深度解析", "三索引检索"]
SELF_BUILT_RINGS = ["事实层", "规则层", "模型网关", "报告 Skill", "溯源与日志"]
ALL_RINGS = RAGFLOW_RINGS + SELF_BUILT_RINGS

# 集成方式里出现这些词 = 动了 vendor 代码，违反 §5.4「不 fork、不改 core」
VENDOR_MODIFICATION_WORDS = ["fork", "改 core", "改core", "patch", "monkeypatch", "改源码"]

# PRD §8.4 准出十条编号（字面写死，不从文档导入）
EXIT_CRITERIA_MARKS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


def _decision_doc() -> Path | None:
    """定位分工决策记录。仓库外文件，不可达时返回 None（调用方须 SKIP）。"""
    env = os.environ.get("EKOS_BASE_DECISION_DOC")
    if env:
        p = Path(env)
        return p if p.is_file() else None
    candidates = sorted(
        Path.home().glob(
            "Documents/@西北人的成长/10-Projects/项目商机/01_商机项目/现代金租/03_实施/决策记录/底座分工决策记录-内部-*.md"
        )
    )
    return candidates[-1] if candidates else None


def _extract_section(text: str, start: str, end: str) -> str:
    """取两个标题之间的正文。start/end 为行首匹配的标题文本。"""
    lines = text.splitlines()
    try:
        i = next(n for n, ln in enumerate(lines) if ln.startswith(start))
    except StopIteration:
        return ""
    for n in range(i + 1, len(lines)):
        if lines[n].startswith(end):
            return "\n".join(lines[i + 1 : n])
    return "\n".join(lines[i + 1 :])


def _table_rows(section: str) -> list[list[str]]:
    """解析 markdown 表格行，返回单元格列表（已 strip）。跳过分隔行与表头。"""
    rows: list[list[str]] = []
    for ln in section.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # 分隔行
        rows.append(cells)
    return rows


def _ring_rows(text: str) -> list[list[str]]:
    """取八环分工表的数据行（跳过表头）。列：# 环节 承担方 谁提供 集成方式 失败替 实证状态"""
    section = _extract_section(text, "### 1.1 八环分工表", "### 1.2")
    assert section, "未找到「1.1 八环分工表」章节"
    rows = [r for r in _table_rows(section) if len(r) >= 7 and r[0].isdigit()]
    return rows


# ---------------------------------------------------------------- 判据 1


@pytest.mark.contract()
def test_decision_doc_version_consistent() -> None:
    """版本号三处一致：①文件名 ② front-matter `version` ③ 正文一级大标题。

    只查 front-matter 会漏正文大标题（历史踩坑：EKOS 技术方案文件名与 front-matter
    已升 v2.1，正文大标题仍写 v2.0，校验脚本却报全部通过）。
    """
    doc = _decision_doc()
    if doc is None:
        pytest.skip("未定位到分工决策记录，设 EKOS_BASE_DECISION_DOC 环境变量后本断言生效")

    text = doc.read_text(encoding="utf-8")
    fn = re.search(r"-v(\d+\.\d+)\.md$", doc.name)
    fm = re.search(r"(?m)^version:\s*(\S+)", text)
    h1 = re.search(r"(?m)^#\s+.*?v(\d+\.\d+)\s*$", text)
    assert fn, f"文件名未带版本号：{doc.name}"
    assert fm, "front-matter 缺 version 字段"
    assert h1, "正文一级大标题未带版本号"

    vals = {fn.group(1), fm.group(1).lstrip("v"), h1.group(1)}
    assert len(vals) == 1, f"版本号三处不一致（文件名/front-matter/正文大标题）：{vals}"


# ---------------------------------------------------------------- 判据 2


@pytest.mark.contract()
def test_eight_rings_all_assigned() -> None:
    """八环齐全且每环六列非空——任务卡核验第一条「八环均有归属，无空白」。

    六列 = 编号 / 环节 / 承担方 / 谁提供 / 集成方式 / 失败时怎么替 / 实证状态。
    空单元格是最容易出现的填充失败：表格看着齐，实际某一环没有失败替代方案。
    """
    doc = _decision_doc()
    if doc is None:
        pytest.skip("未定位到分工决策记录，设 EKOS_BASE_DECISION_DOC 环境变量后本断言生效")

    rows = _ring_rows(doc.read_text(encoding="utf-8"))
    assert len(rows) == 8, f"八环分工表应有 8 行，实际 {len(rows)} 行"

    blanks: list[str] = []
    for r in rows:
        for idx, cell in enumerate(r[:7]):
            if not cell or cell in {"-", "—", "待定"}:
                blanks.append(f"第{r[0]}环第{idx + 1}列为空（{r[1] if len(r) > 1 else '?'}）")
    assert not blanks, f"分工表存在空单元格：{blanks}"

    names = [r[1] for r in rows]
    assert names == ALL_RINGS, f"八环名称/顺序与权威口径不符：{names} vs {ALL_RINGS}"


# ---------------------------------------------------------------- 判据 3


@pytest.mark.contract()
def test_ring_ownership_matches_authoritative_baseline() -> None:
    """三环归 RAGFlow、五环归勤思自研——分工口径不得漂移。

    期望值字面写死在 `RAGFLOW_RINGS` / `SELF_BUILT_RINGS`，不从决策记录导入。
    """
    doc = _decision_doc()
    if doc is None:
        pytest.skip("未定位到分工决策记录，设 EKOS_BASE_DECISION_DOC 环境变量后本断言生效")

    rows = _ring_rows(doc.read_text(encoding="utf-8"))
    assert len(rows) == 8, f"八环分工表应有 8 行，实际 {len(rows)} 行"

    wrong: list[str] = []
    for r in rows:
        ring, owner = r[1], r[2]
        if ring in RAGFLOW_RINGS and "RAGFlow" not in owner:
            wrong.append(f"{ring} 应归 RAGFlow，实际写 {owner!r}")
        if ring in SELF_BUILT_RINGS and "勤思自研" not in owner:
            wrong.append(f"{ring} 应归勤思自研，实际写 {owner!r}")
    assert not wrong, f"分工归属与推进方案 v2.2 §5.2 口径不符：{wrong}"


# ---------------------------------------------------------------- 判据 4


@pytest.mark.contract()
def test_integration_mode_excludes_vendor_modification() -> None:
    """集成方式不得含 fork / 改 core / patch——核验第二条「无 vendor 内修改」。

    扫描的是**整行**（承担方 + 谁提供 + 集成方式 + 失败替代全部在内），因为
    「失败时怎么替」里最容易顺手写「改 RAGFlow 源码」。
    """
    doc = _decision_doc()
    if doc is None:
        pytest.skip("未定位到分工决策记录，设 EKOS_BASE_DECISION_DOC 环境变量后本断言生效")

    text = doc.read_text(encoding="utf-8")
    section = _extract_section(text, "### 1.1 八环分工表", "### 1.2")
    rows = _ring_rows(text)

    offenders: list[str] = []
    for r in rows:
        joined = " ".join(r).lower()
        for word in VENDOR_MODIFICATION_WORDS:
            if word.lower() in joined:
                offenders.append(f"第{r[0]}环（{r[1]}）出现 {word!r}")
    assert not offenders, f"分工表出现修改 vendor 代码的写法，违反 §5.4：{offenders}"

    # 不做清单里必须明写不 fork、不改 core
    no_do = _extract_section(text, "### 1.2 明确不做的事", "### 1.3")
    assert no_do, "未找到「1.2 明确不做的事」章节"
    assert "不 fork" in no_do, "「明确不做」清单未写「不 fork」"
    assert "不改" in no_do and "core" in no_do, "「明确不做」清单未写「不改 core」"
    assert section, "未找到八环分工表章节"


# ---------------------------------------------------------------- 判据 5


@pytest.mark.contract()
def test_exit_criteria_mapping_is_complete() -> None:
    """准出十条编号齐全且状态列非空——TR-5 判据「写得出编号与当前状态」。

    准出十条来自 PRD §8.4。编号用 ①–⑩ 圆圈数字，**不是** 1–10 阿拉伯数字，
    按文档真实写法匹配（T2.09 教训：判据要按真实字符串形态写）。
    """
    doc = _decision_doc()
    if doc is None:
        pytest.skip("未定位到分工决策记录，设 EKOS_BASE_DECISION_DOC 环境变量后本断言生效")

    text = doc.read_text(encoding="utf-8")
    section = _extract_section(text, "### 1.4 本门禁推进了 EKOS 准出十条中的哪几条", "## 二、")
    assert section, "未找到「1.4 准出十条推进映射」章节"

    found = [m for m in EXIT_CRITERIA_MARKS if m in section]
    missing = [m for m in EXIT_CRITERIA_MARKS if m not in section]
    assert not missing, f"准出十条映射缺编号：{missing}"
    assert len(found) == 10, f"准出十条应逐条列出，实际命中 {len(found)} 条"

    empty_state: list[str] = []
    for ln in section.splitlines():
        if not ln.strip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 4 or not cells[0] or cells[0] == "准出#":
            continue
        if cells[0] in EXIT_CRITERIA_MARKS and (not cells[2] or not cells[3]):
            empty_state.append(f"{cells[0]} 的推进/状态列为空")
    assert not empty_state, f"准出十条映射存在空状态：{empty_state}"


# ---------------------------------------------------------------- 判据 6


@pytest.mark.contract()
def test_t1_07_gap_is_recorded_not_silently_closed() -> None:
    """T1.07「可建数据集」未实证这一缺口必须留在记录里，不得被悄悄抹掉。

    T1.07 判据是「容器 healthy + 可建数据集 + API 可调」，实测只达成前两项
    （healthy + API 端点鉴权返回 401，带双对照实验）。第三项需要 API Key，而既有
    实例是在用试验环境，写入即污染。这个缺口如果被删，后面的人会以为三环已验完。
    """
    doc = _decision_doc()
    if doc is None:
        pytest.skip("未定位到分工决策记录，设 EKOS_BASE_DECISION_DOC 环境变量后本断言生效")

    text = doc.read_text(encoding="utf-8")
    assert "附件 C" in text, "决策记录缺附件 C（T1.07 判据缺口），缺口不得静默消失"
    assert "可建数据集" in text, "未写明 T1.07 未达成的具体判据项"
    assert "未实证" in text, "未把「未实证」状态写实（写成「已完成」等于伪造验收）"

    # 八环表里 RAGFlow 三环不得标成已验证——E0 从未调用过
    rows = _ring_rows(text)
    overstated: list[str] = []
    for r in rows:
        if r[1] in RAGFLOW_RINGS and len(r) >= 7:
            status = r[6]
            if "已验证" in status or "已跑通" in status or "已实测" in status:
                overstated.append(f"{r[1]} 实证状态写 {status!r}，但 E0 从未调用")
    assert not overstated, f"RAGFlow 三环实证状态被拔高：{overstated}"
