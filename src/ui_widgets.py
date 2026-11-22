# -*- coding: utf-8 -*-
"""自绘控件：开关（Switch）、线性图标（Glyph）、悬停提示（ToolTip）。

为什么不用 ttk：ttk 主题由系统绘制，深色皮肤下会给按钮铺一块系统白底，也没法
控制圆角与描边。三个控件都是 tk.Canvas 子类，几何与配色全部来自宿主 App 的
token，所以换皮肤只换 token，控件代码零改动。
"""
import math
import tkinter as tk

from sprite import (COV_CACHE as _COV_CACHE,
                    cov_cached as _cov_cached, coverage as _coverage,
                    sprite as _sprite)
from ui_gfx import mix_hex

# ---- Switch / Glyph / ToolTip ----
class Dot(tk.Canvas):
    """实心状态点（v2.15.0，插件行「行内」档专用）。

    6px 的圆在 tk.Canvas 的 create_oval 下没有抗锯齿，边是一圈阶梯；
    所以和图标、开关一样走 sprite 的 4×4 超采样。尺寸跟皮肤走（px），
    底色由宿主在悬停时 set_bg() 同步，避免边缘留一圈旧底色。
    """

    def __init__(self, master, app, color, size=None):
        self.app = app
        self.color = color
        self.d = size or app.px(6)
        self.bg = app.colors['bg']
        tk.Canvas.__init__(self, master, width=self.d, height=self.d,
                           bg=self.bg, highlightthickness=0, bd=0)
        self.draw()

    def set_color(self, color):
        self.color = color
        self.draw()

    def set_bg(self, color):
        self.bg = color
        try:
            self.configure(bg=color)
        except Exception:
            pass
        self.draw()

    def draw(self):
        self.delete('all')
        key = ('__dot__', self.d)
        cov = _COV_CACHE.get(key)
        if cov is None:
            cov = _coverage(self.d, self.d,
                            [(1, self.d / 2.0, self.d / 2.0, self.d / 2.0)])
            _COV_CACHE[key] = cov
        self._img = _sprite(self, self.d, self.d, [(cov, self.color)], self.bg)
        self.create_image(0, 0, anchor='nw', image=self._img)


