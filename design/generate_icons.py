# -*- coding: utf-8 -*-
"""WBOX 图标生成器 —— 零第三方依赖。

几何唯一来源是 design/wbox-preview.html 的 #wbox-icon / #w-glyph 符号：
100 × 100 画布，圆角 24 的方块承载颜色，大写 W 用负形亮出来。
两个颜色，没有渐变、没有描边、没有阴影、没有第二个元素。

  * ≥ 32 px：方块 + W（浅色主题黑底白 W，深色主题白底黑 W）
  * ≤ 24 px：去掉方块只留 W（方块在这个尺寸只剩 3 px 边，反而糊）

用扫描线 + 4x 超采样把矢量栅格化为 RGBA PNG，再组装成 Windows ICO。
运行：python design/generate_icons.py
"""
import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
PNG_DIR = os.path.join(ROOT, 'png')
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
BARE_UP_TO = 24          # 这个尺寸及以下不加方块容器
SS = 4                   # 每个方向 4 次超采样 = 16 子样本/像素

CANVAS = 100.0
CORNER = 24.0            # 圆角 24/100（≈ iOS 连续圆角）

# 大写 W：无描边填充多边形，笔画等宽约 10，中点峰 40 高于底谷 72
W_POLY = [(18, 28), (28, 28), (38, 62), (50, 40), (62, 62), (72, 28),
          (82, 28), (68, 72), (58, 72), (50, 56), (42, 72), (32, 72)]


def rounded_rect_poly(canvas=CANVAS, r=CORNER, steps=10):
    """圆角矩形（顺时针），用折线逼近四个 90° 圆弧。"""
    pts = []
    corners = ((canvas - r, r, -90), (canvas - r, canvas - r, 0),
               (r, canvas - r, 90), (r, r, 180))
    for cx, cy, a0 in corners:
        for i in range(steps + 1):
            a = math.radians(a0 + 90.0 * i / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


# ---------------------------------------------------------------- 光栅化
def _scan(polys, size, canvas):
    """even-odd 扫描线：返回 SS 倍分辨率的 0/255 覆盖行。"""
    side = int(size * SS)
    rows = []
    for sy in range(side):
        y = (sy + 0.5) * canvas / side
        xs = []
        for poly in polys:
            n = len(poly)
            for i in range(n):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % n]
                if (y1 <= y < y2) or (y2 <= y < y1):
                    xs.append(x1 + (x2 - x1) * (y - y1) / (y2 - y1))
        xs.sort()
        row = bytearray(side)
        for i in range(0, len(xs) - 1, 2):
            a = xs[i] / canvas * side
            b = xs[i + 1] / canvas * side
            if b <= a:
                continue
            lo = max(0, int(math.floor(a)))
            hi = min(side, int(math.ceil(b)))
            for x in range(lo, hi):
                cov = min(b, x + 1) - max(a, x)
                if cov <= 0:
                    continue
                row[x] = min(255, row[x] + int(round(cov * 255)))
        rows.append(row)
    return rows


def _down(rows, size):
    side = int(size * SS)
    out = bytearray(size * size)
    for y in range(size):
        for x in range(size):
            acc = 0
            base = x * SS
            for dy in range(SS):
                row = rows[y * SS + dy]
                for dx in range(SS):
                    acc += row[base + dx]
            out[y * size + x] = acc // (SS * SS)
    return out


def coverage(polys, size, canvas=CANVAS):
    return _down(_scan(polys, size, canvas), size)


# ---------------------------------------------------------------- 编码
def chunk(tag, data):
    return (struct.pack('>I', len(data)) + tag + data
            + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))


def png_bytes(size, px):
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)
        raw += px[y * stride:(y + 1) * stride]
    ihdr = struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(bytes(raw), 9))
            + chunk(b'IEND', b''))


def ico_bytes(entries):
    """ICO 目录 + PNG 条目（Vista 及以上原生支持，Win10/11 通用）。"""
    n = len(entries)
    out = bytearray(struct.pack('<HHH', 0, 1, n))
    offset = 6 + 16 * n
    for size, data in entries:
        dim = 0 if size >= 256 else size
        out += struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32,
                           len(data), offset)
        offset += len(data)
    for _size, data in entries:
        out += data
    return bytes(out)


def w_poly(scale=1.0):
    """W 多边形；scale>1 时以画布中心放大（小尺寸去容器后要吃掉方块那圈留白）。"""
    if scale == 1.0:
        return list(W_POLY)
    return [(50 + (x - 50) * scale, 50 + (y - 50) * scale) for x, y in W_POLY]


