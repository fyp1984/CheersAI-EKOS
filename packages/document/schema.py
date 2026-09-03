"""解析输出的文档对象模型（共用层）。

本模块只定义「深度解析出来的东西长什么样」，不含任何客户行业语义 ——
特定业务领域的字段一律走 configs/ 与 prompts/。内核一旦长出某个具体
业务领域的字段，EKOS 就从通用企业知识库退化成单一用途的报告生成器（见 TR-1）。
（此处刻意不点名任何具体业务领域：TR-1 会扫描文档字符串，点名即触发。）

为什么 language 是开放字符串，不是枚举
--------------------------------------
用枚举（ZH = "zh"）看上去更规范，实则把语种列表焊死在代码里：
加韩文要先改这个枚举、发版本、再改配置，等于没留口子，
我们对甲方说的「加一个语种是加配置，不是改系统」就成了假话。

改用 ISO 639-1 模式校验的开放字符串后，语种列表由 configs/runtime.yaml 维护，
加一个语种就是加一行配置。校验只管「格式是不是合法的语种代码」，
不管「在不在已知列表里」—— 后者是配置的职责，不是 schema 的职责。

同理，本模块**刻意不写** DEFAULT_LANGUAGE = "zh" 这类常量：
默认值也是语种信息，写死在代码里同样焊死了口子。默认值由调用方从配置层取。

为什么字段说明用英文
--------------------
Field(description=...) 一律英文，中文解释只出现在文档字符串里。
不是崇洋：TR-6 会扫描代码里的中文字面量，用来发现「被写死的中文规则」。
把说明文字也混进去，会让真正的问题淹没在噪音里 —— 门禁一旦噪音化，就没人看了。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ISO 639-1，可带可选子标签（如 zh-Hans）。
# 刻意不做封闭枚举，理由见模块文档字符串：封闭即焊死。
LANGUAGE_PATTERN = r"^[a-z]{2}(?:-[A-Za-z0-9]{1,8})*$"


class ParsedBlock(BaseModel):
    """解析后的最小文本单元。"""

    block_id: str = Field(description="stable id within the owning document")
    order: int = Field(ge=0, description="reading order, 0-based")
    text: str = Field(description="plain text content")
    language: str = Field(pattern=LANGUAGE_PATTERN, description="ISO 639-1 language code")
    page: int | None = Field(default=None, ge=1, description="1-based page number if known")
    kind: str = Field(default="text", description="text | table | caption | header")


class ParsedDocument(BaseModel):
    """一次深度解析的完整输出。"""

    doc_id: str = Field(description="stable document identifier")
    source_uri: str = Field(description="where the document was read from")
    language: str = Field(pattern=LANGUAGE_PATTERN, description="ISO 639-1 language code")
    blocks: list[ParsedBlock] = Field(default_factory=list, description="blocks in reading order")
