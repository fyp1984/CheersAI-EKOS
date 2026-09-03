---
description: 数据红线与脱敏。处理样本、fixtures、日志、任何可能含真实数据的文件时必须遵守。
alwaysApply: true
---

# 05 · 数据红线与脱敏（TR-8）

## 三条红线

1. **未经甲方安全团队批准脱敏策略，不得用真实客户数据调用云模型。**
2. **甲方资料不得用于其他客户演示或模型训练。**
3. **样本 / 索引 / 日志保留 30 天后人工确认删除。**

## 可执行版本：TR-8 常检

门禁会扫这些目录：`deploy/fixtures` `tests/fixtures` `tests/golden` `tests/data` `fixtures`

扫描模式：身份证、银行卡、统一社会信用代码、手机号。
**PDF / DOCX 会先解出文本再扫** —— 压缩二进制 grep 抓不到，而真实样本恰恰最可能是 PDF。

**样本目录不存在或为空 → 判 SKIP，不判 PASS。**「没查到」不等于「没有」。

## 硬规则

- 进仓的样本**一律合成数据**，禁止任何真实客户信息
- 金租小库（`60.90.03` §6.2 口径）**全为模拟数据**（Q3 判据 BIZ-1）
- 脱敏映射表**存本地 Vault，不得随云端请求发出** —— Q2 出口要抓包验证一次
- 门禁失败不等于样本干净：TR-8 只查四类模式，**其他敏感信息靠人判断**

## 常见错误

```python
# ✗ 错：为了测试方便，把真实样本拷进 fixtures/
cp ~/Downloads/客户资料.pdf tests/fixtures/

# ✗ 错：日志里打印完整请求体（可能含 PII）
logger.info("request: %s", payload)

# ✓ 对：只记结构与脱敏后的字段
logger.info("request: model=%s n_messages=%d masked=%s",
            payload.get("model"), len(payload.get("messages", [])), True)
```

## git 历史是永久的

**真实数据一旦进仓，删文件也删不掉** —— 它在 git 历史里。
如果已经误提交，要做的不是删文件，是联系项目负责人走历史清理。
