# -*- coding: utf-8 -*-
"""托盘 live 回归（v2.9.0 任务 12 永久化）：真 Shell_NotifyIcon 图标 + 收托盘 +
左键叫回 + 游戏结束气泡 + 退出摘图标。会在任务栏短暂出现真图标与一枚气泡，
结束即摘除——这是 live 行为证据，不是离线断言。

临时 ABG_BASE（不碰生产 config）；stub 心跳与开机自启查询，try/finally 还原
（guard_beat 是共享模块，不还原会带偏同进程后跑的测试）。
"""
import ctypes
import json
import os
import shutil
import sys
import tempfile
import time
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

TMP = tempfile.mkdtemp(prefix='abg_tray_live_')
for name in ('config.json', 'icon-light.ico', 'icon-dark.ico'):
    src = os.path.join(ROOT, 'app', name)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(TMP, name))
os.environ['ABG_BASE'] = TMP
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402
import tray_icon  # noqa: E402

PASSED, FAILED = [], []


def check(cond, msg):
    (PASSED if cond else FAILED).append(msg)
    print(('  ok   ' if cond else '  FAIL ') + msg)


SESS = {'st': None}
ORIG_READ = sg.guard_beat.read_status
ORIG_AUTO = sg.autostart_state
sg.guard_beat.read_status = lambda *a, **k: SESS['st']
sg.autostart_state = lambda: False

sg.enable_dpi_awareness()
root = tk.Tk()
root.attributes('-alpha', 0.0)
app = sg.App(root, probe=False)
root.update()


def alive():
    try:
        return bool(root.winfo_exists())
    except tk.TclError:
        return False


def pump(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            root.update()
        except tk.TclError:
            return cond()      # 窗口已销毁（退出命令生效）
        if cond():
            return True
        time.sleep(0.02)
    return False


try:
    check(app.tray is not None and app.tray.active,
          '默认配置托盘图标常驻（存在性默认开，收托盘仍 opt-in）')
    app.open_settings()
    root.update()
    check('tray' in app.switches and 'notify' in app.switches,
          '齿轮菜单里有「启动后收进托盘 / 游戏结束提示」两个开关')
    sw = app.switches['tray']
    sw.set_value(True)
    app.on_tray_toggle()
    root.update()
    check(app.tray is not None and app.tray.active, '打开开关后真托盘图标已注册')
    with open(os.path.join(TMP, 'config.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    check(cfg.get('start_in_tray') is True, 'start_in_tray=True 落盘')
    sw.set_value(False)
    app.on_tray_toggle()
    root.update()
    check(app.tray is not None and app.tray.active,
          '关掉收托盘只改关窗行为，图标仍常驻')
    sw.set_value(True)
    app.on_tray_toggle()
    root.update()

    app.on_close()
    root.update()
    check(root.state() == 'withdrawn', '关窗 = 收进托盘（窗口 withdrawn 而非销毁）')

    # 左键单击托盘 -> CMD_OPEN -> 主线程 pump 取走 -> 窗口回来
    u = ctypes.windll.user32
    u.PostMessageW(app.tray._hwnd, tray_icon.WM_TRAY, 0, tray_icon.WM_LBUTTONUP)
    check(pump(lambda: root.state() == 'normal'), '托盘左键把窗口叫回来')

    # 游戏结束气泡：active -> grace 且窗口收在托盘里
    app.notify_var.set(True)
    SESS['st'] = {'pid': 4321, 'session': 'active'}
    app._tray_watch()
    app.on_close()
    root.update()
    check(root.state() == 'withdrawn', '再次收进托盘')
    SESS['st'] = {'pid': 4321, 'session': 'grace'}
    app._tray_watch()
    check(pump(lambda: app.tray._balloons.qsize() == 0),
          'active->grace 触发气泡且消息线程已投递 Shell_NotifyIcon')

    # 窗口可见时同样的迁移不该弹气泡
    app.tray_open()
    root.update()
    SESS['st'] = {'pid': 4321, 'session': 'active'}
    app._tray_watch()
    SESS['st'] = {'pid': 4321, 'session': 'idle'}
    app._tray_watch()
    check(app.tray._balloons.qsize() == 0, '窗口可见时不弹气泡（状态行已说明）')

    # 右键菜单回路不实测弹菜单（阻塞调用），只验退出命令
    app.tray.commands.put((tray_icon.CMD_EXIT,))
    check(pump(lambda: not alive()), '托盘「退出」真的销毁窗口')
    check(app.tray is None, '退出时托盘图标摘除（_tray_shutdown）')
finally:
    sg.guard_beat.read_status = ORIG_READ
    sg.autostart_state = ORIG_AUTO
    try:
        if app.tray is not None:
            app._tray_shutdown()
    except Exception:
        pass
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