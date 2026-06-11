import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

import cloudinary.api
from flask import current_app, has_app_context

from models import db, Session

UTC = timezone.utc
SESSION_TTL_HOURS = 24
LOCAL_UPLOAD_TTL_HOURS = 24


def _as_naive_utc(value):
    if not value:
        return None
    if value.tzinfo:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _cloudinary_asset_from_url(url):
    if not url or 'res.cloudinary.com' not in url:
        return None

    try:
        parts = [unquote(part) for part in urlparse(url).path.split('/') if part]
        if len(parts) < 4:
            return None

        resource_type = parts[1]
        if resource_type not in ('image', 'video', 'raw'):
            resource_type = 'image'

        upload_index = parts.index('upload')
        public_parts = parts[upload_index + 1:]
        if public_parts and public_parts[0].startswith('v') and public_parts[0][1:].isdigit():
            public_parts = public_parts[1:]
        if not public_parts:
            return None

        filename = public_parts[-1]
        public_parts[-1] = os.path.splitext(filename)[0]
        return resource_type, '/'.join(public_parts)
    except Exception:
        return None


def _add_cloudinary_asset(assets, public_id=None, resource_type='image', url=None):
    if public_id and public_id.startswith('local:'):
        return

    if public_id:
        assets.setdefault(resource_type, set()).add(public_id)
        return

    parsed = _cloudinary_asset_from_url(url)
    if parsed:
        parsed_type, parsed_public_id = parsed
        assets.setdefault(parsed_type, set()).add(parsed_public_id)


def _delete_cloudinary_assets(assets, session_uuid):
    deleted_count = 0

    for resource_type, public_ids in assets.items():
        ids = sorted(public_ids)
        if not ids:
            continue

        try:
            cloudinary.api.delete_resources(ids, resource_type=resource_type)
            deleted_count += len(ids)
            logging.info(f"[CLEANUP] Deleted {len(ids)} Cloudinary {resource_type} resource(s) for session {session_uuid}")
        except Exception as exc:
            logging.warning(f"[CLEANUP] Cloudinary {resource_type} delete error for session {session_uuid}: {exc}")

    return deleted_count


def _local_upload_path_from_ref(value):
    if not value:
        return None

    if value.startswith('local:'):
        relative = value.replace('local:', '', 1).replace('\\', '/')
    elif value.startswith('/uploads/'):
        relative = value.replace('/uploads/', '', 1).replace('\\', '/')
    else:
        parsed = urlparse(value)
        if not parsed.path.startswith('/uploads/'):
            return None
        relative = parsed.path.replace('/uploads/', '', 1).replace('\\', '/')

    root = os.path.abspath(os.path.join(os.getcwd(), 'uploads'))
    target = os.path.abspath(os.path.join(root, relative))
    if target == root or not target.startswith(root + os.sep):
        return None
    return target


def _delete_local_upload_file(value):
    path = _local_upload_path_from_ref(value)
    if not path or not os.path.exists(path):
        return 0

    os.remove(path)
    logging.info(f"[CLEANUP] Deleted local upload file: {path}")
    return 1


def _delete_local_session_folders(session_uuid):
    folders = [
        os.path.join(current_app.config.get('UPLOAD_FOLDER', 'uploads'), str(session_uuid)),
        os.path.join(os.getcwd(), 'uploads', str(session_uuid))
    ]

    for folder in folders:
        if os.path.exists(folder):
            shutil.rmtree(folder)
            logging.info(f"[CLEANUP] Deleted local folder: {folder}")


def expire_session(session):
    """
    Delete media for one completed session and keep the session row for revenue/stats.
    """
    assets = {}

    _add_cloudinary_asset(assets, session.composite_public_id, 'image', session.composite_url)
    _add_cloudinary_asset(assets, session.gif_public_id, 'image', session.gif_url)

    for photo in list(session.photos):
        _add_cloudinary_asset(assets, photo.public_id, 'image', photo.url)
        _add_cloudinary_asset(assets, getattr(photo, 'video_public_id', None), 'video', photo.video_url)

    deleted_count = _delete_cloudinary_assets(assets, session.uuid)

    try:
        _delete_local_session_folders(session.uuid)
    except Exception as exc:
        logging.warning(f"[CLEANUP] Local folder delete error for session {session.uuid}: {exc}")

    deleted_count += _delete_local_upload_file(session.composite_public_id)
    deleted_count += _delete_local_upload_file(session.gif_public_id)
    deleted_count += _delete_local_upload_file(session.composite_url)
    deleted_count += _delete_local_upload_file(session.gif_url)

    for photo in list(session.photos):
        deleted_count += _delete_local_upload_file(photo.public_id)
        deleted_count += _delete_local_upload_file(getattr(photo, 'video_public_id', None))
        deleted_count += _delete_local_upload_file(photo.url)
        deleted_count += _delete_local_upload_file(photo.video_url)
        photo.url = None
        photo.video_url = None
        photo.public_id = None
        if hasattr(photo, 'video_public_id'):
            photo.video_public_id = None
        db.session.delete(photo)

    session.composite_url = None
    session.composite_public_id = None
    session.gif_url = None
    session.gif_public_id = None
    session.status = 'expired'

    return deleted_count


