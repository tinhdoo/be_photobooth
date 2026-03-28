import sqlite3
import os

db_path = 'instance/photobooth_v2.db'
output_path = 'db_schema_instance.txt'

with open(output_path, 'w') as f:
    f.write(f"Checking {db_path}\n")
    if not os.path.exists(db_path):
        f.write("DB file does not exist.\n")
    else:
        size = os.path.getsize(db_path)
        f.write(f"DB file size: {size} bytes\n")
        
        if size > 0:
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                
                f.write("\nTable: sessions\n")
                cursor.execute("PRAGMA table_info(sessions)")
                columns = cursor.fetchall()
                for col in columns:
                    f.write(f"{col}\n")
                    
                conn.close()
            except Exception as e:
                f.write(f"Error reading DB: {e}\n")
        else:
            f.write("DB file is empty.\n")
