# -*- coding: utf-8 -*-
"""v2.12.2 回归：字标不再重复当设置入口 + 托盘图标自愈。

[A] 极简档字标只做身份标识（右上角已有「设置」），玻璃档保留菜单入口。
[B] 托盘：首次 NIM_ADD 失败可重试、线程退出能重建、勾了「收进托盘」但图标
    不在时不隐藏窗口（旧行为会让窗口和图标一起消失，只能重启面板）。

[A] 用 probe=True（不建托盘）；[B] 用 probe=False（真 Shell_NotifyIcon，
结束即摘除），临时 ABG_BASE 不碰生产配置。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

TMP = tempfile.mkdtemp(prefix='abg_brand_tray_')
for _n in ('icon-light.ico', 'icon-dark.ico'):
    _s = os.path.join(ROOT, 'app', _n)
    if os.path.isfile(_s):
        shutil.copy(_s, os.path.join(TMP, _n))
os.makedirs(os.path.join(TMP, 'plugins'))
CFG = os.path.join(TMP, 'config.json')


def write_cfg(skin):
    with open(CFG, 'w', encoding='utf-8') as f:
        json.dump({'ui_skin': skin, 'ui_appearance': 'light',
                   'ui_size': 'small', 'start_in_tray': True}, f)


os.environ['ABG_BASE'] = TMP
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402
import tray_icon  # noqa: E402

sg.enable_dpi_awareness()
FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def part_plain():
    print('[A1] 极简档：字标不绑点击，入口只有「设置」')
    write_cfg('plain')
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=True, dpi_override=96)
    root.update()
    try:
        bind = app.brand_wrap.bind('<ButtonRelease-1>')
        check(not bind, '字标没有 ButtonRelease-1 绑定：%r' % (bind,))
        check(app.brand_wrap.cget('cursor') == 'arrow',
              '字标光标不是手型：%r' % app.brand_wrap.cget('cursor'))
        app.on_brand()
        root.update()
        check(app.pane == 'list', '程序内调 on_brand 也不会切页：pane=%s'
              % app.pane)
        chain = list(getattr(root, '_focus_chain', []))
        check(app.brand_wrap not in chain, 'Tab 链里没有字标')
        check(getattr(app, 'pane_btn', None) in chain,
              'Tab 链首是「设置」按钮')
        # 拖动窗口的手势仍在（只是不再同时当按钮）
        check(bool(app.brand_wrap.bind('<B1-Motion>')),
              '字标仍可拖动窗口')
        app.toggle_pane()
        root.update()
        check(app.pane == 'set', '「设置」按钮能进设置页')
        back = app.pane_btn
        back.event_generate('<ButtonRelease-1>', x=4, y=4)
        root.update()
        check(app.pane == 'list', '「←」能回列表页')
    finally:
        root.destroy()

    print('[A2] 玻璃档：字标仍是菜单入口（没被误删）')
    # v2.11 起液态玻璃不再内置（只剩 plain），这里把同一个 App 切成玻璃分支
    # 重建标题栏，专门盯 build_titlebar 里 glass=True 的那条路径。
    write_cfg('plain')
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=True, dpi_override=96)
    root.update()
    try:
        app.glass = True
        app.build_titlebar(app.main)
        root.update()
        check(bool(app.brand_wrap.bind('<ButtonRelease-1>')),
              '玻璃档字标有点击绑定')
        check(app.brand_wrap.cget('cursor') == 'hand2', '玻璃档字标是手型')
        check(bool(app.brand_wrap.bind('<B1-Motion>')),
              '玻璃档字标仍可拖动窗口')
    finally:
        root.destroy()


def part_tray():
    print('[B1] 托盘：首次失败后可以重试（旧实现第二次 start 永远 False）')
    class Flaky(tray_icon.TrayIcon):
        def __init__(self, *a, **k):
            tray_icon.TrayIcon.__init__(self, *a, **k)
            self.tries = 0

        def _run(self):
            self.tries += 1
            if self.tries == 1:
                self.error = '模拟 NIM_ADD 失败'
                self._ready.set()
                return
            tray_icon.TrayIcon._run(self)

    icon = os.path.join(TMP, 'icon-light.ico')
    t = Flaky(icon)
    first = t.start()
    second = t.start()
    try:
        check(not first, '第一次 start 失败（%s）' % (t.error or '?'))
        check(t.tries == 2, '第二次确实重建了线程：tries=%d' % t.tries)
        check(second and t.active, '第二次 start 成功：active=%s' % t.active)
    finally:
        t.stop()

    print('[B2] 托盘：线程退出后面板自愈重建')
    write_cfg('plain')
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=False, dpi_override=96)
    root.update()
    ok_end = False
    try:
        check(app.tray is not None and app.tray.active, '面板起来就有托盘图标')
        old = app.tray
        old.stop()                      # 模拟 explorer 重启/线程退出
        root.update()
        app._tray_pump()                # pump 发现 active=False 应当重建
        root.update()
        check(app.tray is not None and app.tray is not old,
              '已换成一枚新图标')
        check(app.tray.active, '新图标 active')

        print('[B3] 托盘：TaskbarCreated 广播后能把图标加回来')
        try:
            app.tray._on_msg(app.tray._hwnd, app.tray._taskbar_msg, 0, 0)
            ok = True
        except Exception as e:
            ok = False
            print('      异常：%r' % (e,))
        check(ok and app.tray.active, '收到 TaskbarCreated 不报错、图标仍在')

        print('[B4] 关窗：勾了「收进托盘」但图标补不上时，不隐藏窗口')
        app.tray.stop()
        app.tray = None
        app.tray_var.set(True)
        app.apply_tray_pref = lambda retry=0: None   # 模拟怎么补都补不上
        app.on_close()
        root.update()
        check(bool(root.winfo_exists()) and root.state() != 'withdrawn',
              '第一次关窗保持可见：state=%s' % root.state())
        app.on_close()                  # 第二次：用户明确要关，直接退出
        try:
            root.update()
            gone = not root.winfo_exists()
        except tk.TclError:
            gone = True                 # root 已销毁
        check(gone, '第二次关窗走退出分支（窗口已销毁）')
        ok_end = True
    finally:
        if not ok_end:
            try:
                root.destroy()
            except Exception:
                pass


def main():
    part_plain()
    part_tray()
    print('')
    if FAILS:
        print('FAILED %d：%s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
