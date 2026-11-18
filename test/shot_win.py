# -*- coding: utf-8 -*-
"""把指定标题的窗口提到前台，只截该窗口（PrintWindow 优先，失败回落全屏裁剪）。

用法: python test/shot_win.py <输出bmp> [标题关键字]
"""
import ctypes
import struct
import sys
from ctypes import wintypes

sys.path.insert(0, __file__.rsplit('\\', 1)[0])
from shot import (BITMAPINFO, BITMAPINFOHEADER, gdi32, user32, shot)

PW_RENDERFULLCONTENT = 0x00000002

# 本脚本必须和被测窗口同一个 DPI 感知级别，否则 GetWindowRect / PrintWindow
# 拿到的是被系统虚拟化后的 96 DPI 坐标（144 缩放下窗口看起来"变小且被裁"）。
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p((1 << 64) - 4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def find_hwnd(keyword):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if keyword in buf.value:
            found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


def to_front(hwnd):
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_TOPMOST, NOSIZE|NOMOVE
    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_NOTOPMOST
    user32.SetForegroundWindow(hwnd)


def grab(hwnd, path):
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    size = w * h * 4
    buf = ctypes.create_string_buffer(size)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    with open(path, 'wb') as f:
        f.write(b'BM')
        f.write(struct.pack('<IHHI', 14 + 40 + size, 0, 0, 14 + 40))
        f.write(struct.pack('<IiiHHIIiiII', 40, w, -h, 1, 32, 0, size, 2835, 2835, 0, 0))
        f.write(buf.raw)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return ok, w, h


if __name__ == '__main__':
    out = sys.argv[1]
    key = sys.argv[2] if len(sys.argv) > 2 else 'Afterburner'
    hits = find_hwnd(key)
    if not hits:
        print('NO WINDOW matching', key)
        sys.exit(2)
    hwnd, title = hits[0]
    print('hwnd=%s title=%s' % (hwnd, title))
    to_front(hwnd)
    ok, w, h = grab(hwnd, out)
    print('printwindow=%s size=%dx%d -> %s' % (bool(ok), w, h, out))
