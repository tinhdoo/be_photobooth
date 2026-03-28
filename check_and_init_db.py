from app import app, db
from models import PaymentCode
import sys

log_file = "db_log.txt"

def log(msg):
    with open(log_file, "a") as f:
        f.write(msg + "\n")
    print(msg)

with app.app_context():
    log("Starting DB check...")
    try:
        db.create_all()
        log("db.create_all() executed.")
        
        try:
            count = PaymentCode.query.count()
            log(f"PaymentCode table exists. Row count: {count}")
        except Exception as e:
            log(f"Error querying PaymentCode: {e}")
            
    except Exception as e:
        log(f"Error during DB operations: {e}")