class Switch(tk.Canvas):
    """开关：36×18 胶囊（行内 30×16）。

    v2.17.0 起骨架只剩默认一套，尺寸不再跟皮肤的 performance 分档，
    所有皮肤都是这一组刻度（省一半像素，也就不用为第三方皮肤多画一版）。
    """

    def __init__(self, master, app, value=False, command=None, small=False):
        self.app = app
        self.value = bool(value)
        self.command = command
        self.enabled = True
        self.small = bool(small)
        self._anim_knob = None      # 动效中的把手位置 0..1（None = 按 value 画）
        self._anim_fill = None      # 动效中的轨道色（None = 按 value 取 token）
        # 尺寸固定：36×18（行内 30×16），不再按 performance 分两档。
        if self.small:
            self.w = app.px(30)
            self.h = app.px(16)
        else:
            self.w = app.px(36)
            self.h = app.px(18)
        self.bg = app.colors['bg']
        self._focused = False
        tk.Canvas.__init__(self, master, width=self.w, height=self.h,
                           bg=self.bg, highlightthickness=0, bd=0,
                           cursor='hand2', takefocus=1)
        self.bind('<Button-1>', self.toggle)
        self.bind('<Return>', self.toggle)
        self.bind('<space>', self.toggle)
        self.bind('<FocusIn>', self._focus_in, add='+')
        self.bind('<FocusOut>', self._focus_out, add='+')
        self.draw()

    def _focus_in(self, ev=None):
        self._focused = True
        self.draw()

    def _focus_out(self, ev=None):
        self._focused = False
        self.draw()

    def toggle(self, ev=None):
        if not self.enabled:
            return
        old = self.value
        self.value = not old
        ms = self.app.anim_ms(240)
        if ms <= 0:
            self.draw()
        else:
            # 设计稿：把手 240ms、轨道 220ms，同一条 ease-out 曲线
            frm, to = self._track_fill(old), self._track_fill(self.value)
            self.app.tween(('swk', id(self)), ms,
                           lambda p: self._set_knob(1 - p if old else p),
                           done=lambda: self._set_knob(None))
            self.app.tween(('swt', id(self)), self.app.anim_ms(220),
                           lambda p: self._set_fill(mix_hex(frm, to, p)),
                           done=lambda: self._set_fill(None))
        if self.command:
            self.command()

    def _track_fill(self, value):
        c = self.app.colors
        tok = getattr(self.app, 'tok', {})
        if not self.enabled:
            return c['line'] if value else self.bg
        if value:
            return tok.get('on', c['inv'])
        return tok.get('switch_off', c['line'])

    def _set_knob(self, p):
        self._anim_knob = p
        try:
            self.draw()
        except Exception:
            pass

    def _set_fill(self, color):
        self._anim_fill = color
        try:
            self.draw()
        except Exception:
            pass

    def _stop_anim(self):
        self.app.cancel_tween(('swk', id(self)))
        self.app.cancel_tween(('swt', id(self)))
        self._anim_knob = None
        self._anim_fill = None

    def set_enabled(self, on):
        """置灰：不再响应点击，纯灰阶绘制（只读模式用于"需要管理员权限"）。"""
        self.enabled = bool(on)
        try:
            self.configure(cursor='hand2' if self.enabled else 'arrow',
                           takefocus=1 if self.enabled else 0)
        except Exception:
            pass
        self._stop_anim()
        self.draw()

    def set_value(self, value):
        self.value = bool(value)
        self._stop_anim()
        self.draw()

    def set_bg(self, color):
        self.bg = color
        try:
            self.configure(bg=color)
        except Exception:
            pass
        self.draw()

    def draw(self):
        self.delete('all')
        c = self.app.colors
        w, h = self.w, self.h
        tok = getattr(self.app, 'tok', {})
        off = tok.get('switch_off', c['line'])
        if self.enabled:
            if self.value:
                fill, knob, line = tok.get('on', c['inv']), tok.get('on_knob', c['invfg']), ''
            else:
                # 关闭态不要再描一圈主文字色：iOS 的关是「灰轨道 + 白钮」，
                # 描边会让它看起来像个没填色的按钮，比开态还抢眼。
                fill, knob, line = off, tok.get('knob', '#FFFFFF'), ''
        else:
            # 置灰：全部用中性灰阶，不出现可点击的反色
            fill, line, knob = (c['line'] if self.value else self.bg), c['line'], c['t3']
        if self._anim_fill is not None:
            fill = self._anim_fill
        pad = self.app.px(2)
        k = max(self.app.px(6), h - 2 * pad)     # 把手直径 = 高度减两倍内边距
        kp = self._anim_knob if self._anim_knob is not None else (
            1.0 if self.value else 0.0)
        x0 = pad + (w - 2 * pad - k) * kp
        # **方角**轨道 + 方角把手（设计稿 .p-sw 36×18 / .p-sw.sm 30×16）。
        # v2.12.1：原来这里画的是胶囊，跟设计稿的方角差一截——默认档的
        # 卖点是"方角 + 原生控件"，开关自己先圆了就没法讲这句话。
        # 边缘都落在整像素上，不需要抗锯齿光栅，直接画矩形最省。
        # v2.17.0：骨架只剩这一套，胶囊分支随玻璃骨架删掉了。
        self.create_rectangle(0, 0, w, h, fill=fill, outline='')
        ky = (h - k) / 2.0
        self.create_rectangle(x0, ky, x0 + k, ky + k, fill=knob, outline='')
        if self._focused:
            # 开态轨道是实心底，焦点环反色才看得见；关态用 fg。
            # 环贴着轨道外沿画（Canvas 画不出控件外偏移，见 App._ring 注释）。
            col = c['bg'] if (self.enabled and self.value) else c['fg']
            self.create_rectangle(0.5, 0.5, w - 0.5, h - 0.5,
                                  outline=col, width=1, tags='focusring')


