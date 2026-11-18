# -*- coding: utf-8 -*-
"""WBOX 皮肤性能基准 v1.0.0

目的：给「液态玻璃」和「极简」两种皮肤的量级差异一个可复现的数字，
      而不是靠感觉说"极简更省"。

做法：每种皮肤在**独立子进程**里建一次真实窗口，量四件事：
      1) 首帧耗时       Tk 起来 → 第一次 update() 返回
      2) GDI / USER     GetGuiResources，控件与位图的真实句柄成本
      3) 私有内存       GetProcessMemoryInfo.PagefileUsage（提交量）
      4) 重绘 100 次    模拟悬停/切页，量每帧绘制成本

注意：必须在**有交互桌面**的会话里跑。无显示时 Tk 窗口不映射，update() 不真正绘制，
      首帧与重绘两项会测成接近 0 的假值；GDI / 内存仍可参考。

用法：
    C:\\Python38\\python.exe design\\perf-bench.py            # 跑全部
    C:\\Python38\\python.exe design\\perf-bench.py --mode plain
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import subprocess
import sys
import time
import zlib

W, H = 640, 332
ROW_H = 52
ROWS = 5


# ---------------------------------------------------------------- 计量
class PMC(ctypes.Structure):
    _fields_ = [('cb', wt.DWORD), ('PageFaultCount', wt.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]


def _proc_handle():
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.OpenProcess.restype = wt.HANDLE
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    return k32.OpenProcess(0x0400 | 0x0010, False, os.getpid())


def counters():
    h = _proc_handle()
    u32 = ctypes.WinDLL('user32', use_last_error=True)
    u32.GetGuiResources.restype = wt.DWORD
    u32.GetGuiResources.argtypes = [wt.HANDLE, wt.DWORD]
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.restype = wt.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PMC), wt.DWORD]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(pmc)
    psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
    return {
        'gdi': int(u32.GetGuiResources(h, 0)),
        'user': int(u32.GetGuiResources(h, 1)),
        'priv_mb': round(pmc.PagefileUsage / 1048576.0, 1),
        'ws_mb': round(pmc.WorkingSetSize / 1048576.0, 1),
    }


# ---------------------------------------------------------------- 位图
def ppm_blur_backdrop(path, w, h):
    """生成一张模拟"磨砂背景"的位图：竖向渐变 + 低频色块。

    真实实现里这是从桌面壁纸截屏 + 高斯模糊一次得到的，尺寸同为 w×h；
    这里用同样尺寸、同样 3 字节/像素的 PPM，好让内存与贴图成本和真实一致。
    """
    raw = bytearray()
    for y in range(h):
        for x in range(w):
            v = int(140 + 60 * (y / h) + 25 * ((x * 7 + y * 3) % 37) / 37.0)
            raw += bytes((v, v, v - 6 if v > 6 else 0))
    with open(path, 'wb') as f:
        f.write(b'P6\n%d %d\n255\n' % (w, h))
        f.write(bytes(raw))


def png_rounded_mask(path, w, h, r, color=(255, 255, 255)):
    """圆角 + 1px 边框的 RGBA PNG，用 zlib 手写，避免依赖 PIL。"""
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            dx = min(x, w - 1 - x)
            dy = min(y, h - 1 - y)
            if dx < r and dy < r:
                ddx, ddy = r - dx, r - dy
                dz = (ddx * ddx + ddy * ddy) ** 0.5
                a = 255 if dz <= r - 1 else (0 if dz >= r + 0.6 else int(255 * (r + 0.6 - dz) / 1.6))
            else:
                a = 255
            row += bytes((color[0], color[1], color[2], a))
        rows.append(bytes(row))

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    body = b''.join(b'\x00' + r_ for r_ in rows)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', ihdr))
        f.write(chunk(b'IDAT', zlib.compress(body, 6)))
        f.write(chunk(b'IEND', b''))


# ---------------------------------------------------------------- 两种皮肤
def build_plain(root, tk):
    """极简：原生控件 + 纯色 + 1px 线，零位图，零自绘。"""
    root.configure(bg='#FFFFFF')
    head = tk.Frame(root, bg='#FFFFFF', height=44)
    head.pack(fill='x')
    tk.Label(head, text='WBOX', bg='#FFFFFF', fg='#000000',
             font=('Microsoft YaHei UI', 13, 'bold')).place(x=16, y=12)
    tk.Label(head, text='+', bg='#FFFFFF', fg='#000000',
             font=('Microsoft YaHei UI', 14)).place(relx=1.0, x=-24, y=10)
    tk.Frame(root, bg='#E5E5E5', height=1).pack(fill='x')
    body = tk.Frame(root, bg='#FFFFFF')
    body.pack(fill='both', expand=True)
    for i in range(ROWS):
        row = tk.Frame(body, bg='#FFFFFF', height=ROW_H)
        row.pack(fill='x')
        row.pack_propagate(False)
        tk.Label(row, text='●', bg='#FFFFFF', fg='#000000',
                 font=('Segoe UI', 10)).place(x=16, y=18)
        tk.Label(row, text='插件 %d' % (i + 1), bg='#FFFFFF', fg='#000000',
                 font=('Microsoft YaHei UI', 10)).place(x=44, y=16)
        # 开关用两块纯色矩形，不画圆角、不做动画
        sw = tk.Frame(row, bg='#000000', width=40, height=22)
        sw.place(relx=1.0, x=-52, y=15)
        tk.Frame(sw, bg='#FFFFFF', width=18, height=18).place(x=19, y=2)


def build_glass(root, tk, tmp):
    """液态玻璃：一张整窗磨砂位图 + 圆角遮罩 + 阴影，全部贴在 Canvas 上。"""
    import tkinter.font  # noqa: F401  (真实皮肤也会用到字体度量)
    cv = tk.Canvas(root, width=W, height=H, highlightthickness=0, bd=0, bg='#E9E9EC')
    cv.pack()

    shadow = os.path.join(tmp, 'shadow.png')
    mask = os.path.join(tmp, 'mask.png')
    back = os.path.join(tmp, 'back.ppm')
    if not os.path.exists(mask):
        png_rounded_mask(mask, W, H, 26)
    if not os.path.exists(shadow):
        png_rounded_mask(shadow, W + 40, H + 40, 30, (0, 0, 0))
    if not os.path.exists(back):
        ppm_blur_backdrop(back, W, H)

    img_back = tk.PhotoImage(file=back)
    img_mask = tk.PhotoImage(file=mask)
    img_shadow = tk.PhotoImage(file=shadow)

    cv.create_image(20, 20, image=img_shadow, anchor='nw')   # 阴影层
    cv.create_image(0, 0, image=img_back, anchor='nw')       # 磨砂层（真实实现是截屏模糊）
    cv.create_image(0, 0, image=img_mask, anchor='nw')       # 圆角遮罩
    # 六条发丝线 + 五行文字 + 五个开关 = 逐项自绘
    for i in range(ROWS):
        y = 52 + i * ROW_H
        cv.create_line(50, y, W - 12, y, fill='#E8E8E8')
        cv.create_text(44, y + 26, text='插件 %d' % (i + 1), anchor='w',
                       fill='#111111', font=('Microsoft YaHei UI', 11))
        cv.create_oval(W - 88, y + 14, W - 42, y + 42, fill='#000000', outline='')
        cv.create_oval(W - 66, y + 16, W - 44, y + 40, fill='#FFFFFF', outline='')
    cv.create_text(16, 24, text='WBOX', anchor='w', fill='#000000',
                   font=('Microsoft YaHei UI', 14, 'bold'))
    return cv


# ---------------------------------------------------------------- 单档运行
def run_one(mode):
    import tkinter as tk
    tmp = os.path.join(os.environ.get('TEMP', '.'), 'wbox-bench')
    os.makedirs(tmp, exist_ok=True)

    t0 = time.perf_counter()
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry('%dx%d+60+60' % (W, H))
    cv = None
    if mode == 'plain':
        build_plain(root, tk)
    else:
        cv = build_glass(root, tk, tmp)
    root.update_idletasks()
    root.update()
    first = (time.perf_counter() - t0) * 1000.0

    base = counters()

    # 重绘成本：改一次背景色再刷回来，重复 100 次（悬停/切页的典型动作）
    t1 = time.perf_counter()
    for i in range(100):
        if cv is not None:
            cv.itemconfigure(cv.find_all()[-1], fill='#000000' if i % 2 else '#111111')
        else:
            root.configure(bg='#FAFAFA' if i % 2 else '#FFFFFF')
        root.update_idletasks()
    repaint = (time.perf_counter() - t1) * 1000.0 / 100.0

    after = counters()
    root.update()
    root.destroy()
    return {
        'mode': mode,
        'first_frame_ms': round(first, 1),
        'repaint_ms': round(repaint, 2),
        'gdi': after['gdi'],
        'user': after['user'],
        'priv_mb': after['priv_mb'],
        'ws_mb': after['ws_mb'],
        'gdi_base': base['gdi'],
    }


def main():
    if '--mode' in sys.argv:
        mode = sys.argv[sys.argv.index('--mode') + 1]
        print(json.dumps(run_one(mode), ensure_ascii=False))
        return 0

    out = []
    for mode in ('plain', 'glass'):
        p = subprocess.run([sys.executable, os.path.abspath(__file__), '--mode', mode],
                           capture_output=True, text=True, timeout=120)
        line = (p.stdout or '').strip().splitlines()
        if not line:
            print('FAIL %s: %s' % (mode, (p.stderr or '')[-400:]))
            continue
        out.append(json.loads(line[-1]))

    print('%-7s %10s %12s %6s %6s %9s %9s' %
          ('皮肤', '首帧ms', '重绘ms/次', 'GDI', 'USER', '私有MB', '工作集MB'))
    for r in out:
        print('%-7s %10.1f %12.2f %6d %6d %9.1f %9.1f' %
              (r['mode'], r['first_frame_ms'], r['repaint_ms'],
               r['gdi'], r['user'], r['priv_mb'], r['ws_mb']))
    if len(out) == 2:
        a, b = out[0], out[1]
        print('\n玻璃 / 极简 倍数：首帧 %.2fx  重绘 %.1fx  GDI %.2fx  内存 %.2fx' % (
            b['first_frame_ms'] / max(a['first_frame_ms'], .01),
            b['repaint_ms'] / max(a['repaint_ms'], .01),
            b['gdi'] / max(a['gdi'], 1),
            b['priv_mb'] / max(a['priv_mb'], .1)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
