# -*- coding: utf-8 -*-
"""动效回归（v2.9.0 任务 8 永久化）：曲线 / 时长闸门 / hover 渐变 / 开关双链 /
菜单入场与底部渐隐 / 最大化还原 / 浮层淡入。

离线：probe=True + -alpha 0 建真实控件；动效断言段临时 probe=False。
不 patch 任何跨模块全局，单进程顺序跑安全。
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
    json.dump({'ui_skin': 'liquid-glass', 'ui_appearance': 'light'}, f)
os.environ['ABG_BASE'] = TMP
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402
import skinfix  # noqa: E402

# v2.10.0：液态玻璃不再内置，本测试的 standard 档语义靠用户皮肤 fixture。
skinfix.write_glass(TMP)

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
        f.write('from plugin_api import WBOXPlugin\nclass Plugin(WBOXPlugin):\n    pass\n')


def main():
    print('[0] 曲线与开关')
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
        print('[1] 时长闸门：probe / 极简 / 系统关动画 都归 0')
        check(app.anim_ms(160) == 0, 'probe 下 0ms')
        app.probe = False
        sys_on = sg.client_area_animation()
        check(app.anim_ms(160) == (160 if sys_on else 0),
              '玻璃档时长跟系统动画开关走（系统=%s）' % sys_on)
        app._motion_on = False
        check(app.anim_ms(240) == 0, '系统关动画 -> 0ms')
        app._motion_on = True
        app.set_skin('plain')
        root.update()
        check(app.anim_ms(240) == 0, '极简档零动画')
        swp = sg.Switch(app.main, app, value=False)
        root.update()
        swp.toggle()
        check(swp._anim_knob is None, '极简开关拨动直接跳终态')
        swp.destroy()
        app.set_skin('liquid-glass')
        root.update()

        print('[2] hover 160ms 渐变：中途是混合色，终态是 hover 色')
        lab = tk.Label(app.main, text='x', bg=app.colors['bg'])
        lab.pack()
        root.update()
        base, hot = app.colors['bg'], app.colors['hover']
        app.hover([lab], True)
        pump(root, 0.06)
        mid = lab.cget('bg')
        check(mid not in (base, hot), '60ms 时是中间色：%s' % mid)
        pump(root, 0.25)
        check(lab.cget('bg') == hot, '250ms 后到 hover 色')
        app.hover([lab], False)
        pump(root, 0.25)
        check(lab.cget('bg') == base, 'leave 渐回底色')
        lab.destroy()

        print('[3] 开关：把手 240 / 轨道 220 两条链')
        sw = sg.Switch(app.main, app, value=False)
        root.update()
        old = sw.value
        sw.toggle()
        check(sw._anim_knob is not None and sw._anim_fill is not None,
              '拨动瞬间两条链都在跑')
        pump(root, 0.35)
        check(sw._anim_knob is None and sw._anim_fill is None
              and sw.value != old, '350ms 后链收干净、状态已翻')
        sw.toggle()
        sw.toggle()
        pump(root, 0.35)
        check(sw._anim_knob is None, '连点不留残链')

        print('[4] 菜单入场 200ms + 箭头翻转 + 底部渐隐')
        for i in range(6):
            make_plugin('p%d' % i, 10 + i)
        app.reload_registry()
        root.update()
        app.open_settings()
        root.update()
        alpha0 = float(app.settings_win.attributes('-alpha'))
        check(alpha0 < 0.9, '菜单刚开是淡入起点：%0.2f' % alpha0)
        check(app.chev.flip is True, '菜单展开箭头翻向上')
        pump(root, 0.3)
        check(float(app.settings_win.attributes('-alpha')) == 1.0, '300ms 后全显')
        check(app._menu_fade is not None and
              len(app._menu_fade.find_all()) == 5,
              '内容超 560 高 -> 34px 渐隐五条带：%r'
              % (len(app._menu_fade.find_all()) if app._menu_fade else None,))
        app.close_settings()
        root.update()
        check(app.chev.flip is False and app._menu_fade is None,
              '收菜单箭头翻回、渐隐销毁')

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

        print('[6] 浮层入场淡入（插件设置 / 皮肤面板）')
        app.probe = False
        if app.entries:
            app.on_plugin_settings(app.entries[0])
            root.update()
            a0 = float(app.plugin_win.attributes('-alpha'))
            pump(root, 0.3)
            check(a0 < 0.9 and float(app.plugin_win.attributes('-alpha')) == 1.0,
                  '插件浮层淡入：%0.2f -> 1' % a0)
            app.close_plugin_settings()
        app.open_skin_settings()
        root.update()
        a0 = float(app.skin_win.attributes('-alpha'))
        pump(root, 0.3)
        check(a0 < 0.9 and float(app.skin_win.attributes('-alpha')) == 1.0,
              '皮肤面板淡入：%0.2f -> 1' % a0)
        app.close_skin_settings()
        root.update()
        check(not [w for w in root.winfo_children()
                   if isinstance(w, tk.Toplevel)], '收完无残骸 Toplevel')
    finally:
        app.probe = True
        app.close_skin_settings()
        app.close_settings()
        root.destroy()
    shutil.rmtree(TMP, ignore_errors=True)
    print('')
    if FAILS:
        print('FAILED %d：%s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()