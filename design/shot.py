"""屏幕区域截图（纯 ctypes，不依赖 PIL），写 32 位 BMP。

沙箱里 PowerShell 的 Add-Type 被拦、PIL 不一定装，但 GDI 的 BitBlt + GetDIBits
只用 user32/gdi32，任何 Python 都能跑。产物是 top-down 的 BI_RGB BMP，
ViewImage 与 Windows 资源管理器都能直接打开。

用法：
    C:\\Python38\\python.exe design/shot.py out.bmp            # 全屏
    C:\\Python38\\python.exe design/shot.py out.bmp 100 80 640 400
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
        ('biPlanes', wt.WORD), ('biBitCount', wt.WORD),
        ('biCompression', wt.DWORD), ('biSizeImage', wt.DWORD),
        ('biXPelsPerMeter', wt.LONG), ('biYPelsPerMeter', wt.LONG),
        ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD),
    ]


def screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def grab(path, x=0, y=0, w=None, h=None):
    sw, sh = screen_size()
    w = int(w or sw)
    h = int(h or sh)
    hdc = user32.GetDC(0)
    if not hdc:
        raise OSError('GetDC failed')
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not gdi32.BitBlt(mdc, 0, 0, w, h, hdc, int(x), int(y), SRCCOPY):
            raise OSError('BitBlt failed: %s' % ctypes.get_last_error())
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h              # 负数 = top-down，省一次翻转
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0          # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
        if not got:
            raise OSError('GetDIBits failed')
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(0, hdc)

    pixels = buf.raw
    header = struct.pack('<2sIHHI', b'BM', 14 + 40 + len(pixels), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, w, -h, 1, 32, 0, len(pixels), 2835, 2835, 0, 0)
    with open(path, 'wb') as f:
        f.write(header)
        f.write(info)
        f.write(pixels)
    return path


def grab_window(hwnd, path):
    """直接抓某个窗口的内容（PrintWindow），不受遮挡影响。

    屏幕截图（CopyFromScreen）在窗口被别的程序盖住时只能截到别人的画面，
    验收界面时不可靠；Tk 用 GDI 绘制，PrintWindow 能拿到同样的像素。
    """
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

    class RECT(ctypes.Structure):
        _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                    ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

    rc = RECT()
    user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rc))
    w = rc.right - rc.left
    h = rc.bottom - rc.top
    if w <= 0 or h <= 0:
        raise OSError('窗口尺寸为 0：%r' % ((w, h),))
    hdc = user32.GetWindowDC(ctypes.c_void_p(hwnd))
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        ok = user32.PrintWindow(ctypes.c_void_p(hwnd), mdc, 2)   # PW_RENDERFULLCONTENT
        if not ok:
            user32.PrintWindow(ctypes.c_void_p(hwnd), mdc, 0)
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS):
            raise OSError('GetDIBits failed')
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(ctypes.c_void_p(hwnd), hdc)
    pixels = buf.raw
    with open(path, 'wb') as f:
        f.write(struct.pack('<2sIHHI', b'BM', 14 + 40 + len(pixels), 0, 0, 54))
        f.write(struct.pack('<IiiHHIIiiII', 40, w, -h, 1, 32, 0, len(pixels),
                            2835, 2835, 0, 0))
        f.write(pixels)
    return path


def main(argv):
    path = argv[1] if len(argv) > 1 else 'shot.bmp'
    if len(argv) >= 6:
        grab(path, argv[2], argv[3], argv[4], argv[5])
    else:
        grab(path)
    print(path)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
