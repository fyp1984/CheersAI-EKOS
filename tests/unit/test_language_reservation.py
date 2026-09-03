"""L1 单元测试：语种结构预留（P1 / P2 / P3）。

这批测试只盯一件事：**我们对甲方说过的话，是不是实话。**

原话是「加一个语种是加配置，不是改系统」。这句话值钱的地方在于它可被证伪 ——
要么 schema 收得下配置里没有的语种，要么收不下。收不下，那句话就是空头支票，
而下期加韩文时我们会发现「留了口子」等于没留，成本全回到改架构上。

所以这里的每条断言，防的都是「有人按常规工程审美把它改得更规范」：
    · 把 language 改成封闭枚举（听上去更规范 → 实际把语种焊死在代码里）
    · 把默认值写成模块常量 DEFAULT_LANGUAGE = "zh"（更方便 → 同样焊死）
    · 把脱敏策略表的 lang 列去掉（"本期只有一个语种，留着干嘛" → 下期重写整表）

这三条改动看起来都是改进，实际上都是把口子堵上。测试就是那道闸。

期望值纪律：与 test_modelgw_contract.py 一致 —— 期望值一律写字面值，
不取被测模块的常量，否则常量一改测试跟着改，等于测试给自己发通行证。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from packages.document.schema import ParsedBlock, ParsedDocument

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs"
MASKING_POLICY = REPO_ROOT / "packages" / "vault" / "masking_policy.json"


def _load_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


@pytest.mark.unit()
def test_schema_accepts_a_language_absent_from_config() -> None:
    """加语种 = 加配置，不改系统。

    检验方式：往 schema 塞一个配置里尚不存在的语种代码，schema 必须照收。
    这里的 "ko" 只是「任意合法但未登记」的样本 —— 本期不覆盖韩文，
    测的是结构能不能装下新语种，不是韩文本身。
    """
    block = ParsedBlock(block_id="b1", order=0, text="sample", language="ko")
    assert block.language == "ko"


@pytest.mark.unit()
def test_schema_rejects_malformed_language_code() -> None:
    """模式校验只管格式：非法写法必须被拒，否则配置里的语种会静默写坏。"""
    with pytest.raises(ValueError):
        ParsedBlock(block_id="b1", order=0, text="sample", language="chinese")


@pytest.mark.unit()
@pytest.mark.parametrize("lang", ["zh", "zh-Hans", "ko", "en"])
def test_schema_accepts_all_wellformed_codes(lang: str) -> None:
    """ISO 639-1 主标签 + 可选子标签，一律放行；白名单不是 schema 的职责。"""
    doc = ParsedDocument(doc_id="d1", source_uri="minio://bucket/x.pdf", language=lang)
    assert doc.language == lang


@pytest.mark.unit()
def test_chunking_rules_are_keyed_by_language() -> None:
    """切分规则按语种组织，且有兜底档。

    兜底档的意义：新语种尚未登记粒度时，系统按 default 跑，
    而不是报 KeyError 崩掉 —— 加语种不该是一条会炸的路径。
    """
    cfg = _load_yaml(CONFIG_DIR / "chunking.yaml")
    assert "languages" in cfg, "切分规则未按语种组织，加语种就得改代码"
    assert "zh" in cfg["languages"]
    assert "default" in cfg, "缺兜底档：未登记语种会直接 KeyError"


@pytest.mark.unit()
def test_alias_table_is_keyed_by_language() -> None:
    cfg = _load_yaml(CONFIG_DIR / "aliases.yaml")
    assert "zh" in cfg["languages"]


@pytest.mark.unit()
def test_runtime_config_carries_default_language() -> None:
    """默认值必须在配置里，不在代码里。

    若哪天有人在 schema 里加 DEFAULT_LANGUAGE = "zh"，这条测试不会红 ——
    所以它同时是一条提醒：默认值的位置是设计选择，不是随手一写。
    """
    cfg = _load_yaml(CONFIG_DIR / "runtime.yaml")
    assert cfg["default_language"] == "zh"
    assert "zh" in cfg["supported_languages"]


@pytest.mark.unit()
def test_every_masking_rule_carries_lang() -> None:
    """脱敏策略表每条规则必须带 lang。

    "本期只有一个语种，lang 列留着干嘛" —— 这句话是下期重写整张表的起点。
    lang 在，加语种是加一组规则；lang 不在，加语种是重新设计这张表。
    """
    policy: dict[str, Any] = json.loads(MASKING_POLICY.read_text(encoding="utf-8"))
    rules = policy["rules"]
    assert rules, "策略表为空：结构在位不等于有内容"
    for r in rules:
        assert "lang" in r, f"规则 {r.get('entity', '?')} 缺 lang 列"
