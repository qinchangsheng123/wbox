# -*- coding: utf-8 -*-
"""托盘图标（面板侧，v2.9.0）：Shell_NotifyIcon + 独立消息线程。

为什么单独一个线程：托盘回调（左键/右键/气泡）走 Win32 消息循环，不能塞进
Tk 的 after 链——TrackPopupMenu 是阻塞调用，放在主线程会冻住整个面板。
这里自建 message-only 窗口收 Shell 回调，菜单也在本线程弹；给面板的动作
（打开设置 / 退出）只往 queue 里塞命令，由面板主线程轮询取走再碰 Tk。

降级原则与 guard_beat 一致：任何 Win32 失败都按「没有托盘」处理，
绝不抛异常打断面板。托盘是可选装饰，不是功能前提。
"""
import ctypes
import os
import queue
import threading
from ctypes import wintypes

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 0x10
NIIF_INFO = 1
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_BALLOON = WM_APP + 2
WM_CLOSE = 0x0010
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
MF_STRING = 0x0000
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
HWND_MESSAGE = ctypes.c_void_p(-3)

CMD_OPEN, CMD_EXIT = 1, 2


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD), ('hWnd', wintypes.HWND),
        ('uID', wintypes.UINT), ('uFlags', wintypes.UINT),
        ('uCallbackMessage', wintypes.UINT), ('hIcon', wintypes.HICON),
        ('szTip', wintypes.WCHAR * 128),
        ('dwState', wintypes.DWORD), ('dwStateMask', wintypes.DWORD),
        ('szInfo', wintypes.WCHAR * 256),
        ('uVersion', wintypes.UINT),          # 与 uTimeout 同偏移的 union
        ('szInfoTitle', wintypes.WCHAR * 64), ('dwInfoFlags', wintypes.DWORD),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ('style', wintypes.UINT), ('lpfnWndProc', ctypes.c_void_p),
        ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
        ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HICON),
        ('hCursor', wintypes.HANDLE), ('hbrBackground', wintypes.HANDLE),
        ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ('hwnd', wintypes.HWND), ('message', wintypes.UINT),
        ('wParam', wintypes.WPARAM), ('lParam', wintypes.LPARAM),
        ('time', wintypes.DWORD), ('pt_x', wintypes.LONG),
        ('pt_y', wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


def _u():
    u = ctypes.WinDLL('user32', use_last_error=True)
    u.CreateWindowExW.restype = wintypes.HWND
    u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR,
                                  wintypes.LPCWSTR, wintypes.DWORD,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND,
                                  wintypes.HMENU, wintypes.HINSTANCE,
                                  wintypes.LPVOID]
    u.DefWindowProcW.restype = ctypes.c_long
    u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM]
    u.LoadImageW.restype = wintypes.HANDLE
    u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                             wintypes.UINT, ctypes.c_int, ctypes.c_int,
                             wintypes.UINT]
    u.GetMessageW.restype = ctypes.c_int
    u.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                              wintypes.UINT, wintypes.UINT]
    u.TrackPopupMenu.restype = wintypes.BOOL
    u.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                 wintypes.LPVOID]
    u.CreatePopupMenu.restype = wintypes.HMENU
    u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                              ctypes.c_size_t, wintypes.LPCWSTR]
    u.DestroyMenu.argtypes = [wintypes.HMENU]
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                               wintypes.WPARAM, wintypes.LPARAM]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    u.UnregisterClassW.restype = wintypes.BOOL
    u.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    u.DestroyWindow.argtypes = [wintypes.HWND]
    u.DestroyIcon.restype = wintypes.BOOL
    u.DestroyIcon.argtypes = [wintypes.HICON]
    # explorer 重启会广播 TaskbarCreated：收到后要自己把图标加回去，
    # 否则图标消失到下次启动面板为止。
    u.RegisterWindowMessageW.restype = wintypes.UINT
    u.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    # 图标文件读不出来时退回系统默认图标：hIcon=NULL 的 NIM_ADD 在通知区
    # 可能是一块空白，用户会当成"没显示"。
    u.LoadIconW.restype = wintypes.HANDLE
    u.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    return u


def _s():
    s = ctypes.WinDLL('shell32', use_last_error=True)
    s.Shell_NotifyIconW.restype = wintypes.BOOL
    s.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                    ctypes.POINTER(NOTIFYICONDATAW)]
    return s


