# P-05 交付回执 · 入库即转 MD + 命名规范校验（T-B4 / 准出⑨）

**任务编号：** P-05 / T-B4（准出⑨ = QA-1b，Q3 门禁）
**责任角色：** 研发B
**执行日期：** 2026-09-19
**完成状态：** ✅ PASS
**特性分支：** `feature/P05-md-convert-naming-validate`

---

## 一、代码落点

### 1.1 新增 / 修改文件

| # | 类型 | 绝对路径 | 说明 |
|---|------|----------|------|
| 1 | 新增 | [naming_rules.json](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/configs/naming_rules.json) | 轻量 JSON 规则：命名 + 路径校验（配置层，不硬编码，不引入 OPA/Rego） |
| 2 | 新增 | [ingest.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/document/ingest.py) | 入库流水线主模块：复用 `to_markdown` + 规则加载 + 双域校验（advisory 告警模式） |
| 3 | 修改 | [__init__.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/document/__init__.py) | 导出 `ingest_document` / `IngestResult` / `ValidationAlert` |
| 4 | 新增 | [test_ingest_pipeline.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/tests/unit/test_ingest_pipeline.py) | L1 unit 测试 14 条：含 QA-1b 核心 1:1 对应校验表 |
| 5 | 新增 | [test_ingest_contract.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/tests/contract/test_ingest_contract.py) | L2 contract 测试 5 条：IngestResult 形状 / enforcement 来自配置 / 不修改 ParsedDocument |

### 1.2 复用（非重建）

