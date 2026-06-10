import mimetypes
import os
import time
import uuid

from werkzeug.utils import secure_filename


def is_supabase_configured():
    return all([
        os.environ.get("SUPABASE_URL"),
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY"),
        os.environ.get("SUPABASE_BUCKET"),
    ])


def _create_client():
    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError("Missing Python package 'supabase'. Run: pip install supabase") from exc

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    return create_client(url, key)


def upload_file_to_supabase(file_storage, folder="cloud"):
    if not is_supabase_configured():
        raise RuntimeError("Supabase is not configured")

    bucket = os.environ.get("SUPABASE_BUCKET")
    original_name = secure_filename(file_storage.filename) or "upload.bin"
    _, ext = os.path.splitext(original_name)
    ext = ext.lower() or ".bin"
    object_path = f"{folder}/{time.strftime('%Y/%m/%d')}/{uuid.uuid4().hex}{ext}"
    content_type = file_storage.mimetype or mimetypes.guess_type(original_name)[0] or "application/octet-stream"

    try:
        file_storage.stream.seek(0)
    except Exception:
        pass

    client = _create_client()
    data = file_storage.read()
    client.storage.from_(bucket).upload(
        path=object_path,
        file=data,
        file_options={
            "content-type": content_type,
            "cache-control": "3600",
            "upsert": "false",
        },
    )

    public_url = f"{os.environ.get('SUPABASE_URL').rstrip('/')}/storage/v1/object/public/{bucket}/{object_path}"
    return {
        "url": public_url,
        "public_id": f"supabase:{bucket}/{object_path}",
        "storage": "supabase",
        "bucket": bucket,
        "path": object_path,
    }


def upload_path_to_supabase(file_path, folder="cloud", content_type=None):
    if not is_supabase_configured():
        raise RuntimeError("Supabase is not configured")

    bucket = os.environ.get("SUPABASE_BUCKET")
    original_name = secure_filename(os.path.basename(file_path)) or "upload.bin"
    _, ext = os.path.splitext(original_name)
    ext = ext.lower() or ".bin"
    object_path = f"{folder}/{time.strftime('%Y/%m/%d')}/{uuid.uuid4().hex}{ext}"
    content_type = content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"

    client = _create_client()
    with open(file_path, "rb") as handle:
        client.storage.from_(bucket).upload(
            path=object_path,
            file=handle.read(),
            file_options={
                "content-type": content_type,
                "cache-control": "3600",
                "upsert": "false",
            },
        )

    public_url = f"{os.environ.get('SUPABASE_URL').rstrip('/')}/storage/v1/object/public/{bucket}/{object_path}"
    return {
        "url": public_url,
        "public_id": f"supabase:{bucket}/{object_path}",
        "storage": "supabase",
        "bucket": bucket,
        "path": object_path,
    }