def render(size, bg, fg, bare=False):
    """合成一枚 RGBA PNG：方块用 bg，W 用 fg；bare=True 时不要方块。"""
    w_cov = coverage([w_poly(1.4 if bare else 1.0)], size)
    if bare:
        px = bytearray(size * size * 4)
        for i, a in enumerate(w_cov):
            o = i * 4
            px[o], px[o + 1], px[o + 2], px[o + 3] = bg[0], bg[1], bg[2], a
        return png_bytes(size, px)
    box_cov = coverage([rounded_rect_poly()], size)
    px = bytearray(size * size * 4)
    for i in range(size * size):
        a_box = box_cov[i] / 255.0
        a_w = (w_cov[i] / 255.0) * a_box        # W 只在方块内出现
        if a_box <= 0:
            continue
        # 方块色与 W 色按覆盖率混合，alpha 取方块覆盖率
        r = int(round(bg[0] * (1 - a_w) + fg[0] * a_w))
        g = int(round(bg[1] * (1 - a_w) + fg[1] * a_w))
        b = int(round(bg[2] * (1 - a_w) + fg[2] * a_w))
        o = i * 4
        px[o], px[o + 1], px[o + 2] = r, g, b
        px[o + 3] = box_cov[i]
    return png_bytes(size, px)


# ---------------------------------------------------------------- SVG 源
MARK_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
  <!-- WBOX：圆角方块承载颜色，大写 W 用负形亮出来。只有 #000 与 #FFF 两个值 -->
  <rect width="100" height="100" rx="24" fill="currentColor" opacity=".92"/>
  <path fill="#FFFFFF" d="M18 28H28L38 62L50 40L62 62L72 28H82L68 72H58L50 56L42 72H32Z"/>
</svg>
'''

GLYPH_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
  <!-- 无容器 W：面板内小尺寸、任务栏单色场景 -->
  <path fill="currentColor" d="M18 28H28L38 62L50 40L62 62L72 28H82L68 72H58L50 56L42 72H32Z"/>
</svg>
'''

# 功能图标：与应用图标同一套语言 —— 24 网格 / 实心几何 / 负形镂空 /
# 2.4 单位最小笔画 / 单色 currentColor。不用描边风格，避免与实心主标记割裂。
FEATURE_SVGS = {
    'link': '''<path d="M3 6.2 H16 V3.8 L21.5 8 L16 12.2 V9.8 H3 Z"/>
  <path d="M21 14.2 H8 V11.8 L2.5 16 L8 20.2 V17.8 H21 Z"/>''',
    'shield': '''<path fill-rule="evenodd" d="M12 2 L20.2 5 V11
    C20.2 16.2 16.8 19.7 12 22 C7.2 19.7 3.8 16.2 3.8 11 V5 Z
    M6.4 13 L8.2 11.2 L10.6 13.6 L15.9 8.3 L17.7 10.1 L10.6 17.2 Z"/>''',
    'block': '''<path fill-rule="evenodd" d="M5 3 H19 A2 2 0 0 1 21 5 V19
    A2 2 0 0 1 19 21 H5 A2 2 0 0 1 3 19 V5 A2 2 0 0 1 5 3 Z
    M5.08 6.92 L17.08 18.92 L18.92 17.08 L6.92 5.08 Z"/>''',
}


def feature_svg(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            'width="24" height="24" fill="currentColor">'
            + body + '</svg>\n')


# ---------------------------------------------------------------- 输出
def main():
    if not os.path.isdir(PNG_DIR):
        os.makedirs(PNG_DIR)

    with open(os.path.join(ROOT, 'wbox-mark.svg'), 'w', encoding='utf-8') as f:
        f.write(MARK_SVG)
    with open(os.path.join(ROOT, 'w-glyph.svg'), 'w', encoding='utf-8') as f:
        f.write(GLYPH_SVG)

    for name, body in FEATURE_SVGS.items():
        with open(os.path.join(ROOT, 'feature-%s.svg' % name), 'w',
                  encoding='utf-8') as f:
            f.write(feature_svg(body))

    sheet = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 112 24" '
             'width="336" height="72" fill="currentColor">']
    for i, (_name, body) in enumerate(FEATURE_SVGS.items()):
        sheet.append('<g transform="translate(%d 0)">%s</g>' % (i * 40, body))
    sheet.append('</svg>\n')
    with open(os.path.join(ROOT, 'feature-icons.svg'), 'w', encoding='utf-8') as f:
        f.write(''.join(sheet))

    # light = 黑底白 W（浅色任务栏 / 浅色桌面 / 安装包默认）
    # dark  = 白底黑 W（深色任务栏 / 深色主题）
    variants = (('light', (0, 0, 0), (255, 255, 255)),
                ('dark', (255, 255, 255), (0, 0, 0)))
    for label, bg, fg in variants:
        entries = []
        for size in SIZES:
            data = render(size, bg, fg, bare=(size <= BARE_UP_TO))
            entries.append((size, data))
            with open(os.path.join(PNG_DIR, '%s-%d.png' % (label, size)),
                      'wb') as f:
                f.write(data)
        path = os.path.join(ROOT, 'icon-%s.ico' % label)
        with open(path, 'wb') as f:
            f.write(ico_bytes(entries))
        print('%-6s -> %d PNG + icon-%s.ico (%d bytes)'
              % (label, len(entries), label, os.path.getsize(path)))

    print('SVG sources: wbox-mark.svg / w-glyph.svg / feature-icons.svg / feature-*.svg')


if __name__ == '__main__':
    main()
