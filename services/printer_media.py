"""Đọc số giấy/lượt in còn lại của máy in Citizen/DNP (RX1HS, DS-RX1, QW410...) qua cspstat SDK.

cspstat64.dll là thư viện status của Citizen (DNP RX1HS thực chất là máy Citizen đổi tên).
Hàm GetMediaCounter(port) trả về số lượt in còn lại của cuộn media hiện tại.

QUAN TRỌNG — chạy CÁCH LY: GetMediaCounter từng gây ACCESS VIOLATION trên DNP RX1 (sai
chữ ký SDK) và access violation thì Python KHÔNG bắt được -> sẽ giết luôn tiến trình.
Vì vậy lời gọi DLL được chạy trong một TIẾN TRÌNH CON (spawn lại chính exe với cờ
--probe-media, hoặc python -c khi chạy từ source). Nếu con crash -> chỉ con chết, backend
chính vẫn an toàn, ta chỉ nhận về None.

Module "fail-safe": thiếu DLL / không máy in / con crash -> trả None, không bao giờ làm
crash app. Kết quả cache vài giây để tránh hỏi máy in liên tục.
"""
import ctypes
import os
import subprocess
import sys
import time

PROBE_FLAG = "--probe-media"

_dll = None
_dll_tried = False
_cache = {"ts": 0.0, "value": None}
_CACHE_TTL = 8.0  # giây


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
        # Chữ ký lấy từ impl tham chiếu (FlashgoAI/DNPSDKService, StdCall). SDK trả 'long'
        # 32-bit (Windows long) -> đọc bằng c_int để -1 ra đúng dấu.
        dll.GetPrinterPortNum.restype = ctypes.c_int
        dll.GetPrinterPortNum.argtypes = [ctypes.c_char_p, ctypes.c_int]
        dll.SetUSBTimeout.restype = ctypes.c_int
        dll.SetUSBTimeout.argtypes = [ctypes.c_longlong, ctypes.c_longlong]
        dll.GetMediaCounter.restype = ctypes.c_int
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


def _probe_once():
    """CHẠY TRONG TIẾN TRÌNH CON. Gọi DLL (có thể access violation) và in đúng 1 dòng
    'MEDIA:<int>' hoặc 'MEDIA:NONE' ra stdout. Nếu access violation -> con chết, không in
    được dòng MEDIA -> cha hiểu là đọc thất bại."""
    val = None
    dll = _load()
    if dll is not None:
        try:
            buf = ctypes.create_string_buffer(64)
            count = dll.GetPrinterPortNum(buf, 64)
            sys.stderr.write(f"[probe] GetPrinterPortNum count={count} bytes={list(buf.raw[:4])}\n")
            if count and count >= 1:
                # Trình tự đúng (theo impl tham chiếu): port = INDEX 0 của máy in đầu tiên
                # (buf[0]=DeviceID, buf[1]=UnitID chỉ là thông tin), PHẢI SetUSBTimeout trước
                # khi đọc, KHÔNG gọi InitializePrinter.
                port = 0
                try:
                    dll.SetUSBTimeout(port, 1000)
                except Exception as e:
                    sys.stderr.write(f"[probe] SetUSBTimeout loi: {e}\n")
                remaining = dll.GetMediaCounter(port)
                sys.stderr.write(f"[probe] GetMediaCounter(port={port}) -> {remaining}\n")
                if _is_valid(remaining):
                    val = int(remaining)
        except Exception as e:
            sys.stderr.write(f"[probe] loi: {e}\n")
            val = None
    try:
        sys.stdout.write(f"MEDIA:{val if val is not None else 'NONE'}\n")
        sys.stdout.flush()
    except Exception:
        pass


def _read_via_subprocess():
    """Spawn tiến trình con chạy _probe_once và đọc kết quả. Trả int hoặc None."""
    if os.name != "nt":
        return None
    try:
        if getattr(sys, "frozen", False):
            # Bản đóng gói: re-exec chính exe với cờ --probe-media (app.py thoát sớm khi thấy cờ).
            cmd = [sys.executable, PROBE_FLAG]
            cwd = os.path.dirname(sys.executable)
        else:
            # Chạy từ source: gọi python -c nạp module, cwd = dev_be để import được 'services'.
            here = os.path.dirname(os.path.abspath(__file__))
            cwd = os.path.dirname(here)
            cmd = [sys.executable, "-c",
                   "from services.printer_media import _probe_once; _probe_once()"]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=6, cwd=cwd,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("MEDIA:"):
                token = line.split(":", 1)[1].strip()
                return int(token) if token.isdigit() else None
        # Không có dòng MEDIA -> con đã crash (access violation) hoặc lỗi khác.
        if proc.returncode != 0:
            print(f"[PrinterMedia] Tien trinh con doc media that bai (rc={proc.returncode}) "
                  f"-> so giay=None. stderr: {(proc.stderr or '').strip()[:200]}", flush=True)
        return None
    except subprocess.TimeoutExpired:
        print("[PrinterMedia] Doc media qua thoi gian (>6s) -> None", flush=True)
        return None
    except Exception as e:
        print(f"[PrinterMedia] Loi spawn tien trinh doc media: {e}", flush=True)
        return None


def get_remaining_sheets():
    """Trả về số giấy còn lại (int) hoặc None nếu không đọc được. An toàn: lời gọi DLL
    chạy cách ly trong tiến trình con nên access violation không làm sập backend."""
    now = time.time()
    if now - _cache["ts"] < _CACHE_TTL:
        return _cache["value"]

    value = _read_via_subprocess()
    _cache["ts"] = now
    _cache["value"] = value
    return value
