from flask import Flask
import os

app = Flask(__name__)
# Simulate app config
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///photobooth_v2.db'

with open('paths_result.txt', 'w', encoding='utf-8') as f:
    f.write(f"Root path: {app.root_path}\n")
    f.write(f"Instance path: {app.instance_path}\n")
    f.write(f"DB URI: {app.config['SQLALCHEMY_DATABASE_URI']}\n")

    # Check if file exists in root
    root_db = os.path.join(app.root_path, 'photobooth_v2.db')
    f.write(f"Root DB exists: {os.path.exists(root_db)}\n")
    if os.path.exists(root_db):
        f.write(f"Root DB size: {os.path.getsize(root_db)}\n")

    # Check if file exists in instance
    instance_db = os.path.join(app.instance_path, 'photobooth_v2.db')
    f.write(f"Instance DB exists: {os.path.exists(instance_db)}\n")
    if os.path.exists(instance_db):
        f.write(f"Instance DB size: {os.path.getsize(instance_db)}\n")

    # Check relative to cwd
    cwd_db = os.path.join(os.getcwd(), 'photobooth_v2.db')
    f.write(f"CWD: {os.getcwd()}\n")
    f.write(f"CWD DB exists: {os.path.exists(cwd_db)}\n")
    if os.path.exists(cwd_db):
        f.write(f"CWD DB size: {os.path.getsize(cwd_db)}\n")
