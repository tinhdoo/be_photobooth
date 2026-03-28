import os
import uuid
import subprocess
from flask import Blueprint, request, jsonify, send_file
from threading import Timer

convert_motion_bp = Blueprint("convert_motion", __name__)

TEMP_DIR = "uploads/temp"
os.makedirs(TEMP_DIR, exist_ok=True)

def cleanup_files(path1, path2):
    try:
        if os.path.exists(path1):
            os.remove(path1)
        if os.path.exists(path2):
            os.remove(path2)
    except:
        pass

@convert_motion_bp.route("/api/convert-motion", methods=["POST"])
def convert_motion():

    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400

    file = request.files["file"]

    uid = str(uuid.uuid4())
    webm_path = os.path.join(TEMP_DIR, f"{uid}.webm")
    mp4_path = os.path.join(TEMP_DIR, f"{uid}.mp4")

    file.save(webm_path)

    ffmpeg_bin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ffmpeg-master-latest-win64-gpl", "bin", "ffmpeg.exe")
    if not os.path.exists(ffmpeg_bin):
        ffmpeg_bin = "ffmpeg"

    # ⭐⭐⭐ QUAN TRỌNG — encode chuẩn mobile
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", webm_path,

        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",

        # video codec
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",

        # ⭐ fix iPhone / Instagram
        "-pix_fmt", "yuv420p",
        "-profile:v", "baseline",
        "-level", "3.0",

        # ⭐ cho streaming (cực quan trọng)
        "-movflags", "+faststart",

        # fix instagram metadata
        "-g", "30",

        # remove audio (webcam không cần)
        "-an",

        mp4_path
    ]

    try:
        subprocess.run(
            cmd, 
            check=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            timeout=60
        )
    except subprocess.CalledProcessError as e:
        print(f"FFMPEG ERROR (path: {ffmpeg_bin}):", e.stderr.decode() if e.stderr else e, flush=True)
        import logging
        logging.error(f"FFMPEG ERROR: {e.stderr.decode() if e.stderr else e}")
        cleanup_files(webm_path, "")
        return jsonify({"error": f"ffmpeg failed: {e.stderr.decode() if e.stderr else e}"}), 500
    except subprocess.TimeoutExpired as e:
        print("FFMPEG TIMEOUT", flush=True)
        cleanup_files(webm_path, "")
        return jsonify({"error": "ffmpeg timeout"}), 504

    # delete after 5 mins
    Timer(300, cleanup_files, args=(webm_path, mp4_path)).start()

    # trả file trực tiếp
    abs_mp4_path = os.path.abspath(mp4_path)

    return send_file(
        abs_mp4_path,
        mimetype="video/mp4",
        as_attachment=False,
        download_name="motion.mp4"
    )
