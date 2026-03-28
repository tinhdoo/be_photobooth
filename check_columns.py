import sqlite3
import os

DB_PATH = r"d:\ptb\dev_ptb\dev_be\instance\photobooth_v2.db"
OUTPUT_FILE = "check.txt"

def check():
    with open(OUTPUT_FILE, "w") as f:
        f.write(f"Checking DB: {DB_PATH}\n")
        if not os.path.exists(DB_PATH):
            f.write("DB File NOT FOUND\n")
            return

        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("PRAGMA table_info(payment_codes)")
            columns_info = cursor.fetchall()
            
            if not columns_info:
                f.write("Table 'payment_codes' NOT FOUND or access error\n")
            else:
                columns = [info[1] for info in columns_info]
                f.write(f"Columns in payment_codes: {columns}\n")
                
                if 'used_at' in columns:
                    f.write("used_at: PRESENT\n")
                else:
                    f.write("used_at: MISSING\n")
                    
                if 'expires_at' in columns:
                    f.write("expires_at: PRESENT\n")
                else:
                    f.write("expires_at: MISSING\n")

            conn.close()
        except Exception as e:
            f.write(f"Error: {e}\n")

if __name__ == "__main__":
    check()
