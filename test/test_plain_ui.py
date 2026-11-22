# -*- coding: utf-8 -*-
"""极简档回归（v2.10.0 永久化）：默认 plain / 480 宽 / 40 行高 / 辅助字号 /
行内展开点得开（名称·图标·箭头·行面·键盘）/ 开关不连带展开 / 超量摊全量 /
无可调项与卸载入口 / 抗锯齿光栅 sprite / 同窗换页 / 高 DPI 不越界 /
老配置 liquid-glass 回退 plain 并写回。

临时 ABG_BASE + 临时 plugins/，不碰生产配置；messagebox / filedialog 打桩防阻塞。
"""
import json
import os
import shutil
import sys
import tempfile
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
TMP = tempfile.mkdtemp(prefix='abg_plain_ui_')
PLUGINS = os.path.join(TMP, 'plugins')
os.makedirs(PLUGINS)
for name in ('icon-light.ico', 'icon-dark.ico'):
    src = os.path.join(ROOT, 'app', name)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(TMP, name))
with open(os.path.join(TMP, 'config.json'), 'w', encoding='utf-8') as f:
    json.dump({'ui_theme': 'light'}, f)
os.environ['ABG_BASE'] = TMP
# v2.13.0 拆分后 BASE/CFG 固化在 panel_env 等模块，必须整组一起摘，
# 否则换 ABG_BASE 后重新 import 的 settings_gui 仍拿旧目录。
_RESET = ('settings_gui', 'panel_env', 'win_env', 'panel_state',
          'ui_gfx', 'ui_widgets')
for _m in _RESET:
    sys.modules.pop(_m, None)
import settings_gui as sg  # noqa: E402
import sprite  # noqa: E402  覆盖率缓存在 v2.13.0 拆分后位于 sprite 模块

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def make_plugin(pid, name, order, settings=None):
    d = os.path.join(PLUGINS, pid)
    os.makedirs(d, exist_ok=True)
    manifest = {'id': pid, 'name': name, 'version': '1.0.0', 'api': 1,
                'icon': 'dot', 'order': order, 'home': True,
                'toggle': [pid + '_enabled'], 'entry': 'plugin.py:Plugin',
                'tip': name + ' 简介'}
    if settings:
        manifest['settings'] = settings
    with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
        f.write('from plugin_api import WBOXPlugin\nclass Plugin(WBOXPlugin):\n    pass\n')


SETTINGS_A = [
    {'key': 'demo_a.enabled', 'type': 'bool', 'label': '启用示例'},
    {'key': 'demo_a.count', 'type': 'int', 'label': '等待时间',
     'min': 0, 'max': 100, 'step': 5, 'unit': '秒'},
    {'key': 'demo_a.mode', 'type': 'choice', 'label': '显示位置',
     'options': [{'value': 'tr', 'label': '右上'},
                 {'value': 'bl', 'label': '左下'}]},
    {'key': 'demo_a.exe_path', 'type': 'path', 'label': '程序路径'},
]


def disk(base=None):
    with open(os.path.join(base or TMP, 'config.json'), encoding='utf-8') as f:
        return json.load(f)


def texts(body):
    return [w.cget('text') for w in sg.walk(body) if isinstance(w, tk.Label)]


def row_of(app, pid):
    return app.plugin_gears[pid]


def name_label(app, pid):
    row = row_of(app, pid)
    ent = [e for e in app.entries_all if e.id == pid][0]
    return [w for w in row.winfo_children()
            if isinstance(w, tk.Label) and w.cget('text') == ent.name][0]


def glyph_of(app, pid, kind):
    row = row_of(app, pid)
    return [w for w in row.winfo_children()
            if isinstance(w, sg.Glyph) and w.kind == kind][0]


def click(w, root):
    w.event_generate('<ButtonRelease-1>')
    root.update()