class TrayIcon(object):
    """一枚托盘图标 + 右键菜单（打开设置 / 退出）+ 气泡通知。

    commands 是线程安全队列：面板主线程轮询取 (CMD_OPEN|CMD_EXIT,)。
    active 为 False 表示任何一环没起来，调用方应当作「没有托盘」继续跑。
    """

    def __init__(self, icon_path, tip='WBOX'):
        self.icon_path = icon_path
        self.tip = tip
        self.commands = queue.Queue()
        self.active = False
        self.error = ''           # 起不来的原因，面板写日志用（不弹框）
        self._balloons = queue.Queue()
        self._hwnd = None
        self._thread = None
        self._ready = threading.Event()
        self._wndproc = None      # 保持引用，防回调被 GC
        self._taskbar_msg = 0     # explorer 重启广播（TaskbarCreated）

    # ---- 主线程 API ----
    def start(self):
        """返回本次是否成功。失败后**允许重试**：开机自启时 explorer 还没
        准备好托盘，Shell_NotifyIcon 会失败；旧实现把 _thread 留着，第二次
        start() 直接返回 False，整个会话就再也没有图标。"""
        if self.active:
            return True
        t = self._thread
        if t is not None and t.is_alive():
            self._ready.wait(1.0)
            return self.active
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name='wbox-tray',
                                        daemon=True)
        self._thread.start()
        self._ready.wait(3.0)
        if not self.active:
            self._thread = None   # 线程已结束，下次重建类/窗口
        return self.active

    def balloon(self, title, text):
        if not self.active or self._hwnd is None:
            return
        self._balloons.put((title, text))
        try:
            _u().PostMessageW(self._hwnd, WM_BALLOON, 0, 0)
        except Exception:
            pass

    def set_icon(self, icon_path):
        """主题切换时换托盘 glyph（NIM_MODIFY）。Shell_NotifyIconW 任意线程可调。"""
        if not self.active or self._hwnd is None or icon_path == self.icon_path:
            return
        u = _u()
        hicon = u.LoadImageW(None, icon_path, IMAGE_ICON, 16, 16,
                             LR_LOADFROMFILE)
        if not hicon:
            return
        old = self._nid.hIcon
        self.icon_path = icon_path
        nid = self._nid
        nid.uFlags = NIF_ICON
        nid.hIcon = hicon
        try:
            _s().Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        finally:
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        if old:
            try:
                u.DestroyIcon(old)
            except Exception:
                pass

    def stop(self):
        if self._hwnd is not None:
            try:
                _u().PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self.active = False

    # ---- 消息线程 ----
    def _run(self):
        try:
            self._loop()
        except Exception as e:
            self.active = False
            self.error = '托盘线程异常：%r' % (e,)
        finally:
            self._ready.set()

    def _loop(self):
        u, s = _u(), _s()
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.GetModuleHandleW.restype = wintypes.HMODULE
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        hinst = k.GetModuleHandleW(None)
        # 类名进程全局：同名类第二次 RegisterClassW 必失败（同进程再建托盘
        # 会静默死掉），按实例区分并在销窗后注销。
        cls_name = 'WBOXTrayMsg_%x' % id(self)
        self._wndproc = WNDPROC(self._on_msg)
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        if not u.RegisterClassW(ctypes.byref(wc)):
            self.error = 'RegisterClassW 失败（类名冲突）'
            return
        hwnd = u.CreateWindowExW(0, cls_name, 'WBOX Tray', 0, 0, 0, 0, 0,
                                 HWND_MESSAGE, None, hinst, None)
        if not hwnd:
            u.UnregisterClassW(cls_name, hinst)
            self.error = 'CreateWindowExW 失败'
            return
        self._hwnd = hwnd
        self._taskbar_msg = u.RegisterWindowMessageW('TaskbarCreated')
        hicon = u.LoadImageW(None, self.icon_path, IMAGE_ICON, 16, 16,
                             LR_LOADFROMFILE)
        if not hicon:
            # IDI_APPLICATION = 32512，走 MAKEINTRESOURCE
            hicon = u.LoadIconW(None, ctypes.c_void_p(32512))
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = hicon
        nid.szTip = self.tip[:127]
        if not s.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            u.DestroyWindow(hwnd)
            u.UnregisterClassW(cls_name, hinst)
            self._hwnd = None
            self.error = 'Shell_NotifyIcon(NIM_ADD) 失败（explorer 可能未就绪）'
            return
        self._nid = nid
        self.active = True
        self._ready.set()
        msg = MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        # 循环退出（WM_CLOSE 里 PostQuitMessage）：摘图标再销窗
        try:
            s.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass
        u.DestroyWindow(hwnd)
        u.UnregisterClassW(cls_name, hinst)
        self._hwnd = None
        self.active = False

    def _on_msg(self, hwnd, msg, wp, lp):
        u = _u()
        if msg and msg == self._taskbar_msg:
            self._readd()
            return 0
        if msg == WM_TRAY:
            ev = lp & 0xFFFF
            if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.commands.put((CMD_OPEN,))
            elif ev == WM_RBUTTONUP:
                self._menu(hwnd)
            return 0
        if msg == WM_BALLOON:
            self._show_balloon()
            return 0
        if msg == WM_CLOSE:
            u.PostQuitMessage(0)
            return 0
        return u.DefWindowProcW(hwnd, msg, wp, lp)

    def _readd(self):
        """explorer 重启后通知区被清空：收到 TaskbarCreated 就重挂一次。
        NIM_ADD 对同一 (hWnd,uID) 是幂等的，重复调用不会出现两枚图标。"""
        nid = getattr(self, '_nid', None)
        if nid is None:
            return
        try:
            _s().Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        except Exception:
            pass

    def _menu(self, hwnd):
        u = _u()
        try:
            hmenu = u.CreatePopupMenu()
            u.AppendMenuW(hmenu, MF_STRING, CMD_OPEN, '打开设置')
            u.AppendMenuW(hmenu, MF_STRING, CMD_EXIT, '退出')
            pt = POINT()
            u.GetCursorPos(ctypes.byref(pt))
            # 标准坑：不 SetForegroundWindow，菜单点外面不收、焦点不回 tray
            u.SetForegroundWindow(hwnd)
            cmd = u.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_NONOTIFY,
                                   pt.x, pt.y, 0, hwnd, None)
            u.PostMessageW(hwnd, WM_NULL, 0, 0)
            u.DestroyMenu(hmenu)
            if cmd:
                self.commands.put((int(cmd),))
        except Exception:
            pass

    def _show_balloon(self):
        try:
            title, text = self._balloons.get_nowait()
        except queue.Empty:
            return
        try:
            nid = self._nid
            nid.uFlags = NIF_INFO
            nid.szInfo = text[:255]
            nid.szInfoTitle = title[:63]
            nid.dwInfoFlags = NIIF_INFO
            nid.uVersion = 5000
            _s().Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        except Exception:
            pass