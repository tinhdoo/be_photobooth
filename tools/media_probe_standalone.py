"""Probe đọc số giấy DNP qua cspstat64.dll — bản v3 (đúng trình tự theo impl tham chiếu).

Trình tự: GetPrinterPortNum -> port = index 0 -> SetUSBTimeout(0,1000) -> đọc.
KHÔNG gọi InitializePrinter. Đóng gói kèm sẵn cspstat64.dll.
"""
import ctypes
import os
import sys
from ctypes import c_int, c_longlong, c_char_p, create_string_buffer


def find_dll():
    name = "cspstat64.dll"
    if getattr(sys, "frozen", False):
        for p in (os.path.join(sys._MEIPASS, name),
                  os.path.join(os.path.dirname(sys.executable), name)):
            if os.path.exists(p):
                return p
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "..", "lib", name), os.path.join(os.getcwd(), name)):
        if os.path.exists(p):
            return p
    return name


def main():
    try:
        dll = ctypes.WinDLL(find_dll())
    except Exception as e:
        print("Khong load duoc cspstat64.dll:", e); input("ENTER..."); return

    dll.GetPrinterPortNum.restype = c_int
    dll.GetPrinterPortNum.argtypes = [c_char_p, c_int]
    buf = create_string_buffer(64)
    count = dll.GetPrinterPortNum(buf, 64)
    print(f"GetPrinterPortNum count={count} bytes={list(buf.raw[:4])}  (DeviceID={buf.raw[0]}, UnitID={buf.raw[1]})")
    if count < 1:
        print("Khong thay may in."); input("ENTER..."); return

    port = 0  # index máy in đầu tiên (đúng theo impl tham chiếu)

    # SetUSBTimeout BẮT BUỘC trước khi đọc
    try:
        dll.SetUSBTimeout.restype = c_int
        dll.SetUSBTimeout.argtypes = [c_longlong, c_longlong]
        rc = dll.SetUSBTimeout(port, 1000)
        print(f"SetUSBTimeout({port}, 1000) -> {rc}")
    except Exception as e:
        print(f"SetUSBTimeout EXC: {e}")

    # Đọc các giá trị (restype c_int để -1 ra đúng dấu)
    int_funcs = ["GetStatus", "GetMediaCounter", "GetMediaCounterH",
                 "GetInitialMediaCount", "GetPQTY", "GetCounterA", "GetCounterB",
                 "GetCounterL", "GetFreeBuffer"]
    for fn in int_funcs:
        try:
            f = getattr(dll, fn); f.restype = c_int; f.argtypes = [c_longlong]
        except Exception:
            pass
    print(f"\n===== PORT (index) = {port} sau SetUSBTimeout =====")
    for fn in int_funcs:
        try:
            r = getattr(dll, fn)(port)
            print(f"  {fn}({port}) -> {r}")
        except Exception as e:
            print(f"  {fn}({port}) EXC: {e}")
    # buffer functions
    for fn in ("GetSerialNo", "GetMedia", "GetFirmwVersion"):
        try:
            f = getattr(dll, fn); f.restype = c_int; f.argtypes = [c_longlong, c_char_p]
            b2 = create_string_buffer(64)
            rc = f(port, b2)
            print(f"  {fn}({port}, buf) rc={rc} val={b2.value!r}")
        except Exception as e:
            print(f"  {fn}({port}) EXC: {e}")

    print("\n--- xong --- (GetMediaCounter = so giay con lai)")
    input("ENTER de thoat...")


if __name__ == "__main__":
    main()
