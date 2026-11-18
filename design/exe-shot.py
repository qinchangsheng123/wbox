# -*- coding: utf-8 -*-
"""启动打包后的 exe，抓它的窗口截图（验收真机效果）。

不依赖 Tk 内部对象：按进程找顶层窗口，PrintWindow 直接取像素。
用法：C:/Python38/python.exe design/exe-shot.py <exe> <out.bmp> [menu|plugin]
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shot  # noqa: E402

user32 = ctypes.WinDLL('user32', use_last_error=True)

# 抓图进程自己必须 DPI 感知：否则 GetWindowRect / PrintWindow 拿到的是
# 被系统虚拟化过的坐标（150% 缩放下尺寸会小 1/3，截出来只有窗口一角）。
try:
    ctypes.WinDLL('shcore').SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def windows_of(pid):
    """当前进程的所有可见顶层窗口 [(hwnd, title, w, h)]。"""
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        pid_ = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_))
        if pid_.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        rc = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rc))
        out.append((hwnd, buf.value, rc.right - rc.left, rc.bottom - rc.top))
        return True

    user32.EnumWindows(cb, 0)
    return out


def main(argv):
    exe = argv[1]
    out = argv[2] if len(argv) > 2 else 'D:/06-Temp/exe.bmp'
    mode = argv[3] if len(argv) > 3 else ''
    proc = subprocess.Popen([exe, '--settings'])
    try:
        wins = []
        for _ in range(40):
            time.sleep(0.25)
            # Tk 在映射前会先建一批 0x0 的辅助窗口，必须按尺寸过滤，
            # 否则会抓到"看不见的空壳"再报窗口尺寸为 0。
            wins = [t for t in windows_of(proc.pid) if t[2] >= 300 and t[3] >= 150]
            if wins:
                break
        if not wins:
            print('no window')
            return 1
        for hwnd, title, w, h in wins:
            print('window %r %dx%d hwnd=%s' % (title, w, h, hwnd))
        # 主窗口通常是最大的那个（浮层是 overrideredirect，也算顶层窗口）
        main_win = max(wins, key=lambda t: t[2] * t[3])
        time.sleep(0.8)
        hwnd = main_win[0]
        if mode in ('menu', 'plugin'):
            # 用一次点击打开菜单：先把窗口带到前台，再点字标（左上角 40,40）
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            rc = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            # v2.8.0 起是自绘标题栏（overrideredirect），没有系统标题栏占位：
            # 字标 WBOX 就在内容区左上角，横向 ~26..96、纵向 ~12..36（100% 缩放）。
            # 点它的中心，别再用"让开 40 px 系统标题栏"的老坐标。
            x, y = rc.left + 56, rc.top + 24
            user32.SetCursorPos(x, y)
            time.sleep(0.1)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(1.0)
            wins = windows_of(proc.pid)
            for hwnd2, title2, w2, h2 in wins:
                print('after click: %r %dx%d' % (title2, w2, h2))
            pop = [t for t in wins if t[0] != hwnd and t[3] > 200]
            if pop:
                shot.grab_window(pop[0][0], out)
                print('saved popup', out)
                return 0
        shot.grab_window(hwnd, out)
        print('saved', out)
        return 0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main(sys.argv))