def main():
    make_plugin('demo_a', '甲功能', 10, settings=SETTINGS_A)
    make_plugin('demo_b', '乙功能', 20)
    sg.enable_dpi_awareness()
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=False)
    root.update()
    orig_info = sg.messagebox.showinfo
    orig_ask = sg.messagebox.askyesno
    orig_fd = sg.filedialog.askopenfilename
    sg.messagebox.showinfo = lambda *a, **k: None
    sg.messagebox.askyesno = lambda *a, **k: False
    sg.filedialog.askopenfilename = lambda **k: ''
    try:
        print('[1] 默认极简：皮肤 / 宽度 / 行高 / 辅助字号')
        check(app.skin.id == 'plain',
              '无 ui_skin 的老配置默认 plain：%r' % app.skin)
        check(app.skin.metrics['row'] == 40
              and app.skin.metrics['titlebar'] == 36,
              '行高 40 / 标题栏 36：%r' % app.skin.metrics)
        w = app.root.winfo_width()
        check(w == app.px(480) and w == app.content_width(),
              '窗口宽 480 逻辑像素：%d' % w)
        check(app.f_body.cget('size') == -app.px(12)
              and app.f_small.cget('size') == -app.px(11),
              '辅助字号 12/11（原 11/10）：%d/%d'
              % (app.f_body.cget('size'), app.f_small.cget('size')))
        check(app.add_btn.kind == 'plus', '标题栏＋仍在')
        check('设置' in texts(app.root), '标题栏纯文字设置入口仍在')

        print('[2] 行内展开：名称 / 图标 / 箭头 / 行面 / 键盘 都点得开')
        click(name_label(app, 'demo_a'), root)
        check(app.inline_open == 'demo_a', '点名称展开：%r' % app.inline_open)
        t = texts(app.main)
        check(all(x in t for x in ('启用示例', '等待时间', '显示位置')),
              '前 3 项摊在行下')
        check('程序路径' not in t and '全部 4 项设置…' in t,
              '第 4 项收在「全部 4 项设置…」后面')
        check(not [x for x in root.winfo_children()
                   if isinstance(x, tk.Toplevel)], '行内展开不建 Toplevel')
        click(name_label(app, 'demo_a'), root)
        check(app.inline_open is None and '启用示例' not in texts(app.main),
              '再点名称收起')
        click(glyph_of(app, 'demo_a', 'dot'), root)
        check(app.inline_open == 'demo_a', '点行首图标展开')
        click(glyph_of(app, 'demo_a', 'chev_r'), root)
        check(app.inline_open is None, '点行尾箭头收起')
        click(row_of(app, 'demo_a'), root)
        check(app.inline_open == 'demo_a', '点行面空白处展开')
        row_of(app, 'demo_a').focus_force()
        root.update()
        row_of(app, 'demo_a').event_generate('<Return>')
        root.update()
        check(app.inline_open is None, '行上 Enter 收起（键盘可达）')

        print('[3] 开关不连带展开；超量摊全量；无可调项与卸载入口')
        sw = app.switches['demo_a']
        old = sw.value
        sw.event_generate('<Button-1>')
        root.update()
        check(sw.value != old and app.inline_open is None,
              '点开关只翻状态，不展开设置块')
        click(name_label(app, 'demo_a'), root)
        more = [w for w in sg.walk(app.main) if isinstance(w, tk.Label)
                and w.cget('text') == '全部 4 项设置…'][0]
        more.event_generate('<Button-1>')
        root.update()
        check('程序路径' in texts(app.main) and app.inline_full == 'demo_a',
              '点「全部 4 项设置…」摊全量')
        click(name_label(app, 'demo_b'), root)
        t = texts(app.main)
        check('这个插件没有可调项' in t and '卸载此插件' in t,
              '无可调项插件展开块：空态文案 + 卸载入口')
        check(app.inline_open == 'demo_b', '换一行展开会收上一行')

        print('[4] 极简开关是方角矩形（设计稿 .p-sw）；图标仍走光栅 sprite')
        sw = app.switches['demo_a']
        kinds = [sw.type(i) for i in sw.find_all()]
        check(kinds == ['rectangle', 'rectangle'],
              '开关只有两块矩形（轨道 + 方角把手）：%r' % kinds)
        check(sw.winfo_reqwidth() == app.px(36)
              and sw.winfo_reqheight() == app.px(18),
              '极简开关 36×18 逻辑像素')
        g = app.add_btn
        kinds = [g.type(i) for i in g.find_all()]
        check('image' in kinds, '＋图标画的是光栅 sprite：%r' % kinds)
        check(len(sprite.COV_CACHE) > 0,
              '覆盖率缓存已建立：%d 键' % len(sprite.COV_CACHE))
        n0 = len(sprite.COV_CACHE)
        sw.event_generate('<Button-1>')
        root.update()
        check(sw._anim_knob is None and len(sprite.COV_CACHE) == n0,
              '极简拨动零动画链，几何不变不重算覆盖率')

        print('[5] 同窗换页：设置页入口与返回')
        app.toggle_pane()
        root.update()
        check(app.pane == 'set' and app.set_inner is not None, '换到设置页')
        t = texts(app.set_inner)
        check('从文件安装皮肤…' in t and '外观' in t,
              '设置页有装皮肤入口与外观分组')
        check(not [x for x in root.winfo_children()
                   if isinstance(x, tk.Toplevel)], '设置页不建 Toplevel')
        app.toggle_pane()
        root.update()
        check(app.pane == 'list', '返回列页')

        print('[6] 高 DPI：宽度跟着缩放、行内展开仍可达、不越工作区')
        for dpi in (144, 192, 240):
            app.apply_dpi(dpi)
            root.update()
            w = app.root.winfo_width()
            check(w == app.content_width() == app.px(480),
                  'dpi=%d 宽 480 逻辑像素：%d' % (dpi, w))
            click(name_label(app, 'demo_a'), root)
            check(app.inline_open == 'demo_a'
                  and '启用示例' in texts(app.main),
                  'dpi=%d 重建后行内展开仍点得开' % dpi)
            click(name_label(app, 'demo_a'), root)
            lx, ly, sw_, sh = sg.work_area()
            x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
            ww, wh = app.root.winfo_width(), app.root.winfo_height()
            check(x >= lx and x + ww <= lx + sw_ and y + wh <= ly + sh,
                  'dpi=%d 主窗口在工作区内 (%d,%d %dx%d)' % (dpi, x, y, ww, wh))
        app.apply_dpi(96)
        root.update()
    finally:
        sg.messagebox.showinfo = orig_info
        sg.messagebox.askyesno = orig_ask
        sg.filedialog.askopenfilename = orig_fd
        app.close_plugin_settings()
        for job in root.tk.call('after', 'info'):
            try:
                root.after_cancel(str(job))
            except Exception:
                pass
        root.destroy()

    print('[7] 老配置 ui_skin=liquid-glass：回退 plain + 留痕 + 写回一次')
    tmp2 = tempfile.mkdtemp(prefix='abg_plain_migrate_')
    for name in ('icon-light.ico', 'icon-dark.ico'):
        src = os.path.join(ROOT, 'app', name)
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(tmp2, name))
    with open(os.path.join(tmp2, 'config.json'), 'w', encoding='utf-8') as f:
        json.dump({'ui_skin': 'liquid-glass', 'ui_appearance': 'light'}, f)
    os.environ['ABG_BASE'] = tmp2
    for _m in _RESET:
        sys.modules.pop(_m, None)
    import settings_gui as sg2  # noqa: E402
    root2 = tk.Tk()
    root2.attributes('-alpha', 0.0)
    app2 = sg2.App(root2, probe=False)
    root2.update()
    try:
        check(app2.skin.id == 'plain',
              '玻璃老配置回退默认：%r' % app2.skin)
        check(disk(tmp2).get('ui_skin') == 'plain',
              '解析结果写回 config.json（ui_skin=plain）')
        with open(os.path.join(tmp2, 'guard.log'), encoding='utf-8') as f:
            log = f.read()
        check('liquid-glass' in log and '回退 plain' in log,
              '回退留了一行日志')
    finally:
        for job in root2.tk.call('after', 'info'):
            try:
                root2.after_cancel(str(job))
            except Exception:
                pass
        root2.destroy()
    shutil.rmtree(tmp2, ignore_errors=True)
    shutil.rmtree(TMP, ignore_errors=True)
    print('')
    if FAILS:
        print('FAILED %d：%s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()