# -*- coding: utf-8 -*-
"""极简档与设计稿对齐（v2.12.1）离线回归。

四个用户可见的问题，各对应一组断言：

  1. 首页点插件名打不开设置 —— 展开后窗口不能重新居中（顶边钉住），
     否则点中的那一行会被顶走，看起来像"点了没反应"。
  2. 插件版本看不到 —— 极简档没有设置浮层，行尾必须有 v 小字，
     展开后的来源行必须以「版本 v…」开头。
  3. 按钮和设计稿不一样 —— 开关是方角（36×18 / 行内 30×16 两块矩形），
     行内设置 30 高，卸载是带 1px 描边的方角按钮。
  4. 黑白只剩白 —— 设置页「外观」组必须有明暗三档，选深色后留在本页。

probe=True + alpha 0：布局是真的，窗口不显示。临时 ABG_BASE。
"""
import json
import os
import shutil
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

TMP = tempfile.mkdtemp(prefix='abg_plain121_')
PLUGINS = os.path.join(TMP, 'plugins')
CFG_PATH = os.path.join(TMP, 'config.json')
os.makedirs(PLUGINS)
for _name in ('icon-light.ico', 'icon-dark.ico'):
    _src = os.path.join(ROOT, 'app', _name)
    if os.path.isfile(_src):
        shutil.copy(_src, os.path.join(TMP, _name))

with open(CFG_PATH, 'w', encoding='utf-8') as f:
    json.dump({'ui_skin': 'plain', 'ui_appearance': 'light',
               'ui_size': 'small'}, f)

SETTINGS = [
    {'key': 'demo.on', 'type': 'bool', 'label': '启用示例'},
    {'key': 'demo.wait', 'type': 'int', 'label': '等待时间',
     'min': 0, 'max': 100, 'step': 5, 'unit': '秒'},
    {'key': 'demo.mode', 'type': 'choice', 'label': '显示位置',
     'options': [{'value': 'tr', 'label': '右上'},
                 {'value': 'bl', 'label': '左下'}]},
    {'key': 'demo.exe', 'type': 'path', 'label': '程序路径'},
]


def make_plugin(pid, name, order, settings=None):
    d = os.path.join(PLUGINS, pid)
    os.makedirs(d)
    manifest = {'id': pid, 'name': name, 'version': '1.2.3', 'api': 1,
                'icon': 'dot', 'order': order, 'home': True,
                'toggle': [pid + '_enabled'], 'entry': 'plugin.py:Plugin',
                'publisher': 'WBOX', 'permissions': ['process'],
                'tip': name}
    if settings:
        manifest['settings'] = settings
    with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
        f.write('import plugin_api\n'
                '\n'
                '\n'
                'class Plugin(plugin_api.WBOXPlugin):\n'
                "    DEFAULT_CONFIG = {'%s_enabled': False}\n" % pid)


make_plugin('demo', '示例插件', 10, SETTINGS)
make_plugin('bare', '无设置插件', 20)

os.environ['ABG_BASE'] = TMP
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402

sg.enable_dpi_awareness()

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def disk():
    with open(CFG_PATH, encoding='utf-8') as f:
        return json.load(f)


def texts(body):
    return [w.cget('text') for w in sg.walk(body) if isinstance(w, tk.Label)]


def labels(body, text):
    return [w for w in sg.walk(body)
            if isinstance(w, tk.Label) and w.cget('text') == text]


def main():
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=True, dpi_override=96)
    root.update()
    try:
        entry = [e for e in app.entries if e.id == 'demo'][0]

        print('[1] 首页点名称展开：窗口顶边不动')
        top0 = root.winfo_rooty()
        h0 = root.winfo_height()
        name = labels(app.main, '示例插件')[0]
        name.event_generate('<ButtonRelease-1>', x=4, y=4)
        root.update()
        h1 = root.winfo_height()
        check(app.inline_open == 'demo', '点名称展开：inline=%r' % app.inline_open)
        check(root.winfo_rooty() == top0,
              '展开后窗口顶边不动：%d -> %d' % (top0, root.winfo_rooty()))
        check(0 < h1 - h0 < app.px(340),
              '展开只长 %d px（旧版 500+）' % (h1 - h0))
        name = labels(app.main, '示例插件')[0]
        name.event_generate('<ButtonRelease-1>', x=4, y=4)
        root.update()
        check(app.inline_open is None, '再点一次收起')

        print('[2] 版本号：行尾小字 + 展开块来源行')
        ver = labels(app.main, 'v1.2.3')
        check(len(ver) == 2, '每一行都有 v 小字（2 个插件）：%d' % len(ver))
        ver[0].event_generate('<ButtonRelease-1>', x=2, y=2)
        root.update()
        check(app.inline_open == 'demo', '点版本小字也能展开')
        src = [t for t in texts(app.main) if t.startswith('版本 v')]
        check(bool(src) and src[0].startswith('版本 v1.2.3 · 发布者：WBOX'),
              '来源行以版本开头：%r' % (src[:1],))

        print('[3] 按钮形状：方角开关 / 30 高行内项 / 描边卸载按钮')
        sw = app.switches['demo']
        kinds = [sw.type(i) for i in sw.find_all()]
        check(kinds == ['rectangle', 'rectangle'], '行开关两块矩形：%r' % kinds)
        item = labels(app.main, '启用示例')[0].master
        check(item.winfo_height() == app.px(30),
              '行内设置项高 %d = px(30)' % item.winfo_height())
        small = app._setting_switches['demo.on']
        check(small.winfo_reqwidth() == app.px(30)
              and small.winfo_reqheight() == app.px(16),
              '行内小开关 30×16')
        check([small.type(i) for i in small.find_all()]
              == ['rectangle', 'rectangle'], '小开关也是方角')
        rm = labels(app.main, '卸载此插件')[0]
        box = rm.master
        check(int(box.cget('highlightthickness')) == 1,
              '卸载按钮有 1px 描边（设计稿 .p-del）')

        print('[4] 设置页：明暗三档 + 深色留在本页')
        app.toggle_pane()
        root.update()
        t = texts(app.set_inner)
        check('明暗' in t and '深色' in t and '跟随系统' in t,
              '外观组有明暗三档')
        check('窗口大小' in t, '窗口大小仍在')
        dark = labels(app.set_inner, '深色')[0]
        dark.event_generate('<ButtonRelease-1>', x=4, y=4)
        root.update()
        check(app.theme == 'dark' and app.appearance_pref == 'dark',
              '点深色生效：theme=%s' % app.theme)
        check(app.pane == 'set', '切明暗后仍停在设置页：pane=%s' % app.pane)
        check(disk().get('ui_appearance') == 'dark', 'ui_appearance 落盘 dark')
        dark = labels(app.set_inner, '深色')[0]
        # v2.15.0 B 案：选中格吃强调色 token on（legacy inv），
        # 默认皮肤里 inv 就是 fg，配色主题才显色。
        check(dark.cget('bg') == app.colors['inv'],
              '选中格是强调色底：%s' % dark.cget('bg'))
    finally:
        try:
            root.attributes('-alpha', 1.0)
        except Exception:
            pass
        root.destroy()

    print('')
    if FAILS:
        print('FAILED %d：%s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
