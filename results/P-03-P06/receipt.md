# P-03 + P-06 并联任务回执

- 任务号：P-03（T-D1/T-D2 权限+留痕）+ P-06（T-C1/T-C2/T-C5 三检索+证据链+双层过滤）
- 状态：已完成（准出条件全部满足）
- 分支：`feature/P03-P06-permission-retrieval-evidence`
- 执行日期：2026-09-19
- 基准分支：`feature/P02-data-ingestion-parse-X2`

---

## 代码仓改动（绝对路径）

P-03 T-D1 权限模型适配器（新建）

- [types.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/policy/types.py) — Actor / Resource / AccessCheck / AccessDecision / FilteredResources
- [adapter.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/policy/adapter.py) — PolicyAdapter ABC（Zanzibar `resource#relation@subject` 语法）
- [in_memory.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/policy/in_memory.py) — InMemoryPolicyAdapter（4 继承：wildcard / 直接赋权 / group 嵌套递归 / parent 层级继承+循环检测）
- [spicedb.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/policy/spicedb.py) — SpiceDBAdapter（HTTP Check+fully_consistent / Write TOUCH DELETE）
- [factory.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/policy/factory.py) — `build_policy_adapter_from_config()`（按 `backend` 查表 + `${VAR:-default}` env 展开）
- [__init__.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/policy/__init__.py) — re-export 公开类型 + factory

P-03 T-D2 审计留痕（新建）

- [audit.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/common/audit.py) — AuditEvent / AuditLogger / 6 语义接口 / 事件缓冲 / 结构化 JSON 日志
- [log.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/common/log.py) — 保留未改，作为通用 logging 基础设施

P-06 T-C1 / T-C2 / T-C5 检索与证据链（改现有）

- [service.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/retrieval/service.py) — `91 行骨架 → 615 行完整实现`：三路引擎（KeywordBag / VectorSignature / GraphEntityAdjacency）+ CJK 通用 tokenizer + 滑窗 2/3/4 字实体子串 + `_UNSET` 哨兵区分 policy None 跳过 vs 省略参数走 config + RetrieveResult.hits[].evidence 双保险 100% 非空 + refused_reason 三档文案 + 双层权限过滤 L1 list_allowed + L2 逐条 check + 审计埋点
- [api.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/retrieval/api.py) — 保留未改，20 行骨架

配置层（零硬编码端口/凭据）

- [policy.yaml](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/configs/policy.yaml) — default=in-memory；spicedb 走 `SPICEDB_ENDPOINT / SPICEDB_TOKEN / SPICEDB_SCHEME` env 注入
- [retrieval.yaml](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/configs/retrieval.yaml) — 三引擎权重（keyword 0.40 / vector 0.45 / graph 0.15）+ 混合 top_k=8 + min_score=0.05 + 拒答三档文案（无权限 / 无索引 / 低分）

测试

- [test_policy_service.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/tests/unit/test_policy_service.py) — 8 unit（含 T-A7 4 机制：协作者 / wildcard 分享链接 / 组嵌套 / 层级继承）
- [test_audit_log.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/tests/unit/test_audit_log.py) — 6 unit（三要素恒在 / 语义接口 6 种 / 留痕覆盖率）
- [test_retrieval_service.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/tests/unit/test_retrieval_service.py) — 9 unit（三引擎各自召回 / 混合加权 / evidence 非空 / refused_reason / AST 静态解耦守卫：retrieval.service 禁止 import 具体 Adapter 类名）
- [test_retrieval_contract.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/tests/contract/test_retrieval_contract.py) — X3 契约新增 refused_reason 字段 + refused=True 必带原因断言（原 request_id / hits / refused 三字段 0 破坏）
- [test_p03_policy_p06_retrieval.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/tests/integration/test_p03_policy_p06_retrieval.py) — 3 integration：① 有权限端到端；② outsider 0 命中+拒答原因；③ 10 题 Benchmark 强断言 Recall≥0.90 MRR≥0.80

---

## 实测数据（6 指标）

| 指标 | 数值 | Q4 判据下边界 | 是否达标 |
| --- | --- | --- | --- |
| 权限命中率 | 100%（无权限 bob 检索 → 0 hit；有权限 alice/bench-user → 全部非空） | PERM-1：无权限 = 0 hit | 是 |
| 留痕覆盖率 | 100%（每次检索操作 → retrieval.query 事件含 timestamp+actor_id+action） | T-D2：每笔可追到 人/时间/动作 | 是 |
| Recall@8 | 1.0000（10/10 题全部覆盖） | ≥0.90 | 是（超 11pp） |
| MRR@8 | 1.0000（10/10 题首命中位置 = 1） | ≥0.80 | 是（超 25pp） |
| 证据链覆盖率 | 1.0000（所有 returned Hit 的 evidence 字段非空且结构化） | ≥100%（T-C5） | 是 |
| 拒答率（有权限场景） | 0.0000（10 题均有索引+有权限，0 次 refused） | 拒答 100% 带原因即可 | 是 |

