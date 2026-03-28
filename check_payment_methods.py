from app import app, db, Session
from sqlalchemy import text

with app.app_context():
    print("Checking distinct payment methods in sessions table...")
    # SQL way
    # result = db.session.execute(text("SELECT DISTINCT payment_method FROM sessions"))
    # for row in result:
    #     print(f"DB Value: '{row[0]}'")
    
    # ORM way
    methods = db.session.query(Session.payment_method).distinct().all()
    for m in methods:
        print(f"DB Value: '{m[0]}'")
