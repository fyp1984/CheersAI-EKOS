---
description: TR-6 语种未写死在内核 + P1/P2/P3 三处结构预留。写切分、别名、脱敏策略、schema 时必须遵守。
globs: packages/**/*.py,packages/**/*.json,configs/**,apps/**/*.py
alwaysApply: false
---

# 02 · 语种结构预留（TR-6 / P1·P2·P3）

## 背景

**本期只做中文，韩文全项暂缓**（2026-09-03 用户拍板）。但我们对甲方说过：
「加一个语种是加配置，不是改系统」。

**这句话是实话的前提，是 P1/P2/P3 真的在。** 三处预留已于 2026-09-03 落地，
现在要防的是**有人图省事把中文规则写死回代码里**——那等于把下期的成本
从「加配置」变成「重构」。

## 三条硬约束

### P1 · 解析输出 schema 的 `language` 字段

- 文件：`packages/document/schema.py`
- 要求：保留 `language` 字段，类型用**开放字符串 + ISO 639-1 模式校验**
- **禁止改成枚举**（`class Language: ZH = "zh"`）——枚举一闭合，加语种就得改代码发版本，留口子等于没留
- **禁止写 `DEFAULT_LANGUAGE = "zh"` 常量**——默认值由 `configs/runtime.yaml` 提供
- 本期实测恒填 `zh`

### P2 · 切分规则与别名表落配置层

- 文件：`configs/chunking.yaml`、`configs/aliases.yaml`、`configs/runtime.yaml`
- 要求：**按语种键组织**，含 `default` 兜底档（新语种未登记时按兜底跑，不 KeyError）
- **禁止**在代码里写中文切分正则、内联别名判断、`if lang == "zh"` 分支

### P3 · 脱敏策略表带 `lang` 列

- 文件：`packages/vault/masking_policy.json`
- 要求：每条规则都有 `lang` 列
- 理由：识别模式与语种强相关（中文姓名无空格、身份证固定 18 位），
  不分语种的表加语种就得重写整表

## 测试怎么咬住它

`tests/unit/test_language_reservation.py` 有 10 个用例，均经变异验证：

- 把 `LANGUAGE_PATTERN` 收紧为 `^zh$` → 4 个用例立刻红
- 删掉一条脱敏规则的 `lang` → 1 个用例立刻红

**改这三处文件时，先跑这个测试。**

## 常见错误

```python
# ✗ 错：语种判断分支焊死在代码里
if lang == "zh":
    splitter = ChineseSplitter()

# ✗ 错：中文切分正则写死
PATTERN = r"[。；;]"

# ✗ 错：默认语种写成常量
DEFAULT_LANGUAGE = "zh"

# ✓ 对：规则从配置层读，代码不知道有哪些语种
splitter = build_splitter(config_for(lang))  # 未登记的语种走 default 档
```
