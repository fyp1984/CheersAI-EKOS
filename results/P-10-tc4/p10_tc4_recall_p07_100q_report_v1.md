# P-10 T-C4 召回调优 · P-07 100 题 CSV 实测报告 v1

- run_id: P10-TC4-P07-100Q-1789850738
- run_at: 2026-09-20 04:45:38
- 语料: 24 docs / 466 chunks
- 题库: 100 题（answerable 80 / refuse 20）

## 核心指标

- Recall@8: **0.5406**（≥0.85 PASS=False）
- MRR@8: **0.5509**
- 中→中 Recall@8: **0.5317**（≥0.90 PASS=False）
- 中→中 MRR@8: **0.5597**（≥0.80 PASS=False）
- 拒答率: **0.3000**（6/20 正确拒答）
- 错误预算: {"answerable_refused": 22, "refused_wrong_answered": 14, "missing_expected_stems": 0}

## 逐题明细（节选 Top10 recall=0 或 MRR<0.3）

- Q005（easy）: recall=0.0, mrr=0.0  

  Query: 中→中组检索实测的指标要求是什么？  

  Expected: 04-测试手册-内部-260904-v1.0, 05-质量管控与门禁手册-内部-260904-v1.0  

  Actual Top:   

- Q008（easy）: recall=0.0, mrr=0.0  

  Query: 准出⑨「规范校验」的告警要求是什么？  

  Expected: 01-环境与软件准备手册-内部-260904-v1.0, 01_PRD-EKOS-v5.2-260830-01, 04-测试手册-内部-260904-v1.0, 05-质量管控与门禁手册-内部-260904-v1.0  

  Actual Top:   

- Q010（easy）: recall=0.0, mrr=0.0  

  Query: 准出①「连接器自动同步」的三项判定指标是什么？  

  Expected: 01-环境与软件准备手册-内部-260904-v1.0, 01_PRD-EKOS-v5.2-260830-01, 03-开发实施手册-内部-260904-v1.0  

  Actual Top:   

- Q013（easy）: recall=0.0, mrr=0.0  

  Query: MVP 范围里，韩文相关项本期怎么处理？  

  Expected: 01_PRD-EKOS-v5.2-260830-01  

  Actual Top:   

- Q014（easy）: recall=0.0, mrr=0.0  

  Query: 韩文暂缓项一共有几项？  

  Expected: 01_PRD-EKOS-v5.2-260830-01  

  Actual Top: 04-测试手册-内部-260904-v1.0, 09-运维与维护手册-内部-260904-v1.0, 07-安全与脱敏手册-内部-260904-v1.0  

- Q015（medium）: recall=0.0, mrr=0.0  

  Query: 三处「零成本结构预留」分别是什么？  

  Expected: 01_PRD-EKOS-v5.2-260830-01  

  Actual Top:   

- Q021（easy）: recall=0.0, mrr=0.0  

  Query: EKOS 把自己类比为企业界的什么系统？  

  Expected: 01_PRD-EKOS-v5.2-260830-01, 02_EKOS产品定位声明-v1.0-260821  

  Actual Top:   

- Q023（medium）: recall=0.0, mrr=0.0  

  Query: EKOS 三层版本策略包含哪三个版本？  

  Expected: 01_PRD-EKOS-v5.2-260830-01, 02_EKOS产品定位声明-v1.0-260821, 04_EKOS业务方案-商业模式与定价-v1.0-260828  

  Actual Top:   

- Q024（easy）: recall=0.0, mrr=0.0  

  Query: 商业模式的核心是什么？  

  Expected: 01_PRD-EKOS-v5.2-260830-01, 04_EKOS业务方案-商业模式与定价-v1.0-260828  

  Actual Top:   

- Q026（easy）: recall=0.0, mrr=0.0  

  Query: 年度数据治理服务的价格区间是多少？  

  Expected: 01_PRD-EKOS-v5.2-260830-01, 04_EKOS业务方案-商业模式与定价-v1.0-260828  

  Actual Top:   

- Q028（easy）: recall=0.0, mrr=0.0  

  Query: 调研中约有多少比例的知识库沦为「废墟」？  

  Expected: 01_PRD-EKOS-v5.2-260830-01, 04_EKOS业务方案-商业模式与定价-v1.0-260828  

  Actual Top:   

- Q030（medium）: recall=0.0, mrr=0.0  

  Query: 组件版本冻结表里，PostgreSQL 的冻结版本是哪个？  

  Expected: 01_PRD-EKOS-v5.2-260830-01, 02-范围锚定与底座选型手册-内部-260904-v1.0, 05-质量管控与门禁手册-内部-260904-v1.0  

  Actual Top:   

- Q031（easy）: recall=0.0, mrr=0.0  

  Query: 组件版本冻结表里，vLLM 的冻结版本是多少？  

  Expected: 01_PRD-EKOS-v5.2-260830-01, 02-范围锚定与底座选型手册-内部-260904-v1.0, 05-质量管控与门禁手册-内部-260904-v1.0  

  Actual Top:   

- Q034（medium）: recall=0.5, mrr=0.1429  

  Query: 「端口零硬编码」（TR-7）要求交付端口怎么处理？  

  Expected: 01_PRD-EKOS-v5.2-260830-01, 05-质量管控与门禁手册-内部-260904-v1.0  

  Actual Top: 11_EKOS外部依赖与制品统一目录规范-v1.0-内部-260910, 01-环境与软件准备手册-内部-260904-v1.0, 01-环境与软件准备手册-内部-260904-v1.0  

- Q036（medium）: recall=0.0, mrr=0.0  

  Query: 八道门禁 Q1–Q8 中，哪几道是 MVP 覆盖的？  

  Expected: 00-实施任务清单-内部-260904-v1.1, 01_PRD-EKOS-v5.2-260830-01  

  Actual Top:   

- Q037（easy）: recall=1.0, mrr=0.2  

  Query: 共用资产 X6 是什么？落点在哪？  

  Expected: 01_PRD-EKOS-v5.2-260830-01  

  Actual Top: 04-测试手册-内部-260904-v1.0, 08-部署与发布手册-内部-260904-v1.0, 01_EKOS技术方案-开源集成架构-v2.1-260830  

- Q038（easy）: recall=0.0, mrr=0.0  

  Query: 任务 T-C3 是什么？责任人是谁？  

  Expected: 01_PRD-EKOS-v5.2-260830-01  

  Actual Top:   

- Q039（medium）: recall=1.0, mrr=0.1429  

  Query: 合并题库 100 题的构成是怎样的？  

  Expected: 04-测试手册-内部-260904-v1.0  

  Actual Top: 07-安全与脱敏手册-内部-260904-v1.0, 07-安全与脱敏手册-内部-260904-v1.0, 03-开发实施手册-内部-260904-v1.0  

- Q044（easy）: recall=0.0, mrr=0.0  

  Query: EKOS 开源底座优先选什么 License？  

  Expected: 01_EKOS技术方案-开源集成架构-v2.1-260830, 01_PRD-EKOS-v5.2-260830-01  

  Actual Top:   

- Q049（easy）: recall=0.0, mrr=0.0  

  Query: EKOS 的目标高合规行业有哪些？  

  Expected: 01_PRD-EKOS-v5.2-260830-01, 02_EKOS产品定位声明-v1.0-260821, 04_EKOS业务方案-商业模式与定价-v1.0-260828  

  Actual Top:   


总耗时: 66.0 s
