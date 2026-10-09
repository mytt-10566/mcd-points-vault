#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用估值引擎的真实输出生成 README 首屏演示 GIF.

这是一个构建工具，不是技能运行时的一部分。它读取 `examples/demo-report.md`
（由 `scripts/points_vault.py --demo` 真实生成）并渲染成终端风格的动画 GIF，
因此演示内容与工具实际输出完全一致，不存在手工美化或编造。

依赖 Pillow（仅本工具有依赖，估值引擎本身零依赖）：

    pip install Pillow
    python3 tools/make_demo_gif.py

输出：仓库根目录下的 demo.gif
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# 画布与网格参数
# --------------------------------------------------------------------------

W, H = 860, 500
TITLEBAR_H = 40
PAD_X, PAD_Y = 22, 16
CELL_W, CELL_H = 9, 19
FONT_SIZE = 15
FPS = 10

MAX_COLS = (W - PAD_X * 2) // CELL_W
ROWS = (H - TITLEBAR_H - PAD_Y * 2) // CELL_H
CONTENT_TOP = TITLEBAR_H + PAD_Y
CONTENT_LEFT = PAD_X

PROMPT_PATH_TEXT = "~/mcd-points-vault"
PROMPT_CMD = "python3 scripts/points_vault.py --demo"

# GitHub 暗色终端配色
BG = (13, 17, 23)
TITLEBAR_BG = (22, 27, 34)
BORDER = (48, 54, 61)
TEXT = (201, 209, 217)
WHITE = (255, 255, 255)
MUTED = (139, 148, 158)
HEAD = (88, 166, 255)
PROMPT_PATH = (57, 197, 187)
PROMPT_SIGN = (63, 185, 80)
QUOTE = (166, 179, 196)
QUOTE_BAR = (48, 84, 150)
EMPHASIS = (240, 136, 62)
WARN = (210, 153, 34)
RULE = (90, 100, 114)
DOTS = [(255, 95, 86), (255, 189, 46), (39, 201, 63)]

KIND_COLOR = {
    "h1": HEAD,
    "h2": HEAD,
    "h3": HEAD,
    "quote": QUOTE,
    "table": TEXT,
    "bullet": TEXT,
    "text": TEXT,
    "warn": WARN,
    "prompt": TEXT,
    "blank": TEXT,
    "rule": RULE,
}

BOLD_KINDS = {"h1", "h2", "h3"}


