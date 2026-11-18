# -*- coding: utf-8 -*-
"""裁剪 32bpp BMP：crop.py in.bmp out.bmp x y w h"""
import struct
import sys


def load(path):
    with open(path, 'rb') as f:
        data = f.read()
    off = struct.unpack_from('<I', data, 10)[0]
    hdr = struct.unpack_from('<IiiHHIIiiII', data, 14)
    w, h, bpp = hdr[1], hdr[2], hdr[4]
    topdown = h < 0
    h = abs(h)
    return w, h, bpp, topdown, off, data


def crop(src, dst, x, y, cw, ch):
    w, h, bpp, topdown, off, data = load(src)
    if bpp not in (24, 32):
        raise SystemExit('只支持 24/32bpp')
    stride = w * bpp // 8
    out_stride = cw * bpp // 8
    out = bytearray()
    for row in range(ch):
        sy = y + row if topdown else h - 1 - (y + row)
        start = off + sy * stride + x * (bpp // 8)
        out += data[start:start + out_stride]
    hdr = struct.pack('<IiiHHIIiiII', 40, cw, -ch, 1, bpp, 0, len(out),
                      2835, 2835, 0, 0)
    with open(dst, 'wb') as f:
        f.write(b'BM')
        f.write(struct.pack('<IHHI', 14 + 40 + len(out), 0, 0, 54))
        f.write(hdr)
        f.write(out)
    print('cropped', dst, cw, ch)


if __name__ == '__main__':
    a = sys.argv[1:]
    crop(a[0], a[1], int(a[2]), int(a[3]), int(a[4]), int(a[5]))
