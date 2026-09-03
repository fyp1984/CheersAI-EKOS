---
description: TR-1 内核零行业词。在 packages/ 与 apps/ 下写代码、改注释、写 description 时必须遵守。
globs: packages/**/*.py,apps/**/*.py,tests/**/*.py,pyproject.toml
alwaysApply: false
---

# 01 · 内核零行业词（TR-1）

## 一句话

**内核层出现金租特有术语 = 门禁 FAIL。** 行业特有逻辑一律落 `configs/` 或 `prompts/`。

## 具体约束

1. `packages/` `apps/` `tests/` 下的**代码文件**（`.py`），不得出现行业词，包括：
   - 中文：信贷、授信、征信、经销商、信审、贷前、贷后、放款、还款、逾期、抵押、担保、承租人、租赁物、首付、残值、库存融资、零售金融、对公授信、授信额度、贷后管理、不良贷款、五级分类、汽车金融、融资租赁、金融租赁、客户评级、评级模型、尽调报告、年审报告、6页模板、六页模板、首付比例、残值率
   - 英文：LoanReview、CreditReport、creditLimit
   - **完整词表以 `tools/gate/gate_baseline.json` 的 `TR-1.industry_words` 为准**
2. `pyproject.toml` 的 `name` / `description`、compose 的注释，同样受此约束。
3. **注释与文档字符串现在也排除**（与 TR-6 口径一致，2026-09-03 起）——但这不是写行业词的许可：
   内核一旦自称是信贷系统，EKOS 就已开始退化。**能在注释里不写，就别写。**

## 反例与正例

```python
# ✗ 错：行业词进了内核
def calc_credit_limit(rows: list[dict]) -> float: ...


# ✓ 对：内核用通用语义，行业含义由配置层赋予
def aggregate_fact(field_key: str, rows: list[dict]) -> float: ...
```

## 通用 PII 不算行业词（可以出现在内核）

身份证、银行卡、手机号、统一社会信用代码 —— 这些是**通用个人敏感信息**，不是金租特有，
脱敏内核管它们天经地义，不会触发 TR-1。

## 报错了怎么办

先问「这是不是真的行业特有？」

- 是 → 挪到 `configs/` 或 `prompts/`，内核只留通用能力
- 不是 → 那就是误报，去 `tools/gate/gate_baseline.json` 提变更，**不得在代码里绕过**

**禁止**：加 `# noqa`、拆词拼接（`"授" + "信"`）、用拼音首字母规避。这类规避比违规更糟——
它让门禁失效的同时还留下"检查在跑"的假象。
