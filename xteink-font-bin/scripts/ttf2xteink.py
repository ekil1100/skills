#!/usr/bin/env python3
"""
TTF/OTF -> 阅星瞳 X4 / XTEink 家族墨水屏阅读器 .bin 点阵字体转换器

为什么需要专门的脚本：这类阅读器（阅星瞳 X4、EDCBook、墨菲 3.7、武哒哒·小墨等，
以及 crosspet 开源固件）只认一种定长 1-bit 点阵 .bin 字体，且固件从“文件名”读取
格子尺寸。社区现成的网页转换器在量格子宽度时通常只测拉丁字母，遇到 Maple Mono /
霞鹜文楷这种“汉字 = 2×字母宽”的字体会把汉字裁掉一半——所以这里按汉字实际墨迹测量。

bin 格式（与 crosspet 固件 lib/ExternalFont/ExternalFont.cpp 完全一致）：
  - 文件 = 65536 个 Unicode BMP 码点的定长 1-bit 点阵格子，按码点顺序紧密排列
    （码点 N 的数据偏移 = N * bytesPerChar，缺字的格子全 0，固件按缺字处理）
  - bytesPerRow = ceil(cellW / 8)；bytesPerChar = bytesPerRow * cellH
  - 行优先、每字节高位(MSB)在最左像素
  - 文件名必须是 `字体名_字号_宽x高.bin`（如 MapleMonoCN_36_35x43.bin），
    固件从中解析字号与格子宽高，名字里不要再出现下划线以外的分隔
  - 固件排版：全角(CJK)占满格宽，半角按墨迹宽+2px 紧排，所以中英混排自动正常

用法：
  # 单字号
  python3 ttf2xteink.py 字体.ttf --size 36 --name MapleMonoCN --out bin-fonts --preview

  # 一次多字号（最常见，墨水屏一般备好几档）
  python3 ttf2xteink.py 字体.ttf --size 28 32 36 40 --name MapleMonoCN --out bin-fonts --preview

依赖：freetype-py、pillow（仅 --preview 需要 pillow）
  pip3 install --user freetype-py pillow
"""
import argparse
import math
from pathlib import Path

import freetype

# 这些码点应渲染为空白格（即使字体把它们画成 .notdef 也要留空），与固件预期一致
WHITESPACE = {
    0x0020, 0x00A0, 0x1680,
    0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005,
    0x2006, 0x2007, 0x2008, 0x2009, 0x200A,
    0x202F, 0x205F, 0x3000,
    0x0009, 0x000A, 0x000B, 0x000C, 0x000D,
    0x0085, 0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF,
}

LOAD_FLAGS = freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_MONO

# 量格子尺寸用的代表性字符。覆盖 ASCII / 拉丁补充 / CJK 标点 / 常用汉字 / 全角，
# 这样汉字不会被裁。注意排除 U+3031–3035 竖排重复符号——它们是双倍高度的离群
# 字形，混进来会把行高撑大一倍。
MEASURE_RANGES = [
    (0x0021, 0x007E),
    (0x00C0, 0x00FF),
    (0x3001, 0x3030),
    (0x3036, 0x303F),
    (0x4E00, 0x9FFF),
    (0xFF01, 0xFF5E),
]


def mono_bitmap_rows(bitmap):
    """把 FreeType MONO bitmap 每行解成一个整数（宽 bitmap.width bit，MSB 在最左）。"""
    pitch = bitmap.pitch
    w = bitmap.width
    buf = bytes(bitmap.buffer)
    rows = []
    for y in range(bitmap.rows):
        row = buf[y * pitch:(y + 1) * pitch]
        v = (int.from_bytes(row, "big") >> (pitch * 8 - w)) if w else 0
        rows.append(v)
    return rows


def measure(face, size):
    """扫描代表性字符，返回 (最大墨迹宽, 最大上伸, 最大下伸)。"""
    face.set_pixel_sizes(0, size)
    max_w = max_asc = max_desc = 0
    for lo, hi in MEASURE_RANGES:
        for cp in range(lo, hi + 1):
            if face.get_char_index(cp) == 0:
                continue
            face.load_char(chr(cp), LOAD_FLAGS)
            g = face.glyph
            if g.bitmap.width == 0 or g.bitmap.rows == 0:
                continue
            max_w = max(max_w, g.bitmap.width)
            max_asc = max(max_asc, g.bitmap_top)
            max_desc = max(max_desc, g.bitmap.rows - g.bitmap_top)
    return max_w, max_asc, max_desc


