import sys
import traceback

with open("import_debug.log", "w") as f:
    f.write("DEBUG: Starting import test...\n")
    try:
        import app
        f.write("DEBUG: Import successful!\n")
    except Exception:
        f.write("DEBUG: Import FAILED!\n")
        traceback.print_exc(file=f)
    except SystemExit as e:
        f.write(f"DEBUG: SystemExit: {e}\n")
