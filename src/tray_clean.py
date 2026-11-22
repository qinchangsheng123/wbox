# -*- coding: utf-8 -*-
"""托盘残影清理（v2.14.1）。

问题：内核在会话结束时会把「关到托盘」的程序强杀（msiafterburner / rtss /
游戏加加 …）。这些程序被 TerminateProcess / taskkill 掉时没机会调
Shell_NotifyIcon(NIM_DELETE)，通知区就留下它们的图标——鼠标不扫过去不掉。

为什么不能"通知 explorer 重画"：实测四种办法全部无效（2026-11-20，Win11）：
  - Shell_NotifyIcon(NIM_ADD) 再 NIM_DELETE 一枚临时图标
  - SendMessageTimeout(HWND_BROADCAST, WM_SETTINGCHANGE, 'TraySettings')
  - RedrawWindow(Shell_TrayWnd, RDW_INVALIDATE|UPDATENOW|ALLCHILDREN)
  - PostMessage WM_MOUSEMOVE 到 Shell_TrayWnd / TrayNotifyWnd / 托盘 ToolbarWindow32
    （Win11 的通知区是 XAML 岛，投递消息不产生真实悬停，图标不会被校验）
只有真实指针经过托盘图标行时 explorer 才会重算并丢掉死掉的图标——同一段
代码，SetCursorPos 扫一遍能清掉（实测像素差 2304），投递消息是 0。

所以这里就是"替用户扫一下鼠标"：把指针在托盘图标行上快扫一遍再放回原处。
前置条件很严：用户至少 1.2 秒没动键鼠、托盘图标行能取到矩形、整趟 < 250ms。
宁可不清，也不要在用户正用鼠标时把指针拽走。
"""
import ctypes
import time
from ctypes import wintypes

_u = ctypes.WinDLL('user32', use_last_error=True)

_u.FindWindowW.restype = wintypes.HWND
_u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_u.FindWindowExW.restype = wintypes.HWND
_u.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR,
                             wintypes.LPCWSTR]
_u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
_u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_u.GetCursorPos.argtypes = [ctypes.c_void_p]
_u.GetLastInputInfo.argtypes = [ctypes.c_void_p]

IDLE_BEFORE = 1.2      # 秒：用户这么久没动键鼠才动手
STEP_PX = 8
STEP_SLEEP = 0.008
MAX_POINTS = 40        # 上限，防止托盘特别宽时扫太久


class RECT(ctypes.Structure):
    _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                ('right', wintypes.LONG), ('bottom', wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]


def _idle_sec():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    if not _u.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return max(0.0, (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0)


def _rect(hwnd):
    r = RECT()
    if not hwnd or not _u.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    if r.right <= r.left or r.bottom <= r.top:
        return None
    return r


def icon_strip():
    """托盘图标行的矩形（不含时钟/输入法/显示桌面）。取不到返回 None。

    Win11 的通知区结构：Shell_TrayWnd > TrayNotifyWnd > { SysPager>ToolbarWindow32
    (图标), TrayInputIndicatorWClass, TrayClockWClass, TrayShowDesktopButtonWClass }。
    图标行取「图标容器左边」到「输入法指示器左边」。注意本进程多半是 DPI 不感知
    的，GetWindowRect 拿到的是被虚拟化的逻辑坐标——SetCursorPos 受同样的虚拟化，
    两者一致，所以不需要额外换算。
    """
    tray = _u.FindWindowW('Shell_TrayWnd', None)
    if not tray:
        return None
    notify = _u.FindWindowExW(tray, None, 'TrayNotifyWnd', None)
    if not notify:
        return None
    pager = _u.FindWindowExW(notify, None, 'SysPager', None)
    rp = _rect(pager)
    if not rp:                       # 老式（Win10）托盘：图标工具栏直接挂在下面
        bar = _u.FindWindowExW(notify, None, 'ToolbarWindow32', None)
        rp = _rect(bar)
        if not rp:
            return None
    right_hwnd = (_u.FindWindowExW(notify, None, 'TrayInputIndicatorWClass', None)
                  or _u.FindWindowExW(notify, None, 'TrayClockWClass', None))
    rr = _rect(right_hwnd)
    if not rr:
        return None
    left = rp.left + 2
    right = rr.left - 4
    if right - left < 8:
        return None
    return (left, (rp.top + rp.bottom) // 2, right)


def nudge(log=None, force=False):
    """在托盘图标行上快扫一遍指针，让 explorer 丢掉死进程留下的图标。

    返回清了几点（0 = 没动手）。任何异常都吞掉：这只是收尾美化，
    绝不能因为清残影把守护搞挂。
    """
    try:
        idle = _idle_sec()
        if not force and idle < IDLE_BEFORE:
            return 0
        strip = icon_strip()
        if strip is None:
            return 0
        x0, y, x1 = strip
        cur = POINT()
        if not _u.GetCursorPos(ctypes.byref(cur)):
            return 0
        pts = []
        x = x0
        while x <= x1 and len(pts) < MAX_POINTS:
            pts.append(x)
            x += STEP_PX
        if not pts:
            return 0
        for px in pts:
            _u.SetCursorPos(px, y)
            time.sleep(STEP_SLEEP)
        _u.SetCursorPos(cur.x, cur.y)
        if log:
            log('清理托盘残影：扫过 %d 点（图标行 %d~%d）' % (len(pts), x0, x1))
        return len(pts)
    except Exception as e:
        if log:
            log('清理托盘残影失败：%r' % (e,))
        return 0


def nudge_async(log=None, delay=0.4):
    """会话结束 / 守护启动时用：起个短线程扫一遍，不占主循环的一个 tick。"""
    import threading

    def _run():
        time.sleep(delay)     # 等被杀进程的图标先"死"透 / 等体验证完通知区
        nudge(log)

    try:
        threading.Thread(target=_run, name='wbox-tray-clean',
                         daemon=True).start()
    except Exception:
        pass
