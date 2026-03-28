import os
files = [
    "debug_result.txt", "debug_result_expired.txt", 
    "test_print.py", "inspect_db_file.py", "inspect_db_file_v2.py", 
    "inspect_db.py", "output.txt", "test_output.txt", "force_cleanup.py",
    "inspect_db_file.py" # check if I missed any
]
for f in files:
    if os.path.exists(f):
        try:
            os.remove(f)
            print(f"Deleted {f}")
        except Exception as e:
            print(f"Error deleting {f}: {e}")
