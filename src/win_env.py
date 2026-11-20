# -*- coding: utf-8 -*-
"""Windows 环境探测：DPI 感知、屏幕工作区、权限与可写判断、窗口图标、powershell。

全部是"向系统要一个事实"的薄封装，不含界面代码，所以面板之外的脚本（安装器
自检、诊断工具、截图脚本）也能直接复用。每个函数失败都只返回保守默认值、
不抛异常：探测失败不该拦住院子里的主流程。
"""
import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

import panel_env

BASE = panel_env.BASE
CFG = panel_env.CFG
PS = panel_env.PS
TASKLIST = panel_env.TASKLIST
ICON_LIGHT = panel_env.ICON_LIGHT
ICON_DARK = panel_env.ICON_DARK
# 拉起子进程时不弹控制台窗口（CREATE_NO_WINDOW）。
NOWIN = 0x08000000
DPI_MODE = 'unknown'

# ---- DPI 感知 + 工作区 ----
def enable_dpi_awareness():
    """必须在创建 Tk 窗口之前调用。返回实际生效的模式。"""
    global DPI_MODE
    u = ctypes.windll.user32
    try:
        f = u.SetProcessDpiAwarenessContext
        f.argtypes = [ctypes.c_void_p]
        f.restype = wintypes.BOOL
        if f(ctypes.c_void_p((1 << 64) - 4)):       # PER_MONITOR_AWARE_V2 = -4
            DPI_MODE = 'PerMonitorV2'
            return DPI_MODE
    except Exception:
        pass
    try:
        f = ctypes.windll.shcore.SetProcessDpiAwareness
        f.argtypes = [ctypes.c_int]
        f.restype = ctypes.c_long
        if f(2) == 0:                                # PER_MONITOR_DPI_AWARE
            DPI_MODE = 'PerMonitor'
            return DPI_MODE
    except Exception:
        pass
    try:
        if u.SetProcessDPIAware():
            DPI_MODE = 'System'
            return DPI_MODE
    except Exception:
        pass
    # 到这里也可能是 exe manifest 已声明感知（此时调用会被系统拒绝，但 DPI 已正确）
    DPI_MODE = 'AlreadySetByManifest'
    return DPI_MODE


def system_dpi():
    u = ctypes.windll.user32
    try:
        f = u.GetDpiForSystem
        f.restype = wintypes.UINT
        d = int(f())
        if d >= 72:
            return d
    except Exception:
        pass
    try:
        h = u.GetDC(0)
        d = int(ctypes.windll.gdi32.GetDeviceCaps(h, 88))
        u.ReleaseDC(0, h)
        if d >= 72:
            return d
    except Exception:
        pass
    return 96


def window_dpi(hwnd):
    try:
        f = ctypes.windll.user32.GetDpiForWindow
        f.argtypes = [wintypes.HWND]
        f.restype = wintypes.UINT
        d = int(f(hwnd))
        if d >= 72:
            return d
    except Exception:
        pass
    return system_dpi()


class RECT(ctypes.Structure):
    _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                ('right', wintypes.LONG), ('bottom', wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]


def work_area():
    """屏幕工作区（排除任务栏），返回 left, top, width, height。"""
    try:
        r = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        pass
    u = ctypes.windll.user32
    return 0, 0, int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))