- **to_markdown：** [parser.py#L398-L424](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/document/parser.py#L398-L424) — 原样复用，未重写
- **契约测试回绿：** [test_document_contract.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/tests/contract/test_document_contract.py) 20 条全绿（0 修改）

---

## 二、规范依据落位

| 条款 | 要求 | 实现位置 | 执行结果 |
|------|------|----------|----------|
| PRD §8.4 准出⑨ | 命名 / 路径违规 100% 告警 | `test_exit_criteria_9_one_to_one_violation_alerts` | ✅ 15 违规 = 15 告警（一一对应） |
| 技术方案 §4.8 | MVP 用轻量 JSON 规则 0.5 天 | [naming_rules.json](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/configs/naming_rules.json) | ✅ JSON 规则，无 OPA/Rego |
| 技术方案 §4.8 | 执行力度 advisory（只告警） | `ingest_document` 不抛异常，返回 `alerts` 列表 | ✅ advisory |
| 红线 | 规则在配置层，不硬编码 | `_RULES_PATH` 指向 configs/naming_rules.json | ✅ 零规则字面量写死在 Python |
| 红线 | 内核零行业词 | TR-1 扫描 60 文件 0 命中 | ✅ PASS |
| 红线 | 无硬编码端口 | TR-7 代码 53 文件 0 host:port | ✅ PASS |
| 红线 | 数据零真实 PII | TR-8 fixtures 扫描 7 文件 0 命中 | ✅ PASS |

---

## 三、QA-1b 核心数据：违规样本 vs 告警数（100% 对应）

**用例：** `test_exit_criteria_9_one_to_one_violation_alerts`（unit 测试断言层）

| 样本 # | doc_id | source_uri | 违规规则（预期） | 实际告警数 | 匹配 |
|---------|--------|------------|-------------------|------------|------|
| 0 | `bad name with spaces` | `minio://bucket/ok.pdf` | naming.pattern + naming.forbidden_substring | 2 | ✅ |
| 1 | `too long bad id x200` | `minio://bucket/ok.pdf` | naming.pattern + naming.forbidden_substring + naming.max_length | 3 | ✅ |
| 2 | `ok_name-1.0` | `ftp://host/internal/file.pdf` | path.scheme_not_allowed | 1 | ✅ |
| 3 | `bad#id` | `noscheme-local.pdf` | path.scheme_missing + naming.pattern | 2 | ✅ |
| 4 | `traverse` | `file:///data/../../secret/path 1/x.pdf` | path.forbidden_substring x2 + path.path_traversal | 3 | ✅ |
| 5 | `slash/in/id` | `file:///data/../etc/x.pdf` | naming.pattern + naming.forbidden_substring + path.forbidden_substring + path.path_traversal | 4 | ✅ |
| **合计** | — | — | **15 条违规** | **15 条告警** | **100%** |

---

## 四、质量门禁（本地实跑结果）

### 4.1 make gate 分解执行（因 Makefile GATE 变量 python 缺失，最后一步已用 uv run 补跑）

| 子目标 | 命令 | 结果 | 计数 |
|--------|------|------|------|
| lint | ruff check + format --check | ✅ PASS | 91 files |
| type | mypy strict | ✅ PASS | 53 files, 0 issues |
| test-unit | pytest tests/unit -m unit -q | ✅ PASS | **72 passed** / 0 failed |
| test-contract | pytest tests/contract -m contract -q | ✅ PASS | **74 passed** / 3 skipped / 0 failed |
| gate-auto（6 条自动化常检） | gate_check.py auto | ✅ **FAIL=0 / PASS=15** | TR-1/2/4/6/7/8 全部 |

### 4.2 6 条自动化常检明细（gate_check.py auto）

| ID | 名称 | 结论 | 关键信息 |
|----|------|------|----------|
| TR-1 | 内核零行业词 | ✅ OK | 60 文件，0 命中 |
| TR-2 | 开源组件零侵入 | ✅ INFO | external hash 基线 2f0eceb… |
| TR-4 | 版本零漂移 | ✅ OK | Python 3.11.10 / FastAPI 0.115.6 / Redis 7.4.1 / MinIO RELEASE.2024-11-07… 9 项全中；pgvector 0.5.0 (PyPI client) vs 0.8.0 (服务端扩展) 为已知同名不同物 WARN（非 FAIL） |
| TR-6 | 语种未写死在内核 | ✅ P1/P2/P3 全在位 | schema language 字段 / configs 切分别名表 / vault masking_policy lang 列 三处预留均存在 |
| TR-7 | 端口零硬编码 | ✅ OK | 代码 53 文件 0 host:port；compose 主机侧均在 15xxx 段 |
| TR-8 | 样本目录零真实数据 | ✅ OK | 7 fixtures 文件，身份证 / 银行卡 / 统一社会信用代码 / 手机号 0 命中 |

---

## 五、设计说明（评审备忘）

1. **为什么用 JSON 不用 YAML**：技术方案 §4.8 明确"轻量 JSON 规则（0.5 天）"，JSON 语法自检更强、不需要额外 schema 验证也能保证类型。
2. **enforcement 为什么也写在 JSON**：将来要切到 blocking 模式，改 JSON 不用重发版本，与"加配置不发版"承诺一致。
3. **IngestResult 不继承 ParsedDocument**：两条链路职责不同。入库结果是"解析后产物 + 校验意见 + MD 文本"的合成值，不是文档对象模型。需要重新解析时调用方仍持有原始 ParsedDocument。
4. **forbidden_substring 为什么是列表、不是单独正则**：规则要给运营同学看，列表项一眼能懂。如果写成一个大正则 ( | | )，配置层就是写死在代码的另一种形式。

---

## 六、交付结论

**T-B4 准出⑨（QA-1b）验证通过：** 命名/路径违规 100% 告警（15:15 一一对应）；make gate 自动化常检 6 条 **0 FAIL**；unit 72 + contract 74 全部通过，原有 X2 契约测试 20 条全绿未破坏。

**后续交接：** 切换 blocking 模式只需把 configs/naming_rules.json 里 `"enforcement": "advisory"` 改成 `"blocking"` 即可；调用方可选地检查 `result.has_alerts and result.enforcement == "blocking"` 决定是否中断入库。
