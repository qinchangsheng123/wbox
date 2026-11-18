# -*- coding: utf-8 -*-
"""任务栏按钮 live 回归（v2.11.2 永久化）：overrideredirect 面板必须在任务栏
有按钮。历史坑：init 时 wrapper 未诞生，WS_EX_APPWINDOW 加到 TkChild 上；
map 后 Tk 给 wrapper 设 WS_EX_TOOLWINDOW，按钮永远不出现。

按钮出现的条件（探针实测，见 releases/v2.11.2/evidence）：
  1. wrapper（wm_frame）风格位 = +WS_EX_APPWINDOW -WS_EX_TOOLWINDOW；
  2. 可见路径 shell 在 wrapper 首次 show 时按 TOOLWINDOW 缓存排除决定，
     Map 里改位后必须 hide/show 刷新一次才出按钮（probe_tb_deploy 外部
     hide/show 对照截图）；alpha=0 探针路径改位即够，两路径都覆盖。
     刷新会清键盘焦点（焦点 None 时 Tk 丢弃键事件、菜单 Esc 失效），
     故刷新前记焦点、刷新后 focus_force 回填（focus_set 非前景不生效）。
Win10 19045 的任务栏按钮数拿不到客观枚举（MSTaskListWClass 不响应
TB_BUTTONCOUNT，IAccessible accChildCount 是缓存值，UIA 不暴露按钮子节点），
所以这里断言风格位 + 诱导重置后钩子修位并刷新 + 焦点不被刷新清掉 +
withdraw/deiconify/最小化还原各路径下风格位保持；
按钮的视觉存在由发版证据截图兜底。

临时 ABG_BASE（不碰生产 config）；probe=True 不注册托盘，避免通知区噪声。
"""
import os
import shutil
import sys
import tempfile
import time
import tkinter as tk
import ctypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

TMP = tempfile.mkdtemp(prefix='abg_tb_live_')
for name in ('config.json', 'icon-light.ico', 'icon-dark.ico'):
    src = os.path.join(ROOT, 'app', name)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(TMP, name))
os.environ['ABG_BASE'] = TMP
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402

PASSED, FAILED = [], []


def check(cond, msg):
    (PASSED if cond else FAILED).append(msg)
    print(('  ok   ' if cond else '  FAIL ') + msg)


u = ctypes.windll.user32
GWL_EXSTYLE = -20
WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = 0x00040000, 0x00000080


def exstyle(hwnd):
    u.GetWindowLongW.restype = ctypes.c_long
    return u.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)


def bits_ok():
    ex = exstyle(sg.win_hwnd(root))
    return bool(ex & WS_EX_APPWINDOW) and not (ex & WS_EX_TOOLWINDOW)


sg.enable_dpi_awareness()
root = tk.Tk()
root.attributes('-alpha', 0.0)
app = sg.App(root, probe=True)
MAPS, UNMAPS = [], []
root.bind('<Map>', lambda e: MAPS.append(time.time()), add='+')
root.bind('<Unmap>', lambda e: UNMAPS.append(time.time()), add='+')


def pump(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            root.update()
        except tk.TclError:
            return cond()
        if cond():
            return True
        time.sleep(0.02)
    return False


try:
    check(pump(lambda: root.state() == 'normal' and len(MAPS) >= 1),
          '面板完成首次 map')
    check(bits_ok(), 'wrapper 风格位 +APPWINDOW -TOOLWINDOW（ex=%08x）'
          % exstyle(sg.win_hwnd(root)))
    # 焦点/Esc 交叉回归：init 的补位刷新会清焦点，focus_force 回填后
    # open_settings 的 focus_set 才落得住（非前景进程也成立）。
    app.open_settings()
    root.update()
    check(root.focus_get() is not None,
          '菜单打开焦点落在首项（补位刷新后 focus_force 回填）')
    app.settings_win.event_generate('<Escape>')
    root.update()
    check(not (app.settings_win and app.settings_win.winfo_exists()),
          'Esc 收起菜单（键事件不因焦点 None 被丢弃）')
    # 诱导一次「wrapper 被重置回 TOOLWINDOW」（Tk 重建 wrapper 时的历史坑）：
    # 手动加回 TOOL 再 withdraw/deiconify（手动循环 +1/+1），<Map> 钩子应修位
    # 并 hide/show 刷新一次（钩子刷新再 +1/+1），且焦点不被刷新清掉。
    hwnd = sg.win_hwnd(root)
    u.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE,
                     ctypes.c_long(exstyle(hwnd) | WS_EX_TOOLWINDOW))
    n_map, n_unmap = len(MAPS), len(UNMAPS)
    root.withdraw()
    root.deiconify()
    check(pump(lambda: bits_ok() and len(MAPS) >= n_map + 2
               and len(UNMAPS) >= n_unmap + 2),
          '风格位被重置后钩子修位并 hide/show 刷新（map=%d unmap=%d）'
          % (len(MAPS), len(UNMAPS)))
    check(root.focus_get() is not None,
          '诱导 hide/show 后焦点被 focus_force 回填（不清焦点）')
    n_unmap = len(UNMAPS)
    root.withdraw()
    check(pump(lambda: len(UNMAPS) > n_unmap), 'withdraw 触发 unmap（按钮摘除路径）')
    n_map = len(MAPS)
    root.deiconify()
    check(pump(lambda: len(MAPS) > n_map and bits_ok()),
          'deiconify 重新 map 且风格位保持')
    app.on_min()
    check(pump(lambda: root.state() == 'iconic'), '进入最小化')
    root.deiconify()
    check(pump(lambda: root.state() == 'normal' and bits_ok()),
          '还原自绘外壳后风格位仍正确（_restore_chrome 重补）')
finally:
    try:
        if root.winfo_exists():
            root.destroy()
    except tk.TclError:
        pass
    shutil.rmtree(TMP, ignore_errors=True)

print('\nPASS %d / FAIL %d' % (len(PASSED), len(FAILED)))
for m in FAILED:
    print('  - ' + m)
sys.exit(1 if FAILED else 0)