# ---- 管理员 / 可写判断 / 提权重开 ----
def is_admin():
    """当前进程是否已提权。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def file_writable(path):
    """真开一次追加模式来判可写：os.access 在 UAC 虚拟化 / 继承 ACL 下会误报。"""
    try:
        with open(path, 'a', encoding='utf-8'):
            pass
        return True
    except Exception:
        return False


def dir_writable(path):
    """配置文件还不存在时，退而探测所在目录能否新建文件。"""
    probe = os.path.join(path, '.abg_write_probe.tmp')
    try:
        with open(probe, 'a', encoding='utf-8'):
            pass
        try:
            os.remove(probe)
        except OSError:
            pass
        return True
    except Exception:
        return False


def can_write_config():
    """面板能否把设置写进 config.json（决定是否给功能项置灰）。

    保存走的是"建 config.json.tmp → os.replace"，既需要**文件**可写，也需要
    **目录**可新建文件。只测文件追加写会在"文件被单独授权、目录仍受限"时误判
    为可写，用户改完开关保存失败且无提示（2026-11-11 审计发现）。
    """
    if not os.path.isdir(BASE):
        return False
    if os.path.exists(CFG) and not file_writable(CFG):
        return False
    return dir_writable(BASE)


def elevate_settings(args):
    """配置写不进去且当前不是管理员时自我提权重开面板。

    返回 True 表示已经把提权副本拉起来（本进程应立即退出）。返回 False 表示
    用户拒绝了 UAC（ShellExecuteW ≤ 32，含取消）或本次已是 --elevated 副本，
    调用方应以"置灰只读"模式正常打开面板。
    """
    if '--elevated' in args:
        return False            # 已经提权过一次仍然不可写：不再递归弹 UAC
    params = '--settings --elevated'
    if not getattr(sys, 'frozen', False):
        # 源码调试时 sys.executable 是 python.exe，只传开关会变成"python --settings"，
        # 提权副本启动即报错退出；必须把入口脚本一起带上。
        # python -c / -m 调试时 sys.argv[0] 是 '-c'/'pkg.mod'，不是可执行脚本路径，
        # 这种情况回退到本文件旁边的 abg_main.py（源码树里一定存在）。
        script = os.path.abspath(sys.argv[0] or '')
        if not (os.path.isfile(script) and script.lower().endswith('.py')):
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'abg_main.py')
        params = '"%s" %s' % (script, params)
    try:
        sh = ctypes.windll.shell32
        sh.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                     wintypes.LPCWSTR, wintypes.LPCWSTR,
                                     wintypes.LPCWSTR, ctypes.c_int]
        sh.ShellExecuteW.restype = ctypes.c_ssize_t
        rc = sh.ShellExecuteW(None, 'runas', sys.executable, params, BASE, 1)
    except Exception:
        return False
    return bool(rc and rc > 32)



# ---- 面板单实例 ----
_PANEL_MUTEX = None
PANEL_MUTEX_NAME = 'Local\\WBOXPanelSingleton'


def panel_single_instance(name=PANEL_MUTEX_NAME):
    """命名互斥体：同一会话只留一个面板。返回 True 表示本进程应继续运行。

    守护有 Local\\WBOXSingleton，面板此前没有互斥：连开两次就留下两个面板
    进程，各自往通知区挂一枚 WBOX 图标（2026-11-20 实测「托盘多个 W」的成因）。
    重复启动时先通知已有面板弹出，再返回 False 让调用方退出。互斥体本身
    创建失败则放行——单实例是约束，不是功能前提。

    name 可覆盖：测试必须用独立名字，否则机器上正跑着面板时永远拿到
    ERROR_ALREADY_EXISTS，断言会被环境带偏，还会给真实面板发弹窗消息。
    """
    global _PANEL_MUTEX
    try:
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                  wintypes.LPCWSTR]
        k.CreateMutexW.restype = wintypes.HANDLE
        h = k.CreateMutexW(None, False, name)
        err = ctypes.get_last_error()
        if h:
            _PANEL_MUTEX = h
        if err != 183:                    # ERROR_ALREADY_EXISTS
            return True
    except Exception:
        return True
    _notify_existing_panel()
    return False


def _notify_existing_panel():
    """让已有面板弹出：向其托盘消息窗模拟一次左键（走 CMD_OPEN 队列）。

    message-only 窗口收不到广播、FindWindowW 也找不到，必须用
    FindWindowExW(HWND_MESSAGE, ...) 枚举。托盘没就绪（开机自启创建失败）
    时退回按标题直接显示主窗口。
    """
    try:
        import tray_icon
        u = ctypes.windll.user32
        u.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                  wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowExW.restype = wintypes.HWND
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM]
        hwnd = u.FindWindowExW(tray_icon.HWND_MESSAGE, None, None, 'WBOX Tray')
        if hwnd:
            u.PostMessageW(hwnd, tray_icon.WM_TRAY, 1, tray_icon.WM_LBUTTONUP)
            return
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowW.restype = wintypes.HWND
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        hwnd = u.FindWindowW(None, 'WBOX')
        if hwnd:
            u.ShowWindow(hwnd, 5)         # SW_SHOW
            u.SetForegroundWindow(hwnd)
    except Exception:
        pass


# ---- ps_run ----
def ps_run(cmd, timeout=25):
    r = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-Command', cmd],
                       creationflags=NOWIN, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).decode('utf-8', 'replace').strip()



# ---- 窗口图标（任务栏 / 任务管理器） ----
# ---------------- 窗口图标（任务管理器 / 任务栏 / Alt+Tab） ----------------
# Tk 的 wm iconbitmap 只保证大图标；任务管理器「进程」列表、任务栏预览
# 与 Alt+Tab 取的是窗口 ICON_SMALL，不显式设置会回退成 Tk 默认羽毛图标。
WM_SETICON = 0x0080
WM_GETICON = 0x007F
ICON_SMALL, ICON_BIG = 0, 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
_HICONS = {}


def _user32_calls():
    u = ctypes.windll.user32
    load = u.LoadImageW
    load.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                     ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    load.restype = ctypes.c_void_p
    send = u.SendMessageW
    send.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                     ctypes.c_size_t, ctypes.c_ssize_t]
    send.restype = ctypes.c_ssize_t
    return send, load


def win_hwnd(win):
    """取顶层 frame 的 HWND：任务栏与任务管理器读它，而不是客户区子窗口。"""
    try:
        frame = win.wm_frame()
        if frame:
            return int(frame, 16)
    except Exception:
        pass
    return int(win.winfo_id())


def set_window_icons(win, path):
    """同时设置 ICON_SMALL / ICON_BIG 为设计图标，返回 (small_ok, big_ok)。"""
    if not os.path.isfile(path):
        return False, False
    try:
        send, load = _user32_calls()
        u = ctypes.windll.user32
        hwnd = win_hwnd(win)
        small_size = u.GetSystemMetrics(49) or 16      # SM_CXSMICON
        big_size = u.GetSystemMetrics(11) or 32        # SM_CXICON
        ok = []
        for which, size in ((ICON_SMALL, small_size), (ICON_BIG, big_size)):
            key = (path, size)
            h = _HICONS.get(key)
            if not h:
                h = load(None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE)
                if h:
                    _HICONS[key] = h
            if h:
                send(hwnd, WM_SETICON, which, h)
                ok.append(which)
        return ICON_SMALL in ok, ICON_BIG in ok
    except Exception:
        return False, False


def get_window_icons(win):
    """自检用：读回窗口当前的大小图标句柄是否都已设置。"""
    try:
        send, _ = _user32_calls()
        hwnd = win_hwnd(win)
        return (bool(send(hwnd, WM_GETICON, ICON_SMALL, 0)),
                bool(send(hwnd, WM_GETICON, ICON_BIG, 0)))
    except Exception:
        return False, False
