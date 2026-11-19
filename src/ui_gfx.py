# -*- coding: utf-8 -*-
"""配色映射与 Canvas 绘图原语（面板自绘层的地基）。

Tk 没有圆角矩形、没有颜色过渡、没有贝塞尔缓动，这三样都得自己实现。这里只放
**不依赖任何窗口状态**的纯函数，因此面板、安装器界面、截图脚本可以共用。

颜色来源仍是 src/skins.py 的 token；legacy_colors 只是把 26 个 token 映射回
11 个历史键名（bg/fg/line/hover/inv…），新代码请直接用 app.tok['fill_2']。
"""
import ctypes
import math

import skins

# ---- LEGACY_KEYS ----
# 颜色不再写死：全部来自 src/skins.py 的 token（见 docs/SKIN-SPEC.md）。
# 旧代码里用的是 11 个历史键名（bg/fg/line/hover/inv…），这里只做一层映射，
# 新代码请直接用 app.tok['fill_2'] 这类 token 名，别再往 _legacy 里加键。
LEGACY_KEYS = ('bg', 'fg', 't2', 't3', 'line', 'border', 'hover', 'active',
               'inv', 'invfg', 'tipbg', 'tipfg')

# ---- legacy_colors / THEMES ----
def legacy_colors(tok, appearance='light'):
    """26 个 token -> 面板历史代码用的 11 个键（外加一个语义 danger）。"""
    return {
        'bg': tok['glass'], 'fg': tok['fg'], 't2': tok['t2'], 't3': tok['t3'],
        'line': tok['hair'], 'border': tok['hair_strong'],
        'hover': tok['fill_2'], 'active': tok['press'],
        'inv': tok['on'], 'invfg': tok['on_knob'],
        'tipbg': tok['fg'], 'tipfg': tok['glass'],
        't4': tok['t4'],                       # 第四级文字（禁用 / 极弱提示）
        'hair': tok['hair'], 'fill': tok['fill'], 'press': tok['press'],
        'inset': tok['inset'], 'line_strong': tok['hair_strong'],
        # 皮肤 token 里刻意没有彩色（零彩色是品牌约定），但「卸载 / 删除」
        # 这类破坏性操作需要一个可识别的红，单独给一个语义色，不进皮肤。
        'danger': '#B3261E' if appearance == 'light' else '#FF8A80',
    }


def _compat_themes():
    """THEMES['light'/'dark'] 的兼容壳：老代码、老测试按明暗两套色板取色。

    真正的光源是 skins.py——这里只把默认皮肤的 light/dark 两套 token 按旧键名
    摆好，避免每个调用点都改成 app.tok[...]。新代码请直接用 app.tok。
    """
    base = skins.get(skins.DEFAULT_ID) or skins.builtin_skins()[-1]
    return {k: legacy_colors(base.tokens(k), k) for k in ('light', 'dark')}


THEMES = _compat_themes()

# ---- round_rect ----
def round_rect(cv, x0, y0, x1, y1, r, **kw):
    """Canvas 没有原生圆角矩形：采样四段真圆弧，smooth=False 折线填充。

    v2.8.0 修正：原来靠 `smooth=True` 让 Tk 自己插值，样条会把直边也一起拉弯，
    卡片底边于是变成一条断断续续的虚线（1px 描边在 150% 缩放下尤其明显）。
    现在显式采样圆弧、直边保持真正的直线，描边才连续。
    """
    r = max(0.0, min(float(r), (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    if r <= 0:
        return cv.create_polygon(x0, y0, x1, y0, x1, y1, x0, y1, **kw)
    n = 8                      # 每角 8 段：20px 圆角上肉眼已经看不出折线
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90.0), (x1 - r, y1 - r, 0.0),
                       (x0 + r, y1 - r, 90.0), (x0 + r, y0 + r, 180.0)):
        for i in range(n + 1):
            a = math.radians(a0 + 90.0 * i / n)
            pts.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
    return cv.create_polygon(pts, smooth=False, **kw)


# ---- 抗锯齿光栅 + mix_hex / ease / 动画开关 ----
# ---------------- 程序化抗锯齿（极简档） ----------------
# 光栅化单一来源在 src/sprite.py：安装器极简界面与面板共用同一套实现与缓存。
from sprite import (SS as _SS, COV_CACHE as _COV_CACHE,
                    coverage as _coverage, cov_cached as _cov_cached,
                    sprite as _sprite, round_rect_pts as _rr_pts)


def mix_hex(c1, c2, p):
    """两个不透明 #RRGGBB 按 p 线性插值（Tk 没有 CSS transition，颜色自己混）。"""
    if p <= 0:
        return c1
    if p >= 1:
        return c2
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return '#%02X%02X%02X' % tuple(
        int(round(x + (y - x) * p)) for x, y in zip(a, b))


def _cb(t, a, b):
    u = 1 - t
    return 3 * a * u * u * t + 3 * b * u * t * t + t * t * t


def ease(x):
    """设计稿唯一动效曲线 cubic-bezier(.22,.61,.36,1)。

    牛顿法解 x(t)=x 得 t 再求 y(t)；6 轮足够收敛到 1e-4 以内。
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    t = x
    for _ in range(6):
        u = 1 - t
        fx = _cb(t, .22, .36) - x
        dx = 3 * .22 * u * u + 6 * (.36 - .22) * u * t + 3 * (1 - .36) * t * t
        if dx <= 1e-6:
            break
        t = min(1.0, max(0.0, t - fx / dx))
    return _cb(t, .61, 1.0)


def client_area_animation():
    """系统「在窗口中显示动画」开关（SPI_GETCLIENTAREAANIMATION=0x1042）。

    关掉 = 设计稿的 prefers-reduced-motion：所有动效时长归 0。
    极简档本来就零动画，这个开关只影响玻璃档。
    """
    try:
        v = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(v), 0)
        return bool(v.value)
    except Exception:
        return True
