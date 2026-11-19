"""Minimal Windows HID access for the LEOBOG AMG65, standard library only (ctypes).

Only the two vendor interfaces are opened:
  - command channel: interface 2, usage page 0xFF68, 64-byte reports (+1 report-id byte)
  - bulk channel:    interface 3, usage page 0xFF67, 4096-byte reports (+1)
See docs/PROTOCOL.md.
"""
import ctypes as C
import ctypes.wintypes as W

VID, PID = 0x0C45, 0x800A
CMD_PAGE, BULK_PAGE = 0xFF68, 0xFF67

_hid = C.WinDLL('hid')
_setup = C.WinDLL('setupapi')
_k32 = C.WinDLL('kernel32', use_last_error=True)

_k32.CreateFileW.restype = W.HANDLE
_k32.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
_k32.CreateEventW.restype = W.HANDLE
_k32.WriteFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
_k32.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
_k32.GetOverlappedResult.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(W.DWORD), W.BOOL]
_k32.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
_k32.CancelIo.argtypes = [W.HANDLE]
_k32.CloseHandle.argtypes = [W.HANDLE]
_setup.SetupDiGetClassDevsW.restype = C.c_void_p

GENERIC_RW = 0xC0000000
SHARE_RW = 3
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
INVALID = C.c_void_p(-1).value


class _GUID(C.Structure):
    _fields_ = [('a', C.c_ulong), ('b', C.c_ushort), ('c', C.c_ushort), ('d', C.c_ubyte * 8)]


class _IFD(C.Structure):
    _fields_ = [('cb', W.DWORD), ('g', _GUID), ('f', W.DWORD), ('r', C.c_void_p)]


class _CAPS(C.Structure):
    _fields_ = [('Usage', C.c_ushort), ('UsagePage', C.c_ushort), ('In', C.c_ushort),
                ('Out', C.c_ushort), ('Feat', C.c_ushort), ('res', C.c_ushort * 17),
                ('x', C.c_ushort * 10)]


class _OVERLAPPED(C.Structure):
    _fields_ = [('Internal', C.c_void_p), ('InternalHigh', C.c_void_p),
                ('Offset', W.DWORD), ('OffsetHigh', W.DWORD), ('hEvent', W.HANDLE)]


class HidError(Exception):
    pass


def _paths():
    g = _GUID()
    _hid.HidD_GetHidGuid(C.byref(g))
    h = _setup.SetupDiGetClassDevsW(C.byref(g), None, None, 0x12)
    i = 0
    try:
        while True:
            d = _IFD(); d.cb = C.sizeof(_IFD)
            if not _setup.SetupDiEnumDeviceInterfaces(C.c_void_p(h), None, C.byref(g), i, C.byref(d)):
                return
            i += 1
            need = W.DWORD()
            _setup.SetupDiGetDeviceInterfaceDetailW(C.c_void_p(h), C.byref(d), None, 0, C.byref(need), None)
            buf = C.create_string_buffer(need.value)
            C.cast(buf, C.POINTER(W.DWORD))[0] = 8 if C.sizeof(C.c_void_p) == 8 else 6
            _setup.SetupDiGetDeviceInterfaceDetailW(C.c_void_p(h), C.byref(d), buf, need, None, None)
            yield C.wstring_at(C.addressof(buf) + 4)
    finally:
        _setup.SetupDiDestroyDeviceInfoList(C.c_void_p(h))


def _caps(handle):
    pp = C.c_void_p()
    if not _hid.HidD_GetPreparsedData(W.HANDLE(handle), C.byref(pp)):
        return None
    try:
        c = _CAPS(); _hid.HidP_GetCaps(pp, C.byref(c)); return c
    finally:
        _hid.HidD_FreePreparsedData(pp)


class Channel:
    """One opened HID interface: write fixed-size reports, read replies with a timeout."""

    def __init__(self, path, out_len, in_len):
        self.path, self.out_len, self.in_len = path, out_len, in_len
        self.h = _k32.CreateFileW(path, GENERIC_RW, SHARE_RW, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if self.h in (None, INVALID):
            raise HidError(f'cannot open {path} (error {C.get_last_error()})')
        self.ev = _k32.CreateEventW(None, True, False, None)

    def _io(self, fn, buf, n, timeout_ms):
        ov = _OVERLAPPED(); ov.hEvent = self.ev
        _k32.ResetEvent(W.HANDLE(self.ev))
        done = W.DWORD()
        if not fn(self.h, buf, n, C.byref(done), C.byref(ov)):
            if C.get_last_error() != ERROR_IO_PENDING:
                raise HidError(f'I/O failed (error {C.get_last_error()})')
            if _k32.WaitForSingleObject(self.ev, timeout_ms) != 0:
                _k32.CancelIo(self.h)
                _k32.GetOverlappedResult(self.h, C.byref(ov), C.byref(done), True)
                return None
        if not _k32.GetOverlappedResult(self.h, C.byref(ov), C.byref(done), True):
            raise HidError(f'I/O failed (error {C.get_last_error()})')
        return done.value

    def write(self, payload, timeout_ms=1000):
        if len(payload) > self.out_len - 1:
            raise ValueError('payload too long')
        buf = C.create_string_buffer(b'\x00' + bytes(payload).ljust(self.out_len - 1, b'\x00'), self.out_len)
        if self._io(_k32.WriteFile, buf, self.out_len, timeout_ms) is None:
            raise HidError('write timed out')

    def read(self, timeout_ms=500):
        if not self.in_len:
            return None
        buf = C.create_string_buffer(self.in_len)
        n = self._io(_k32.ReadFile, buf, self.in_len, timeout_ms)
        return None if n is None else buf.raw[1:n]

    def flush(self):
        _hid.HidD_FlushQueue(W.HANDLE(self.h))

    def close(self):
        if self.h not in (None, INVALID):
            _k32.CloseHandle(self.h); _k32.CloseHandle(self.ev); self.h = None


def open_channels():
    """Return (command, None); raise HidError if the keyboard isn't plugged in by cable.

    Only the command channel is opened. The second channel (the 4096-byte bulk one) exists only to upload pictures to
    the LCD, which leo-sys never does, so it is deliberately never opened."""
    found = {}
    for p in _paths():
        lp = p.lower()
        if f'vid_{VID:04x}&pid_{PID:04x}' not in lp:
            continue
        h = _k32.CreateFileW(p, 0, SHARE_RW, None, OPEN_EXISTING, 0, None)
        if h in (None, INVALID):
            continue
        try:
            c = _caps(h)
        finally:
            _k32.CloseHandle(h)
        if c and c.UsagePage == CMD_PAGE:
            found[c.UsagePage] = (p, c.Out, c.In)
    if CMD_PAGE not in found:
        raise HidError('AMG65 command channel not found - is the keyboard plugged in by cable?')
    cmd = Channel(*found[CMD_PAGE])
    return cmd, None
