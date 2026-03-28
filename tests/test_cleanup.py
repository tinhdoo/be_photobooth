import sys
import os
import datetime
from datetime import timedelta, timezone
import logging

# Add parent directory to path to import app and models
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup logging to file
logging.basicConfig(filename='cleanup_test.log', level=logging.INFO, format='%(asctime)s - %(message)s')

def test_cleanup():
    logging.info("TEST: Starting cleanup verification...")
    
    try:
        from app import app, db
        from models import Session, Photo
        from services.cleanup_manager import cleanup_expired_sessions
    except Exception as e:
        logging.error(f"TEST: Import failed: {e}")
        return

    try:
        with app.app_context():
            # 1. Create a dummy expired session
            logging.info("TEST: Creating dummy expired session...")
            utc_now = datetime.datetime.now(timezone.utc)
            expired_time = utc_now - timedelta(hours=73) # 73 hours ago
            
            # Mock session
            session = Session(
                layout_id='test_layout',
                status='completed',
                composite_url='http://example.com/test.jpg',
                composite_public_id='test_composite_id',
                created_at=expired_time
            )
            db.session.add(session)
            db.session.commit()
            session_id = session.id
            logging.info(f"TEST: Created session {session_id} at {expired_time}")
            
            # 2. Run cleanup
            logging.info("TEST: Running cleanup_expired_sessions...")
            try:
                cleanup_expired_sessions()
            except Exception as e:
                logging.error(f"TEST: Cleanup function raised exception: {e}")

            # 3. Verify preservation
            logging.info("TEST: Verifying preservation...")
            updated_session = Session.query.get(session_id)
            
            if updated_session is not None:
                logging.info("TEST: Session record preserved (Success).")
                if updated_session.composite_url is None and updated_session.composite_public_id is None:
                    logging.info("TEST: SUCCESS - Session URLs nullified.")
                else:
                    logging.error(f"TEST: FAILED - Session URLs not nullified: {updated_session.composite_url}")
                
                # Check photos
                photo_count = Photo.query.filter_by(session_id=session_id).count()
                if photo_count == 0:
                    logging.info("TEST: SUCCESS - Related photo records deleted.")
                else:
                    logging.error(f"TEST: FAILED - {photo_count} photo records still exist.")
            else:
                logging.error("TEST: FAILED - Session record was deleted.")

    except Exception as e:
        logging.error(f"TEST: Unexpected error: {e}")

if __name__ == "__main__":
    test_cleanup()
