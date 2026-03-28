import os
import shutil
from datetime import datetime, timedelta, timezone
# Fix cho Python 3.9
UTC = timezone.utc
import cloudinary.api
from models import db, Session, Photo
import logging
from flask import current_app, has_app_context

def cleanup_expired_sessions():
    """
    Marks sessions older than 72 hours as 'expired',
    deletes their Cloudinary assets, and nullifies image URLs.
    Keeps the session record for revenue/stats.
    """
    try:
        if not has_app_context():
            from app import app
            context = app.app_context()
            context.push()
        else:
            context = None

        logging.info(f"[CLEANUP] Starting cleanup job at {datetime.utcnow()}")

        # Use naive UTC datetime — SQLite stores datetimes without timezone info
        expiration_limit = datetime.utcnow() - timedelta(hours=72)

        # Only target completed sessions that haven't been expired yet
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
                # 1. Collect Cloudinary public IDs
                ids_to_delete = []
                if session.composite_public_id:
                    ids_to_delete.append(session.composite_public_id)
                if session.gif_public_id:
                    ids_to_delete.append(session.gif_public_id)
                for photo in session.photos:
                    if photo.public_id:
                        ids_to_delete.append(photo.public_id)

                # 2. Delete from Cloudinary
                if ids_to_delete:
                    try:
                        cloudinary.api.delete_resources(ids_to_delete)
                        count_resources += len(ids_to_delete)
                        logging.info(f"[CLEANUP] Deleted {len(ids_to_delete)} Cloudinary resource(s) for session {session.uuid}")
                    except Exception as e:
                        logging.warning(f"[CLEANUP] Cloudinary delete error for session {session.uuid}: {e}")

                # 3. Delete local upload folders
                try:
                    for folder in [
                        os.path.join(current_app.config.get('UPLOAD_FOLDER', 'uploads'), str(session.uuid)),
                        os.path.join(os.getcwd(), 'uploads', str(session.uuid))
                    ]:
                        if os.path.exists(folder):
                            shutil.rmtree(folder)
                            logging.info(f"[CLEANUP] Deleted local folder: {folder}")
                except Exception as e:
                    logging.warning(f"[CLEANUP] Local folder delete error for session {session.uuid}: {e}")

                # 4. Nullify all photo URL fields and delete photo records
                for photo in session.photos:
                    photo.url = None
                    photo.video_url = None
                    photo.public_id = None
                    db.session.delete(photo)

                # 5. Nullify session image fields
                session.composite_url = None
                session.composite_public_id = None
                session.gif_url = None
                session.gif_public_id = None

                # 6. Mark session as expired (keeps revenue data intact)
                session.status = 'expired'

                count_sessions += 1

            except Exception as e:
                logging.error(f"[CLEANUP] Error processing session {session.id}: {e}")

        db.session.commit()
        logging.info(f"[CLEANUP] Done. Expired {count_sessions} session(s), deleted {count_resources} Cloudinary resource(s).")

        if context:
            context.pop()

    except Exception as e:
        logging.error(f"[CLEANUP] Critical error: {e}")

def cleanup_old_payment_codes():
    """
    Deletes payment codes that are:
    1. Used more than 24 hours ago.
    2. Expired more than 24 hours ago.
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

        limit = (datetime.now(UTC) - timedelta(hours=24)).replace(tzinfo=None)

        # 1. Used codes
        used_expired = PaymentCode.query.filter(
            PaymentCode.is_used == True,
            PaymentCode.used_at < limit
        ).delete()

        # 2. Expired codes (not used but expired > 24h)
        # expires_at is and should be in UTC as per models.py default
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

    except Exception as e:
        logging.error(f"[CLEANUP] Error in payment codes cleanup job: {e}")
