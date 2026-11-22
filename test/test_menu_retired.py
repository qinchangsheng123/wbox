# -*- coding: utf-8 -*-
"""退役守卫：齿轮气泡菜单（v2.18.0 删除）不许悄悄回来。

为什么留这份测试而不是直接删掉原 test_gear_menu.py：
  那套菜单曾经是"设置"的唯一入口，删除时连带改了 12 个文件。将来若有人为了
  "加一个二级菜单" 重新引入 self.settings_win / build_settings / 点字标弹窗，
  第二级就会变成两条路（同窗换页 + 浮层），这正是本次要清掉的东西。
  这份测试钉住三件事：

    [1] 模块与类上不再有那套符号（menu_row_count / build_settings / ...）
    [2] 点「设置」只换页，不创建任何 Toplevel
    [3] 第二级唯一：窗口里只有 titlebar + 一页内容（没有浮层挂在 root 下）

用临时 ABG_BASE（自动清理），probe=True，窗口 alpha=0 不真显示。
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

TMP = tempfile.mkdtemp(prefix='abg_menu_retired_')
for name in ('config.json', 'icon-light.ico', 'icon-dark.ico'):
    src = os.path.join(ROOT, 'app', name)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(TMP, name))
os.environ['ABG_BASE'] = TMP

# BASE 是 import 期固化的，同进程跑第二个 GUI 测试前必须摘掉旧模块
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402
import plugin_api          # noqa: E402

sg.enable_dpi_awareness()
PASSED = []


def step(cond, msg):
    if not cond:
        raise AssertionError(msg)
    PASSED.append(msg)
    print('  [ok] ' + msg)


root = tk.Tk()
root.attributes('-alpha', 0.0)
app = sg.App(root, probe=True)
root.update()

try:
    print('[1] 那套符号已经不在')
    for name in ('menu_row_count', 'CORE_MENU_ROWS', 'PLUGIN_STATUS_HINT',
                 'plugin_hint'):
        step(not hasattr(sg, name), '模块级 %s 已删除' % name)
    for name in ('toggle_settings', 'open_settings', 'close_settings',
                 'build_settings', 'menu_row', 'menu_sep', 'place_menu',
                 'plugin_switch_live', 'refresh_status', 'on_brand',
                 'menu_fade_update', '_menu_outside'):
        step(not hasattr(sg.App, name), 'App.%s 已删除' % name)
    step(not hasattr(plugin_api.WBOXPlugin, 'gui_menu_rows'),
         '插件 API 不再有 gui_menu_rows 契约')
    step(not hasattr(app, 'settings_win'), '实例上没有 settings_win 浮层句柄')

    print('[2] 点「设置」只换页，不开窗')
    before = len(root.winfo_children())
    step(app.pane == 'list', '初始在列表页')
    app.toggle_pane()
    root.update()
    step(app.pane == 'set', '点「设置」切到设置页（同窗换页）')
    step(len(root.winfo_children()) == before,
         '换页不创建 Toplevel：root 子控件 %d -> %d'
         % (before, len(root.winfo_children())))
    app.toggle_pane()
    root.update()
    step(app.pane == 'list', '再点回到列表页')

    print('[3] 第二级唯一：窗口里只有标题栏 + 页内控件')
    kinds = [w.winfo_class() for w in app.main.winfo_children()]
    step(all(k in ('Frame', 'Canvas') for k in kinds),
         '主区只有 Frame / Canvas，没有浮层容器：%r' % (kinds,))
    step(kinds[0] == 'Frame' and kinds[1] == 'Canvas',
         '标题栏 Frame + 列表 Canvas 固定在前两位：%r' % (kinds[:2],))
    step(len([w for w in root.winfo_children()
              if w is not app.main]) == 0,
         'root 下只挂主区一个控件（没有并列的浮层）')
    # 托盘 / 通知两个开关是设置页的（页内），不在任何浮层里
    app.toggle_pane()
    root.update()
    step('tray' in app.switches and 'notify' in app.switches,
         '托盘 / 通知两个开关挂在设置页里')
    step(app.switches['tray'].master.winfo_toplevel() is root,
         '它们属于主窗口，不是浮层（toplevel is root）')
finally:
    root.destroy()
    shutil.rmtree(TMP, ignore_errors=True)

print('ALL PASS (%d checks)' % len(PASSED))
