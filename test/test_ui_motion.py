# -*- coding: utf-8 -*-
"""零动画与浮层交互回归（v2.17.0 起只有默认一套骨架、过渡一律跳终态）。

历史：这份测试原本覆盖 standard 档（玻璃）的 tween 链——hover 160ms 渐变、
开关双链 240/220ms、菜单 200ms 淡入。骨架合并后动效整体退役，测试改成
反向断言：**任何皮肤、任何系统设置下都不该有过渡**，以及 tween(ms=0) 的
「直接跳终态」路径仍然正确（将来要加回动画，先让这些断言变红）。

离线：probe=True + -alpha 0 建真实控件；不 patch 跨模块全局，单进程顺序跑安全。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
TMP = tempfile.mkdtemp(prefix='abg_ui_motion_')
for name in ('icon-light.ico', 'icon-dark.ico'):
    src = os.path.join(ROOT, 'app', name)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(TMP, name))
with open(os.path.join(TMP, 'config.json'), 'w', encoding='utf-8') as f:
    json.dump({'ui_skin': 'plain', 'ui_appearance': 'light'}, f)
os.environ['ABG_BASE'] = TMP
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402
import skinfix  # noqa: E402

skinfix.write_glass(TMP)          # 第三方 standard 老包，见 [7]

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def pump(root, seconds):
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        root.update()
        time.sleep(0.01)


def make_plugin(pid, order):
    d = os.path.join(TMP, 'plugins', pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump({'id': pid, 'name': pid, 'version': '1.0.0', 'api': 1,
                   'icon': 'dot', 'order': order, 'home': True,
                   'toggle': [pid + '_enabled'], 'entry': 'plugin.py:Plugin'},
                  f)
    with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
        f.write('from plugin_api import WBOXPlugin\n'
                'class Plugin(WBOXPlugin):\n    pass\n')


def main():
    print('[0] 曲线与混色（tween 的数学基础还在，只是时长恒 0）')
    check(sg.ease(0) == 0 and sg.ease(1) == 1, 'ease 端点 0/1')
    vals = [sg.ease(i / 20.0) for i in range(21)]
    check(all(b >= a for a, b in zip(vals, vals[1:])) and vals[10] > 0.5,
          'ease 单调且前半程过半（ease-out）：%0.3f' % vals[10])
    check(sg.mix_hex('#000000', '#FFFFFF', 0.5) == '#808080', 'mix_hex 中点灰')

    sg.enable_dpi_awareness()
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=True)
    root.update()
    try:
        print('[1] 时长闸门：probe / 正常 / 系统开动画 全部 0ms')
        check(app.anim_ms(160) == 0, 'probe 下 0ms')
        app.probe = False
        check(app.anim_ms(160) == 0, '默认皮肤 0ms（不看系统动画开关）')
        check(app.anim_ms(2000) == 0, '长时长同样 0ms')

        print('[2] hover：立即到 hover 色，没有中间色')
        lab = tk.Label(app.main, text='x', bg=app.colors['bg'])
        lab.pack()
        root.update()
        base, hot = app.colors['bg'], app.colors['hover']
        app.hover([lab], True)
        root.update()
        check(lab.cget('bg') == hot, '进入即 hover 色：%s' % lab.cget('bg'))
        app.hover([lab], False)
        root.update()
        check(lab.cget('bg') == base, '离开即回底色')
        lab.destroy()

        print('[3] 开关：拨动直接跳终态，连点不留残链')
        sw = sg.Switch(app.main, app, value=False)
        root.update()
        old = sw.value
        sw.toggle()
        root.update()
        check(sw._anim_knob is None and sw._anim_fill is None,
              '拨动后没有动画中间态')
        check(sw.value != old, '状态已翻')
        sw.toggle()
        sw.toggle()
        root.update()
        check(sw._anim_knob is None and sw._anim_fill is None, '连点不留残链')
        check(app._tweens == {}, 'tween 表已空：%r' % (app._tweens,))
        sw.destroy()

        print('[4] 浮层：不淡入，打开即全显')
        for i in range(6):
            make_plugin('p%d' % i, 10 + i)
        app.reload_registry()
        root.update()
        if app.entries:
            app.on_plugin_settings(app.entries[0])
            root.update()
            check(float(app.plugin_win.attributes('-alpha')) == 1.0,
                  '插件浮层 alpha 直接 1')
            app.close_plugin_settings()
        app.open_skin_settings()
        root.update()
        check(float(app.skin_win.attributes('-alpha')) == 1.0,
              '皮肤面板 alpha 直接 1')
        app.close_skin_settings()
        root.update()

        print('[5] 最大化：铺满工作区 / 还原回原尺寸位置')
        lx, ly, sw_, sh = sg.work_area()
        geo0 = app.root.geometry()
        app.on_max()
        root.update()
        g = app.root.geometry()
        wh = g.split('+')[0].split('x')
        check(app._maximized and int(wh[0]) == sw_ and int(wh[1]) == sh,
              '最大化 = 工作区 %dx%d：%s' % (sw_, sh, g))
        check(app.max_wrap.glyph.kind == 'restore', '图标换 restore')
        check(app.board.winfo_reqwidth() >= sw_ - app.px(20),
              '列表跟着拉宽：%d' % app.board.winfo_reqwidth())
        app.on_max()
        root.update()
        check(not app._maximized and app.root.geometry() == geo0,
              '还原回最大化前 geometry')
        check(app.max_wrap.glyph.kind == 'max', '图标换回 max')

        print('[6] 收完无残骸 Toplevel')
        check(not [w for w in root.winfo_children()
                   if isinstance(w, tk.Toplevel)], '没有遗留 Toplevel')

        print('[7] 第三方 standard 老包：照常加载，按默认骨架渲染')
        app.set_skin('liquid-glass')
        root.update()
        check(app.skin.id == 'liquid-glass' and
              app.skin.performance == 'standard',
              '老包已加载：%r' % app.skin)
        check(app.skin.perf_label == '默认骨架',
              '成本标签统一：%s' % app.skin.perf_label)
        check(app.row_h == app.px(52), '行高仍按它自己的 metrics：%d' % app.row_h)
        check(getattr(app, 'pane_btn', None) is not None,
              '入口仍是右上角「设置」（不是字标菜单）')
        check(not hasattr(app, 'chev'), '没有玻璃档的下拉箭头')
        check(bool(app.plugin_gears) and all(
            w is not None and w.winfo_exists() for w in app.plugin_gears.values()),
            '每行行尾入口都建起来了')
        app.toggle_pane()
        root.update()
        check(app.pane == 'set' and app.set_inner is not None, '同窗换页到设置页')
        app.toggle_pane()
        root.update()
    finally:
        app.probe = True
        app.close_skin_settings()
        root.destroy()
    shutil.rmtree(TMP, ignore_errors=True)
    print('')
    if FAILS:
        print('FAILED %d：%s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
