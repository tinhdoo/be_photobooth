import sqlite3
import json
import os

DB_PATH = 'd:/ptb/dev_ptb/dev_be/photobooth_v2.db'

def check_db():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: DB not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Check Schema
    print("--- SCHEMA CHECK ---")
    cursor.execute("PRAGMA table_info(sessions)")
    columns = cursor.fetchall()
    has_meta = False
    for col in columns:
        print(f"Col: {col[1]} ({col[2]})")
        if col[1] == 'meta_data':
            has_meta = True
            
    if not has_meta:
        print("❌ 'meta_data' COLUMN MISSING!")
    else:
        print("✅ 'meta_data' COLUMN EXISTS.")
        
    # 2. Check Latest Session
    print("\n--- LATEST SESSION DATA ---")
    try:
        cursor.execute("SELECT id, created_at, meta_data FROM sessions ORDER BY created_at DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            print(f"ID: {row[0]}")
            print(f"Created: {row[1]}")
            print(f"Meta Data (Raw): {row[2]}")
            if row[2]:
                try:
                    data = json.loads(row[2])
                    print(f"Meta Data (Parsed): {json.dumps(data, indent=2)}")
                except:
                    print("Meta Data is not valid JSON string (might be empty string or blob?)")
        else:
            print("No sessions found.")
    except Exception as e:
        print(f"Error querying session: {e}")

    conn.close()

if __name__ == "__main__":
    check_db()
