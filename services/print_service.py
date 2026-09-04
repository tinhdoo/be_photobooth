import os
import subprocess
import tempfile
import threading
import time
import uuid
from io import BytesIO

from PIL import Image, ImageOps, ImageFilter


PREFERRED_PRINTER_KEYWORDS = ("RX1HS", "DS-RX1", "RX1", "DNP")

# Tên file blob DEVMODE đã "chụp" sẵn từ UI driver (xem tools/capture_dnp_devmode.py).
# Dùng để điều khiển dao cắt DNP theo từng lệnh in thay vì phụ thuộc Printing Defaults.
CUT_DEVMODE_FILE = "devmode_cut.bin"
NOCUT_DEVMODE_FILE = "devmode_nocut.bin"

# Các giá trị cut_mode được hiểu là "cắt đôi 4x6 -> 2 strip 2x6".
_CUT_MODE_VALUES = {"2x6", "2-inch", "2inch", "cut"}


def devmode_profile_dir():
    """Thư mục chứa các blob DEVMODE đã chụp (cạnh thư mục làm việc của backend)."""
    return os.path.join(os.getcwd(), "printer_profiles")


def _is_cut_mode(cut_mode):
    return str(cut_mode or "").strip().lower() in _CUT_MODE_VALUES


def _resolve_cut_devmode(cut_mode):
    """
    Nạp blob DEVMODE phù hợp với cut_mode:
      - cut_mode cắt đôi  -> devmode_cut.bin
      - còn lại           -> devmode_nocut.bin
    Trả về bytes nếu file tồn tại & đọc được, ngược lại None (sẽ fallback DC mặc định).
    """
    filename = CUT_DEVMODE_FILE if _is_cut_mode(cut_mode) else NOCUT_DEVMODE_FILE
    path = os.path.join(devmode_profile_dir(), filename)
    try:
        if os.path.exists(path):
            with open(path, "rb") as fh:
                data = fh.read()
            return data or None
    except Exception:
        pass
    return None


def _create_printer_dc(printer_name, cut_mode, win32ui):
    """
    Tạo DC máy in. Nếu có blob DEVMODE phù hợp với cut_mode -> dùng nó để điều khiển
    dao cắt DNP theo từng lệnh in. Nếu chưa cấu hình blob -> dùng DC mặc định của driver
    (hành vi cũ) và ghi log cảnh báo.
    """
    devmode = _resolve_cut_devmode(cut_mode)
    if devmode:
        try:
            from services import printer_devmode

            hdc = printer_devmode.create_dc_handle(printer_name, devmode)
            dc = win32ui.CreateDCFromHandle(hdc)
            print(f"[Print] Dung DEVMODE tuy chinh cho cut_mode={cut_mode} "
                  f"({'CUT' if _is_cut_mode(cut_mode) else 'NO-CUT'}, {len(devmode)} bytes)",
                  flush=True)
            return dc
        except Exception as exc:
            print(f"[Print] Khong dung duoc DEVMODE tuy chinh ({exc}); quay ve DC mac dinh.",
                  flush=True)
    elif _is_cut_mode(cut_mode):
        print("[Print] CANH BAO: thieu devmode_cut.bin -> dao cat phu thuoc Printing Defaults "
              "cua driver. Chay tools/capture_dnp_devmode.py de cau hinh cat per-job.", flush=True)
    else:
        print("[Print] CANH BAO: thieu devmode_nocut.bin -> neu driver dang bat 2inch cut thi "
              "anh van bi cat doi. Chay tools/capture_dnp_devmode.py de cau hinh.", flush=True)

    dc = win32ui.CreateDC()
    dc.CreatePrinterDC(printer_name)
    return dc


def _get_printers_powershell():
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Printer | Select-Object -ExpandProperty Name",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception:
        return []

    if result.returncode != 0:
        return []

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


# Cache danh sách máy in: EnumPrinters là lệnh native CHẶN (blocking) và đôi khi treo vài chục
# giây (spooler bận / máy in vừa ngủ). Cache theo thời gian để nhiều lệnh gọi liên tiếp (health
# poll ~15s + lệnh in) không phải liệt kê lại mỗi lần -> giảm mạnh số lần chạm native.
_PRINTER_CACHE_TTL = 20.0  # giây
_printer_cache = {"ts": 0.0, "printers": None}
_printer_cache_lock = threading.Lock()


