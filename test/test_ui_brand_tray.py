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
        check(not hasattr(app, 'on_brand'),
              'on_brand 已随菜单一起退役（字标不绑点击）')
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

    print('[A2] 字标不是菜单入口（v2.17.0 骨架合并后只剩「设置」入口）')
    write_cfg('plain')
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=True, dpi_override=96)
    root.update()
    try:
        check(not app.brand_wrap.bind('<ButtonRelease-1>'),
              '字标没有点击绑定：入口是右上角「设置」')
        check(app.brand_wrap.cget('cursor') == 'arrow', '字标是箭头光标')
        check(bool(app.brand_wrap.bind('<B1-Motion>')),
              '字标仍可拖动窗口')
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
        if not t.active:
            # explorer 繁忙时 Shell_NotifyIcon(NIM_ADD) 会阻塞超过 start() 的
            # 3 秒等待窗口（系统托盘图标多时尤其明显）。线程还在跑，补一段时间
            # 再看结果——这不是放宽断言，落点仍然是「图标最终挂上了」。
            t._ready.wait(6.0)
        check(t.active, '第二次 start 成功：active=%s err=%r'
              % (t.active, t.error))
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

        print('[B3b] 托盘：模拟左键消息弹回窗口（单实例通知复用此路径）')
        import ctypes
        root.withdraw()
        root.update()
        u = ctypes.windll.user32
        u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_size_t, ctypes.c_ssize_t]
        u.PostMessageW(app.tray._hwnd, tray_icon.WM_TRAY, 1,
                       tray_icon.WM_LBUTTONUP)
        time.sleep(0.3)
        root.update()
        check(root.state() != 'withdrawn',
              'WM_TRAY + WM_LBUTTONUP -> CMD_OPEN -> 窗口弹回：state=%s'
              % root.state())

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


def part_single_instance():
    print('[C] 面板单实例（v2.14.0：托盘多个 W 的根因修复）')
    import win_env
    notified = []
    orig = win_env._notify_existing_panel
    win_env._notify_existing_panel = lambda: notified.append(1)
    # 独立互斥体名：开发机上面板通常正跑着，用生产名永远拿到「已存在」，
    # 断言会被环境带偏；顺带避免测试给真实面板发 WM_TRAY 弹窗。
    name = 'Local\\WBOXPanelSingleton-test-%d' % os.getpid()
    try:
        first = win_env.panel_single_instance(name)
        second = win_env.panel_single_instance(name)
        check(first is True, '首次启动拿到互斥体，继续运行')
        check(second is False, '重复启动让位（返回 False，调用方退出）')
        check(notified == [1], '让位时恰好通知一次已有面板弹出')
    finally:
        win_env._notify_existing_panel = orig


def part_tray_cleanup():
    print('[D] 托盘：消息循环异常也摘图标（不留幽灵图标）')
    calls = []

    class FakeS(object):
        def Shell_NotifyIconW(self, op, nid):
            calls.append(op)
            return True

    base = tray_icon._u()

    class BoomU(object):
        def __getattr__(self, n):
            return getattr(base, n)

        def GetMessageW(self, *a):
            raise RuntimeError('模拟消息循环崩溃')

    orig_u, orig_s = tray_icon._u, tray_icon._s
    tray_icon._u = lambda: BoomU()
    tray_icon._s = lambda: FakeS()
    try:
        t = tray_icon.TrayIcon(os.path.join(TMP, 'icon-light.ico'))
        t.start()
        # _ready 在消息循环崩溃**之前**就已置位，start() 可能先返回 True；
        # 等线程真正结束再断言状态，避免竞态误报。
        if t._thread is not None:
            t._thread.join(2.0)
        check(not t.active, '消息循环异常后托盘标记为不可用')
        check(tray_icon.NIM_ADD in calls and tray_icon.NIM_DELETE in calls,
              '异常路径也摘图标（NIM_ADD 之后有 NIM_DELETE）：%r' % (calls,))
    finally:
        tray_icon._u, tray_icon._s = orig_u, orig_s