class Glyph(tk.Canvas):
    """统一图标（v2.2.0）：品牌盾唯一实心，其余全线性。

    不画背景色、不用背景色伪造镂空；几何与底色解耦，hover 只换底色。
    24x24 设计坐标（品牌盾 64x64），统一光学框 20x20、安全边距 2，
    线宽 round(1.8 * size/24)，方端尖角，双主题只翻 token。
    """

    def __init__(self, master, app, kind, size=None, ink=None, badge=None):
        self.app = app
        self.kind = kind
        self.s = size or app.px(22)
        self.bg = app.colors['bg']
        self.dim = False
        # 显式指定墨色：标题栏的加号/箭头/窗口控制不能用默认的 t2，
        # 设计稿里它们分别是 fg / t3 / t3 三档。
        self.ink = ink
        # v2.15.0 状态角标：图标右上角挂一枚状态点（插件行专用）。
        # Canvas 向外扩出 pad，图标整体下移 pad——多出来的右上角空白归角标。
        # 角标必须落在图标框外，压着 16px 图标的线条画多少 px 都糊。
        self.badge = badge
        self.pad = app.px(6) if badge else 0
        self.ox, self.oy = 0, self.pad
        self.cw = self.ch = self.s + self.pad
        # 一律走程序化抗锯齿光栅（见 sprite.coverage 注释）：
        # v2.17.0 起没有「standard 档用矢量图元」那条路了，图标与开关
        # 在小尺寸下都要抗锯齿，覆盖率按 (kind, size) 缓存，重绘只做 colorize。
        self._raster = True
        tk.Canvas.__init__(self, master, width=self.cw, height=self.ch,
                           bg=self.bg, highlightthickness=0, bd=0)
        self.draw()

    def set_dim(self, on):
        self.dim = bool(on)
        self.draw()

    def p(self, *vals):
        unit = self.s / (100.0 if self.kind == 'mark' else 24.0)
        return [v * unit for v in vals]

    def set_bg(self, color):
        self.bg = color
        try:
            self.configure(bg=color)
        except Exception:
            pass
        # 几何不依赖底色，重绘只为刷新 Canvas；不再有「镂空填背景色」。
        self.draw()

    def draw(self):
        self.delete('all')
        c = self.app.colors
        ink = c['fg'] if self.kind == 'mark' else c['t2']
        if self.ink:
            ink = c.get(self.ink, ink)
        if getattr(self, 'dim', False):
            ink = c['t3']
        lw_f = max(1.0, self.p(1.8)[0])   # 光栅用浮点线宽，AA 自己消化小数
        lw = max(1, int(round(lw_f)))
        u = self.s / (100.0 if self.kind == 'mark' else 24.0)
        raster = getattr(self, '_raster', False)
        groups = []                      # 光栅模式：[(prims, ink 色)] 依序叠色
        prims = []

        def P(pts):
            # 光栅模式先在 s×s 里算覆盖率（缓存 key 只认 kind/size/flip），
            # 出图时再用 create_image 平移；矢量模式直接按偏移量算坐标。
            if raster or not (self.ox or self.oy):
                return [v * u for v in pts]
            out = []
            for i, v in enumerate(pts):
                out.append(v * u + (self.ox if i % 2 == 0 else self.oy))
            return out

        def flush():
            if prims:
                groups.append((list(prims), ink))
                del prims[:]

        def L(pts):
            q = P(pts)
            if raster:
                for i in range(0, len(q) - 2, 2):
                    prims.append((0, q[i], q[i + 1], q[i + 2], q[i + 3], lw_f / 2))
            else:
                self.create_line(*q, fill=ink, width=lw)

        def P0(pts):
            q = P(pts)
            if raster:
                n = len(q)
                for i in range(0, n, 2):
                    j = (i + 2) % n
                    prims.append((0, q[i], q[i + 1], q[j], q[j + 1], lw_f / 2))
            else:
                self.create_polygon(*q, fill='', outline=ink, width=lw)

        def O(pts):
            q = P(pts)
            if raster:
                prims.append((2, (q[0] + q[2]) / 2, (q[1] + q[3]) / 2,
                              (q[2] - q[0]) / 2, lw_f / 2))
            else:
                self.create_oval(*q, fill='', outline=ink, width=lw)

        def OF(pts, color):
            q = P(pts)
            if raster:
                flush()
                groups.append(([(1, (q[0] + q[2]) / 2, (q[1] + q[3]) / 2,
                                 (q[2] - q[0]) / 2)], color))
            else:
                self.create_oval(*q, fill=color, outline='')

        def F(pts):
            q = P(pts)
            if raster:
                flush()
                groups.append(([(4, [(q[i], q[i + 1])
                                     for i in range(0, len(q), 2)])], ink))
            else:
                self.create_polygon(*q, fill=ink, outline='')

        def ARC(pts, start, extent):
            q = P(pts)
            if raster:
                cx, cy = (q[0] + q[2]) / 2, (q[1] + q[3]) / 2
                r = (q[2] - q[0]) / 2
                n = 24
                prev = None
                for i in range(n + 1):
                    a = math.radians(start + extent * i / float(n))
                    pt = (cx + r * math.cos(a), cy - r * math.sin(a))
                    if prev is not None:
                        prims.append((0, prev[0], prev[1], pt[0], pt[1], lw_f / 2))
                    prev = pt
            else:
                self.create_arc(*q, start=start, extent=extent,
                                style='arc', outline=ink, width=lw)

        k = self.kind
        if k == 'mark':
            # v2.8.0：品牌标记换成大写 W 负形（design/wbox-preview.html #w-glyph）。
            # 与旧盾牌的区别：5 笔封口多边形，16 px 下仍然认得出，也不和
            # 「ACE 防护」的盾牌图标撞形。
            # 坐标已按 1.4 倍放大到画布 5~95：面板里没有方块容器，
            # 原尺寸（18~82）在 16~22 px 上会显得又小又空。
            F((5.2, 19.2, 19.2, 19.2, 33.2, 66.8, 50, 36,
               66.8, 66.8, 80.8, 19.2, 94.8, 19.2,
               80.8, 80.8, 66.8, 80.8, 50, 58.4,
               33.2, 80.8, 19.2, 80.8))
        elif k == 'dot':
            # 唯一微实心例外：16px 下线性小圆会退化成空心环。
            OF((10, 10, 14, 14), c['t3'])
        elif k == 'plane':
            P0((2, 11, 22, 3, 15, 21, 11.5, 14.5))
            L((11.5, 14.5, 22, 3))
        elif k == 'pulse':
            P0((3, 5, 21, 5, 21, 19, 3, 19))
            L((5.5, 14.5, 9, 14.5, 11, 9.5, 13.5, 17, 15.5, 12.5, 19, 12.5))
        elif k == 'shield':
            P0((12, 2, 21, 6, 21, 13, 12, 22, 3, 13, 3, 6))
            L((8, 12, 11, 15, 16, 9))
        elif k == 'block':
            P0((2, 7, 15.5, 7, 15.5, 20, 2, 20))
            L((2, 11, 15.5, 11))
            L((17, 3, 22, 8))
            L((22, 3, 17, 8))
        elif k == 'link':
            # 旧版双向箭头，仅为兼容仍在 manifest 里写 link 的第三方插件。
            P0((3, 6.2, 16, 6.2, 16, 3.8, 21.5, 8, 16, 12.2, 16, 9.8, 3, 9.8))
            P0((21, 14.2, 8, 14.2, 8, 11.8, 2.5, 16, 8, 20.2, 8, 17.8, 21, 17.8))
        elif k == 'gear':
            O((9, 9, 15, 15))
            for i in range(6):
                a = -math.pi / 2 + i * math.pi / 3
                L((12 + 6 * math.cos(a), 12 + 6 * math.sin(a),
                   12 + 9 * math.cos(a), 12 + 9 * math.sin(a)))
        elif k == 'play':
            P0((8, 5, 19, 12, 8, 19))
        elif k == 'stop':
            P0((7, 7, 17, 7, 17, 17, 7, 17))
        elif k == 'boot':
            O((4, 4, 20, 20))
            L((12, 2, 12, 10))
        elif k == 'rescan':
            ARC((3, 3, 21, 21), 30, 300)
            L((18, 3, 21, 6, 18, 9))
        elif k == 'log':
            P0((4, 3, 18, 3, 18, 21, 4, 21))
            for yy in (8, 12, 16):
                L((7, yy, 15, yy))
        elif k == 'cfg':
            for yy in (7, 12, 17):
                L((3, yy, 21, yy))
            for box in ((8, 5, 11, 9), (14, 10, 17, 14), (6, 15, 9, 19)):
                O(box)
        elif k == 'sun':
            O((9, 9, 15, 15))
            for i in range(8):
                a = i * math.pi / 4
                L((12 + 8 * math.cos(a), 12 + 8 * math.sin(a),
                   12 + 10.5 * math.cos(a), 12 + 10.5 * math.sin(a)))
        elif k == 'moon':
            P0((16, 3, 9, 6, 6, 12, 9, 18, 16, 21, 11.5, 12))
        elif k == 'check':
            L((4, 12, 9.5, 17.5, 20, 6))
        elif k == 'close':
            L((5, 5, 19, 19))
            L((19, 5, 5, 19))
        elif k == 'plus':
            L((12, 4, 12, 20))
            L((4, 12, 20, 12))
        elif k == 'chev':
            # 字标右侧的下拉箭头：菜单展开时翻成向上
            if getattr(self, 'flip', False):
                L((7, 15, 12, 9.5, 17, 15))
            else:
                L((7, 9.5, 12, 15, 17, 9.5))
        elif k == 'chev_r':
            L((9.5, 6, 15.5, 12, 9.5, 18))
        elif k == 'min':
            L((6, 12.5, 18, 12.5))
        elif k == 'max':
            P0((6.5, 6.5, 17.5, 6.5, 17.5, 17.5, 6.5, 17.5))
        elif k == 'restore':
            P0((8.5, 5.5, 18.5, 5.5, 18.5, 15.5, 8.5, 15.5))
            L((5.5, 8.5, 5.5, 18.5, 15.5, 18.5))
        elif k == 'trash':
            L((4, 6, 20, 6))
            L((9, 6, 9, 4, 15, 4, 15, 6))
            P0((6, 6, 18, 6, 17, 21, 7, 21))
            L((10, 10, 10, 17))
            L((14, 10, 14, 17))
        elif k == 'folder':
            P0((3, 7, 9, 7, 11, 9, 21, 9, 21, 19, 3, 19))
        elif k == 'admin':
            P0((6, 11, 18, 11, 18, 21, 6, 21))
            L((9, 11, 9, 8, 10, 5, 14, 5, 15, 8, 15, 11))
        elif k == 'tray':
            # 收进托盘：下箭头落进开口槽
            L((12, 3, 12, 13))
            L((8, 9.5, 12, 13.5, 16, 9.5))
            L((3, 13, 3, 19, 21, 19, 21, 13))
        elif k == 'bell':
            L((12, 3, 12, 5))
            ARC((5, 5, 19, 17), 0, 180)
            L((5, 11, 5, 15, 3, 17, 21, 17, 19, 15, 19, 11))
            L((10, 20, 14, 20))
        else:
            # 未知 kind 兜底成点，绝不留空白方块。
            OF((10, 10, 14, 14), c['t3'])
        if raster:
            flush()
            key = (self.kind, self.s, bool(getattr(self, 'flip', False)))
            covs = _COV_CACHE.get(key)
            if covs is None:
                covs = [_coverage(self.s, self.s, g[0]) for g in groups]
                _COV_CACHE[key] = covs
            self._img = _sprite(self, self.s, self.s,
                                list(zip(covs, [g[1] for g in groups])), self.bg)
            self.create_image(self.ox, self.oy, anchor='nw', image=self._img)
        if self.badge:
            self.draw_badge()

    def draw_badge(self):
        """图标右上角的状态角标（v2.15.0，插件行专用）。

        三个约束缺一不可：
          · 直径 7px —— 5px 时扣掉隔离环只剩 2px 实心，肉眼只是一团灰；
          · 圆心在图标框右外 1px、上外 1px —— 压在框内必然啃图标线条，
            16px 图标的安全边距只有 2px，没有地方放一枚干净的点；
          · 4×4 超采样 —— tk.Canvas 的 create_oval 没有抗锯齿，7px 圆是阶梯块。

        隔离环用 self.bg 画大一圈，把图标线条从点下面擦掉。bg 由 set_bg() 在
        悬停 / 换肤 / 换明暗时同步，所以这一圈永远等于行底色，不需要额外挂钩子。
        """
        d = self.app.px(10)                    # 外径（含隔离环）
        r_in = self.app.px(7) / 2.0            # 实心半径
        key = ('__badge__', d, r_in)
        covs = _COV_CACHE.get(key)
        if covs is None:
            covs = [_coverage(d, d, [(1, d / 2.0, d / 2.0, d / 2.0)]),
                    _coverage(d, d, [(1, d / 2.0, d / 2.0, r_in)])]
            _COV_CACHE[key] = covs
        self._bimg = _sprite(self, d, d,
                             [(covs[0], self.bg), (covs[1], self.badge)],
                             self.bg)
        cx = self.ox + self.s + self.app.px(1)
        cy = self.oy + self.app.px(1)
        self.create_image(cx - d // 2, cy - d // 2, anchor='nw',
                          image=self._bimg)

