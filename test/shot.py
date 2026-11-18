# -*- coding: utf-8 -*-
"""抓整屏存 BMP（不依赖 Pillow，仅 ctypes + GDI）。"""
import ctypes
import struct
import sys
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

# 必须先声明 DPI 感知：否则在 150% 缩放下 GetSystemMetrics 只拿到 96 DPI 逻辑值，
# 截出来的图会被系统位图拉伸（发虚、尺寸对不上）。
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p((1 << 64) - 4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wintypes.DWORD * 3)]


def shot(path):
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)  # SRCCOPY

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h            # 负数 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    size = w * h * 4
    buf = ctypes.create_string_buffer(size)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

    row_bytes = w * 4
    with open(path, 'wb') as f:
        f.write(b'BM')
        f.write(struct.pack('<IHHI', 14 + 40 + size, 0, 0, 14 + 40))
        f.write(struct.pack('<IiiHHIIiiII', 40, w, -h, 1, 32, 0, size, 2835, 2835, 0, 0))
        f.write(buf.raw)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, hdc)
    return w, h


if __name__ == '__main__':
    p = sys.argv[1] if len(sys.argv) > 1 else r'C:\Windows\Temp\shot.bmp'
    print('saved', p, shot(p))
