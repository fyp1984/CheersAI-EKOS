=== 门禁自动检查 · TR-1 / TR-2 / TR-4 / TR-6 / TR-7 / TR-8 ===
时间：2026-09-05 20:00:56
被检根目录：/Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS
判据源：gate_baseline.json（= 《双线推进方案-最小任务集与门禁验证-内部-260903-v2.2.md》§4.2）

[TR-1 内核零行业词]
  判据：金租特有逻辑必须落在配置层/提示词层，共用层代码零信贷术语。内核长了信贷字段，EKOS 就从企业知识库退化成信贷报告生成器。
  [ OK ]  命中 0 处（扫描 36 个文件）

[TR-2 开源组件零侵入]
  判据：集成方式只能是 adapter 或 REST 调用，无 vendor 内修改。一旦开始改 core，升级就跟不上了——这是开源复用最常见的翻车点。
  [SKIP]  未发现 vendor 目录（vendor、third_party、external、deps、ragflow）；引入开源组件当天必须跑 `baseline` 建基线

[TR-4 版本零漂移]
  判据：与 60.90.03 §3.7 冻结组件版本表逐项 diff，差异数=0。任何升级须走变更记录，不得顺手升。顺手升是最常见的环境不可复现来源。
  [ OK ]  python → Python = 3.11.10
  [ OK ]  fastapi → FastAPI = 0.115.6
  [ OK ]  uvicorn → Uvicorn = 0.34.0
  [ OK ]  celery → Celery = 5.4.0
  [ OK ]  redis → Redis = 7.4.1
  [ OK ]  postgres → PostgreSQL = 16（主版本匹配，小版本滚动）
  [WARN]  pgvector → pgvector 冻结版本 0.8.0 在位（精确匹配）；但另有声明 0.5.0，请人工确认是否为无关位置
  [ OK ]  minio → MinIO = RELEASE.2024-11-07T00-52-20Z
  [ OK ]  pymupdf → PyMuPDF = 1.25.1
  [WARN]  未落地 → 7 个组件未在版本声明文件中找到：Docker Engine 27.3.1；Docker Compose 2.29.7；NVIDIA Driver 550.127.05；CUDA Runtime 12.4；vLLM 0.8.5；llama.cpp server b5401；PaddleOCR 2.9.1 → 早期门禁属正常，Q1 出口（版本表落地）须全部补齐

[TR-6 语种未写死在内核]
  判据：防的是本期图省事把中文规则写进代码、下期加韩文要重构。三处结构预留 P1/P2/P3（schema 留 language 字段、切分与别名落配置层、脱敏策略表留 lang 列）必须真的在。
  [WARN]  tests/unit/test_retrieval_service.py:23 → 出现语种字面量，确认是否在共用层 → variant = cfg["languages"]["zh"]["aliases"][0]["variants"][0]
  [WARN]  tests/unit/test_retrieval_service.py:24 → 出现语种字面量，确认是否在共用层 → canonical = cfg["languages"]["zh"]["aliases"][0]["canonical"]
  [WARN]  tests/unit/test_language_reservation.py:56 → 出现语种字面量，确认是否在共用层 → ParsedBlock(block_id="b1", order=0, text="sample", language="chinese")
  [WARN]  tests/unit/test_language_reservation.py:60 → 出现语种字面量，确认是否在共用层 → @pytest.mark.parametrize("lang", ["zh", "zh-Hans", "ko", "en"])
  [WARN]  tests/unit/test_language_reservation.py:76 → 出现语种字面量，确认是否在共用层 → assert "zh" in cfg["languages"]
  [WARN]  tests/unit/test_language_reservation.py:83 → 出现语种字面量，确认是否在共用层 → assert "zh" in cfg["languages"]
  [WARN]  tests/unit/test_language_reservation.py:94 → 出现语种字面量，确认是否在共用层 → assert cfg["default_language"] == "zh"
  [WARN]  tests/unit/test_language_reservation.py:95 → 出现语种字面量，确认是否在共用层 → assert "zh" in cfg["supported_languages"]
  [WARN]  tests/unit/test_language_reservation.py:75 → 代码内中文字符串字面量（疑似被写死的规则）→ assert "languages" in cfg, "切分规则未按语种组织，加语种就得改代码"
  [WARN]  tests/unit/test_language_reservation.py:77 → 代码内中文字符串字面量（疑似被写死的规则）→ assert "default" in cfg, "缺兜底档：未登记语种会直接 KeyError"
  [WARN]  tests/unit/test_language_reservation.py:107 → 代码内中文字符串字面量（疑似被写死的规则）→ assert rules, "策略表为空：结构在位不等于有内容"
  [WARN]  tests/contract/test_model_contract.py:58 → 代码内中文字符串字面量（疑似被写死的规则）→ assert key in data, f"缺少 OpenAI 标准字段 {key}"
  [WARN]  tests/contract/test_model_contract.py:86 → 代码内中文字符串字面量（疑似被写死的规则）→ assert REASONING_FIELD in msg, "reasoning profile 必须单独返回推理字段"
  [WARN]  tests/contract/test_model_contract.py:87 → 代码内中文字符串字面量（疑似被写死的规则）→ assert msg[REASONING_FIELD], "推理字段不得为空"
  [WARN]  packages/modelgw/stub.py:58 → 代码内中文字符串字面量（疑似被写死的规则）→ "[stub] 这是模型契约 Stub 的固定响应。"
  [WARN]  packages/modelgw/stub.py:59 → 代码内中文字符串字面量（疑似被写死的规则）→ "真实推理只在 E1（x86_64 真机）进行，本响应不具备任何质量含义。"
  [WARN]  packages/modelgw/stub.py:61 → 代码内中文字符串字面量（疑似被写死的规则）→ _TEMPLATE_REASONING = "[stub] 模拟推理过程。该字段不得写入报告、日志或多轮历史。"
  [WARN]  packages/modelgw/stub.py:110 → 代码内中文字符串字面量（疑似被写死的规则）→ app = FastAPI(title="EKOS 模型契约 Stub", version="0.1.0")
  [ OK ]  P1 → 结构预留 P1 在位：解析输出 schema 含 language 字段
  [ OK ]  P2 → 结构预留 P2 在位：切分规则与别名表落在配置层
  [ OK ]  P3 → 结构预留 P3 在位：脱敏策略表含 lang 列

[TR-7 端口零硬编码]
  判据：交付端口（60.90.03 §3.8）不得写死在代码里，一律走环境变量注入。硬编码的代价要等切环境时才暴露：E0 用 15xxx 段、E1 用冻结段，同一份代码两种行为，而报错永远指向「连接被拒」而不是「端口写死」——排查方向从一开始就是错的。
  [ OK ]  代码中无 host:port 字面量（扫描 32 个文件）
  [ OK ]  compose 主机侧端口均避开冻结段（扫描 2 个编排文件）

[TR-8 样本目录零真实数据]
  判据：样本 / fixtures 目录不得出现真实敏感标识。这是数据红线第二条的可执行版本：真实客户数据一旦进仓，git 历史里就永久留痕，删文件也删不掉。
  [SKIP]  样本目录存在但为空（deploy/fixtures、tests/golden）→ 未扫描任何文件。空目录不等于安全

—— 汇总 ——
FAIL 0 / WARN 20 / INFO 0 / SKIP 2 / PASS 14
结论：PASS 通过（无 FAIL）。WARN 项须人工确认后签字，不得因 WARN 未看而默认放行。
