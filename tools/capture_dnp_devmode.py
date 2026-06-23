"""
Chụp 2 blob DEVMODE cho dao cắt DNP RX1HS — CHẠY 1 LẦN trên máy booth.

    cd dev_be
    python tools/capture_dnp_devmode.py

Kịch bản:
    - Lần 1: hộp thoại driver hiện ra -> đặt "2inch cut" = ON  -> OK
             (lưu devmode_cut.bin)
    - Lần 2: hộp thoại driver hiện ra -> đặt "2inch cut" = OFF -> OK
             (lưu devmode_nocut.bin)

Hai file được lưu vào <cwd>/printer_profiles/. Sau đó backend sẽ tự nạp đúng blob
theo cut_mode của từng lệnh in (cut_mode "2x6" -> cắt, còn lại -> không cắt).

Lưu ý: ngoài mục cắt, hãy giữ nguyên các thiết lập khác (khổ 4x6, hướng, màu...)
giống nhau ở cả 2 lần để chỉ khác duy nhất ở dao cắt.
"""

import os
import sys

# Cho phép import package services khi chạy script từ thư mục dev_be hoặc bất kỳ đâu.
_DEV_BE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _DEV_BE not in sys.path:
    sys.path.insert(0, _DEV_BE)

from services import printer_devmode  # noqa: E402
from services.print_service import (  # noqa: E402
    CUT_DEVMODE_FILE,
    NOCUT_DEVMODE_FILE,
    devmode_profile_dir,
    resolve_printer_name,
)


def _diff_count(a, b):
    """Số byte khác nhau giữa 2 blob (để kiểm tra chỉ khác ở vùng cắt)."""
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
    data = printer_devmode.prompt_devmode(printer_name)
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
    printer_name, printers = resolve_printer_name(configured)
    if not printer_name:
        print("Không tìm thấy máy in RX1HS/DNP. Danh sách máy in hiện có:")
        for name in printers:
            print(f"  - {name}")
        print("\nDùng: python tools/capture_dnp_devmode.py \"<ten may in>\"")
        return 1

    print(f"Máy in: {printer_name}")
    out_dir = devmode_profile_dir()
    os.makedirs(out_dir, exist_ok=True)
    print(f"Sẽ lưu vào: {out_dir}")

    cut = _capture(
        printer_name,
        "BƯỚC 1/2 — BẬT dao cắt",
        "Tìm mục '2inch cut' (hoặc Cut = 2inch) và đặt BẬT, rồi bấm OK.",
    )
    nocut = _capture(
        printer_name,
        "BƯỚC 2/2 — TẮT dao cắt",
        "Đặt '2inch cut' = TẮT (Off), rồi bấm OK.",
    )

    saved = []
    if cut is not None:
        path = os.path.join(out_dir, CUT_DEVMODE_FILE)
        with open(path, "wb") as fh:
            fh.write(cut)
        saved.append(path)
        print(f"Đã lưu: {path}")
    if nocut is not None:
        path = os.path.join(out_dir, NOCUT_DEVMODE_FILE)
        with open(path, "wb") as fh:
            fh.write(nocut)
        saved.append(path)
        print(f"Đã lưu: {path}")

    if cut is not None and nocut is not None:
        diff = _diff_count(cut, nocut)
        if diff is None:
            print("\nCẢNH BÁO: 2 blob khác độ dài — có thể driver/khổ giấy không nhất quán.")
        elif diff == 0:
            print("\nCẢNH BÁO: 2 blob GIỐNG HỆT nhau — có thể bạn chưa đổi mục cắt giữa 2 lần.")
        else:
            print(f"\nOK: 2 blob khác nhau {diff} byte (kỳ vọng chỉ khác ở thiết lập cắt).")

    if len(saved) < 2:
        print("\nChưa đủ 2 file. Hãy chạy lại và bấm OK ở cả 2 bước.")
        return 1

    print("\nHoàn tất. Backend sẽ tự dùng các blob này cho từng lệnh in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
