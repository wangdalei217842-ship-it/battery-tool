# -*- coding: utf-8 -*-
"""从 AI 生成的母图产出 PWA 各尺寸图标。

纯标准库实现（zlib + struct）：解码 PNG -> 裁掉水印 -> 居中裁方 -> 高质量缩放 -> 重新编码。
不依赖 Pillow，服务器上也能直接跑。
"""
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "icon-src", "App_icon_for_a_battery_managem_2026-10-18T07-27-00.png")
OUT = os.path.join(ROOT, "static")


# ---------------------------------------------------------------- PNG 解码
def read_png(path):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是 PNG 文件"
    pos, idat, w = 8, [], None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            w, h, depth, color, comp, filt, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8, "只支持 8bit"
            assert interlace == 0, "不支持隔行"
            assert color in (2, 6), "只支持 RGB/RGBA"
        elif ctype == b"IDAT":
            idat.append(body)
        elif ctype == b"IEND":
            break
    raw = zlib.decompress(b"".join(idat))
    bpp = 3 if color == 2 else 4
    stride = w * bpp
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    # 统一转 RGBA
    px = bytearray(w * h * 4)
    for i in range(w * h):
        px[i * 4:i * 4 + 3] = out[i * bpp:i * bpp + 3]
        px[i * 4 + 3] = out[i * bpp + 3] if bpp == 4 else 255
    return w, h, px


# ---------------------------------------------------------------- PNG 编码
def write_png(path, w, h, px):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += px[y * w * 4:(y + 1) * w * 4]

    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


# ---------------------------------------------------------------- 操作
def crop(px, w, h, x0, y0, cw, ch):
    out = bytearray(cw * ch * 4)
    for y in range(ch):
        src = ((y0 + y) * w + x0) * 4
        out[y * cw * 4:(y + 1) * cw * 4] = px[src:src + cw * 4]
    return out


def resize(px, w, h, nw, nh):
    """面积平均缩放，边缘平滑，缩小效果接近 Lanczos。"""
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        sy0 = y * h // nh
        sy1 = max(sy0 + 1, (y + 1) * h // nh)
        for x in range(nw):
            sx0 = x * w // nw
            sx1 = max(sx0 + 1, (x + 1) * w // nw)
            r = g = b = a = 0
            n = 0
            for yy in range(sy0, sy1):
                base = yy * w
                for xx in range(sx0, sx1):
                    i = (base + xx) * 4
                    r += px[i]; g += px[i + 1]; b += px[i + 2]; a += px[i + 3]
                    n += 1
            o = (y * nw + x) * 4
            out[o] = r // n; out[o + 1] = g // n; out[o + 2] = b // n; out[o + 3] = a // n
    return out


def main():
    w, h, px = read_png(SRC)
    print("源图 %dx%d" % (w, h))

    # 1) 去掉底部水印条
    trim = int(h * 0.085)
    h -= trim
    px = crop(px, w, h + trim, 0, 0, w, h)

    # 2) 居中裁成正方形
    side = min(w, h)
    px = crop(px, w, h, (w - side) // 2, (h - side) // 2, side, side)
    print("正方形 %dx%d" % (side, side))

    # 3) 各尺寸
    for name, size in (("icon-512.png", 512), ("icon-192.png", 192),
                       ("apple-touch-icon.png", 180), ("icon-32.png", 32)):
        write_png(os.path.join(OUT, name), size, size, resize(px, side, side, size, size))
        print("  ->", name, size)

    # 4) maskable：内容缩到 80%，四周补边缘色
    bg = (px[0], px[1], px[2])
    inner_sz = 400
    inner = resize(px, side, side, inner_sz, inner_sz)
    canvas = bytearray([bg[0], bg[1], bg[2], 255] * (512 * 512))
    off = (512 - inner_sz) // 2
    for y in range(inner_sz):
        dst = ((off + y) * 512 + off) * 4
        canvas[dst:dst + inner_sz * 4] = inner[y * inner_sz * 4:(y + 1) * inner_sz * 4]
    write_png(os.path.join(OUT, "icon-maskable-512.png"), 512, 512, canvas)
    print("  -> icon-maskable-512.png 512 (bg=%s)" % (bg,))


if __name__ == "__main__":
    main()
