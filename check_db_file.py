import sqlite3
import json
import os

DB_PATH = 'd:/ptb/dev_ptb/dev_be/instance/photobooth_v2.db'
LOG_FILE = 'd:/ptb/dev_ptb/dev_be/db_status.txt'

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

log("Starting check...")

if not os.path.exists(DB_PATH):
    log(f"ERROR: DB not found at {DB_PATH}")
else:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        log("--- SCHEMA ---")
        cursor.execute("PRAGMA table_info(sessions)")
        columns = cursor.fetchall()
        has_meta = False
        for col in columns:
            log(f"Col: {col[1]} ({col[2]})")
            if col[1] == 'meta_data':
                has_meta = True
        
        if has_meta:
            log("meta_data column FOUND.")
        else:
            log("meta_data column MISSING.")
            
        log("--- LATEST SESSION ---")
        cursor.execute("SELECT id, created_at, meta_data FROM sessions ORDER BY created_at DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            log(f"ID: {row[0]}")
            log(f"Created: {row[1]}")
            log(f"Meta: {row[2]}")
        else:
            log("No sessions found")
            
        conn.close()
    except Exception as e:
        log(f"EXCEPTION: {e}")

log("Done.")
