
import sqlite3
import os

# Use absolute path (Python handles forward slashes on Windows fine, but raw string is safer)
DB_PATH = r"d:\ptb\dev_ptb\dev_be\instance\photobooth_v2.db"

def fix_schema():
    print(f"Checking database at: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("ERROR: Database file does not exist at specified path.")
        # List dir to see what's there
        print(f"Contents of {os.path.dirname(DB_PATH)}:")
        try:
            print(os.listdir(os.path.dirname(DB_PATH)))
        except Exception as e:
            print(f"Cannot list dir: {e}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check 'frames' table
        print("Checking 'frames' table schema...")
        cursor.execute("PRAGMA table_info(frames)")
        columns_info = cursor.fetchall()
        columns = [info[1] for info in columns_info]
        
        if 'icon_path' not in columns:
            print("Column 'icon_path' is MISSING in frames. Attempting to add...")
            cursor.execute("ALTER TABLE frames ADD COLUMN icon_path VARCHAR(255)")
            print("SUCCESS: 'icon_path' column added.")
        
        # Check 'payment_codes' table
        print("Checking 'payment_codes' table schema...")
        cursor.execute("PRAGMA table_info(payment_codes)")
        columns_info = cursor.fetchall()
        # If table doesn't exist, this might be empty/fail? No, just empty list.
        if not columns_info:
            print("Table 'payment_codes' likely missing or empty info.")
        else:
            columns = [info[1] for info in columns_info]
            print(f"Existing columns in payment_codes: {columns}")

            if 'used_at' not in columns:
                print("Column 'used_at' is MISSING in payment_codes. Adding...")
                cursor.execute("ALTER TABLE payment_codes ADD COLUMN used_at DATETIME")
                print("SUCCESS: 'used_at' column added.")

            if 'expires_at' not in columns:
                print("Column 'expires_at' is MISSING in payment_codes. Adding...")
                cursor.execute("ALTER TABLE payment_codes ADD COLUMN expires_at DATETIME")
                print("SUCCESS: 'expires_at' column added.")

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"CRITICAL ERROR during DB operation: {e}")

if __name__ == '__main__':
    fix_schema()
