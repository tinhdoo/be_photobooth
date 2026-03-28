from app import app, db
from models import Config
import logging

# Disable logging to avoid clutter
logging.getLogger('werkzeug').setLevel(logging.ERROR)

def update_canon_mode():
    with app.app_context():
        try:
            config = Config.query.get('camera_mode')
            if config:
                print(f"Current camera_mode: {config.value}")
                config.value = 'canon'
            else:
                config = Config(key='camera_mode', value='canon')
                db.session.add(config)
            
            db.session.commit()
            print("Successfully updated camera_mode to 'canon'")
        except Exception as e:
            print(f"Error updating config: {e}")
            db.session.rollback()

if __name__ == "__main__":
    update_canon_mode()