def _enumerate_printers():
    try:
        import win32print

        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [printer[2] for printer in win32print.EnumPrinters(flags)]
    except Exception:
        return _get_printers_powershell()


def get_available_printers(force=False):
    """Danh sách máy in, có cache TTL. force=True để bỏ cache, liệt kê lại ngay.

    Lưu ý: hàm này CHẶN khi cache hết hạn (gọi EnumPrinters). Trên server eventlet, hãy gọi
    qua tpool.execute(...) để không đóng băng hub."""
    now = time.monotonic()
    with _printer_cache_lock:
        cached = _printer_cache["printers"]
        if not force and cached is not None and (now - _printer_cache["ts"]) < _PRINTER_CACHE_TTL:
            return cached

    printers = _enumerate_printers()

    with _printer_cache_lock:
        _printer_cache["printers"] = printers
        _printer_cache["ts"] = time.monotonic()
    return printers


def resolve_printer_name(configured_name=None, force=False):
    printers = get_available_printers(force=force)
    if configured_name:
        exact = next((name for name in printers if name.lower() == configured_name.lower()), None)
        if exact:
            return exact, printers

        partial = next((name for name in printers if configured_name.lower() in name.lower()), None)
        if partial:
            return partial, printers

    for keyword in PREFERRED_PRINTER_KEYWORDS:
        match = next((name for name in printers if keyword.lower() in name.lower()), None)
        if match:
            return match, printers

    return None, printers


