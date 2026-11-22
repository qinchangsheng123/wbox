# -*- coding: utf-8 -*-
"""窗口大小三档 + 插件版本可见（v2.12.0）离线回归。

盯着两条用户可见的约定：

  1. 「外观」里新增窗口大小 小/中/大，默认「小」，且小 = 升级前尺寸
     （ui_size 缺键 / 坏值都回落 small，不能因为读配置失败把面板画大）。
  2. 每个插件的设置浮层能直接读到版本号（版本胶囊 + 接口 / 发布者），
     不用去翻 plugin.json。

用 probe=True + alpha 0 建面板：布局是真的，只是不显示。
临时 ABG_BASE，不碰生产配置。
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

TMP = tempfile.mkdtemp(prefix='abg_ui_size_')
PLUGINS = os.path.join(TMP, 'plugins')
CFG_PATH = os.path.join(TMP, 'config.json')
os.makedirs(PLUGINS)
for _name in ('icon-light.ico', 'icon-dark.ico'):
    _src = os.path.join(ROOT, 'app', _name)
    if os.path.isfile(_src):
        shutil.copy(_src, os.path.join(TMP, _name))

with open(CFG_PATH, 'w', encoding='utf-8') as f:
    json.dump({'ui_skin': 'plain', 'ui_appearance': 'light'}, f)

# 一个最小可加载插件：带版本 / 发布者 / 接口，供版本胶囊断言。
_pdir = os.path.join(PLUGINS, 'demo_size')
os.makedirs(_pdir)
with open(os.path.join(_pdir, 'plugin.json'), 'w', encoding='utf-8') as f:
    json.dump({
        'id': 'demo_size', 'name': '尺寸示例', 'version': '3.4.5', 'api': 1,
        'icon': 'dot', 'order': 10, 'home': True,
        'toggle': ['demo_size_enabled'], 'entry': 'plugin.py:Plugin',
        'publisher': 'WBOX', 'tip': '示例',
    }, f, ensure_ascii=False)
with open(os.path.join(_pdir, 'plugin.py'), 'w', encoding='utf-8') as f:
    f.write('# -*- coding: utf-8 -*-\n'
            'import plugin_api\n'
            '\n'
            '\n'
            'class Plugin(plugin_api.WBOXPlugin):\n'
            "    DEFAULT_CONFIG = {'demo_size_enabled': False}\n")

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
    return [w.cget('text') for w in sg.walk(body)
            if isinstance(w, tk.Label)]


def new_app():
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=True, dpi_override=96)
    root.update()
    app.probe = False        # 让浮层真的建出来（alpha=0 仍然不显示）
    return root, app


def drop(root, app):
    app.close_plugin_settings()
    app.close_skin_settings()
    root.destroy()


def main():
    root, app = new_app()
    try:
        print('[1] 默认窗口大小为小：倍率 1.0，宽度 = 480 逻辑像素')
        check(app.ui_size == 'small' and app.ui_zoom == 1.0,
              '缺 ui_size 时回落 small（%s / %.2f）'
              % (app.ui_size, app.ui_zoom))
        check(app.scale == 1.0 and app.content_width() == 480,
              '96dpi 下 scale=%.2f，宽=%d' % (app.scale, app.content_width()))

        print('[2] 三档切换：中 1.15 / 大 1.30，落盘 ui_size')
        app.set_ui_size('large')
        root.update()
        check(abs(app.scale - 1.30) < 1e-6 and app.content_width() == 624,
              '大：scale=%.2f，宽=%d' % (app.scale, app.content_width()))
        check(disk().get('ui_size') == 'large', 'ui_size=large 已落盘')
        head_px = -int(app.f_head.cget('size'))
        check(head_px == 17, '字号跟着放大：正文 %d px（13 × 1.3）' % head_px)
        app.set_ui_size('medium')
        root.update()
        check(abs(app.scale - 1.15) < 1e-6 and app.content_width() == 552,
              '中：scale=%.2f，宽=%d' % (app.scale, app.content_width()))
        check(disk().get('ui_size') == 'medium', 'ui_size=medium 已落盘')

        print('[3] 换明暗 / 重开面板：大小不被冲掉')
        app.set_theme('dark')
        root.update()
        check(app.ui_size == 'medium' and app.content_width() == 552,
              '换深色后仍是「中」')
        drop(root, app)
        root, app = new_app()
        check(app.ui_size == 'medium' and abs(app.scale - 1.15) < 1e-6,
              '重开面板读到 medium（scale=%.2f）' % app.scale)

        print('[4] 外观里能看到「窗口大小」三档，点「大」生效')
        app.toggle_pane()          # 极简档：设置页
        root.update()
        t = texts(app.set_inner)
        check('窗口大小' in t, '设置页外观组有「窗口大小」')
        big = [w for w in sg.walk(app.set_inner)
               if isinstance(w, tk.Label) and w.cget('text') == '大'][0]
        big.event_generate('<ButtonRelease-1>')
        root.update()
        check(app.ui_size == 'large' and app.pane == 'set',
              '点「大」：倍率生效且留在设置页')
        app.toggle_pane()
        root.update()

        print('[5] 插件设置浮层：版本 / 接口 / 发布者都能看到')
        entry = app.entries[0]
        app.open_plugin_settings(entry)
        root.update()
        t = texts(app.plugin_body)
        check('版本 v3.4.5' in t, '版本胶囊文案：%r' % t[:4])
        check(any('插件接口 API 1' in x and '发布者 WBOX' in x for x in t),
              '接口与发布者在副行：%r' % t[:6])

        print('[6] 坏 ui_size：回落小，不报错')
        drop(root, app)
        cfg = disk()
        cfg['ui_size'] = 'huge'
        with open(CFG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False)
        root, app = new_app()
        check(app.ui_size == 'small' and app.ui_zoom == 1.0,
              'ui_size=huge 回落 small')
    finally:
        drop(root, app)
    shutil.rmtree(TMP, ignore_errors=True)
    print('')
    if FAILS:
        print('FAILED %d：%s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
