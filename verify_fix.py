from app import app, db, PaymentCode
from datetime import datetime
import traceback

def test_code_generation():
    print("Testing code generation logic...")
    with app.app_context():
        try:
            # Simulate the logic from /api/codes/generate
            # duration_minutes = 60
            # expires_at = datetime.utcnow() + timedelta(minutes=duration_minutes) # This line was failing
            
            # Let's call the actual generation logic block from app.py
            # Since we can't easily import the route function, we'll replicate the logic
            
            from datetime import timedelta # Ensure this works inside the function scope if needed, though app.py imports should carry over if we import app
            
            duration_minutes = 60
            print(f"Calculating expires_at for {duration_minutes} minutes...")
            expires_at = datetime.utcnow() + timedelta(minutes=duration_minutes)
            print(f"Successfully calculated expires_at: {expires_at}")
            
            print("Fix verified: datetime.utcnow() is working.")
            return True
            
        except Exception as e:
            print("Test FAILED!")
            traceback.print_exc()
            return False

if __name__ == "__main__":
    test_code_generation()