def convert(font_path, size, name, char_spacing, line_spacing, out_dir):
    face = freetype.Face(str(font_path))
    max_w, max_asc, max_desc = measure(face, size)

    cell_w = max_w + char_spacing
    cell_h = max_asc + max_desc + line_spacing
    baseline = max_asc + line_spacing // 2

    width_byte = math.ceil(cell_w / 8)
    char_byte = width_byte * cell_h
    pad = width_byte * 8 - cell_w  # 行内尾部填充位数
    total = 0x10000

    print(f"  墨迹测量: 宽 {max_w}px, 上伸 {max_asc}px, 下伸 {max_desc}px")
    print(f"  格子: {cell_w}x{cell_h}, 基线 {baseline}, 每字 {char_byte} 字节, "
          f"文件约 {char_byte * total / 1024 / 1024:.1f} MB")

    buf = bytearray(char_byte * total)
    rendered = 0

    for cp in range(total):
        if cp in WHITESPACE or face.get_char_index(cp) == 0:
            continue
        face.load_char(chr(cp), LOAD_FLAGS)
        g = face.glyph
        bw, bh = g.bitmap.width, g.bitmap.rows
        if bw == 0 or bh == 0:
            continue

        rows = mono_bitmap_rows(g.bitmap)
        dx = (cell_w - bw) // 2
        dy = baseline - g.bitmap_top

        # 水平越界裁切（极少数过宽字形）
        if dx < 0:
            crop_l = -dx
            bw -= crop_l
            rows = [r & ((1 << bw) - 1) for r in rows]
            dx = 0
        if dx + bw > cell_w:
            crop_r = dx + bw - cell_w
            bw -= crop_r
            rows = [r >> crop_r for r in rows]

        base = cp * char_byte
        wrote = False
        for y, rv in enumerate(rows):
            cy = dy + y
            if cy < 0 or cy >= cell_h or rv == 0:
                continue  # 垂直越界或空行
            cell_row = rv << (cell_w - dx - bw) << pad
            off = base + cy * width_byte
            for i, b in enumerate(cell_row.to_bytes(width_byte, "big")):
                if b:
                    buf[off + i] |= b
            wrote = True
        if wrote:
            rendered += 1

    out_path = Path(out_dir) / f"{name}_{size}_{cell_w}x{cell_h}.bin"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(buf)
    print(f"  已渲染 {rendered} 个字符 -> {out_path}")
    return out_path, cell_w, cell_h


def render_preview(bin_path, cell_w, cell_h, out_png):
    """按固件排版逻辑（全角占满格、半角按墨迹宽+2px）渲染预览 PNG，用于验证效果。"""
    from PIL import Image

    text_lines = [
        "永远相信美好的事情即将发生。",
        "墨水屏阅读器，等宽字体测试！",
        "The quick brown fox 0123456789",
        "「中英混排」Maple Mono — NF/CN ©",
    ]
    width_byte = math.ceil(cell_w / 8)
    char_byte = width_byte * cell_h
    data = bin_path.read_bytes()

    def glyph_cell(cp):
        base = cp * char_byte
        cell = []
        for y in range(cell_h):
            row = int.from_bytes(data[base + y * width_byte: base + (y + 1) * width_byte], "big")
            cell.append(row >> (width_byte * 8 - cell_w))
        return cell

    def is_fullwidth(cp):
        return (0x2E80 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x30FF
                or 0xF900 <= cp <= 0xFAFF or 0xFF00 <= cp <= 0xFF60)

    img_w = 480
    img_h = (cell_h + 4) * len(text_lines) + 8
    img = Image.new("1", (img_w, img_h), 1)
    px = img.load()

    for li, line in enumerate(text_lines):
        x_cursor = 4
        y0 = 4 + li * (cell_h + 4)
        for ch in line:
            cp = ord(ch)
            if cp > 0xFFFF:
                continue
            cell = glyph_cell(cp)
            ink_min, ink_max = cell_w, -1
            for rv in cell:
                if rv:
                    hi = cell_w - 1 - (rv.bit_length() - 1)
                    lo = cell_w - 1 - ((rv & -rv).bit_length() - 1)
                    ink_min = min(ink_min, hi)
                    ink_max = max(ink_max, lo)
            if ink_max < 0:  # 空白
                x_cursor += cell_w if cp in (0x3000, 0x2003) else cell_w // 3
                continue
            if is_fullwidth(cp):
                draw_off, adv = 0, cell_w
            else:
                draw_off, adv = ink_min, (ink_max - ink_min + 1) + 2
            if x_cursor + adv > img_w:
                break
            for y, rv in enumerate(cell):
                for x in range(cell_w):
                    if rv >> (cell_w - 1 - x) & 1:
                        tx = x_cursor + x - draw_off
                        if 0 <= tx < img_w:
                            px[tx, y0 + y] = 0
            x_cursor += adv

    img.save(out_png)
    print(f"  预览图 -> {out_png}")


def main():
    ap = argparse.ArgumentParser(description="TTF/OTF -> 阅星瞳/XTEink .bin 点阵字体")
    ap.add_argument("font", help="源 .ttf / .otf 文件")
    ap.add_argument("--size", type=int, nargs="+", required=True,
                    help="一个或多个字号(像素)，如 --size 28 32 36 40")
    ap.add_argument("--name", default=None,
                    help="输出文件名中的字体名(默认取源文件名，会去掉下划线)")
    ap.add_argument("--char-spacing", type=int, default=0, help="额外字间距(像素)")
    ap.add_argument("--line-spacing", type=int, default=0, help="额外行间距(像素)")
    ap.add_argument("--out", default=".", help="输出目录")
    ap.add_argument("--preview", action="store_true", help="同时生成预览 PNG")
    args = ap.parse_args()

    font_path = Path(args.font)
    # 文件名里只能有一个用作分隔的下划线规则，所以把名字里的下划线换成连字符
    name = (args.name or font_path.stem).replace("_", "-")

    for size in args.size:
        print(f"转换 {font_path.name} @ {size}px ...")
        bin_path, cw, ch = convert(font_path, size, name,
                                   args.char_spacing, args.line_spacing, args.out)
        if args.preview:
            render_preview(bin_path, cw, ch, bin_path.with_suffix(".preview.png"))


if __name__ == "__main__":
    main()
