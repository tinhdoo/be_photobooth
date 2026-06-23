"""
Điều khiển dao cắt DNP RX1HS theo từng lệnh in (per-job) qua DEVMODE.

Bối cảnh:
    Thiết lập "2inch cut" của driver DNP nằm trong vùng dmDriverExtra (private của
    hãng), KHÔNG truy cập được qua các trường DEVMODE công khai. Vì vậy ta "chụp"
    sẵn 2 blob DEVMODE thô (1 lần, trên máy booth):
        - devmode_cut.bin   : driver đặt 2inch cut = ON  (cắt đôi 4x6 -> 2 strip 2x6)
        - devmode_nocut.bin : driver đặt 2inch cut = OFF (giữ nguyên 1 tờ 4x6)
    Khi in, nạp đúng blob theo cut_mode rồi tạo HDC từ blob đó. Vì là byte thô nên
    giữ nguyên cả vùng dmDriverExtra -> điều khiển được dao cắt mà không cần biết
    offset riêng của DNP.

Chỉ hoạt động trên Windows (ctypes + win32 spooler).
"""

import ctypes
from ctypes import wintypes


# fMode flags cho DocumentPropertiesW
DM_OUT_BUFFER = 2
DM_IN_PROMPT = 4
DM_IN_BUFFER = 8

# Offset (byte) trong DEVMODEW:
#   dmDeviceName[CCHDEVICENAME=32 WCHAR] = 64B
#   dmSpecVersion (WORD) @64, dmDriverVersion (WORD) @66,
#   dmSize (WORD) @68, dmDriverExtra (WORD) @70
_DMSIZE_OFFSET = 68
_DMDRIVEREXTRA_OFFSET = 70

# IDOK trả về từ hộp thoại khi dùng DM_IN_PROMPT
_IDOK = 1


_winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

_OpenPrinterW = _winspool.OpenPrinterW
_OpenPrinterW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
_OpenPrinterW.restype = wintypes.BOOL

_ClosePrinter = _winspool.ClosePrinter
_ClosePrinter.argtypes = [wintypes.HANDLE]
_ClosePrinter.restype = wintypes.BOOL

_DocumentPropertiesW = _winspool.DocumentPropertiesW
_DocumentPropertiesW.argtypes = [
    wintypes.HWND,        # hWnd
    wintypes.HANDLE,      # hPrinter
    wintypes.LPCWSTR,     # pDeviceName
    ctypes.c_void_p,      # pDevModeOutput
    ctypes.c_void_p,      # pDevModeInput
    wintypes.DWORD,       # fMode
]
_DocumentPropertiesW.restype = wintypes.LONG

_CreateDCW = _gdi32.CreateDCW
_CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
_CreateDCW.restype = wintypes.HDC


def _open_printer(printer_name):
    handle = wintypes.HANDLE()
    if not _OpenPrinterW(printer_name, ctypes.byref(handle), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _devmode_total_size(raw):
    """Tổng kích thước DEVMODE = dmSize + dmDriverExtra (đọc từ chính buffer)."""
    dm_size = int.from_bytes(raw[_DMSIZE_OFFSET:_DMSIZE_OFFSET + 2], "little")
    dm_extra = int.from_bytes(raw[_DMDRIVEREXTRA_OFFSET:_DMDRIVEREXTRA_OFFSET + 2], "little")
    return dm_size + dm_extra


def get_default_devmode(printer_name):
    """Lấy DEVMODE mặc định hiện tại của máy in (bytes), gồm cả dmDriverExtra."""
    handle = _open_printer(printer_name)
    try:
        needed = _DocumentPropertiesW(None, handle, printer_name, None, None, 0)
        if needed <= 0:
            raise ctypes.WinError(ctypes.get_last_error())
        buf = ctypes.create_string_buffer(needed)
        rc = _DocumentPropertiesW(None, handle, printer_name, buf, None, DM_OUT_BUFFER)
        if rc < 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return bytes(buf.raw[:_devmode_total_size(buf.raw)])
    finally:
        _ClosePrinter(handle)


def prompt_devmode(printer_name, hwnd=0):
    """
    Mở hộp thoại Printing Preferences của driver cho người dùng chỉnh tay
    (ví dụ bật/tắt 2inch cut). Trả về bytes DEVMODE nếu bấm OK, None nếu Cancel.
    """
    handle = _open_printer(printer_name)
    try:
        needed = _DocumentPropertiesW(None, handle, printer_name, None, None, 0)
        if needed <= 0:
            raise ctypes.WinError(ctypes.get_last_error())
        out_buf = ctypes.create_string_buffer(needed)
        in_buf = ctypes.create_string_buffer(needed)
        # Nạp DEVMODE hiện tại vào input để hộp thoại hiển thị đúng trạng thái đang có.
        _DocumentPropertiesW(None, handle, printer_name, in_buf, None, DM_OUT_BUFFER)
        rc = _DocumentPropertiesW(
            hwnd, handle, printer_name, out_buf, in_buf,
            DM_IN_PROMPT | DM_IN_BUFFER | DM_OUT_BUFFER,
        )
        if rc != _IDOK:
            return None
        return bytes(out_buf.raw[:_devmode_total_size(out_buf.raw)])
    finally:
        _ClosePrinter(handle)


def create_dc_handle(printer_name, devmode_bytes):
    """
    Tạo HDC máy in với DEVMODE chỉ định (byte thô, gồm dmDriverExtra).
    Trả về handle (int). Người gọi chịu trách nhiệm DeleteDC.
    """
    if not devmode_bytes:
        raise ValueError("devmode_bytes rỗng")
    buf = ctypes.create_string_buffer(devmode_bytes, len(devmode_bytes))
    hdc = _CreateDCW("WINSPOOL", printer_name, None, buf)
    if not hdc:
        raise ctypes.WinError(ctypes.get_last_error())
    return hdc
