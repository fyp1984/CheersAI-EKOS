"""生成 T-B3 三类中文极端场景测试 PDF（全模拟数据 · 零行业词 · 零真实 PII）。

输出目录: deploy/fixtures/p02/
  - scan_cn.pdf          扫描件（全图像层，无文本层）
  - crosstable_cn.pdf    跨页表格（10 行表格，第 6 行起跨页）
  - stamp_cn.pdf         盖章遮挡（红章 PNG 覆盖第三/四条正文）
"""

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
FONT_PATH = "/System/Library/Fonts/Songti.ttc"
PAGE_W, PAGE_H = 1240, 1754

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def _render_page_image(lines, idx):
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype(FONT_PATH, 34)
    except OSError:
        f = ImageFont.load_default()
    y = 90
    for ln in lines:
        d.text((80, y), ln, fill="black", font=f)
        y += 56
    p = HERE / f"_scan_p{idx}.png"
    img.save(p)
    return p


def gen_scan():
    pages = [
        [
            "样例说明文档 A（全模拟数据 · 非真实主体）",
            "文档编号：MOCK-DOC-A-2026-0001",
            "主体：模拟组织（通用样例）",
            "描述：模拟正文说明，仅供解析链路验证使用。",
            "本页为扫描影像，文本由 OCR 识别，原始无电子文本层。",
            "备注：本章节所有字段均为虚构，不代表任何真实主体。",
        ],
        [
            "意见：样例结论，仅供解析链路验证使用。",
            "作者：模拟角色（样例）  日期：2026-09-18",
            "备注：本章节所有字段均为虚构，不代表任何真实主体。",
            "条件：样例条款，如需结构化请在系统中补录。",
        ],
    ]
    paths = [_render_page_image(p, i + 1) for i, p in enumerate(pages)]
    pdf = str(HERE / "scan_cn.pdf")
    c = canvas.Canvas(pdf, pagesize=(PAGE_W, PAGE_H))
    for p in paths:
        c.drawImage(str(p), 0, 0, width=PAGE_W, height=PAGE_H)
        c.showPage()
    c.save()
    for p in paths:
        os.remove(p)
    print("生成 scan_cn.pdf（扫描件·图像层·无文本层）")


def gen_crosstable():
    rows = [
        ["序号", "物料名称", "数量", "单价(元)", "金额(元)"],
        ["1", "模拟车床", "2", "30000", "60000"],
        ["2", "模拟焊接机", "1", "20000", "20000"],
        ["3", "模拟检测仪", "3", "5000", "15000"],
        ["4", "模拟传送带", "1", "8000", "8000"],
        ["5", "模拟控制柜", "2", "4000", "8000"],
        ["6", "模拟电机", "4", "2500", "10000"],
        # ---- cross page ----
        ["7", "模拟传感器", "10", "300", "3000"],
        ["8", "模拟线缆", "20", "100", "2000"],
        ["9", "模拟工控机", "1", "9000", "9000"],
        ["10", "模拟电源", "1", "7000", "7000"],
    ]
    col_x = [60, 260, 460, 660, 860]
    row_h = 50
    top = 1600

    def draw_table(c_, start_row, end_row, page_top_y):
        y = page_top_y
        for r in range(start_row, end_row):
            c_.setStrokeColorRGB(0, 0, 0)
            c_.setLineWidth(0.8)
            c_.line(60, y, 1060, y)
            for cx, val in zip(col_x, rows[r], strict=False):
                c_.setFont("STSong-Light", 13)
                c_.drawString(cx + 6, y + 16, str(val))
            y -= row_h
        c_.line(60, y, 1060, y)
        for cx in col_x + [1060]:
            c_.line(cx, page_top_y, cx, y)

    pdf = str(HERE / "crosstable_cn.pdf")
    c = canvas.Canvas(pdf, pagesize=(PAGE_W, PAGE_H))
    draw_table(c, 0, 6, top)
    c.showPage()
    draw_table(c, 6, 11, top)
    c.showPage()
    c.save()
    print("生成 crosstable_cn.pdf（跨页表格·第1页表头+5行/第2页5行）")


def gen_stamp():
    w = h = 240
    img = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype(FONT_PATH, 30)
    except OSError:
        f = ImageFont.load_default()
    d.ellipse([6, 6, w - 6, h - 6], outline=(200, 20, 20, 255), width=6)
    d.text((w // 2 - 70, h // 2 - 36), "样例专用章", fill=(200, 20, 20, 255), font=f)
    d.text((w // 2 - 70, h // 2 + 8), "2026.09.18", fill=(200, 20, 20, 255), font=f)
    stamp_png = HERE / "_stamp.png"
    img.save(stamp_png)

    pdf = str(HERE / "stamp_cn.pdf")
    c = canvas.Canvas(pdf, pagesize=(PAGE_W, PAGE_H))
    c.setFont("STSong-Light", 14)
    paras = [
        "第一条 本样例文件由甲方模拟组织与乙方样例服务组织签订（非真实主体）。",
        "第二条 服务内容为文档所列条目，期限自样例起算日起三十六个月。",
        "第三条 服务费用按等额方式按月支付，样例月供金额为人民币贰仟元整。",
        "第四条 使用方应按期足额完成，延迟视为违约并承担样例违约金。",
        "第五条 本文件所涉全部主体与金额均为虚构，仅用于解析链路验证。",
        "第六条 争议解决方式以样例条款为准，提交样例仲裁委员会处理。",
    ]
    y = 1600
    for p in paras:
        c.drawString(80, y, p)
        y -= 60
    c.drawImage(str(stamp_png), 590, 1190, width=240, height=240, mask="rgba")
    c.save()
    os.remove(stamp_png)
    print("生成 stamp_cn.pdf（盖章遮挡·红章 PNG 覆盖第三四条）")


if __name__ == "__main__":
    gen_scan()
    gen_crosstable()
    gen_stamp()
    print("全部样例已生成于:", HERE)
