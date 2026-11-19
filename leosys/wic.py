"""Read GIF, PNG, JPEG and BMP files with Windows' own Windows Imaging Component (ctypes, no extra packages).

decode() returns every frame already scaled and fitted to the size you ask for (the LCD's 240 x 135 or the matrix's
63 x 5), as raw 32-bit BGRA bytes, with the frame delays of a GIF. Interface IDs and method order are from
wincodec.h. Colours are premultiplied against black, so a transparent pixel is black.

COM belongs to the thread that made it: call decode() from one thread at a time per thread (each thread sets COM up).
"""
import ctypes
import struct
from ctypes import POINTER, byref, c_int, c_ubyte, c_uint, c_void_p, wintypes

from .media import GUID, _call, _guid, _out, _release

CLSID_FACTORY = "cacaf262-9370-4615-a13b-9f5539da4c0a"
IID_FACTORY = "ec5ec8a9-c395-4314-9c77-54d7a935ff70"
PBGRA = _guid("6fddc324-4e03-4bfe-b185-3d77768dc910")     # GUID_WICPixelFormat32bppPBGRA
GENERIC_READ = 0x80000000
NEAREST, FANT = 0, 3
MAX_SIDE = 2048                     # a bigger GIF canvas would use too much memory to composite
MAX_GIF_FRAMES = 400
MAX_FILE_BYTES = 50_000_000
EXTENSIONS = (".gif", ".png", ".jpg", ".jpeg", ".bmp")
FITS = ("contain", "cover", "stretch")


class ImageError(Exception):
    pass


def _com():
    try:
        ctypes.OleDLL("ole32").CoInitializeEx(None, 0)
    except OSError:
        pass                        # this thread already set COM up another way: fine


def _factory():
    f = c_void_p()
    ctypes.OleDLL("ole32").CoCreateInstance(byref(_guid(CLSID_FACTORY)), None, 1, byref(_guid(IID_FACTORY)), byref(f))
    return f


def _size(src):
    w, h = c_uint(), c_uint()
    _call(src, 3, [POINTER(c_uint), POINTER(c_uint)], byref(w), byref(h))
    return w.value, h.value


def _copy(src, w, h):
    stride = w * 4
    buf = (c_ubyte * (stride * h))()
    _call(src, 7, [c_void_p, c_uint, c_uint, c_void_p], None, stride, stride * h, buf)
    return bytes(buf)


def _pbgra(factory, source):
    conv = _out(factory, 10)
    try:
        _call(conv, 8, [c_void_p, POINTER(GUID), c_int, c_void_p, ctypes.c_double, c_int],
              source, byref(PBGRA), 0, None, 0.0, 0)
    except OSError:
        _release(conv)
        raise
    return conv


def _scale(factory, source, w, h, mode):
    sc = _out(factory, 11)
    try:
        _call(sc, 8, [c_void_p, c_uint, c_uint, c_int], source, w, h, mode)
    except OSError:
        _release(sc)
        raise
    return sc


def _meta(reader, name):
    """One metadata number by its query name, or None."""
    if not reader or not reader.value:
        return None
    pv = ctypes.create_string_buffer(24)
    try:
        _call(reader, 5, [ctypes.c_wchar_p, c_void_p], name, pv)
    except OSError:
        return None
    vt = struct.unpack_from("<H", pv, 0)[0]
    fmt = {17: "<B", 16: "<b", 18: "<H", 2: "<h", 19: "<I", 3: "<i", 11: "<h", 20: "<q", 21: "<Q"}.get(vt)
    return struct.unpack_from(fmt, pv, 8)[0] if fmt else None


def _reader(obj, slot):
    p = c_void_p()
    try:
        _call(obj, slot, [POINTER(c_void_p)], byref(p))
    except OSError:
        return None
    return p


def _target_size(sw, sh, tw, th, fit):
    if fit == "stretch":
        return tw, th
    if fit == "cover":
        k = max(tw / sw, th / sh)
        return max(tw, round(sw * k)), max(th, round(sh * k))
    k = min(tw / sw, th / sh)
    return max(1, min(tw, round(sw * k))), max(1, min(th, round(sh * k)))


def _place(pixels, nw, nh, tw, th):
    """Centre an nw x nh picture on a black tw x th canvas, cropping what falls outside."""
    if (nw, nh) == (tw, th):
        return pixels
    out = bytearray(tw * th * 4)
    ox, oy = (tw - nw) // 2, (th - nh) // 2
    x0, x1 = max(0, ox), min(tw, ox + nw)
    if x1 <= x0:
        return bytes(out)
    for y in range(th):
        sy = y - oy
        if 0 <= sy < nh:
            s = (sy * nw + (x0 - ox)) * 4
            out[(y * tw + x0) * 4:(y * tw + x1) * 4] = pixels[s:s + (x1 - x0) * 4]
    return bytes(out)


def _render(factory, source, tw, th, fit):
    """A WIC bitmap source -> tw x th BGRA bytes, fitted. Upscaling keeps hard pixels (pixel art stays sharp)."""
    sw, sh = _size(source)
    if sw < 1 or sh < 1:
        raise ImageError("the picture is empty")
    nw, nh = _target_size(sw, sh, tw, th, fit)
    if (nw, nh) == (sw, sh):
        return _place(_copy(source, sw, sh), nw, nh, tw, th)
    scaler = _scale(factory, source, nw, nh, NEAREST if nw >= sw and nh >= sh else FANT)
    try:
        return _place(_copy(scaler, nw, nh), nw, nh, tw, th)
    finally:
        _release(scaler)


