from app import app, db
from models import PaymentCode

with app.app_context():
    print("Creating database tables...")
    try:
        db.create_all()
        print("Tables created successfully.")
        
        # Verify if table exists by querying
        try:
            count = PaymentCode.query.count()
            print(f"PaymentCode table exists. Current count: {count}")
        except Exception as e:
            print(f"Error querying PaymentCode: {e}")
            
    except Exception as e:
        print(f"Error creating tables: {e}")
