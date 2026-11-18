# -*- coding: utf-8 -*-
"""齿轮弹出菜单交互回归：开合、逐行项目、主题/开关保持、Esc/外部点击收起。

用临时 ABG_BASE（自动清理），不碰生产 config.json；probe=True 不真改计划任务。
"""
import os
import shutil
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

TMP = tempfile.mkdtemp(prefix='abg_menu_test_')
for name in ('config.json', 'icon-light.ico', 'icon-dark.ico'):
    src = os.path.join(ROOT, 'app', name)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(TMP, name))
# v2.10.0：本测试是 standard 档字标菜单的回归；生产配置已是 plain，
# 这里显式选 fixture 用户皮肤（液态玻璃不再内置）。
import json  # noqa: E402
_cfg_path = os.path.join(TMP, 'config.json')
with open(_cfg_path, encoding='utf-8') as _f:
    _cfg = json.load(_f)
_cfg['ui_skin'] = 'liquid-glass'
_cfg['ui_appearance'] = 'light'
with open(_cfg_path, 'w', encoding='utf-8') as _f:
    json.dump(_cfg, _f, ensure_ascii=False, indent=2)
os.environ['ABG_BASE'] = TMP

# v2.3.0 复核补丁 R2：BASE 是 import 期固化的，同进程跑第二个 GUI 测试前必须摘掉旧模块
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402
import skinfix  # noqa: E402

skinfix.write_glass(TMP)

sg.enable_dpi_awareness()
root = tk.Tk()
root.attributes('-alpha', 0.0)
app = sg.App(root, probe=True)
root.update()

PASSED = []


def rows():
    return [w for w in sg.walk(app.settings_inner)
            if isinstance(w, tk.Frame) and hasattr(w, 'hint_label')]


def label(text):
    for w in sg.walk(app.settings_inner):
        if isinstance(w, tk.Label) and w.cget('text') == text:
            return w
    raise AssertionError('menu label not found: ' + text)


def gear_click():
    app.gear_btn.event_generate('<ButtonRelease-1>')
    root.update()


def opened():
    return bool(app.settings_win and app.settings_win.winfo_exists())


def step(cond, msg):
    if not cond:
        raise AssertionError(msg)
    PASSED.append(msg)
    print('  [ok] ' + msg)


try:
    entries = sg.plugin_entries()          # 真实注册表（TMP 下没有 plugins\ → [])
    app.open_settings()
    root.update()
    step(opened(), '齿轮菜单能打开')
    step(len(rows()) == sg.menu_row_count(entries),
         '菜单 %d 项，每项独占一行' % len(rows()))
    step(app.gear_btn.winfo_exists(), '左上角是字标 WBOX（菜单入口）')

    gear_click()
    step(not opened(), '再点齿轮收起')
    gear_click()
    step(opened(), '再点齿轮展开')

    # v2.7.0：明暗两项合成「外观与皮肤…」，点了打开独立的皮肤浮层（菜单收起）
    label('外观与皮肤…').event_generate('<Button-1>')
    root.update()
    step(app.skin_win is not None and app.skin_win.winfo_exists(),
         '「外观与皮肤…」打开皮肤浮层')
    step(not opened(), '打开皮肤浮层时菜单收起（不叠两层）')
    app.on_pick_appearance('dark')
    root.update()
    step(app.theme == 'dark', '皮肤浮层里切深色生效果')
    app.on_pick_appearance('light')
    root.update()
    step(app.theme == 'light', '皮肤浮层里切回浅色生效')
    app.close_skin_settings()
    root.update()
    step(app.skin_win is None, '皮肤浮层可关闭')
    app.open_settings()
    root.update()

    boot = app.switches['boot']
    old = boot.value
    boot.event_generate('<Button-1>')
    root.update()
    step(app.switches['boot'].value != old and opened(),
         '开机自启开关可切换且菜单不关闭')

    app.settings_win.event_generate('<Escape>')
    root.update()
    step(not opened(), 'Esc 收起')

    app.open_settings()
    root.update()
    root.event_generate('<Button-1>')
    root.update()
    step(not opened(), '点击主窗口空白处收起')

    app.open_settings()
    root.update()
    label('关闭菜单').event_generate('<Button-1>')
    root.update()
    step(not opened(), '菜单项「关闭」收起')
finally:
    app.close_settings()
    root.destroy()
    shutil.rmtree(TMP, ignore_errors=True)

print('ALL PASS (%d checks)' % len(PASSED))