def find_font_path() -> str:
    candidates = [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise SystemExit("找不到可用的中英文字体，请手动指定字体路径")


CJK_PATH = find_font_path()
ASCII_PATH = next(
    (
        p
        for p in (
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
            "/System/Library/Fonts/Supplemental/Courier New.ttf",
        )
        if os.path.exists(p)
    ),
    CJK_PATH,
)

# Menlo 在 15px 下的字符推进量恰好是 9.0px，与 CELL_W 完全吻合，
# 因此英文和数字能获得真正的等宽效果；汉字交给中文字体渲染。
ASCII_FONT = ImageFont.truetype(ASCII_PATH, FONT_SIZE, index=0)
ASCII_FONT_BOLD = ImageFont.truetype(ASCII_PATH, FONT_SIZE, index=1)
CJK_FONT = ImageFont.truetype(CJK_PATH, FONT_SIZE, index=0)
CJK_FONT_BOLD = ImageFont.truetype(CJK_PATH, FONT_SIZE, index=1)


def font_for(ch: str, bold: bool) -> ImageFont.FreeTypeFont:
    """汉字用中文字体，其余用等宽英文字体。"""
    if is_wide(ch):
        return CJK_FONT_BOLD if bold else CJK_FONT
    return ASCII_FONT_BOLD if bold else ASCII_FONT


def is_wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def cell_width(text: str) -> int:
    """按「东亚全角占 2 格、其余占 1 格」计算显示宽度。"""
    return sum(2 if is_wide(ch) else 1 for ch in text)


# --------------------------------------------------------------------------
# Markdown → 显示行
#   span 结构：(文本, 是否加粗, 颜色覆盖或 None)
# --------------------------------------------------------------------------

SEPARATOR_ROW = re.compile(r"^\|[\s:\-|]+\|$")


def strip_inline(text: str, color: tuple[int, int, int] | None = None):
    parts = text.split("**")
    spans = []
    for index, part in enumerate(parts):
        if not part:
            continue
        spans.append((part, index % 2 == 1, color))
    return spans or [("", False, color)]


def classify(raw: str) -> tuple[str, list]:
    line = raw.rstrip()
    if not line:
        return "blank", []
    if "⚠" in line:
        return "warn", strip_inline(line.replace("⚠️", "!").replace("⚠", "!"))
    if SEPARATOR_ROW.match(line):
        return "rule", []
    if line.startswith("---"):
        return "hr", []
    if line.startswith("### "):
        return "h3", strip_inline(line[4:])
    if line.startswith("## "):
        return "h2", strip_inline(line[3:])
    if line.startswith("# "):
        return "h1", strip_inline(line[2:])
    if line.startswith("> "):
        return "quote", strip_inline(line[2:])
    if line.startswith("|"):
        return "table", strip_inline(line)
    if line.startswith("- "):
        return "bullet", strip_inline(line[2:])
    return "text", strip_inline(line)


def wrap_spans(spans: list, limit: int) -> list[list]:
    """按显示宽度折行。"""
    if not spans:
        return [[]]
    rows: list[list] = [[]]
    used = 0
    for text, bold, color in spans:
        buffer = ""
        for ch in text:
            width = 2 if is_wide(ch) else 1
            if used + width > limit and (buffer or rows[-1]):
                if buffer:
                    rows[-1].append((buffer, bold, color))
                rows.append([])
                used = 0
                buffer = ""
            buffer += ch
            used += width
        if buffer:
            rows[-1].append((buffer, bold, color))
    return rows


def build_display_lines(raw_lines: list[str], limit: int = MAX_COLS) -> list[dict]:
    """把原始 Markdown 行转换成可直接逐行渲染的显示行。"""
    display: list[dict] = []
    for raw in raw_lines:
        kind, spans = classify(raw)
        if kind in ("blank", "hr"):
            display.append({"kind": "blank", "spans": []})
            continue
        if kind == "rule":
            # 分隔线宽度对齐上一行（即表头），而不是铺满整个终端
            width = 0
            if display:
                width = sum(cell_width(t) for t, _, _ in display[-1]["spans"])
            display.append({"kind": "rule", "spans": [], "width": width})
            continue
        for chunk in wrap_spans(spans, limit):
            display.append({"kind": kind, "spans": chunk})
    return display


# --------------------------------------------------------------------------
# 绘制
# --------------------------------------------------------------------------


def draw_chrome(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle([(10, 10), (W - 10, H - 10)], outline=BORDER, width=1)
    draw.rectangle([(11, 11), (W - 11, TITLEBAR_H - 4)], fill=TITLEBAR_BG)
    draw.line([(10, TITLEBAR_H - 4), (W - 10, TITLEBAR_H - 4)], fill=BORDER, width=1)
    for index, color in enumerate(DOTS):
        cx = 34 + index * 20
        draw.ellipse([(cx - 5, 15), (cx + 5, 25)], fill=color)
    draw.text((W // 2, 20), "points_vault — 麦麦积分钱庄", font=CJK_FONT, fill=MUTED, anchor="mm")


def draw_item(draw: ImageDraw.ImageDraw, row: int, item: dict, cursor: bool) -> None:
    kind = item["kind"]
    if kind == "blank":
        return

    y = CONTENT_TOP + row * CELL_H + CELL_H // 2
    x = CONTENT_LEFT

    if kind == "quote":
        draw.line([(x - 8, y - 9), (x - 8, y + 9)], fill=QUOTE_BAR, width=2)

    if kind == "rule":
        width = item.get("width") or MAX_COLS
        draw.line(
            [(CONTENT_LEFT, y), (CONTENT_LEFT + width * CELL_W, y)], fill=RULE, width=1
        )
        return

    base_color = KIND_COLOR.get(kind, TEXT)
    line_bold = kind in BOLD_KINDS

    for text, bold, color in item["spans"]:
        use_bold = bold or line_bold
        if color is not None:
            fill = color
        elif bold and kind == "table":
            fill = WHITE
        else:
            fill = base_color
        for ch in text:
            draw.text((x, y), ch, font=font_for(ch, use_bold), fill=fill, anchor="lm")
            x += CELL_W * (2 if is_wide(ch) else 1)

    if cursor:
        draw.rectangle([(x + 1, y - 8), (x + CELL_W - 1, y + 8)], fill=TEXT)


def render(items: list[dict], cursor_row: int | None = None) -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw_chrome(draw)
    for index, item in enumerate(items[:ROWS]):
        draw_item(draw, index, item, cursor=cursor_row == index)
    return image


def prompt_item(command: str, cursor: bool = False) -> dict:
    return {
        "kind": "prompt",
        "spans": [
            (PROMPT_PATH_TEXT, False, PROMPT_PATH),
            (" $ ", True, PROMPT_SIGN),
            (command, False, WHITE),
        ],
        "cursor": cursor,
    }


def render_prompt(command: str, cursor: bool = True) -> Image.Image:
    item = prompt_item(command)
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw_chrome(draw)
    draw_item(draw, 0, item, cursor=False)
    if cursor:
        y = CONTENT_TOP + CELL_H // 2
        x = CONTENT_LEFT + (cell_width(PROMPT_PATH_TEXT) + 3 + cell_width(command)) * CELL_W
        draw.rectangle([(x + 1, y - 8), (x + CELL_W - 1, y + 8)], fill=TEXT)
    return image


# --------------------------------------------------------------------------
# 分镜
# --------------------------------------------------------------------------


def slice_section(lines: list[str], start_marker: str, stop_markers: list[str]) -> list[str]:
    start = next((i for i, l in enumerate(lines) if l.startswith(start_marker)), None)
    if start is None:
        return []
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if any(lines[i].startswith(m) for m in stop_markers):
            end = i
            break
    return lines[start:end]


def build_frames(report_path: str) -> list[Image.Image]:
    raw = open(report_path, "r", encoding="utf-8").read().split("\n")

    head_end = next(i for i, l in enumerate(raw) if l.startswith("## 过期排险"))
    screen1 = build_display_lines(raw[:head_end])
    screen2 = build_display_lines(slice_section(raw, "## 积分变现效率排行", ["## ", "---"]))

    frames: list[Image.Image] = []

    # --- 阶段 A：逐字敲命令 ---
    for count in range(1, len(PROMPT_CMD) + 1):
        frames.append(render_prompt(PROMPT_CMD[:count], cursor=True))
    for _ in range(2):
        frames.append(render_prompt(PROMPT_CMD, cursor=False))

    # --- 阶段 B：输出第一屏 ---
    blank = {"kind": "blank", "spans": []}
    room1 = ROWS - 2
    for upto in range(1, room1 + 1):
        rows = [prompt_item(PROMPT_CMD), blank] + screen1[:upto]
        frames.append(render(rows, cursor_row=1 + upto))
    frames.append(render([prompt_item(PROMPT_CMD), blank] + screen1[:room1]))
    for _ in range(3):
        frames.append(render([prompt_item(PROMPT_CMD), blank] + screen1[:room1]))

    # --- 阶段 C：输出第二屏（效率排行）---
    head_rows = [prompt_item(PROMPT_CMD), {"kind": "blank", "spans": []}]
    room2 = ROWS - 2
    for upto in range(1, min(len(screen2), room2) + 1):
        frames.append(render(head_rows + screen2[:upto], cursor_row=1 + upto))
    final_rows = head_rows + screen2[:room2]
    frames.append(render(final_rows))

    # --- 阶段 D：尾部定格 ---
    for _ in range(16):
        frames.append(render(final_rows))

    return frames


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, os.pardir))
    report = os.path.join(root, "examples", "demo-report.md")
    output = os.path.join(root, "demo.gif")

    if not os.path.exists(report):
        print(f"找不到报告文件：{report}", file=sys.stderr)
        print(
            "请先运行：python3 scripts/points_vault.py --demo > examples/demo-report.md",
            file=sys.stderr,
        )
        return 2

    frames = build_frames(report)
    print(f"等宽字体：{ASCII_PATH}")
    print(f"中文字体：{CJK_PATH}")
    print(f"已渲染 {len(frames)} 帧，画布 {W}×{H}，网格 {MAX_COLS}×{ROWS}")

    palette_source = frames[len(frames) // 2].quantize(colors=48, method=Image.MEDIANCUT)
    quantized = [f.quantize(palette=palette_source, dither=Image.Dither.NONE) for f in frames]

    quantized[0].save(
        output,
        save_all=True,
        append_images=quantized[1:],
        duration=int(1000 / FPS),
        loop=0,
        optimize=True,
        disposal=1,
    )

    size_mb = os.path.getsize(output) / 1024 / 1024
    print(f"✅ 已生成 {output}")
    print(f"   {size_mb:.2f} MB，{len(frames) / FPS:.1f} 秒，{len(frames)} 帧")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