def _from_memory(factory, canvas, w, h):
    bmp = c_void_p()
    _call(factory, 20, [c_uint, c_uint, POINTER(GUID), c_uint, c_uint, c_void_p, POINTER(c_void_p)],
          w, h, byref(PBGRA), w * 4, w * h * 4, (c_ubyte * len(canvas)).from_buffer(canvas), byref(bmp))
    return bmp


def _draw(canvas, cw, ch, pixels, left, top, w, h):
    """Paint a GIF frame onto the canvas; pixels with no alpha leave the canvas as it was."""
    opaque = pixels[3::4].count(255) == w * h
    for y in range(h):
        cy = top + y
        if not 0 <= cy < ch:
            continue
        x0, x1 = max(0, left), min(cw, left + w)
        if x1 <= x0:
            continue
        s = (y * w + (x0 - left)) * 4
        d = (cy * cw + x0) * 4
        n = (x1 - x0) * 4
        if opaque:
            canvas[d:d + n] = pixels[s:s + n]
            continue
        row = pixels[s:s + n]
        for i in range(0, n, 4):
            if row[i + 3]:
                canvas[d + i:d + i + 4] = row[i:i + 4]


def _gif_frames(factory, decoder, count, tw, th, fit):
    first = c_void_p()
    _call(decoder, 13, [c_uint, POINTER(c_void_p)], 0, byref(first))
    try:
        fw, fh = _size(first)
    finally:
        _release(first)
    rd = _reader(decoder, 8)
    try:
        cw = _meta(rd, "/logscrdesc/Width") or fw
        ch = _meta(rd, "/logscrdesc/Height") or fh
    finally:
        if rd:
            _release(rd)
    if cw > MAX_SIDE or ch > MAX_SIDE:
        raise ImageError(f"the GIF is {cw} x {ch}; the limit is {MAX_SIDE}")
    canvas = bytearray(cw * ch * 4)
    out, saved, prev = [], None, None
    for i in range(min(count, MAX_GIF_FRAMES)):
        frame = c_void_p()
        _call(decoder, 13, [c_uint, POINTER(c_void_p)], i, byref(frame))
        conv = None
        try:
            r = _reader(frame, 8)
            try:
                left, top = _meta(r, "/imgdesc/Left") or 0, _meta(r, "/imgdesc/Top") or 0
                delay = _meta(r, "/grctlext/Delay")
                disposal = _meta(r, "/grctlext/Disposal") or 0
            finally:
                if r:
                    _release(r)
            w, h = _size(frame)
            conv = _pbgra(factory, frame)
            pixels = _copy(conv, w, h)
        finally:
            if conv:
                _release(conv)
            _release(frame)
        if prev:                                             # undo what the previous frame asked to be undone
            pl, pt, pw, ph, pd = prev
            if pd == 2:
                for y in range(max(0, pt), min(ch, pt + ph)):
                    x0, x1 = max(0, pl), min(cw, pl + pw)
                    canvas[(y * cw + x0) * 4:(y * cw + x1) * 4] = bytes((x1 - x0) * 4)
            elif pd == 3 and saved is not None:
                canvas[:] = saved
        if disposal == 3:
            saved = bytes(canvas)
        _draw(canvas, cw, ch, pixels, left, top, w, h)
        prev = (left, top, w, h, disposal)
        ms = (delay if delay and delay > 1 else 10) * 10   # a delay of 0 or 1 is read as 100 ms, as browsers do
        mem = _from_memory(factory, canvas, cw, ch)
        try:
            out.append((_render(factory, mem, tw, th, fit), ms))
        finally:
            _release(mem)
    return out


def check(path):
    if not str(path).lower().endswith(EXTENSIONS):
        raise ImageError("not a GIF, PNG, JPEG or BMP file")
    try:
        import os
        if os.path.getsize(path) > MAX_FILE_BYTES:
            raise ImageError("the file is larger than 50 MB")
    except OSError as e:
        raise ImageError(str(e))


def decode(path, tw, th, fit="contain"):
    """[(BGRA bytes of tw x th, delay in ms)] for a GIF; one (bytes, None) for a still. Raises ImageError."""
    check(path)
    if fit not in FITS:
        fit = "contain"
    _com()
    factory = _factory()
    decoder = c_void_p()
    try:
        try:
            _call(factory, 3, [ctypes.c_wchar_p, c_void_p, wintypes.DWORD, c_int, POINTER(c_void_p)],
                  str(path), None, GENERIC_READ, 0, byref(decoder))
        except OSError as e:
            raise ImageError(f"Windows cannot read this picture ({e})")
        try:
            count = c_uint()
            _call(decoder, 12, [POINTER(c_uint)], byref(count))
            if count.value > 1:
                try:
                    return _gif_frames(factory, decoder, count.value, tw, th, fit)
                except OSError as e:
                    raise ImageError(f"the animation could not be read ({e})")
            frame = c_void_p()
            try:
                _call(decoder, 13, [c_uint, POINTER(c_void_p)], 0, byref(frame))
                conv = _pbgra(factory, frame)
                try:
                    return [(_render(factory, conv, tw, th, fit), None)]
                finally:
                    _release(conv)
            except OSError as e:
                raise ImageError(f"the picture could not be read ({e})")
            finally:
                if frame.value:
                    _release(frame)
        finally:
            _release(decoder)
    finally:
        _release(factory)