def part_destroy_guard():
    print('[E] 托盘：重建子控件不误杀托盘（<Destroy> 冒泡回归）')
    write_cfg('plain')
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=False, dpi_override=96)
    root.update()
    try:
        check(app.tray is not None and app.tray.active, '托盘就绪')
        app.rebuild()
        root.update()
        check(app.tray is not None and app.tray.active,
              'rebuild 后托盘仍存活（旧实现被 <Destroy> 冒泡误杀）')
    finally:
        if app.tray is not None:
            app.tray.stop()
        root.destroy()


def part_stop_sync():
    print('[F] 托盘：stop() 不等消息线程也必须当场摘图标（v2.14.1 托盘残余）')
    import ctypes
    import threading
    calls = []

    class FakeS(object):
        def Shell_NotifyIconW(self, op, nid):
            calls.append(op)
            return True

    orig_s = tray_icon._s
    tray_icon._s = lambda: FakeS()
    try:
        t = tray_icon.TrayIcon(os.path.join(TMP, 'icon-light.ico'))
        nid = tray_icon.NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = 1
        nid.uID = 1
        t._nid = nid
        t.active = True
        # 模拟「消息线程还卡在右键菜单里」：线程活着但永远不处理 WM_CLOSE
        stuck = threading.Thread(target=lambda: time.sleep(1.5), daemon=True)
        t._thread = stuck
        stuck.start()
        t0 = time.time()
        t.stop(timeout=0.2)
        cost = time.time() - t0
        check(cost < 0.9, 'stop() 没有干等线程：%.2fs' % cost)
        check(calls == [tray_icon.NIM_DELETE],
              '线程没退出也当场摘图标：%r' % (calls,))
        check(not t.active, 'stop() 后 active=False')
        t.delete()
        t.delete()
        check(calls == [tray_icon.NIM_DELETE], 'delete() 幂等，不重复摘：%r'
              % (calls,))
    finally:
        tray_icon._s = orig_s


def part_close_panel():
    print('[G] 安装/卸载：stop_guard 会请面板自己退出（而不是 taskkill /F）')
    import uninstall_core
    import tkinter as tk2
    root = tk2.Tk()
    root.geometry('120x80')
    root.update()
    orig = uninstall_core.guard_pids
    uninstall_core.guard_pids = lambda target, exe=None: [os.getpid()]
    try:
        n = uninstall_core.close_panel_windows('X:\\none')
        check(n >= 1, '对 TkTopLevel 窗口发了 WM_CLOSE：命中 %d 个' % n)
        for _ in range(20):
            try:
                root.update()
            except tk2.TclError:
                break                   # 窗口已被 WM_CLOSE 销毁
            time.sleep(0.05)
        try:
            alive = bool(root.winfo_exists())
        except tk2.TclError:
            alive = False
        check(not alive, '面板收到 WM_CLOSE 后自行销毁（不走强杀）')
    finally:
        uninstall_core.guard_pids = orig
        try:
            root.destroy()
        except Exception:
            pass


def part_tray_clean():
    print('[H] 托盘残影：只在用户没动鼠标时扫，取不到图标行就不动手')
    import tray_clean as tc
    orig_strip, orig_idle = tc.icon_strip, tc._idle_sec
    try:
        strip = tc.icon_strip()
        check(strip is None or (len(strip) == 3 and strip[2] > strip[0]),
              '图标行矩形合理：%r' % (strip,))
        tc.icon_strip = lambda: None
        check(tc.nudge(force=True) == 0, '取不到图标行 -> 返回 0，不动指针')
        tc.icon_strip = lambda: (100, 100, 300)
        tc._idle_sec = lambda: 0.2
        check(tc.nudge() == 0, '用户 1.2 秒内动过鼠标 -> 不扫')
    finally:
        tc.icon_strip, tc._idle_sec = orig_strip, orig_idle


def main():
    part_plain()
    part_tray()
    part_single_instance()
    part_tray_cleanup()
    part_destroy_guard()
    part_stop_sync()
    part_close_panel()
    part_tray_clean()
    print('')
    if FAILS:
        print('FAILED %d：%s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