def get_printer_status(configured_name=None):
    printer_name, printers = resolve_printer_name(configured_name)

    # Số giấy còn lại đọc qua Citizen/DNP status SDK (cspstat). None nếu không đọc được.
    try:
        from services.printer_media import get_remaining_sheets
        remaining = get_remaining_sheets()
    except Exception:
        remaining = None

    status = {
        "online": bool(printer_name),
        "name": printer_name,
        "configured_name": configured_name or "",
        "available_printers": printers,
        "status": "Online" if printer_name else "Not found",
        "paper": "4x6",
        "remaining": remaining,
        "remaining_label": (f"{remaining} tấm" if remaining is not None else "Không đọc được từ driver"),
        "driver": "Unknown",
        "message": "Đã kết nối" if printer_name else "Không tìm thấy máy in",
    }

    if not printer_name or os.name != "nt":
        return status

    try:
        ps_command = (
            f"$p = Get-Printer -Name {printer_name!r} -ErrorAction Stop; "
            "$p | Select-Object Name,PrinterStatus,DriverName | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_command],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            import json

            info = json.loads(result.stdout)
            printer_status = str(info.get("PrinterStatus") or "").strip()
            driver_name = str(info.get("DriverName") or "").strip()
            status["status"] = printer_status or status["status"]
            status["driver"] = driver_name or status["driver"]
            status["online"] = printer_status.lower() not in {"offline", "error", "not available"}
            status["message"] = "Đã kết nối" if status["online"] else printer_status or "Máy in không sẵn sàng"
    except Exception:
        pass

    return status


def save_print_image(file_storage, output_dir, sharpen=70):
    os.makedirs(output_dir, exist_ok=True)
    raw = file_storage.read()
    image = Image.open(BytesIO(raw))
    image = ImageOps.exif_transpose(image).convert("RGB")

    # Làm nét nhẹ để bù độ mềm cố hữu của máy in nhiệt nhuộm (dye-sub). Tắt bằng print_sharpen=0.
    try:
        amount = max(0, min(200, int(sharpen)))
    except Exception:
        amount = 0
    if amount > 0:
        image = image.filter(ImageFilter.UnsharpMask(radius=1.2, percent=amount, threshold=3))

    filename = f"print_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.jpg"
    path = os.path.join(output_dir, filename)
    image.save(path, "JPEG", quality=95, subsampling=0)
    return path


def _rotate_to_match_page(image, page_width, page_height):
    image_is_landscape = image.width >= image.height
    page_is_landscape = page_width >= page_height
    if image_is_landscape != page_is_landscape:
        return image.rotate(90, expand=True)
    return image


def _print_with_windows_dc(image_path, printer_name, copies, cut_mode="none", scale_x=100, scale_y=100, offset_x=0, offset_y=0, sharpen=0):
    import win32con
    import win32ui
    from PIL import ImageWin

    image = Image.open(image_path)
    image = ImageOps.exif_transpose(image).convert("RGB")

    # Tạo DC: ưu tiên blob DEVMODE đã chụp để điều khiển dao cắt theo từng lệnh in.
    dc = _create_printer_dc(printer_name, cut_mode, win32ui)
    try:
        printable_width = dc.GetDeviceCaps(win32con.HORZRES)
        printable_height = dc.GetDeviceCaps(win32con.VERTRES)
        phys_offset_x = dc.GetDeviceCaps(win32con.PHYSICALOFFSETX)
        phys_offset_y = dc.GetDeviceCaps(win32con.PHYSICALOFFSETY)

        if printable_width <= 0 or printable_height <= 0:
            raise RuntimeError("Khong doc duoc kich thuoc vung in tu driver.")

        page_image = _rotate_to_match_page(image, printable_width, printable_height)

        # Parse factors and shift values
        factor_x = float(scale_x) / 100.0 if scale_x else 1.0
        factor_y = float(scale_y) / 100.0 if scale_y else 1.0
        shift_x = int(offset_x) if offset_x else 0
        shift_y = int(offset_y) if offset_y else 0

        # Log thông số máy in để biết vì sao bị cắt / có viền: nếu phys_offset > 0 nghĩa là
        # driver báo có lề KHÔNG in được -> full-bleed sẽ cắt đúng phần đó. DNP borderless
        # đúng chuẩn phải cho phys_offset = 0 (in tràn lề, không cắt, không viền).
        try:
            phys_w = dc.GetDeviceCaps(win32con.PHYSICALWIDTH)
            phys_h = dc.GetDeviceCaps(win32con.PHYSICALHEIGHT)
        except Exception:
            phys_w = phys_h = 0
        print(f"[Print] DeviceCaps {printer_name}: printable={printable_width}x{printable_height}, "
              f"physical={phys_w}x{phys_h}, phys_offset=({phys_offset_x},{phys_offset_y}), "
              f"image={image.width}x{image.height}", flush=True)

        # Full-bleed: vẽ phủ kín toàn trang vật lý (không viền trắng theo yêu cầu).
        # Phần lọt vào lề không in được (phys_offset) sẽ bị máy in cắt - chỉ thực sự hết cắt
        # khi phys_offset = 0 (driver để borderless đúng).
        x1 = -phys_offset_x
        y1 = -phys_offset_y
        x2 = printable_width + phys_offset_x
        y2 = printable_height + phys_offset_y

        width = x2 - x1
        height = y2 - y1

        # Apply scaling and shifting
        new_w = width * factor_x
        new_h = height * factor_y
        center_x = (x1 + x2) / 2 + shift_x
        center_y = (y1 + y2) / 2 + shift_y

        # Resize bằng LANCZOS (chất lượng cao) sang ĐÚNG kích thước in, để GDI vẽ 1:1
        # thay vì tự co giãn bằng thuật toán kém -> tránh ảnh in bị mờ.
        target_w = max(1, int(round(new_w)))
        target_h = max(1, int(round(new_h)))
        render_image = page_image.resize((target_w, target_h), Image.LANCZOS)
        # Làm nét sau khi resize đúng kích thước in để bù độ mềm của máy dye-sub (giống SDK).
        try:
            amount = max(0, min(200, int(sharpen)))
        except Exception:
            amount = 0
        if amount > 0:
            render_image = render_image.filter(ImageFilter.UnsharpMask(radius=1.2, percent=amount, threshold=3))
        dib = ImageWin.Dib(render_image)

        left = int(round(center_x - target_w / 2))
        top = int(round(center_y - target_h / 2))
        target_box = (left, top, left + target_w, top + target_h)

        for index in range(copies):
            dc.StartDoc(f"Tomato Photobooth {cut_mode} {index + 1}/{copies}")
            try:
                dc.StartPage()
                # HALFTONE để mọi co giãn còn sót (do làm tròn) vẫn chất lượng cao
                try:
                    import win32gui
                    win32gui.SetStretchBltMode(dc.GetHandleOutput(), win32con.HALFTONE)
                except Exception:
                    pass
                dib.draw(dc.GetHandleOutput(), target_box)
                dc.EndPage()
            finally:
                dc.EndDoc()
    finally:
        dc.DeleteDC()


def _color_int(settings, key):
    try:
        return int(float((settings or {}).get(key, 0) or 0))
    except Exception:
        return 0


def apply_print_color_to_file(image_path, settings):
    """Áp chỉnh màu in (sáng/tương phản/bão hòa/ấm + R/G/B) lên ảnh, GHI RA FILE TẠM và trả
    đường dẫn. KHÔNG đụng file gốc -> in lại đọc config MỚI mỗi lần (màu không bị đóng băng).
    Trả None nếu mọi giá trị = 0 (không cần xử lý). Công thức KHỚP với frontend cũ để màu
    nhất quán dù canh bằng slider nào."""
    brightness = _color_int(settings, "brightness")
    contrast = _color_int(settings, "contrast")
    saturation = _color_int(settings, "saturation")
    warmth = _color_int(settings, "warmth")
    red = _color_int(settings, "red")
    green = _color_int(settings, "green")
    blue = _color_int(settings, "blue")

    if not any((brightness, contrast, saturation, warmth, red, green, blue)):
        return None

    import numpy as np

    img = Image.open(image_path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    arr = np.asarray(img, dtype=np.float32)
    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]

    contrast_value = max(-80.0, min(80.0, contrast * 2.0))
    contrast_factor = (259.0 * (contrast_value + 255.0)) / (255.0 * (259.0 - contrast_value))
    saturation_factor = 1.0 + (saturation / 100.0)

    r = r + brightness + warmth + red
    g = g + brightness + (warmth * 0.25) + green
    b = b + brightness - warmth + blue

    r = contrast_factor * (r - 128.0) + 128.0
    g = contrast_factor * (g - 128.0) + 128.0
    b = contrast_factor * (b - 128.0) + 128.0

    gray = 0.299 * r + 0.587 * g + 0.114 * b
    r = gray + (r - gray) * saturation_factor
    g = gray + (g - gray) * saturation_factor
    b = gray + (b - gray) * saturation_factor

    out = np.clip(np.stack([r, g, b], axis=2), 0, 255).astype(np.uint8)

    fd, tmp_path = tempfile.mkstemp(prefix="print_color_", suffix=".jpg")
    os.close(fd)
    Image.fromarray(out, "RGB").save(tmp_path, "JPEG", quality=95, subsampling=0)
    print(f"[Print] Da ap mau in: B={brightness} C={contrast} S={saturation} W={warmth} "
          f"R={red} G={green} B={blue} -> {tmp_path}", flush=True)
    return tmp_path


def print_image_file(image_path, printer_name, copies=1, cut_mode="none", scale_x=100, scale_y=100, offset_x=0, offset_y=0,
                     sharpen=0, use_sdk=True, sdk_kwargs=None, color_settings=None):
    if os.name != "nt":
        raise RuntimeError("Chỉ hỗ trợ in trực tiếp trên Windows.")

    copies = max(1, min(int(copies or 1), 20))
    image_path = os.path.abspath(image_path)

    if not os.path.exists(image_path):
        raise FileNotFoundError(image_path)

    # Áp chỉnh màu in từ config HIỆN TẠI lên 1 file TẠM (không nung vào file gốc) -> in mới lẫn
    # in lại đều theo config mới nhất. Lỗi -> bỏ qua, in màu gốc, không vỡ luồng in.
    render_path = image_path
    temp_color_path = None
    try:
        temp_color_path = apply_print_color_to_file(image_path, color_settings)
        if temp_color_path:
            render_path = temp_color_path
    except Exception as exc:
        print(f"[Print] Bo qua chinh mau in (loi): {exc}", flush=True)

    try:
        result = _do_print_image(render_path, printer_name, copies, cut_mode, scale_x, scale_y,
                                 offset_x, offset_y, sharpen, use_sdk, sdk_kwargs)
        result["file_path"] = image_path  # trả file GỐC, không phải file màu tạm (đã xóa)
        return result
    finally:
        if temp_color_path:
            try:
                os.remove(temp_color_path)
            except Exception:
                pass


def _do_print_image(image_path, printer_name, copies, cut_mode, scale_x, scale_y,
                    offset_x, offset_y, sharpen, use_sdk, sdk_kwargs):
    method = None
    sdk_error = None
    # 1) Ưu tiên in TRỰC TIẾP qua DNP SDK (cspstat64.dll) -> nét như FlashgoAI. Chạy cách ly
    #    trong tiến trình con; nếu thất bại/không có máy -> rơi sang GDI bên dưới.
    if use_sdk:
        try:
            from services.printer_sdk import print_via_sdk
            ok, note = print_via_sdk(image_path, copies=copies, cut=_is_cut_mode(cut_mode),
                                     sharpen=sharpen, **(sdk_kwargs or {}))
            if ok:
                method = "dnp_sdk"
                print(f"[Print] In qua DNP SDK thanh cong: {note}", flush=True)
            else:
                sdk_error = note
                print(f"[Print] DNP SDK that bai -> fallback GDI. Ly do: {note}", flush=True)
        except Exception as exc:
            sdk_error = str(exc)
            print(f"[Print] DNP SDK loi -> fallback GDI: {exc}", flush=True)

    # 2) Dự phòng: in qua Windows DC (GDI). KHÔNG fallback sang mspaint: mspaint in "fit to
    #    page" -> letterbox VIỀN TRẮNG, không bắt lỗi (luôn coi như thành công), và nếu đã
    #    spool vài bản rồi mới lỗi thì in lại đủ copies -> IN TRÙNG. Thà để lỗi ném ra ngoài
    #    để endpoint đánh dấu PrintJob 'failed' và in lại có kiểm soát.
    if method is None:
        _print_with_windows_dc(image_path, printer_name, copies, cut_mode, scale_x, scale_y, offset_x, offset_y,
                               sharpen=sharpen)
        method = "windows_dc"

    cut_note = None
    # Chỉ cảnh báo về devmode_cut.bin khi THỰC SỰ in qua GDI. In qua SDK thì việc cắt do
    # SetCutterMode(2INCHCUT) lo, không liên quan DEVMODE -> không hiện note gây hiểu nhầm.
    if method == "windows_dc" and _is_cut_mode(cut_mode):
        if _resolve_cut_devmode(cut_mode) is None:
            cut_note = ("Chưa có devmode_cut.bin -> dao cắt đang phụ thuộc Printing Defaults của "
                        "driver. Chạy tools/capture_dnp_devmode.py để điều khiển cắt per-job.")

    return {
        "printer": printer_name,
        "copies": copies,
        "cut_mode": cut_mode,
        "cut_note": cut_note,
        "file_path": image_path,
        "method": method,
        "sdk_error": sdk_error,
    }


def create_test_print_image(output_dir):
    # Tạo ảnh test NEUTRAL (màu gốc). Việc chỉnh màu áp lúc IN qua cùng pipeline như in thật.
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"test_print_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
    image = Image.new("RGB", (1200, 1800), "#fff6df")

    try:
        from PIL import ImageDraw, ImageFont

        draw = ImageDraw.Draw(image)
        font_large = ImageFont.truetype("arial.ttf", 72)
        font_medium = ImageFont.truetype("arial.ttf", 42)
    except Exception:
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        font_large = None
        font_medium = None

    draw.rectangle((80, 80, 1120, 1720), outline="#8b6a4b", width=8)
    draw.text((160, 260), "Tomato Photobooth", fill="#2f3e46", font=font_large)
    draw.text((160, 380), "DNP RX1HS test print", fill="#52796f", font=font_medium)
    draw.text((160, 480), time.strftime("%Y-%m-%d %H:%M:%S"), fill="#8b6a4b", font=font_medium)
    draw.rectangle((160, 620, 460, 920), fill="#f2c7bd")
    draw.rectangle((500, 620, 800, 920), fill="#f7dccd")
    draw.rectangle((840, 620, 980, 920), fill="#ffffff")
    draw.text((160, 980), "Color calibration sample", fill="#8b6a4b", font=font_medium)
    draw.text((160, 1560), "If this prints cleanly, printer path is ready.", fill="#2f3e46", font=font_medium)
    image.save(path, "JPEG", quality=95, subsampling=0)
    return path
