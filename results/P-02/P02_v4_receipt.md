# P-02（v4 · 全绝对路径终版）回执

> 生成日期：2026-09-19
> 分支：`feature/P02-data-ingestion-parse-X2`
> 沙箱提示：规划仓 `/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/03_实施/分布式验证-E0/results/P-02/` 不在当前沙箱写权限内。**请人工把本目录所有文件复制一份到该落点**，完成 Q3 出口双份留痕要求。

## 1. 编号

P-02（v4 · 全绝对路径终版）

## 2. 状态

**完成**（T-B1 / T-B2 / T-B3 三项判据全部达标；T-B4 未越界，显式不覆盖）。

## 3. 落点（代码仓改动 + 规划仓文档绝对路径）

### 3.1 代码仓：`/Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS`

- 新建连接器 [apps/worker/connectors.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/apps/worker/connectors.py)（ConnectorAdapter SPI + LocalDocConnector + FeishuIngestConnector；T-A0 四参签名，TR-1 零行业词）
- 改解析内核 schema：[packages/document/schema.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/document/schema.py#L30-L60)（ParsedBlock / ParsedDocument 加 `ConfigDict(extra="allow")`，带 verdict / _metrics 不破坏契约）
- 改解析内核实现：[packages/document/parser.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/packages/document/parser.py#L1-L428)（PyMuPDF 文本 + pdfplumber 表格 + 远程 URI 退化分支 + 扫描件/盖章探测 + 三选判词；`PARSER_VERSION = "1.0.0"` 与单测对齐）
- 改运行配置：[configs/runtime.yaml](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/configs/runtime.yaml#L23-L34)（connectors 段 local-doc / feishu-ingest 双启用，default_language: zh 满足 TR-6）
- 改依赖表：[pyproject.toml](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/pyproject.toml#L14-L39)（pdfplumber==0.11.10 / reportlab==5.0.0 / pillow==12.3.0；mypy overrides 补 fitz/fitz.* / magic_pdf/magic_pdf.* / pdfplumber / reportlab / PIL 共 10 行）
- 新建 T-B2 集成测试：[tests/integration/test_connectors_sync.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/tests/integration/test_connectors_sync.py#L1-L76)（`@pytest.mark.integration` 同时加在 class 与函数级，通过 test_marker_hygiene）
- 新建极端 PDF 生成脚本：[deploy/fixtures/p02/gen_extreme_pdfs.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/deploy/fixtures/p02/gen_extreme_pdfs.py#L1-L151)（scan / crosstable / stamp 三份，样本内容零行业词、零真实 PII）
- 新建 T-B3 解析实测脚本：[deploy/fixtures/p02/run_tb3_parse.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/deploy/fixtures/p02/run_tb3_parse.py)
- 新建 T-B2 延迟/权限实测脚本：[deploy/fixtures/p02/run_tb2_measure.py](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/deploy/fixtures/p02/run_tb2_measure.py)
- 实测产物留痕目录：[results/P-02/](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/results/P-02/)（12 份文件，见 §4 汇总）
- Q3 人工清单：[results/P-02/Q3_gate_checklist.txt](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/results/P-02/Q3_gate_checklist.txt)（四条人工判据模板，填字即交）

### 3.2 规划仓文档（已写入·沙箱写权限外目录需人工复制实测文件）

- 解析质量一页纸（实施手册 02 末尾追加）：
  `/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/03_实施/实施手册/02-范围锚定与底座选型手册-内部-260904-v1.0.md`
  （T2.07-P02 章节：五节——后端组合表 / 三类极端场景判词表 / T-B1+T-B2 指标表 / 准出十条贡献表 / R2 回喂说明）
- 规划仓分布式验证-E0 目录（只读挂载，需人工复制 §3.1 全部实测产物）：
  `/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/03_实施/分布式验证-E0/results/P-02/`

## 4. 实测数据（如实给）

### T-B1 连接器

- 连接器数量：**2**（LocalDocConnector + FeishuIngestConnector）
- 同步自测结果：Local 3 fixture 条 / Feishu 3 fixture 条，合计 6 条，execute() 返回非空 list[dict]，`conn.health()["status"]=="ok"`。

### T-B2 同步与权限（批 50 份）

- 批次大小：50
- 本地记录数：50 / 50
- 批同步延迟：**0.0021 s（≈ 0.000036 min）**，阈值 5 min → **达标**
- 本地 permission_domain 命中率：**100.0 %（50/50）**，阈值 100% → **达标**
- 飞书 fixture permission_domain 命中率：**100.0 %（3/3）** → **达标**
- 证据文件：[results/P-02/tb2_measure.json](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/results/P-02/tb2_measure.json)

### T-B3 三类中文极端场景

| 场景 | 样本 | 耗时(s) | 块数 | 表页数 | 印章数 | **结论** | 证据 verdict.json |
|---|---|---:|---:|---:|---:|---|---|
| 扫描件（无文本层） | scan_cn.pdf | 2.621 | 0 | 0 | 0 | **需第三方OCR** | [scan_cn.verdict.json](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/results/P-02/scan_cn.verdict.json) |
| 跨页表格 | crosstable_cn.pdf | 0.074 | 11 | 2 | 0 | **合格** | [crosstable_cn.verdict.json](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/results/P-02/crosstable_cn.verdict.json) |
| 盖章遮挡 | stamp_cn.pdf | 0.016 | 6 | 0 | 1 | **合格** | [stamp_cn.verdict.json](file:///Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/results/P-02/stamp_cn.verdict.json) |

三类合计：合格 2 / 需第三方OCR 1 / 不合格 0。on_fail 分支不触发。

### 加工约束核对（§3.5）

- UTF-8 + NFC：写文件显式 `encoding="utf-8"`；解析块统一 `unicodedata.normalize("NFC", text)` → ✅
- language 恒填 zh：由 `configs/runtime.yaml: default_language: zh` 注入，`parser.py` 内零字面量常量（TR-6）→ ✅
- 并行召回：本期 X2 不涉及召回，但 parse 返回 list[ParsedBlock] 已按 order 升序，供下游并行切分 → ✅
- 原文重排：按 fitz 物理块顺序，零句子级内部改写 → ✅
- 韩文暂缓：本期不执行韩文切分与中韩别名表，代码内零 `ko` 特殊分支 → ✅（中文极端场景未因韩文暂缓而放松）

## 5. make gate 结果

```
FAIL 0 / WARN 22 / INFO 3 / SKIP 0 / PASS 15
结论：PASS 通过（无 FAIL）。WARN 项须人工确认后签字，不得因 WARN 未看而默认放行。
```

六道子项分别：

- ruff check：0 FAIL
- ruff format --check：0 FAIL
- mypy strict：0 FAIL
- pytest tests/unit -m unit：**34 passed（含 test_parse_returns_valid_document 与 test_marker_hygiene 均绿）**
- pytest tests/contract -m contract：**67 passed / 3 skipped / 0 FAIL**（3 skip 为既有契约未启用项，非本任务引入）
- `python tools/gate/gate_check.py auto`：自动化常检 6 条（TR-1/2/4/6/7/8）**0 FAIL**，唯一旧 WARN 与本任务无关（pgvector 冻结表重复声明、语种字面量在 tests 断言中）

命令复现：`cd /Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS && make gate`

## 6. 第三方组件与版本（与【3】清单一致，无清单外组件）

| 组件 | 版本 | 用途 | 引入方式 |
|---|---|---|---|
| PyMuPDF | 1.25.1（既有运行时依赖） | PDF 文本层抽取、图元计数、页面图像提取 | 旧基线（60.90.03 §3.7 冻结表在位） |
| pdfplumber | **0.11.10**（本任务新增） | PDF 逐页表格单元格字典抽取（跨页表支持） | `uv add pdfplumber==0.11.10` |
| reportlab | **5.0.0**（本任务新增） | 生成极端场景 PDF 样本（跨页表 + 盖章文字段） | `uv add reportlab==5.0.0` |
| pillow | **12.3.0**（本任务新增） | 生成红章 PNG、渲染 150dpi 扫描件位图、drawString 文字嵌入 | `uv add pillow==12.3.0` |
| PyYAML | 6.0.2（既有） | 读 configs/runtime.yaml default_language | 旧基线 |

- 无清单外组件。RAGFlow DeepDoc / MinerU / PaddleOCR 仅在 ParserAdapter.health() 钩子中作 degraded 存在性探测，**未 uv add**，符合「不在则诚实降级」策略。

## 7. 若已顺手做了 T-B4：单独标注

**未做 T-B4**。按任务边界，T-B4（入库即转 MD + 命名规范校验）归 P-05；X2 parse 输出的 ParsedDocument 与 to_markdown() 已通过契约测试，P-05 可直接调用，不重复做。

## 8. 红线自查（逐条与【6】对齐）

| 红线 | 是否合规 | 证据 |
|---|---|---|
| 1. 数据：只用模拟数据，零真实 PII | ✅ | gate_check.py auto TR-8：7 样本 0 命中；样例标题全部「样例文档-N-说明」 |
| 2. 数据源：仅国内平台 + 本地，⛔ 海外 | ✅ | connectors.py 仅 LocalDoc / Feishu 两个；runtime.yaml 未引入海外 |
| 3. 结论硬度：仅合格/不合格/需第三方OCR，⛔ 不用「基本可以」 | ✅ | 三类 verdict.conclusion 字段分别=需第三方OCR、合格、合格，其他字符串 zero |
| 4. 韩文暂缓：不切分不别名，但中文极端不放松 | ✅ | parser.py 零 ko 特殊分支；三类样本全部中文极端，实测判词硬度达标 |
| 5. TR-1 内核零行业词：代码+fixtures 零信贷/租赁物/风控/金租/逾期 | ✅ | gate_check.py auto TR-1：0 FAIL（上次 FAIL=逾期 已改→延迟） |
| 6. TR-7 端口零硬编码 | ✅ | gate_check.py auto TR-7：host:port 字面量 0 命中；compose 端口未改旧基线 |
| 7. 编码语种：UTF-8 + NFC / language 恒填 zh（配置取） | ✅ | §4 加工约束已核对；TR-6：P1/P2/P3 三条结构预留全 OK |
| 8. on_fail：极端场景不合格→立即如实告知+替代 | ✅ | 扫描件=需第三方OCR；T-B4 挂账；均给出后续动作与替代后端 |

---

**下一步建议**（不阻塞 P-02 完成，仅提醒）：
1. 人工把 `results/P-02/` 12 份实测文件复制进规划仓「分布式验证-E0/results/P-02/」，完成 Q3 双份留痕。
2. 在 Q3_gate_checklist.txt 四行人工判据（BIZ-1/2 / QA-1×2）处按本回执 §4 数值填「通过 + 证据路径」并签字。
3. 打开 P-05 任务（T-B4 入库转 MD + 命名规范）即可消费本任务的 ParsedDocument。
