"""Fast, local proposal posters and schematic diagrams; no network calls."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
BUNDLED_FONT_PATHS = {
    False: FONT_DIR / "NotoSansSC-Regular.ttf",
    True: FONT_DIR / "NotoSansSC-Bold.ttf",
}

PALETTES = {
    "宋野": ("#FAEDE4", "#D6603D", "城市探索", "CITY / DISCOVER"),
    "沈知行": ("#EEECE2", "#727A4E", "文化漫游", "CULTURE / STORIES"),
    "夏橙": ("#E6F1EB", "#238576", "户外共创", "OUTDOOR / TOGETHER"),
}
INK = "#203945"


@lru_cache(maxsize=24)
def font(size, bold=False):
    path = BUNDLED_FONT_PATHS[bool(bold)]
    if not path.is_file():
        raise FileNotFoundError(f"缺少内置中文字体：{path}")
    return ImageFont.truetype(str(path), size)


def text(draw, content, xy, width, size=25, color=INK, lines=3, bold=False):
    """Wrap on measured glyph widths, with a visible ellipsis if truncated."""
    face = font(size, bold)
    rows, row = [], ""
    for char in str(content):
        if char == "\n" or draw.textlength(row + char, font=face) > width:
            rows.append(row)
            row = "" if char == "\n" else char
        else:
            row += char
    if row:
        rows.append(row)
    if len(rows) > lines:
        rows = rows[:lines]
        while rows[-1] and draw.textlength(rows[-1] + "…", font=face) > width:
            rows[-1] = rows[-1][:-1]
        rows[-1] += "…"
    for index, value in enumerate(rows):
        draw.text((xy[0], xy[1] + index * (size + 12)), value, font=face, fill=color)


def png(image):
    stream = BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()


@lru_cache(maxsize=64)
def proposal_poster(name, title, pitch, activities, advantage, tradeoff, context):
    background, accent, label, english = PALETTES.get(name, PALETTES["宋野"])
    canvas = Image.new("RGB", (780, 1120), background)
    draw = ImageDraw.Draw(canvas)
    text(draw, "OPC  /  团建旅行提案", (46, 32), 680, 20, lines=1)
    text(draw, english, (46, 92), 680, 22, accent, lines=1)
    # Original geometric illustrations distinguish the three planners.
    if name == "沈知行":
        draw.polygon([(458, 252), (598, 158), (738, 252)], fill=accent)
        for x in range(480, 720, 55):
            draw.rectangle((x, 263, x + 26, 356), fill=accent)
        draw.rectangle((452, 365, 743, 378), fill=accent)
    elif name == "夏橙":
        draw.ellipse((634, 143, 700, 209), fill="#E7BA63")
        draw.polygon([(420, 375), (553, 180), (690, 375)], fill=accent)
        draw.polygon([(560, 375), (663, 250), (754, 375)], fill="#80B7A2")
        draw.line([(440, 370), (520, 336), (540, 360), (610, 330)], fill="white", width=6)
    else:
        for x, top, height in [(464, 233, 140), (535, 165, 208), (622, 212, 161), (693, 273, 100)]:
            draw.rounded_rectangle((x, top, x + 52, top + height), radius=5, fill=accent)
            for y in range(top + 18, top + height - 10, 27):
                draw.rectangle((x + 15, y, x + 33, y + 8), fill=background)
    text(draw, label, (46, 165), 365, 42, lines=1, bold=True)
    text(draw, name, (46, 231), 350, 27, accent, lines=1, bold=True)
    text(draw, context, (46, 289), 355, 20, lines=3)
    draw.rounded_rectangle((26, 406, 754, 1095), radius=24, fill="#FFFFFF")
    text(draw, title, (52, 433), 670, 33, lines=2, bold=True)
    text(draw, pitch, (52, 540), 670, 24, lines=2)
    text(draw, "核心活动", (52, 634), 670, 23, accent, lines=1, bold=True)
    for index, activity in enumerate(activities[:3]):
        text(draw, f"0{index + 1}  {activity}", (52, 678 + index * 57), 666, 23, lines=1)
    draw.line((52, 860, 723, 860), fill="#E3E8E8", width=2)
    text(draw, "亮点  " + (advantage or "按团队目标定制"), (52, 885), 666, 23, lines=2)
    text(draw, "取舍  " + (tradeoff or "具体安排需进一步确认"), (52, 975), 666, 23, lines=2)
    return png(canvas)


@lru_cache(maxsize=48)
def itinerary_diagram(theme, schedule):
    """All schedule entries remain represented; positions are not geography."""
    nodes = schedule or ("行程待完善",)
    height = 200 + 132 * len(nodes)
    canvas = Image.new("RGB", (1300, height), "#F4F7F6")
    draw = ImageDraw.Draw(canvas)
    text(draw, theme + " · 行程顺序图", (44, 32), 1210, 31, lines=1, bold=True)
    text(draw, "按方案顺序展示 · 非导航地图 · 距离、交通与场地状态待核实", (44, 88), 1210, 22, "#617778", lines=1)
    for i, item in enumerate(nodes):
        y = 160 + i * 132
        if i < len(nodes) - 1:
            draw.line((77, y + 33, 77, y + 155), fill="#9FC5BB", width=5)
        draw.ellipse((50, y, 104, y + 54), fill="#238576")
        text(draw, str(i + 1), (65, y + 9), 35, 23, "white", lines=1, bold=True)
        draw.rounded_rectangle((133, y - 6, 1253, y + 105), radius=15, fill="white")
        text(draw, item, (156, y + 9), 1070, 24, lines=2)
    return png(canvas)


@lru_cache(maxsize=16)
def collaboration_diagram(active, compact=True, style_branch=False):
    canvas = Image.new("RGB", (1300, 290), "#F4F7F6")
    draw = ImageDraw.Draw(canvas)
    text(draw, "方案如何形成", (32, 24), 1200, 29, lines=1, bold=True)
    steps = ([("策划提案", "按所选风格设计" if style_branch else "三个精简风格"), ("履约检查", "资源与执行条件"), ("经理整合", "同步检查并处理分歧"), ("客户确认", "程序规则 / 必要复核")]
        if compact else [("经理派策划", "历史三角色提案"), ("角色互评", "旧版策划流程"), ("经理组合", "由客户微调"), ("检查后交履约", "经理 + 程序规则")])
    for i, (title, note) in enumerate(steps):
        x = 32 + i * 318
        color = "#238576" if i == active else "#DFE8E4"
        draw.rounded_rectangle((x, 100, x + 280, 231), radius=20, fill=color)
        ink = "white" if i == active else INK
        text(draw, title, (x + 21, 119), 242, 28, ink, lines=1, bold=True)
        text(draw, note, (x + 21, 173), 242, 22, ink, lines=1)
        if i < 3:
            text(draw, "→", (x + 286, 143), 34, 25, "#6B8B81", lines=1)
    return png(canvas)


@lru_cache(maxsize=48)
def journey_frame(activity, index, total, tick, weather_alert=False, member_alert=False):
    """Animated by successive local frames; illustration, not a real-world image."""
    canvas = Image.new("RGB", (1300, 640), "#ECF4F0")
    draw = ImageDraw.Draw(canvas)
    text(draw, "正在游玩 · 压缩时间演示", (36, 26), 1190, 30, lines=1, bold=True)
    text(draw, "插画与事件均为模拟 · 无真实定位或天气监测", (36, 78), 1190, 21, "#657D76", lines=1)
    sky = "#C9DBE0" if weather_alert else "#DCEDEF"
    draw.rounded_rectangle((34, 128, 1265, 410), radius=22, fill=sky)
    draw.ellipse((1050, 152, 1124, 226), fill="#E8BA69")
    if any(word in activity for word in ("博物馆", "古迹", "文化", "历史")):
        draw.polygon([(470, 230), (650, 155), (830, 230)], fill="#87916B")
        for x in range(490, 830, 67):
            draw.rectangle((x, 245, x + 38, 363), fill="#87916B")
        draw.rectangle((460, 372, 841, 385), fill="#87916B")
    elif any(word in activity for word in ("户外", "公园", "登山", "草地", "徒步")):
        draw.polygon([(360, 388), (540, 180), (745, 388)], fill="#5A9B7E")
        draw.polygon([(590, 388), (800, 225), (1040, 388)], fill="#88B99A")
    else:
        for x, y in ((380, 255), (490, 188), (620, 222), (760, 270), (870, 210)):
            draw.rounded_rectangle((x, y, x + 83, 385), radius=7, fill="#8BA5A3")
            for wy in range(y + 17, 375, 34):
                draw.rectangle((x + 20, wy, x + 61, wy + 12), fill="#EAF2EC")
    # Small group moves gently between frames, independent of real coordinates.
    for i in range(4):
        x = 120 + i * 48 + (tick % 3) * 12
        color = "#DB8A60" if i % 2 else "#288777"
        draw.ellipse((x, 305, x + 21, 326), fill="#9B765F")
        draw.line((x + 10, 328, x + 10, 361), fill=color, width=12)
        draw.line((x + 10, 359, x, 382), fill=color, width=6)
        draw.line((x + 10, 359, x + 24, 382), fill=color, width=6)
    if weather_alert:
        for x in range(75, 1250, 100):
            draw.line((x, 165, x - 14, 207), fill="#5C879C", width=4)
        text(draw, "模拟降雨预警", (55, 145), 350, 25, "#9A5428", lines=1, bold=True)
    if member_alert:
        text(draw, "成员提出休息需求", (55, 224), 350, 24, "#9A5428", lines=1, bold=True)
    text(draw, f"行程 {index + 1} / {total}", (40, 432), 1190, 23, "#238576", lines=1, bold=True)
    text(draw, activity, (40, 476), 1190, 27, lines=2)
    draw.line((55, 607, 1243, 607), fill="#C3D8CE", width=6)
    x = 55 + int(1188 * tick / 12)
    draw.ellipse((x - 9, 598, x + 9, 616), fill="#238576")
    return png(canvas)
