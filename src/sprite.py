# -*- coding: utf-8 -*-
"""程序化抗锯齿光栅化（极简档共用单一来源）。

Tk Canvas 矢量图元没有抗锯齿：小尺寸下开关胶囊与图标斜边的阶梯锯齿
肉眼可见（v2.9.0 用户实测）。这里用超采样光栅化：每像素 4×4 子采样算
覆盖率，按覆盖率混 ink/底色写进 PhotoImage。纯 Python、不引 PIL；
覆盖率只依赖几何，按 (kind, size) 缓存，重绘只做廉价 colorize。
极简档零过渡，光栅化只发生在状态/配色变化时，不进每帧路径；
standard 档保留矢量图元（尺寸大 + 每帧 tween，光栅化反而贵）。

v2.11.0 从 settings_gui 抽出：安装器极简界面与面板共用同一套实现与缓存。
只依赖标准库；tkinter 仅在 sprite() 内惰性 import（卸载链不碰本模块）。
"""
import math

SS = 4
COV_CACHE = {}


def coverage(w, h, prims):
    """prims 图元组：
    (0, ax, ay, bx, by, hw)        线段描边（圆头）
    (1, cx, cy, r)                 实心圆
    (2, cx, cy, r, hw)             圆环描边
    (3, ax, ay, bx, by, r, hw)     胶囊环描边（置灰开关外沿）
    (4, [(x, y), ...])             实心多边形
    返回 w*h 覆盖率列表，值 0..1。"""
    cov = [0] * (w * h)
    step = 1.0 / SS
    total = float(SS * SS)
    for py in range(h):
        for px in range(w):
            cnt = 0
            for sy in range(SS):
                y = py + (sy + 0.5) * step
                for sx in range(SS):
                    x = px + (sx + 0.5) * step
                    for pr in prims:
                        t = pr[0]
                        if t == 0 or t == 3:
                            ax, ay, bx, by = pr[1], pr[2], pr[3], pr[4]
                            dx, dy = bx - ax, by - ay
                            l2 = dx * dx + dy * dy
                            tt = ((x - ax) * dx + (y - ay) * dy) / l2 if l2 else 0.0
                            tt = 0.0 if tt < 0.0 else (1.0 if tt > 1.0 else tt)
                            ex, ey = x - (ax + tt * dx), y - (ay + tt * dy)
                            d2 = ex * ex + ey * ey
                            if t == 0:
                                hit = d2 <= pr[5] * pr[5]
                            else:
                                hit = abs(math.sqrt(d2) - pr[5]) <= pr[6]
                        elif t == 1:
                            ex, ey = x - pr[1], y - pr[2]
                            hit = ex * ex + ey * ey <= pr[3] * pr[3]
                        elif t == 2:
                            ex, ey = x - pr[1], y - pr[2]
                            hit = abs(math.sqrt(ex * ex + ey * ey) - pr[3]) <= pr[4]
                        else:
                            pts = pr[1]
                            hit = False
                            j = len(pts) - 1
                            for i in range(len(pts)):
                                xi, yi = pts[i]
                                xj, yj = pts[j]
                                if (yi > y) != (yj > y) and \
                                        x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                                    hit = not hit
                                j = i
                        if hit:
                            cnt += 1
                            break
            cov[py * w + px] = cnt / total
    return cov


def cov_cached(key, w, h, prims):
    cov = COV_CACHE.get(key)
    if cov is None:
        cov = coverage(w, h, prims)
        COV_CACHE[key] = cov
    return cov


def sprite(widget, w, h, layers, bg):
    """layers=[(覆盖率, ink 色), ...] 依序叠在 bg 上，返回 PhotoImage。"""
    import tkinter as tk
    parsed = [(cov, int(ink[1:3], 16), int(ink[3:5], 16), int(ink[5:7], 16))
              for cov, ink in layers]
    br, bgc, bb = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
    rows = []
    for y in range(h):
        row = []
        off = y * w
        for x in range(w):
            r, g, b = br, bgc, bb
            for cov, ir, ig, ib in parsed:
                a = cov[off + x]
                if a <= 0.0:
                    continue
                if a >= 1.0:
                    r, g, b = ir, ig, ib
                else:
                    u = 1.0 - a
                    r = int(r * u + ir * a + 0.5)
                    g = int(g * u + ig * a + 0.5)
                    b = int(b * u + ib * a + 0.5)
            row.append('#%02X%02X%02X' % (r, g, b))
        rows.append(row)
    img = tk.PhotoImage(master=widget, width=w, height=h)
    img.put(rows)
    return img


def round_rect_pts(x0, y0, x1, y1, r, n=8):
    """圆角矩形顶点序列（供 coverage 的实心多边形图元用）。"""
    r = max(0.0, min(float(r), (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    if r <= 0:
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90.0), (x1 - r, y1 - r, 0.0),
                       (x0 + r, y1 - r, 90.0), (x0 + r, y0 + r, 180.0)):
        for i in range(n + 1):
            a = math.radians(a0 + 90.0 * i / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts