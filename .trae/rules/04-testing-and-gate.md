---
description: 测试纪律与门禁纪律。写代码、改测试、改门禁脚本、提交前必须遵守。
alwaysApply: true
---

# 04 · 测试与门禁纪律

## 五层测试金字塔

| 层 | 内容 | 命令 |
|---|---|---|
| L0 静态 | ruff / mypy strict | `make lint` `make type` |
| L1 单测 | `tests/unit/` 纯函数 | `make test` |
| L2 契约 | `tests/contract/` 模型网关契约 | `make test` |
| L3 集成 | `tests/integration/` | `make test` |
| L4 Golden | `tests/golden/` **仅 E1 跑** | — |
| L5 门禁 | 6 条常检 | `make gate` |

## 测试期望值的纪律（最重要的一条）

**所有期望值一律取 `60.90.03` 的文档字面值，严禁从被测常量取。**

```python
# ✗ 错：让被测代码给自己发通行证
assert max_tokens("cpu") == MAX_OUTPUT_TOKENS["cpu"]  # 常量改了期望值同步改，断言永远成立

# ✓ 对：写死文档字面值
assert max_tokens("cpu") == 2000  # 60.90.03 §3.6
```

**实证**：CPU 上限从 2000 改到 4000，引用常量的断言静默通过，写死字面值的断言立刻抓到。

## 门禁纪律

### 判据源唯一

`tools/gate/gate_baseline.json`。**改它＝改门禁，禁止为通过而放宽。**
放一次，后面所有合规论据全废。

### 改门禁脚本后必跑自检

```bash
uv run python tools/gate/gate_check.py selftest
```

自检会造一个含违规的样例仓库，验证六条检查真的抓得到。
**门禁脚本本身失效却无人察觉，是最危险的失效方式** —— 比没有门禁更危险，因为它给出虚假的安全感。

### 每条新断言必须做变异测试

**不会失败的断言，等于这条判据还没被验证过。**

做法：把修复改回缺陷态 → 断言必须变红 → 还原 → 复绿。

⚠️ **变异脚本在变异点定位不唯一时必须硬失败。** 否则替换未生效、selftest 照样打印 PASS，
你会据此判定一条改动安全 —— 这是本项目的真实教训（2026-09-03）。

### 「未找到」一律判 SKIP，不判 PASS

空目录、空清单不等于安全。**「没查到」不等于「没有」。**

## 提交纪律

```bash
git add -A && git commit    # 钩子会自动跑 10 项检查
```

⚠️ **门禁脚本自身在改动中时，必须与被检内容同批提交。**
pre-commit 会 stash 未暂存文件，拆两笔提交时钩子跑的是**已提交的旧脚本**去检查新内容 ——
会报出旧判据的错误，或漏掉新判据要抓的问题。

## 提交信息格式

```
<type>(<scope>): <一句话> [T-XXX]

<body>：为什么这么改，不写改了什么（diff 里有）
```

type：`feat` / `fix` / `chore` / `refactor` / `test` / `docs`
