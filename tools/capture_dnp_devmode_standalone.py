"""
Chụp 2 blob DEVMODE cho dao cắt DNP RX1HS — bản STANDALONE (đóng gói thành exe).

Dùng trên máy booth (không cần Python): chạy capture_dnp_devmode.exe trong thư mục tools.
    - Lần 1: hộp thoại driver hiện ra -> BẬT "2inch cut" -> OK  (lưu devmode_cut.bin)
    - Lần 2: hộp thoại driver hiện ra -> TẮT "2inch cut" -> OK  (lưu devmode_nocut.bin)

Hai file lưu vào <thư mục gốc app>\printer_profiles\ (cùng nơi backend đọc khi in).
File này TỰ CHỨA (không import services) để đóng gói exe gọn, độc lập với backend.
"""

import ctypes
import os
import sys
from ctypes import wintypes


# ----- DEVMODE qua Win32 (ctypes) -----------------------------------------
DM_OUT_BUFFER = 2
DM_IN_PROMPT = 4
DM_IN_BUFFER = 8
_DMSIZE_OFFSET = 68
_DMDRIVEREXTRA_OFFSET = 70
_IDOK = 1

# Cờ enum máy in
PRINTER_ENUM_LOCAL = 0x00000002
PRINTER_ENUM_CONNECTIONS = 0x00000004

PREFERRED_KEYWORDS = ("RX1HS", "DS-RX1", "RX1", "DNP")

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
    wintypes.HWND, wintypes.HANDLE, wintypes.LPCWSTR,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
]
_DocumentPropertiesW.restype = wintypes.LONG

_EnumPrintersW = _winspool.EnumPrintersW
_EnumPrintersW.argtypes = [
    wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
]
_EnumPrintersW.restype = wintypes.BOOL


class PRINTER_INFO_4W(ctypes.Structure):
    _fields_ = [
        ("pPrinterName", wintypes.LPWSTR),
        ("pServerName", wintypes.LPWSTR),
        ("Attributes", wintypes.DWORD),
    ]


def list_printers():
    flags = PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    needed = wintypes.DWORD(0)
    returned = wintypes.DWORD(0)
    _EnumPrintersW(flags, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(returned))
    if needed.value == 0:
        return []
    buf = ctypes.create_string_buffer(needed.value)
    if not _EnumPrintersW(flags, None, 4, buf, needed.value,
                          ctypes.byref(needed), ctypes.byref(returned)):
        return []
    count = returned.value
    arr = ctypes.cast(buf, ctypes.POINTER(PRINTER_INFO_4W))
    names = []
    for i in range(count):
        if arr[i].pPrinterName:
            names.append(arr[i].pPrinterName)
    return names


def resolve_printer(configured=None):
    printers = list_printers()
    if configured:
        for name in printers:
            if name.lower() == configured.lower():
                return name, printers
        for name in printers:
            if configured.lower() in name.lower():
                return name, printers
    for kw in PREFERRED_KEYWORDS:
        for name in printers:
            if kw.lower() in name.lower():
                return name, printers
    return None, printers


def _devmode_total_size(raw):
    dm_size = int.from_bytes(raw[_DMSIZE_OFFSET:_DMSIZE_OFFSET + 2], "little")
    dm_extra = int.from_bytes(raw[_DMDRIVEREXTRA_OFFSET:_DMDRIVEREXTRA_OFFSET + 2], "little")
    return dm_size + dm_extra


def prompt_devmode(printer_name, hwnd=0):
    handle = wintypes.HANDLE()
    if not _OpenPrinterW(printer_name, ctypes.byref(handle), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = _DocumentPropertiesW(None, handle, printer_name, None, None, 0)
        if needed <= 0:
            raise ctypes.WinError(ctypes.get_last_error())
        out_buf = ctypes.create_string_buffer(needed)
        in_buf = ctypes.create_string_buffer(needed)
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


# ----- Lưu blob đúng thư mục backend đọc -----------------------------------
def profile_dir():
    # Ưu tiên thư mục chỉ định qua env (vd CAPTURE_CUT.bat trỏ thẳng vào release\Tomato\printer_profiles).
    env_dir = os.environ.get("TOMATO_PRINTER_PROFILES")
    if env_dir:
        return env_dir
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    # exe nằm trong tools\ -> blob lưu ở thư mục cha (gốc app, nơi backend lấy cwd).
    if os.path.basename(base).lower() == "tools":
        base = os.path.dirname(base)
    return os.path.join(base, "printer_profiles")


def _diff_count(a, b):
    if len(a) != len(b):
        return None
    return sum(1 for x, y in zip(a, b) if x != y)


def _capture(printer_name, label, instruction):
    print()
    print("=" * 70)
    print(f"  {label}")
    print(f"  >>> {instruction}")
    print("  (Hộp thoại Printing Preferences của driver sắp hiện ra. Bấm OK để lưu,")
    print("   Cancel để bỏ qua.)")
    print("=" * 70)
    input("  Nhấn ENTER để mở hộp thoại...")
    data = prompt_devmode(printer_name)
    if data is None:
        print("  -> Đã bấm Cancel, KHÔNG lưu.")
    else:
        print(f"  -> Đã nhận DEVMODE ({len(data)} bytes).")
    return data


def main():
    if os.name != "nt":
        print("Chỉ chạy được trên Windows.")
        return 1

    configured = sys.argv[1] if len(sys.argv) > 1 else None
    printer_name, printers = resolve_printer(configured)
    if not printer_name:
        print("Không tìm thấy máy in RX1HS/DNP. Danh sách máy in hiện có:")
        for name in printers:
            print(f"  - {name}")
        print('\nDùng: capture_dnp_devmode.exe "<ten may in>"')
        input("Nhấn ENTER để thoát...")
        return 1

    print(f"Máy in: {printer_name}")
    out_dir = profile_dir()
    os.makedirs(out_dir, exist_ok=True)
    print(f"Sẽ lưu vào: {out_dir}")

    cut = _capture(printer_name, "BƯỚC 1/2 — BẬT dao cắt",
                   "Tìm mục '2inch cut' (hoặc Cut = 2inch) và đặt BẬT, rồi bấm OK.")
    nocut = _capture(printer_name, "BƯỚC 2/2 — TẮT dao cắt",
                     "Đặt '2inch cut' = TẮT (Off), rồi bấm OK.")

    saved = 0
    if cut is not None:
        with open(os.path.join(out_dir, "devmode_cut.bin"), "wb") as fh:
            fh.write(cut)
        saved += 1
        print("Đã lưu: devmode_cut.bin")
    if nocut is not None:
        with open(os.path.join(out_dir, "devmode_nocut.bin"), "wb") as fh:
            fh.write(nocut)
        saved += 1
        print("Đã lưu: devmode_nocut.bin")

    if cut is not None and nocut is not None:
        diff = _diff_count(cut, nocut)
        if diff is None:
            print("\nCẢNH BÁO: 2 blob khác độ dài — driver/khổ giấy có thể không nhất quán.")
        elif diff == 0:
            print("\nCẢNH BÁO: 2 blob GIỐNG HỆT — có thể bạn chưa đổi mục cắt giữa 2 lần.")
        else:
            print(f"\nOK: 2 blob khác nhau {diff} byte (kỳ vọng chỉ khác ở thiết lập cắt).")

    if saved < 2:
        print("\nChưa đủ 2 file. Hãy chạy lại và bấm OK ở cả 2 bước.")
        input("Nhấn ENTER để thoát...")
        return 1

    print("\nHoàn tất. Backend sẽ tự dùng các blob này cho từng lệnh in.")
    input("Nhấn ENTER để thoát...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
