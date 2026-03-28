import sqlite3
import os

DB_PATH = 'd:/ptb/dev_ptb/dev_be/instance/photobooth_v2.db'
LOG_FILE = 'd:/ptb/dev_ptb/dev_be/db_fix_video.txt'

def log(msg):
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(str(msg) + "\n")
    except:
        pass

if os.path.exists(LOG_FILE):
    try:
        os.remove(LOG_FILE)
    except:
        pass

log("Starting FIX (video_url) on " + DB_PATH)

if not os.path.exists(DB_PATH):
    log("ERROR: DB not found!")
else:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if exists
        cursor.execute("PRAGMA table_info(photos)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'video_url' in columns:
            log("Column video_url ALREADY EXISTS.")
        else:
            log("Adding video_url column...")
            cursor.execute("ALTER TABLE photos ADD COLUMN video_url VARCHAR(500)")
            conn.commit()
            log("SUCCESS: Column added.")
            
        conn.close()
    except Exception as e:
        log(f"EXCEPTION: {e}")

log("Done.")