class ToolTip(object):
    """悬停名称 320ms 后弹出反色简介；移开、点击、切主题立即收起。"""

    def __init__(self, app, widget, text):
        self.app = app
        self.widget = widget
        self.text = text
        self.tip = None
        self.job = None
        widget.bind('<Enter>', self.schedule)
        widget.bind('<Leave>', self.hide)
        widget.bind('<ButtonPress>', self.hide)

    def schedule(self, ev=None):
        self.cancel()
        self.job = self.widget.after(320, self.show)

    def cancel(self):
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except Exception:
                pass
            self.job = None

    def show(self):
        if self.tip or not self.widget.winfo_exists():
            return
        c = self.app.colors
        tip = tk.Toplevel(self.app.root)
        tip.wm_overrideredirect(True)
        try:
            tip.wm_attributes('-topmost', True)
        except Exception:
            pass
        frm = tk.Frame(tip, bg=c['tipbg'], highlightthickness=1,
                       highlightbackground=c['line'])
        frm.pack()
        tk.Label(frm, text=self.text, justify='left', anchor='w',
                 bg=c['tipbg'], fg=c['tipfg'], font=self.app.f_small,
                 wraplength=self.app.px(320), padx=self.app.px(10),
                 pady=self.app.px(8)).pack()
        tip.update_idletasks()
        x, y = self.widget.winfo_pointerxy()
        x += self.app.px(14)
        y += self.app.px(16)
        w, h = tip.winfo_width(), tip.winfo_height()
        sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
        if x + w > sw - 8:
            x = max(8, sw - w - 8)
        if y + h > sh - 8:
            y = max(8, sh - h - 8)
        tip.wm_geometry('+%d+%d' % (x, y))
        self.tip = tip
        ms = self.app.anim_ms(180)
        if ms > 0:
            def step(p):
                try:
                    tip.attributes('-alpha', p)
                except Exception:
                    pass
            try:
                tip.attributes('-alpha', 0.0)
                self.app.tween(('tip', id(tip)), ms, step)
            except Exception:
                pass

    def hide(self, ev=None):
        self.cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None