def expire_session_if_needed(session):
    created_at = _as_naive_utc(session.created_at)
    if not created_at or session.status != 'completed':
        return False

    expiration_limit = datetime.utcnow() - timedelta(hours=SESSION_TTL_HOURS)
    if created_at >= expiration_limit:
        return False

    expire_session(session)
    db.session.commit()
    logging.info(f"[CLEANUP] Expired session on access: {session.uuid}")
    return True


def cleanup_expired_sessions():
    """
    Marks sessions older than 24 hours as expired, deletes their media, and keeps
    the session record for revenue/stats.
    """
    try:
        if not has_app_context():
            from app import app
            context = app.app_context()
            context.push()
        else:
            context = None

        logging.info(f"[CLEANUP] Starting cleanup job at {datetime.utcnow()}")

        expiration_limit = datetime.utcnow() - timedelta(hours=SESSION_TTL_HOURS)
        expired_sessions = Session.query.filter(
            Session.created_at < expiration_limit,
            Session.status == 'completed'
        ).all()

        if not expired_sessions:
            logging.info("[CLEANUP] No expired sessions found.")
            if context:
                context.pop()
            return

        logging.info(f"[CLEANUP] Found {len(expired_sessions)} session(s) to expire.")

        count_sessions = 0
        count_resources = 0

        for session in expired_sessions:
            try:
                count_resources += expire_session(session)
                count_sessions += 1
            except Exception as exc:
                logging.error(f"[CLEANUP] Error processing session {session.id}: {exc}")

        db.session.commit()
        logging.info(f"[CLEANUP] Done. Expired {count_sessions} session(s), deleted {count_resources} Cloudinary resource(s).")

        if context:
            context.pop()

    except Exception as exc:
        logging.error(f"[CLEANUP] Critical error: {exc}")


def cleanup_old_local_upload_files():
    """
    Deletes local media files older than 24 hours from upload cache folders.
    This includes print job images that are not stored in the session table.
    """
    try:
        upload_root = os.path.abspath(os.path.join(os.getcwd(), 'uploads'))
        managed_dirs = [
            os.path.join(upload_root, 'cloud'),
            os.path.join(upload_root, 'temp'),
            os.path.join(upload_root, 'print_jobs'),
        ]
        cutoff = datetime.utcnow() - timedelta(hours=LOCAL_UPLOAD_TTL_HOURS)
        deleted_count = 0

        for folder in managed_dirs:
            folder = os.path.abspath(folder)
            if not folder.startswith(upload_root + os.sep) or not os.path.isdir(folder):
                continue

            for root, _, files in os.walk(folder):
                root = os.path.abspath(root)
                if not root.startswith(upload_root + os.sep):
                    continue

                for filename in files:
                    path = os.path.abspath(os.path.join(root, filename))
                    if not path.startswith(upload_root + os.sep):
                        continue

                    try:
                        modified_at = datetime.utcfromtimestamp(os.path.getmtime(path))
                        if modified_at < cutoff:
                            os.remove(path)
                            deleted_count += 1
                            logging.info(f"[CLEANUP] Deleted old local upload: {path}")
                    except Exception as exc:
                        logging.warning(f"[CLEANUP] Could not delete old local upload {path}: {exc}")

        if deleted_count:
            logging.info(f"[CLEANUP] Deleted {deleted_count} old local upload file(s).")
        else:
            logging.info("[CLEANUP] No old local upload files found.")
    except Exception as exc:
        logging.error(f"[CLEANUP] Local upload cleanup error: {exc}")


def cleanup_old_payment_codes():
    """
    Deletes payment codes that are:
    1. Used more than 15 days ago.
    2. Expired more than 15 days ago.
    """
    try:
        if not has_app_context():
            from app import app
            context = app.app_context()
            context.push()
        else:
            context = None

        from models import PaymentCode
        logging.info(f"[CLEANUP] Starting payment codes cleanup job at {datetime.now(UTC)}")

        limit = (datetime.now(UTC) - timedelta(days=15)).replace(tzinfo=None)

        used_expired = PaymentCode.query.filter(
            PaymentCode.is_used == True,
            PaymentCode.used_at < limit
        ).delete()

        time_expired = PaymentCode.query.filter(
            PaymentCode.is_used == False,
            PaymentCode.expires_at != None,
            PaymentCode.expires_at < limit
        ).delete()

        total_deleted = used_expired + time_expired
        if total_deleted > 0:
            db.session.commit()
            logging.info(f"[CLEANUP] Deleted {total_deleted} old payment codes ({used_expired} used, {time_expired} expired).")
        else:
            logging.info("[CLEANUP] No old payment codes to delete.")

        if context:
            context.pop()

    except Exception as exc:
        logging.error(f"[CLEANUP] Error in payment codes cleanup job: {exc}")
