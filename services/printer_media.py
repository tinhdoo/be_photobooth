"""Đọc số giấy/lượt in còn lại của máy in Citizen/DNP (RX1HS, DS-RX1, QW410...) qua cspstat SDK.

cspstat64.dll là thư viện status của Citizen (DNP RX1HS thực chất là máy Citizen đổi tên).
Hàm GetMediaCounter(port) trả về số lượt in còn lại của cuộn media hiện tại.

Module thiết kế "fail-safe": nếu thiếu DLL / không có máy in / lỗi gì đó -> trả None,
KHÔNG bao giờ làm crash app. Kết quả được cache vài giây để tránh hỏi máy in liên tục.
"""
import ctypes
import os
import sys
import time

_dll = None
_dll_tried = False
_cache = {"ts": 0.0, "value": None}
_CACHE_TTL = 8.0  # giây

# TẠM TẮT GetMediaCounter: trên máy DNP RX1 (DS-RX1-Cut), hàm này gây ACCESS VIOLATION
# (chữ ký SDK chưa đúng) -> làm bất ổn tiến trình, hỏng luôn luồng in. Để False cho an
# toàn (số giấy còn lại trả None) tới khi có tài liệu/SDK đúng của cspstat/DNP.
_MEDIA_COUNTER_ENABLED = False
_disabled_warned = False


def _find_dll_path():
    name = "cspstat64.dll"
    candidates = []
    # 1) Khi đóng gói bằng PyInstaller: nằm trong thư mục giải nén / cạnh exe
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, name))
        candidates.append(os.path.join(os.path.dirname(sys.executable), name))
    # 2) Chạy từ source: dev_be/lib/cspstat64.dll
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "..", "lib", name))
    candidates.append(os.path.join(os.getcwd(), "lib", name))
    candidates.append(os.path.join(os.getcwd(), name))
    for path in candidates:
        if path and os.path.exists(path):
            return os.path.abspath(path)
    return None


def _load():
    global _dll, _dll_tried
    if _dll is not None or _dll_tried:
        return _dll
    _dll_tried = True
    if os.name != "nt":
        return None
    try:
        path = _find_dll_path()
        if not path:
            print("[PrinterMedia] Khong tim thay cspstat64.dll", flush=True)
            return None
        dll = ctypes.WinDLL(path)
        dll.GetPrinterPortNum.restype = ctypes.c_longlong
        dll.GetPrinterPortNum.argtypes = [ctypes.c_char_p, ctypes.c_int]
        dll.GetMediaCounter.restype = ctypes.c_longlong
        dll.GetMediaCounter.argtypes = [ctypes.c_longlong]
        _dll = dll
        print(f"[PrinterMedia] Da load cspstat64.dll: {path}", flush=True)
    except Exception as e:
        print(f"[PrinterMedia] Khong load duoc cspstat64.dll: {e}", flush=True)
        _dll = None
    return _dll


def _is_valid(value):
    # Mã lỗi của SDK thường là -1 / 0xFFFFFFFF; số giấy thực tế nằm trong khoảng hợp lý
    return isinstance(value, int) and 0 <= value <= 100000


def get_remaining_sheets():
    """Trả về số giấy còn lại (int) hoặc None nếu không đọc được."""
    global _disabled_warned
    # An toàn trên hết: không gọi GetMediaCounter khi đang tắt (tránh access violation
    # làm hỏng tiến trình/luồng in). Trả None gọn gàng.
    if not _MEDIA_COUNTER_ENABLED:
        if not _disabled_warned:
            print("[PrinterMedia] GetMediaCounter dang TAT (tranh access violation tren DNP). "
                  "So giay con lai = None.", flush=True)
            _disabled_warned = True
        return None

    now = time.time()
    if now - _cache["ts"] < _CACHE_TTL:
        return _cache["value"]

    value = None
    dll = _load()
    if dll is not None:
        try:
            buf = ctypes.create_string_buffer(64)
            count = dll.GetPrinterPortNum(buf, 64)
            # Log chẩn đoán: cho biết SDK thấy bao nhiêu máy in + vài byte cổng đầu tiên.
            first_bytes = list(buf.raw[:8])
            print(f"[PrinterMedia] GetPrinterPortNum -> count={count}, port_bytes={first_bytes}", flush=True)
            if count and count >= 1:
                b = buf.raw
                # Số cổng có thể là 1 byte, 2 byte hoặc 4 byte little-endian. Log thực tế cho
                # port_bytes=[5,1,...] -> nhiều khả năng cổng = 0x0105 = 261 chứ không phải 5
                # (code cũ chỉ đọc 1 byte đầu nên GetMediaCounter trả -1). Thử lần lượt, lấy
                # giá trị hợp lệ đầu tiên.
                candidates = []
                for p in (b[0], b[0] | (b[1] << 8), int.from_bytes(b[0:4], 'little')):
                    if p not in candidates:
                        candidates.append(p)
                for port in candidates:
                    remaining = dll.GetMediaCounter(port)
                    print(f"[PrinterMedia] GetMediaCounter(port={port}) -> {remaining} (valid={_is_valid(remaining)})", flush=True)
                    if _is_valid(remaining):
                        value = int(remaining)
                        break
            else:
                print("[PrinterMedia] SDK khong thay may in Citizen/DNP nao (count<1). "
                      "Kiem tra: may in dung model Citizen? Da bat? Driver dung?", flush=True)
        except Exception as e:
            print(f"[PrinterMedia] Loi doc media counter: {e}", flush=True)
            value = None

    _cache["ts"] = now
    _cache["value"] = value
    return value
