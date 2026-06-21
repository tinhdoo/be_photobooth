import os
import subprocess
import time
import uuid
from io import BytesIO

from PIL import Image, ImageEnhance, ImageOps, ImageFilter


PREFERRED_PRINTER_KEYWORDS = ("RX1HS", "DS-RX1", "RX1", "DNP")


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


def get_available_printers():
    try:
        import win32print

        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [printer[2] for printer in win32print.EnumPrinters(flags)]
    except Exception:
        return _get_printers_powershell()


def resolve_printer_name(configured_name=None):
    printers = get_available_printers()
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


def _print_with_windows_dc(image_path, printer_name, copies, cut_mode="none", scale_x=100, scale_y=100, offset_x=0, offset_y=0):
    import win32con
    import win32ui
    from PIL import ImageWin

    image = Image.open(image_path)
    image = ImageOps.exif_transpose(image).convert("RGB")

    dc = win32ui.CreateDC()
    dc.CreatePrinterDC(printer_name)
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


def print_image_file(image_path, printer_name, copies=1, cut_mode="none", scale_x=100, scale_y=100, offset_x=0, offset_y=0):
    if os.name != "nt":
        raise RuntimeError("Chỉ hỗ trợ in trực tiếp trên Windows.")

    copies = max(1, min(int(copies or 1), 20))
    image_path = os.path.abspath(image_path)

    if not os.path.exists(image_path):
        raise FileNotFoundError(image_path)

    method = "windows_dc"
    # KHÔNG fallback sang mspaint: mspaint in "fit to page" -> letterbox VIỀN TRẮNG (khác
    # full-bleed), dùng Popen không chờ/không bắt lỗi (luôn coi như thành công), và nếu
    # windows_dc đã spool được vài bản rồi mới lỗi thì mspaint in lại đủ copies -> IN TRÙNG.
    # Thà để lỗi ném ra ngoài để endpoint đánh dấu PrintJob 'failed' và in lại có kiểm soát.
    _print_with_windows_dc(image_path, printer_name, copies, cut_mode, scale_x, scale_y, offset_x, offset_y)

    cut_note = None
    if str(cut_mode).lower() in {"2x6", "2-inch", "2inch"}:
        cut_note = "DNP 2x6 cut must be enabled in the printer driver's Printing Defaults."

    return {
        "printer": printer_name,
        "copies": copies,
        "cut_mode": cut_mode,
        "cut_note": cut_note,
        "file_path": image_path,
        "method": method,
    }


def _number_setting(settings, key, default=0):
    try:
        return float((settings or {}).get(key, default) or default)
    except Exception:
        return float(default)


def apply_basic_print_color_settings(image, settings=None):
    brightness = _number_setting(settings, "print_brightness")
    contrast = _number_setting(settings, "print_contrast")
    saturation = _number_setting(settings, "print_saturation")
    warmth = _number_setting(settings, "print_warmth")

    if brightness:
        image = ImageEnhance.Brightness(image).enhance(max(0.2, 1 + brightness / 100))
    if contrast:
        image = ImageEnhance.Contrast(image).enhance(max(0.2, 1 + contrast / 100))
    if saturation:
        image = ImageEnhance.Color(image).enhance(max(0.2, 1 + saturation / 100))
    if warmth:
        r, g, b = image.split()
        r = r.point(lambda value: max(0, min(255, value + warmth)))
        b = b.point(lambda value: max(0, min(255, value - warmth)))
        image = Image.merge("RGB", (r, g, b))

    return image


def create_test_print_image(output_dir, color_settings=None):
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
    image = apply_basic_print_color_settings(image, color_settings)
    image.save(path, "JPEG", quality=95, subsampling=0)
    return path
