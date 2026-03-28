import sqlite3
import os

def migrate():
    db_path = r'd:\ptb\dev_ptb\dev_be\instance\photobooth_v2.db'
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Columns to add to sessions
    session_cols = [
        ('composite_public_id', 'TEXT'),
        ('gif_public_id', 'TEXT')
    ]

    for col_name, col_type in session_cols:
        try:
            cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_type}")
            print(f"Added column {col_name} to sessions table.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"Column {col_name} already exists in sessions.")
            else:
                print(f"Error adding {col_name} to sessions: {e}")

    # Columns to add to photos
    photo_cols = [
        ('public_id', 'TEXT')
    ]

    for col_name, col_type in photo_cols:
        try:
            cursor.execute(f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}")
            print(f"Added column {col_name} to photos table.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"Column {col_name} already exists in photos.")
            else:
                print(f"Error adding {col_name} to photos: {e}")

    conn.commit()
    conn.close()
    print("Migration finished.")

if __name__ == "__main__":
    migrate()
