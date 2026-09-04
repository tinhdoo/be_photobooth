"""In TRỰC TIẾP qua DNP/Citizen SDK (cspstat64.dll) - bỏ qua driver GDI để ảnh in NÉT
như FlashgoAI 2.5.2. Trình tự gọi + chữ ký P/Invoke + định dạng buffer lấy nguyên từ
FlashgoAI.DNPSDKService (decompile), nên rủi ro sai chữ ký -> access violation rất thấp.

Vì sao nét hơn GDI: ảnh được resize đúng kích thước pixel gốc của máy (RX1HS 4x6@300 =
1844x1280) rồi đẩy thẳng pixel vào firmware máy in; firmware DNP tự xử lý. Đường GDI cũ đi
qua driver Windows + thêm một lớp co giãn -> mềm ảnh.

Buffer ảnh (đúng như LoadImageAsRgb24Async):
    - 24bpp, thứ tự byte BGR (GDI Format24bppRgb), đóng gói liền (stride = width*3).
    - LẬT NGANG (horizontal mirror). FlashgoAI lật trong lúc build buffer; nếu KHÔNG lật,
      ảnh in ra sẽ bị soi gương trái-phải.

AN TOÀN: mọi lời gọi DLL (có thể access violation - native crash Python KHÔNG bắt được)
chạy trong một TIẾN TRÌNH CON. Con crash -> chỉ con chết, backend chính vẫn sống, caller
nhận về (False, lý do) để rơi sang luồng GDI dự phòng.

Cờ tiến trình con: app.py thấy "--print-sdk" thì gọi _print_child() rồi thoát (giống
--probe-media). Tham số truyền qua biến môi trường PTB_PRINT_PARAMS (đường dẫn file JSON).
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time

PRINT_FLAG = "--print-sdk"
PARAMS_ENV = "PTB_PRINT_PARAMS"
RESULT_PREFIX = "PRINTSDK:"   # con in dòng kết quả: PRINTSDK:OK:<note> / PRINTSDK:FAIL:<lý do>
SENT_MARKER = "PRINTSDK:SENT" # phát NGAY sau SendImageData thành công: lệnh in ĐÃ commit.
# Vì sao cần SENT_MARKER: nếu con gửi ảnh xong rồi crash (access violation ở PrintImageData)
# hoặc cha timeout, ta KHÔNG được fallback sang GDI -> sẽ IN TRÙNG. Thấy SENT (kể cả khi
# không có OK) -> coi như đã in, báo thành công, KHÔNG fallback.


# ---- Hằng số SDK (FlashgoAI.DNPSDKService) ----
class Resolution:
    R300 = 300
    R600 = 600


class Overcoat:
    GLOSSY = 0       # bóng (mặc định, nhìn nét/trong nhất)
    MATTE1 = 1       # mờ
    FINEMATTE = 21
    LUSTER = 31


class Cutter:
    STANDARD = 0     # không cắt -> 1 tờ 4x6
    CUT_2INCH = 120  # CUTTER_MODE_2INCHCUT -> cắt đôi 4x6 thành 2 strip 2x6


class Media:
    CSP_PC = 3       # RX1/DS-RX1/DS620...: 4x6 (postcard)
    CSP_4x6 = 55     # QW410: 4x6


# Kích thước pixel CHÍNH XÁC theo (model, media, dpi) - trích GetExactPixelSize.
# Ảnh PHẢI đúng kích thước này, nếu lệch firmware tự scale -> phóng to/mờ.
_EXACT_SIZE = {
    # RX1/DS-RX1/DS620/DS820... dùng media CSP_PC(3) cho 4x6
    ("RX1", Media.CSP_PC, 300): (1844, 1280),
    # QW410 dùng media CSP_4x6(55)
    ("QW410", Media.CSP_4x6, 300): (1400, 1816),
}


def target_pixel_size(model, media, resolution):
    """Trả (w, h) đúng pixel hoặc None nếu không có trong bảng (caller tự tính theo inch)."""
    key = (str(model).upper(), int(media), int(resolution))
    return _EXACT_SIZE.get(key)


# =====================================================================================
#  PHẦN CHẠY TRONG TIẾN TRÌNH CON
# =====================================================================================
def _find_dll_path():
    """Tái dùng đúng logic định vị DLL như printer_media (PyInstaller / source / cwd)."""
    name = "cspstat64.dll"
    candidates = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, name))
        candidates.append(os.path.join(os.path.dirname(sys.executable), name))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "..", "lib", name))
    candidates.append(os.path.join(os.getcwd(), "lib", name))
    candidates.append(os.path.join(os.getcwd(), name))
    for path in candidates:
        if path and os.path.exists(path):
            return os.path.abspath(path)
    return None


def _bind_dll(dll):
    """Gắn chữ ký P/Invoke ĐÚNG như FlashgoAI.DNPSDKService (mọi 'long' C# = Int64).
    Sai chữ ký = access violation, nên giữ nguyên không phỏng đoán."""
    ll = ctypes.c_longlong
    ui = ctypes.c_uint
    pb = ctypes.POINTER(ctypes.c_ubyte)

    dll.GetPrinterPortNum.restype = ll
    dll.GetPrinterPortNum.argtypes = [pb, ctypes.c_int]
    dll.SetUSBTimeout.restype = ll
    dll.SetUSBTimeout.argtypes = [ll, ll]
    dll.GetFirmwVersion.restype = ll
    dll.GetFirmwVersion.argtypes = [ll, pb]
    dll.SetResolution.restype = ll
    dll.SetResolution.argtypes = [ll, ll]
    dll.SetOvercoatFinish.restype = ll
    dll.SetOvercoatFinish.argtypes = [ll, ui]
    dll.SetMediaSize.restype = ll
    dll.SetMediaSize.argtypes = [ll, ll]
    dll.SetCutterMode.restype = ctypes.c_bool
    dll.SetCutterMode.argtypes = [ll, ui]
    dll.GetFreeBuffer.restype = ll
    dll.GetFreeBuffer.argtypes = [ll]
    dll.SetPQTY.restype = ll
    dll.SetPQTY.argtypes = [ll, ll]
    dll.SendImageData.restype = ll
    dll.SendImageData.argtypes = [ll, ctypes.c_void_p, ll, ll, ll, ll]
    dll.PrintImageData.restype = ll
    dll.PrintImageData.argtypes = [ll]


def _build_bgr_mirror_buffer(image_path, target_w, target_h, sharpen=0):
    """Đọc ảnh -> resize LANCZOS đúng target -> (tùy chọn) làm nét -> BGR 24bpp + LẬT NGANG.
    Trả (bytes, w, h). Khớp byte-for-byte với LoadImageAsRgb24Async của FlashgoAI."""
    import numpy as np
    from PIL import Image, ImageOps, ImageFilter

    img = Image.open(image_path)
    img = ImageOps.exif_transpose(img).convert("RGB")

    # Xoay cho khớp hướng giấy (giống _rotate_to_match_page bên GDI).
    if (img.width >= img.height) != (target_w >= target_h):
        img = img.rotate(90, expand=True)

    if (img.width, img.height) != (target_w, target_h):
        img = img.resize((target_w, target_h), Image.LANCZOS)

    if sharpen and sharpen > 0:
        amount = max(0, min(200, int(sharpen)))
        if amount > 0:
            img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=amount, threshold=3))

    arr = np.asarray(img, dtype=np.uint8)          # (H, W, 3) RGB
    arr = arr[:, ::-1, ::-1]                        # lật ngang (cột) + đảo kênh RGB->BGR
    arr = np.ascontiguousarray(arr)
    return arr.tobytes(), target_w, target_h


def _do_print(p):
    """Thực thi toàn bộ quy trình in trong tiến trình con. Trả (ok, note)."""
    image_path = p["image_path"]
    copies = max(1, min(int(p.get("copies", 1)), 50))
    resolution = int(p.get("resolution", Resolution.R300))
    media = int(p.get("media", Media.CSP_PC))
    overcoat = int(p.get("overcoat", Overcoat.GLOSSY))
    cutter = int(p.get("cutter", Cutter.STANDARD))
    model = str(p.get("model", "RX1"))
    sharpen = int(p.get("sharpen", 0))
    dry_run = bool(p.get("dry_run", False))

    # RX1/DS-RX1 chỉ in chuẩn ở 300dpi - FlashgoAI FORCE 300 để tránh firmware tự phóng to.
    if "RX1" in model.upper() and resolution != 300:
        sys.stderr.write(f"[printsdk] RX1 khong ho tro {resolution}dpi -> ep ve 300dpi\n")
        resolution = 300

    # Kích thước đích: ưu tiên override (sau khi test phần cứng), rồi bảng chính xác, rồi tính theo inch.
    tw = int(p.get("width") or 0)
    th = int(p.get("height") or 0)
    if not (tw > 0 and th > 0):
        exact = target_pixel_size(model, media, resolution)
        if exact:
            tw, th = exact
    if not (tw > 0 and th > 0):
        # Fallback tính theo 4x6 inch * dpi (kèm chút bleed như DNP).
        tw, th = int(6.15 * resolution), int(4.27 * resolution)

    log = sys.stderr.write
    log(f"[printsdk] image={image_path} model={model} media={media} res={resolution} "
        f"overcoat={overcoat} cutter={cutter} copies={copies} target={tw}x{th} dry={dry_run}\n")

    buf, w, h = _build_bgr_mirror_buffer(image_path, tw, th, sharpen=sharpen)
    log(f"[printsdk] buffer dung xong: {len(buf)} bytes ({w}x{h}x3)\n")

    if dry_run:
        # Ghi buffer + preview ra đĩa để kiểm tra mắt thường, KHÔNG gửi máy in.
        out_dir = os.path.dirname(os.path.abspath(image_path))
        raw_path = os.path.join(out_dir, "sdk_dryrun.bgr")
        with open(raw_path, "wb") as f:
            f.write(buf)
        try:
            import numpy as np
            from PIL import Image
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)[:, :, ::-1]  # BGR->RGB để xem
            Image.fromarray(arr).save(os.path.join(out_dir, "sdk_dryrun_preview.png"))
        except Exception as e:
            log(f"[printsdk] luu preview loi: {e}\n")
        return True, f"dryrun ({w}x{h}) -> {raw_path}"

    dll_path = _find_dll_path()
    if not dll_path:
        return False, "khong tim thay cspstat64.dll"
    dll = ctypes.WinDLL(dll_path)
    _bind_dll(dll)
    log(f"[printsdk] da load {dll_path}\n")

    # 1) Tìm cổng máy in (port index = 0 cho máy đầu tiên, theo impl tham chiếu).
    portbuf = (ctypes.c_ubyte * 256)()
    count = dll.GetPrinterPortNum(portbuf, 256)
    log(f"[printsdk] GetPrinterPortNum -> count={count}\n")
    if not count or count < 1:
        return False, "khong tim thay may in DNP qua SDK (count<1)"
    port = 0

    # 2) Timeout USB trước khi mọi thao tác (bắt buộc, tránh treo).
    try:
        dll.SetUSBTimeout(port, 1000)
    except Exception as e:
        log(f"[printsdk] SetUSBTimeout loi (bo qua): {e}\n")

    # 3) Thiết lập tham số in - best-effort (FlashgoAI cũng "log & tiếp tục" với hầu hết).
    try:
        if resolution > 0:
            log(f"[printsdk] SetResolution -> {dll.SetResolution(port, resolution)}\n")
        log(f"[printsdk] SetOvercoatFinish -> {dll.SetOvercoatFinish(port, overcoat)}\n")
        rc_media = dll.SetMediaSize(port, media)
        log(f"[printsdk] SetMediaSize({media}) -> {rc_media}\n")
        log(f"[printsdk] SetCutterMode({cutter}) -> {dll.SetCutterMode(port, cutter)}\n")
        try:
            log(f"[printsdk] GetFreeBuffer -> {dll.GetFreeBuffer(port)}\n")
        except Exception as e:
            log(f"[printsdk] GetFreeBuffer loi (bo qua): {e}\n")
        log(f"[printsdk] SetPQTY({copies}) -> {dll.SetPQTY(port, copies)}\n")
    except Exception as e:
        return False, f"loi thiet lap tham so in: {e}"

    # 4) Gửi dữ liệu ảnh -> in.
    cbuf = (ctypes.c_ubyte * len(buf)).from_buffer_copy(buf)
    ptr = ctypes.cast(cbuf, ctypes.c_void_p)
    rc_send = dll.SendImageData(port, ptr, 0, 0, w, h)
    log(f"[printsdk] SendImageData -> {rc_send}\n")
    if rc_send <= 0:
        return False, f"SendImageData that bai (rc={rc_send})"

    # Đã gửi ảnh vào máy in -> từ đây coi như ĐÃ commit. Phát SENT (flush ngay) để dù
    # PrintImageData có crash/treo thì cha cũng KHÔNG fallback GDI -> tránh in trùng.
    try:
        sys.stdout.write(SENT_MARKER + "\n")
        sys.stdout.flush()
    except Exception:
        pass

    rc_print = dll.PrintImageData(port)
    log(f"[printsdk] PrintImageData -> {rc_print}\n")
    return True, f"da gui {w}x{h} x{copies} (send={rc_send}, print={rc_print})"


def _print_child():
    """ENTRY tiến trình con. Đọc tham số từ PTB_PRINT_PARAMS, in đúng 1 dòng kết quả."""
    ok, note = False, "khong co tham so"
    try:
        params_path = os.environ.get(PARAMS_ENV)
        if params_path and os.path.exists(params_path):
            with open(params_path, "r", encoding="utf-8") as f:
                p = json.load(f)
            ok, note = _do_print(p)
        else:
            note = f"thieu file tham so ({PARAMS_ENV})"
    except Exception as e:
        ok, note = False, f"exception: {e}"
    try:
        sys.stdout.write(f"{RESULT_PREFIX}{'OK' if ok else 'FAIL'}:{note}\n")
        sys.stdout.flush()
    except Exception:
        pass


# =====================================================================================
#  API GỌI TỪ BACKEND (tiến trình cha)
# =====================================================================================
def _parse_sdk_output(stdout):
    """Đọc stdout của tiến trình con -> (ok, committed, note).
      - committed=True nếu thấy SENT (ảnh đã vào máy in) -> TUYỆT ĐỐI không fallback GDI.
      - ok=True nếu thấy dòng OK, hoặc đã committed (in trùng còn tệ hơn thiếu 1 tấm).
    """
    ok = False
    committed = False
    note = ""
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if line == SENT_MARKER:
            committed = True
        elif line.startswith(RESULT_PREFIX):
            status, _, n = line[len(RESULT_PREFIX):].partition(":")
            note = n
            if status == "OK":
                ok = True
    return ok, committed, note


def print_via_sdk(image_path, copies=1, cut=False, sharpen=0, model="RX1",
                  media=Media.CSP_PC, resolution=Resolution.R300, overcoat=Overcoat.GLOSSY,
                  width=0, height=0, dry_run=False, timeout=90):
    """In qua DNP SDK trong tiến trình con cách ly. Trả (ok: bool, note: str).
    KHÔNG bao giờ raise vì DLL: con crash trước khi gửi -> (False, lý do) để caller rơi sang
    GDI. Nếu con ĐÃ gửi ảnh (SENT) rồi crash/timeout -> (True, ...) để KHÔNG in trùng.

    `cut`: True nếu cắt đôi 4x6 -> 2 strip 2x6 (caller tự tính qua _is_cut_mode)."""
    if os.name != "nt":
        return False, "chi ho tro tren Windows"
    image_path = os.path.abspath(image_path)
    if not os.path.exists(image_path):
        return False, f"khong thay file: {image_path}"

    cutter = Cutter.CUT_2INCH if cut else Cutter.STANDARD
    params = {
        "image_path": image_path, "copies": int(copies), "sharpen": int(sharpen),
        "model": model, "media": int(media), "resolution": int(resolution),
        "overcoat": int(overcoat), "cutter": int(cutter),
        "width": int(width), "height": int(height), "dry_run": bool(dry_run),
    }

    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(params, tmp)
        tmp.close()
        env = dict(os.environ, **{PARAMS_ENV: tmp.name})
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, PRINT_FLAG]
            cwd = os.path.dirname(sys.executable)
        else:
            here = os.path.dirname(os.path.abspath(__file__))
            cwd = os.path.dirname(here)
            cmd = [sys.executable, "-c",
                   "from services.printer_sdk import _print_child; _print_child()"]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as te:
            # Timeout: vẫn đọc output đã bắt được. Nếu đã SENT -> coi như đã in (không fallback).
            stdout = te.stdout.decode(errors="replace") if isinstance(te.stdout, bytes) else (te.stdout or "")
            stderr = te.stderr.decode(errors="replace") if isinstance(te.stderr, bytes) else (te.stderr or "")
            rc = "timeout"

        ok, committed, note = _parse_sdk_output(stdout)
        if ok:
            return True, note
        if committed:
            # Ảnh đã vào máy in nhưng con chết/treo trước khi báo OK -> KHÔNG in lại.
            print(f"[PrinterSDK] da gui anh roi con chet/treo (rc={rc}) -> coi nhu DA IN, "
                  f"khong fallback de tranh in trung.", flush=True)
            return True, f"committed nhung con loi (rc={rc})"
        # Chưa gửi gì -> an toàn để fallback GDI.
        err = (stderr or "").strip()
        print(f"[PrinterSDK] in qua SDK that bai (rc={rc}, note={note}). stderr: {err[-400:]}", flush=True)
        return False, note or f"tien trinh con loi (rc={rc})"
    except Exception as e:
        return False, f"loi spawn: {e}"
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
