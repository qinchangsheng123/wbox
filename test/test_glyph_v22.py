# -*- coding: utf-8 -*-
"""v2.2.0 图标回归：每个 kind 在双主题 / 置灰 / hover 下都能画出图元。

钉住三件事：
  1. 新图标名 plane / pulse / folder / admin 已进白名单，不会被宿主回落成 dot；
  2. Glyph 不再出现空白方块（未知 kind 也有兜底）；
  3. 品牌盾在 16px 托盘尺寸下仍画得出东西（不再依赖背景色镂空）。
"""
import os
import re
import shutil
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix='abg_glyph_')
os.makedirs(os.path.join(TMP, 'plugins'))
os.environ['ABG_BASE'] = TMP
sys.path.insert(0, os.path.join(ROOT, 'src'))

import plugin_api
# v2.3.0 复核补丁 R2：BASE 是 import 期固化的，同进程跑第二个 GUI 测试前必须摘掉旧模块
sys.modules.pop('settings_gui', None)
import settings_gui as sg


def step(cond, msg):
    if not cond:
        raise AssertionError(msg)


class FakeApp(object):
    def __init__(self, colors):
        self.colors = colors

    def px(self, v):
        return v


def main():
    kinds = sorted(set(plugin_api.ICON_WHITELIST) | set(('mark',)))
    for name in ('plane', 'pulse', 'folder', 'admin', 'plus', 'trash'):
        step(name in kinds, '%s 在图标白名单里' % name)
    print('[1] 白名单共 %d 个 kind，新增图标名齐全' % len(kinds))

    root = tk.Tk()
    root.withdraw()
    try:
        for theme in ('light', 'dark'):
            app = FakeApp(sg.THEMES[theme])
            for k in kinds:
                g = sg.Glyph(root, app, k, size=24)
                step(len(g.find_all()) > 0, '%s/%s 画出了图元' % (theme, k))
                g.set_dim(True)
                step(len(g.find_all()) > 0, '%s/%s 置灰后仍有图元' % (theme, k))
                g.set_bg(app.colors['hover'])
                step(len(g.find_all()) > 0, '%s/%s hover 换底后仍有图元' % (theme, k))
                g.destroy()
        print('[2] 双主题 x 置灰 x hover：全部 %d 个 kind 无空白' % len(kinds))

        for theme in ('light', 'dark'):
            app = FakeApp(sg.THEMES[theme])
            for size in (16, 20, 22, 28):
                g = sg.Glyph(root, app, 'mark', size=size)
                items = g.find_all()
                step(len(items) == 1, '%s 品牌 W %dpx 是一块负形多边形' % (theme, size))
                step(g.type(items[0]) == 'polygon',
                     '%s 品牌 W %dpx 用多边形画（5 笔封口，不靠线宽）' % (theme, size))
                co = g.coords(items[0])
                xs, ys = co[0::2], co[1::2]
                step(min(xs) <= size * 0.12 and max(xs) >= size * 0.88,
                     '%s 品牌 W %dpx 横向占满画布（面板里没有方块容器）' % (theme, size))
                g.destroy()
        print('[3] 品牌 W 16/20/22/28px 均为单块负形多边形且横向占满')

        app = FakeApp(sg.THEMES['light'])
        g = sg.Glyph(root, app, '__unknown_kind__', size=24)
        step(len(g.find_all()) > 0, '未知 kind 兜底成点')
        g.destroy()
        print('[4] 未知 kind 兜底成点，不留空白')

        # v2.3.0：这里原来写死等值比对 '2.2.0'，每次升版本都得改测试。
        # 真正要守的是"版本号是三段式 vMAJOR.MINOR.PATCH"这条硬约束
        # （面板底部小字、安装器标题、注册表 DisplayVersion 都读它），
        # 而不是某个具体数字，所以改成格式断言。
        # v2.3.0 复核补丁：用 [0-9] 而不是 \d —— Python 的 \d 认 Unicode 数字，
        # 全角 '２.３.０' 会混过 ^\d+\.\d+\.\d+$（QA P2-3）。
        step(re.match(r'^[0-9]+\.[0-9]+\.[0-9]+$', sg.VERSION or '') is not None,
             '面板版本号是三段式 vMAJOR.MINOR.PATCH，实为 %r' % sg.VERSION)
        print('[5] VERSION = %s' % sg.VERSION)
    finally:
        root.destroy()
        shutil.rmtree(TMP, ignore_errors=True)
    print('ALL PASS')


if __name__ == '__main__':
    main()