10 题明细见 [P-06 中文召回实测表](#p-06-中文召回实测明细表_10题) 与 [benchmark_detail.json](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/results/P-03-P06/benchmark_detail.json)。

---

## make gate 终局结果

```
FAIL 0 / WARN 22 / INFO 3 / SKIP 0 / PASS 15
结论：PASS 通过（无 FAIL）。
```

逐条门控

- Lint (ruff check + format)：All checks passed / 87 files formatted
- Type (mypy strict)：Success — no issues found in 50 source files
- L1 Unit (pytest -m unit)：58 passed
- L2 Contract (pytest -m contract)：69 passed / 3 skipped（T3.04 chunking 接入 / 契约文档 env 未设 — 历史遗留 skip，与本任务无关）
- TR-1 内核零行业词：FAIL 0（integration 内 6 条金租词已改零行业通用词：合规指引 / 合作方 / 异常项目 / 缓冲覆盖 / 资产保障比例）
- TR-2 开源零侵入：PASS（外部 hash 基线 2f0eceb2）
- TR-4 版本零漂移：Python 3.11.10 / FastAPI 0.115.6 / Uvicorn 0.34.0 / Celery 5.4.0 / Redis 7.4.1 / MinIO RELEASE.2024-11-07T00-52-20Z / PyMuPDF 1.25.1 — 全部精确匹配；7 项 E1 组件（Docker/vLLM/PaddleOCR 等）未在版本表中，Q1 出口补
- TR-6 语种未写死：P1/P2/P3 全部在位；16 条测试层语种字面量仅 WARN（schema 留 language 字段为 open string，非 enum；语种别名与切分规则全部落 configs/aliases.yaml chunking.yaml）
- TR-7 端口零硬编码：代码 0 host:port 字面量；SpiceDB endpoint 走 `${SPICEDB_ENDPOINT:-localhost:18443}`（18xxx 段，不碰 8080/5432/6379/9000 冻结段）
- TR-8 样本零真实数据：7 fixture 0 敏感标识命中

---

## 组件与版本

- Python：3.11.10（pyproject.toml L12 冻结，`==` 精确锁）
- FastAPI：0.115.6（对齐 60.90.03 §3.7）
- PyYAML：6.0.1（configs 驱动）
- httpx：0.28.1（SpiceDBAdapter HTTP / 对齐 60.90.03 版本）
- pydantic：2.13.5（RetrieveRequest/Result schema）
- pytest：9.1.1 / mypy：1.15.0 / ruff：0.11.6
- PolicyAdapter InMemory 版本：1.0（4 继承机制 + 循环检测 + ACTION_ROLES 蕴含表）
- PolicyAdapter SpiceDB 版本：1.0（fully_consistent Check / Write TOUCH DELETE）
- RetrievalService 三引擎版本：1.0（keyword bag-of-words overlap 衰减 / 签名伪向量 cosine / 图谱实体 0-hop+1-hop 衰减 0.6）

---

## T-A7 前置补充结论（SpiceDB 表达飞书非层级场景）

SpiceDB Zanzibar 三元组 **完全可表达飞书权限模型 4 类非层级继承**，InMemoryPolicyAdapter 已实现等价子集，E1 切 SpiceDB 时代码零改动：

1. 协作者 = `doc:x#collaborator@user:bob`（ACTION_ROLES.retrieve 含 viewer + collaborator）
2. 分享链接通配符 = `doc:x#share-viewer@user:*`（InMemory `_resolve_wildcard` + SpiceDB subject `user:*`）
3. 组嵌套递归 = `group:sub#member@group:parent`（InMemory DFS 递归 + SpiceDB 原生支持）
4. 层级继承 = `doc:x#parent@space:y` → `space:y#parent@org:z`（InMemory `_walk_parents` 栈循环检测 + SpiceDB `arrow` 语法）

**新增权限源 = 一个 Adapter 类 + factory backend 分支 + config 条目**，内核 `packages/retrieval/service.py` 零改（AST 静态守卫在 test_retrieval_service.py L175-L188 禁止 ImportFrom 出现 SpiceDBAdapter / InMemoryPolicyAdapter 类名）。
