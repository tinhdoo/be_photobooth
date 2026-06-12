import os
import subprocess
import time
import uuid
from io import BytesIO

from PIL import Image, ImageOps


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
    status = {
        "online": bool(printer_name),
        "name": printer_name,
        "configured_name": configured_name or "",
        "available_printers": printers,
        "status": "Online" if printer_name else "Not found",
        "paper": "4x6",
        "remaining": None,
        "remaining_label": "Không đọc được từ driver",
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


def save_print_image(file_storage, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    raw = file_storage.read()
    image = Image.open(BytesIO(raw))
    image = ImageOps.exif_transpose(image).convert("RGB")

    filename = f"print_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.jpg"
    path = os.path.join(output_dir, filename)
    image.save(path, "JPEG", quality=95, subsampling=0)
    return path


def print_image_file(image_path, printer_name, copies=1):
    if os.name != "nt":
        raise RuntimeError("Chỉ hỗ trợ in trực tiếp trên Windows.")

    copies = max(1, min(int(copies or 1), 20))
    image_path = os.path.abspath(image_path)

    if not os.path.exists(image_path):
        raise FileNotFoundError(image_path)

    # mspaint /pt delegates final media/cut/color settings to the installed printer driver.
    # This is the most dependency-light path; pywin32 is not required on kiosk machines.
    for _ in range(copies):
        subprocess.Popen(
            ["mspaint.exe", "/pt", image_path, printer_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    return {
        "printer": printer_name,
        "copies": copies,
        "file_path": image_path,
    }


def create_test_print_image(output_dir):
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
    draw.text((160, 1560), "If this prints cleanly, printer path is ready.", fill="#2f3e46", font=font_medium)
    image.save(path, "JPEG", quality=95, subsampling=0)
    return path
