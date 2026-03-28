from app import app, db
from models import Session, Photo

def clear_data():
    with app.app_context():
        try:
            # Delete all photos first (foreign key dependency)
            num_photos = db.session.query(Photo).delete()
            # Delete all sessions
            num_sessions = db.session.query(Session).delete()
            
            db.session.commit()
            print(f"SUCCESS: Deleted {num_sessions} sessions and {num_photos} photos from DB.")

            # Delete files
            import os
            import shutil
            
            headers = ["uploads/enhanced", "uploads/temp", "captured_images"]
            for folder in headers:
                folder_path = os.path.join(os.getcwd(), folder)
                if os.path.exists(folder_path):
                    for filename in os.listdir(folder_path):
                        file_path = os.path.join(folder_path, filename)
                        try:
                            if os.path.isfile(file_path) or os.path.islink(file_path):
                                os.unlink(file_path)
                            elif os.path.isdir(file_path):
                                shutil.rmtree(file_path)
                        except Exception as e:
                            print(f"Failed to delete {file_path}. Reason: {e}")
            
            print("Files in uploads/enhanced, uploads/temp, and captured_images have been cleared.")
            print("Revenue dashboard should now be empty.")
        except Exception as e:
            db.session.rollback()
            print(f"ERROR: Failed to clear data. {e}")

if __name__ == "__main__":
    confirm = input("Are you sure you want to delete ALL Revenue/Session history? (y/n): ")
    if confirm.lower() == 'y':
        clear_data()
    else:
        print("Operation cancelled.")
