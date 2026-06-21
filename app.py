# PHẢI là thứ chạy đầu tiên tuyệt đối. Khi đóng gói PyInstaller, multiprocessing dùng
# 'spawn' trên Windows -> tiến trình con chạy lại chính exe từ đầu. Không có freeze_support()
# thì con sẽ re-exec toàn bộ phần khởi động (mở COM1, load model...) và không bao giờ tới
# socketio.run -> port 5000 không mở, app kẹt trong vòng lặp tự khởi động lại.
import multiprocessing
multiprocessing.freeze_support()

import eventlet
eventlet.monkey_patch()

# Console Windows mặc định cp1252 không encode được tiếng Việt -> mọi print có dấu sẽ
# crash (UnicodeEncodeError). Đặt errors='replace' để in an toàn, không bao giờ vỡ luồng.
import sys as _sys
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except Exception:
        pass

import logging
logging.basicConfig(filename='app.log', level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logging.info("APP: STARTING UP...")

print("APP: Importing libs...", flush=True)
from flask import Flask, request, jsonify, send_from_directory, send_file
import os
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.getcwd(), '.env'))
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass
# ... (rest of imports)

# ... (inside __main__)

from flask_socketio import SocketIO
from flask_cors import CORS
print("APP: Libs imported", flush=True)
import datetime
from datetime import timezone
UTC = timezone.utc
import uuid
import json
import cv2
import hmac
import urllib.request
from urllib.parse import urlencode, urlparse, unquote
from werkzeug.utils import secure_filename
import pillow_heif
pillow_heif.register_heif_opener()
from PIL import Image, ImageOps
from models import db, Session, Photo, Frame, PaymentCode, PaymentOrder, Device, Config, DeviceConfig, MobileUpload, BillCashEntry, PrintJob
import random
import string
import cloudinary
import cloudinary.uploader
from cloudinary.utils import cloudinary_url
from sqlalchemy import func
import shutil
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
# Import cleanup manager (will be created next)
from services.cleanup_manager import cleanup_expired_sessions, cleanup_old_local_upload_files, cleanup_old_payment_codes, expire_session_if_needed
from services.print_service import create_test_print_image, get_available_printers, get_printer_status, print_image_file, resolve_printer_name, save_print_image
from services.supabase_storage import is_supabase_configured, upload_file_to_supabase, upload_path_to_supabase

# Khởi tạo App
print("APP: Initializing Flask...", flush=True)
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-only-change-me')
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads', 'frames')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///photobooth_v2.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 # Allow larger branding/background videos

cloudinary.config( 
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key = os.environ.get("CLOUDINARY_API_KEY"),
    api_secret = os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True
)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_frontend_dist_dir():
    candidates = [
        os.environ.get('FRONTEND_DIST_DIR'),
        os.path.join(os.getcwd(), 'dist'),
        os.path.join(os.path.dirname(__file__), 'dist'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'dev_fe', 'photobooth_fe', 'dist'),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(os.path.join(candidate, 'index.html')):
            return os.path.abspath(candidate)
    return None

db.init_app(app)
# Initialize SocketIO correctly
# Default to eventlet (we monkey_patch eventlet at the top); the bundled exe would
# otherwise auto-fall back to the threading/Werkzeug server, which refuses to run.
socketio_async_mode = os.environ.get("SOCKETIO_ASYNC_MODE") or "eventlet"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=socketio_async_mode)

@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({'error': 'File quá lớn. Vui lòng chọn file dưới 100MB.'}), 413

# Initialize Scheduler
def run_with_context(fn):
    """Wrapper to run a function inside Flask app context (required for background threads)."""
    def wrapper():
        with app.app_context():
            fn()
    return wrapper

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(func=run_with_context(cleanup_expired_sessions), trigger="interval", hours=1, id="cleanup_sessions", replace_existing=True)
scheduler.add_job(func=run_with_context(cleanup_old_local_upload_files), trigger="interval", hours=1, id="cleanup_local_uploads", replace_existing=True)
scheduler.add_job(func=run_with_context(cleanup_old_payment_codes), trigger="interval", hours=1, id="cleanup_codes", replace_existing=True)
scheduler.start()

# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())

def migrate_data():
    print("APP: migrate_data start", flush=True)
    """Migrate existing file-based frames to Database"""
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        return

    print("Checking for data migration...", flush=True)
    layouts = os.listdir(app.config['UPLOAD_FOLDER'])
    count = 0
    
    for layout in layouts:
        layout_path = os.path.join(app.config['UPLOAD_FOLDER'], layout)
        if not os.path.isdir(layout_path):
            continue
            
        for filename in os.listdir(layout_path):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                # Check if exists in DB - handle potential schema errors gracefully
                exists = None
                try:
                    exists = Frame.query.filter_by(layout=layout, name=filename).first()
                except Exception as e:
                    print(f"Error checking frame existence (schema mismatch?): {e}", flush=True)
                    # If we can't query, we can't migrate properly without risking duplicates or errors.
                    # But if we assume it's a schema error, maybe we should skip or retry? 
                    # For now, let's log and continue to avoid crashing the whole app.
                    continue
                
                if not exists:
                    print(f"Migrating {filename} ({layout})...", flush=True)
                    
                    # Load config if exists
                    config = {}
                    config_path = os.path.join(layout_path, f"{filename}.json")
                    if os.path.exists(config_path):
                        try:
                            with open(config_path, 'r') as f:
                                config = json.load(f)
                        except:
                            pass
                            
                    new_frame = Frame(
                        layout=layout,
                        name=filename,
                        image_path=filename, # Just the filename, path constructed relative to layout
                        config=config
                    )
                    db.session.add(new_frame)
                    count += 1
    
    if count > 0:
        db.session.commit()
        print(f"Migrated {count} frames to Database.", flush=True)
    else:
        print("Database is up to date.", flush=True)
    print("APP: migrate_data end", flush=True)

def ensure_runtime_schema():
    """Add lightweight SQLite columns that db.create_all cannot add to existing tables."""
    if db.engine.dialect.name != 'sqlite':
        return

    columns_to_add = {
        'photos': {
            'video_public_id': 'VARCHAR(100)'
        },
        'mobile_uploads': {
            'slot_index': 'INTEGER'
        }
    }

    with db.engine.begin() as conn:
        for table, columns in columns_to_add.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
            for column, column_type in columns.items():
                if column not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                    logging.info(f"DB migration: added {table}.{column}")



@app.route('/')
def index():
    dist_dir = get_frontend_dist_dir()
    if dist_dir:
        return send_from_directory(dist_dir, 'index.html')
    return "Photobooth Backend Running (with SQLite)"

@app.route('/uploads/<path:filename>')
def serve_uploads(filename):
    return send_from_directory(os.path.join(os.getcwd(), 'uploads'), filename)

# --- Frame Management APIs ---

# --- Frame Management APIs ---





# --- Frame Management APIs ---

@app.route('/api/frames', methods=['GET'])
def get_frames():
    layout = request.args.get('layout')
    query = Frame.query
    if layout:
        query = query.filter_by(layout=layout)
    frames = query.all()
    
    return jsonify([{
        'id': f.name, # Keep using filename as ID for frontend compatibility
        'db_id': f.id,
        'name': f.name,
        'layout': f.layout,
        'url': f"http://localhost:5000/uploads/frames/{f.layout}/{f.image_path}",
        'icon_url': f"http://localhost:5000/uploads/frames/icons/{f.layout}/{f.icon_path}" if f.icon_path else None
    } for f in frames])

from services.image_processor import get_processor
import uuid

@app.route('/api/enhance', methods=['POST'])
def enhance_photo():
    # Handle file upload (Multipart)
    if 'photo' in request.files:
        file = request.files['photo']
        filter_type = request.form.get('filter_type', 'natural')
        
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
            
        try:
            # 1. Save input to temp
            temp_filename = f"temp_{uuid.uuid4().hex}.jpg"
            temp_path = os.path.join("uploads", "temp", temp_filename)
            os.makedirs(os.path.join("uploads", "temp"), exist_ok=True)
            file.save(temp_path)
            
            # 2. Process
            output_filename = f"enhanced_{uuid.uuid4().hex}.jpg"
            output_path = os.path.join("uploads", "enhanced", output_filename)
            os.makedirs(os.path.join("uploads", "enhanced"), exist_ok=True)
            
            success, result_or_error = get_processor().process(temp_path, output_path, filter_type=filter_type)
            
            # Cleanup input
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
            if success:
                # 3. Optimize Speed: Serve locally
                local_url = f"http://localhost:5000/uploads/enhanced/{output_filename}"
                return jsonify({'url': local_url})
            else:
                return jsonify({'error': result_or_error}), 500

        except Exception as e:
            import traceback
            logging.error(f"Enhance upload error: {e}")
            logging.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    return jsonify({'error': 'No photo uploaded'}), 400
@app.route('/api/frames', methods=['POST'])
def upload_frame():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    layout = request.form.get('layout')
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if not layout:
        return jsonify({'error': 'No layout specified'}), 400

    if file:
        filename = secure_filename(file.filename)

        # Check for duplicate
        existing = Frame.query.filter_by(layout=layout, name=filename).first()
        if existing:
            return jsonify({'error': f'Frame "{filename}" already exists in layout "{layout}"'}), 409
        
        layout_path = os.path.join(app.config['UPLOAD_FOLDER'], layout)
        os.makedirs(layout_path, exist_ok=True)
        
        file.save(os.path.join(layout_path, filename))
        
        # Add to DB
        new_frame = Frame(
            layout=layout,
            name=filename,
            image_path=filename,
            config={'borderRadius': 0, 'boxes': []} # Default config
        )
        db.session.add(new_frame)
        db.session.commit()
        
        return jsonify({'message': 'File uploaded successfully', 'filename': filename, 'layout': layout}), 201

@app.route('/api/frames/<layout>/<filename>/config', methods=['GET'])
def get_frame_config(layout, filename):
    frame = Frame.query.filter_by(layout=layout, name=filename).first()
    
    if frame and frame.config:
        return jsonify(frame.config)
        
    # Default config
    return jsonify({
        'borderRadius': 0, 
        'boxes': []
    })

@app.route('/api/frames/<layout>/<filename>/config', methods=['POST'])
def save_frame_config(layout, filename):
    frame = Frame.query.filter_by(layout=layout, name=filename).first()
    
    if frame:
        frame.config = request.json
        db.session.commit()
        
        # Sync to file for backup
        try:
            config_path = os.path.join(app.config['UPLOAD_FOLDER'], layout, f"{filename}.json")
            with open(config_path, 'w') as f:
                json.dump(request.json, f)
        except:
            pass
            
        return jsonify({'message': 'Config saved successfully'}), 200
        
    return jsonify({'error': 'Frame not found'}), 404

@app.route('/api/frames/<layout>/<filename>/icon', methods=['POST'])
def upload_frame_icon(layout, filename):
    frame = Frame.query.filter_by(layout=layout, name=filename).first()
    
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404
        
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file:
        original_filename = secure_filename(file.filename)
        # Use frame name as prefix to keep it organized or just unique
        # Icon name: icon_<frame_uuid>_<timestamp>.png
        unique_icon_name = f"icon_{uuid.uuid4().hex}_{original_filename}"
        
        # Path: uploads/icons/<layout>/
        icon_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'icons', layout)
        os.makedirs(icon_folder, exist_ok=True)
        
        file.save(os.path.join(icon_folder, unique_icon_name))
        
        # Remove old icon if exists? (Optional, let's keep it simple for now)
        if frame.icon_path:
             old_icon_path = os.path.join(app.config['UPLOAD_FOLDER'], 'icons', layout, frame.icon_path)
             if os.path.exists(old_icon_path):
                 try:
                     os.remove(old_icon_path)
                 except:
                     pass

        frame.icon_path = unique_icon_name
        db.session.commit()
        
        return jsonify({'message': 'Icon uploaded successfully', 'icon_url': f"http://localhost:5000/uploads/frames/icons/{layout}/{unique_icon_name}"}), 200

@app.route('/api/frames/<layout>/<filename>', methods=['DELETE'])
def delete_frame(layout, filename):
    frame = Frame.query.filter_by(layout=layout, name=filename).first()
    
    if frame:
        # Delete file
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], layout, filename)
        config_path = os.path.join(app.config['UPLOAD_FOLDER'], layout, f"{filename}.json")
        icon_path = os.path.join(app.config['UPLOAD_FOLDER'], 'icons', layout, frame.icon_path) if frame.icon_path else None
        
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(config_path):
            os.remove(config_path)
        if icon_path and os.path.exists(icon_path):
            os.remove(icon_path)
            
        db.session.delete(frame)
        db.session.commit()
        return jsonify({'message': 'Frame deleted successfully'}), 200
        
    return jsonify({'error': 'Frame not found'}), 404

@app.route('/uploads/frames/<path:filename>')
def serve_frame(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/uploads/frames/icons/<layout>/<path:filename>')
def serve_frame_icon(layout, filename):
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], 'icons', layout), filename)

# Serve generated QR codes or other assets if needed
# ...

# --- Session Management APIs ---
@app.route('/api/sessions', methods=['POST'])
def create_session():
    data = request.json
    layout_id = data.get('layout_id')
    photos_data = data.get('photos', []) # List of { url: '', type: 'raw' }
    composite_url = data.get('composite_url')
    composite_public_id = data.get('composite_public_id')
    gif_url = data.get('gif_url') # Future proofing
    gif_public_id = data.get('gif_public_id')
    payment_method = data.get('payment_method')
    amount = data.get('amount', 0)
    meta_data = data.get('meta_data', {})
    
    # Use provided session_id (which is a UUID from frontend QR code) or let DB generate
    session_uuid = data.get('session_id')
    
    # Create Session
    new_session = Session(
        uuid=session_uuid if session_uuid else uuid.uuid4().hex,
        layout_id=layout_id,
        status='completed',
        composite_url=composite_url,
        composite_public_id=composite_public_id,
        gif_url=gif_url,
        gif_public_id=gif_public_id,
        payment_method=payment_method,
        amount=amount,
        meta_data=meta_data,
        created_at=datetime.datetime.now(UTC)
    )
    db.session.add(new_session)
    db.session.flush() # Get ID
    
    # Add Photos
    for p in photos_data:
        new_photo = Photo(
            session_id=new_session.id,
            file_path=p.get('url'), # Using URL as file_path for cloud storage
            url=p.get('url'),
            video_url=p.get('video_url'),
            public_id=p.get('public_id'),
            video_public_id=p.get('video_public_id'),
            type=p.get('type', 'raw'),
            created_at=datetime.datetime.now(UTC)
        )
        db.session.add(new_photo)
        
    db.session.commit()
    return jsonify(new_session.to_dict()), 201

@app.route('/api/sessions/<id>', methods=['GET'])
def get_session(id):
    if str(id).isdigit():
        session = db.session.get(Session, int(id))
    else:
        session = Session.query.filter_by(uuid=str(id)).first()
        
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    expire_session_if_needed(session)
        
    return jsonify(session.to_dict())

@app.route('/api/sessions', methods=['GET'])
def get_all_sessions():
    sessions = Session.query.order_by(Session.created_at.desc()).all()
    return jsonify([s.to_dict() for s in sessions])

# --- Cloud & Mobile Upload API ---

@app.route('/api/upload/mobile', methods=['POST'])
def upload_mobile():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    session_id = request.form.get('session_id') # Identify which photobooth session this is for
    expected_count = request.form.get('expected_count', '0')
    slot_index = request.form.get('slot_index')
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if not session_id:
        return jsonify({'error': 'No session_id provided'}), 400

    try:
        expected_count = max(1, min(int(expected_count or 0), 12))
    except (TypeError, ValueError):
        expected_count = 0

    try:
        slot_index = int(slot_index) if slot_index is not None else None
    except (TypeError, ValueError):
        slot_index = None

    if slot_index is not None:
        existing_slot = MobileUpload.query.filter_by(session_uuid=session_id, slot_index=slot_index).first()
        if existing_slot:
            return jsonify({
                'success': True,
                'duplicate': True,
                'url': existing_slot.url,
                'public_id': existing_slot.public_id,
                'upload': existing_slot.to_dict()
            }), 200

    if expected_count:
        current_count = MobileUpload.query.filter_by(session_uuid=session_id).count()
        if current_count >= expected_count:
            return jsonify({
                'error': f'Phiên này đã nhận đủ {expected_count} ảnh.',
                'code': 'SESSION_FULL'
            }), 409

    try:
        # Save to temp folder (Processed with Pillow)
        new_filename = f"mobile_{uuid.uuid4().hex}.jpg"
        dest_path = os.path.join(app.config['UPLOAD_FOLDER'], "..", "temp", new_filename)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        
        # Open image with Pillow, handling HEIC automatically via pillow_heif
        img = Image.open(file.stream)
        
        # Fix EXIF orientation (e.g. iPhone photos rotated sideways/upside down)
        img = ImageOps.exif_transpose(img)
        
        # Convert to RGB (in case of HEIC or transparent PNGs)
        img = img.convert("RGB")
        
        # Resize to a max bounding box (e.g., 1800x1800) to save RAM and Kiosk display performance
        img.thumbnail((1600, 1600))
        
        # Save as optimized JPEG
        img.save(dest_path, "JPEG", quality=88, optimize=True)
        
        # URL tương đối để proxy của Vite tự forward tải ảnh đúng IP
        local_url = f"/uploads/temp/{new_filename}"
        final_url = local_url
        public_id = f"local:temp/{new_filename}"

        if is_supabase_configured():
            try:
                uploaded = upload_path_to_supabase(dest_path, folder="mobile")
                final_url = uploaded['url']
                public_id = uploaded['public_id']
            except Exception as upload_error:
                logging.warning(f"Mobile Supabase upload failed; using local temp file: {upload_error}")

        mobile_upload = MobileUpload(session_uuid=session_id, slot_index=slot_index, url=final_url, public_id=public_id)
        db.session.add(mobile_upload)
        db.session.commit()

        socketio.emit('mobile_photo_uploaded', {'session_id': session_id, 'url': final_url, 'slot_index': slot_index})
        
        return jsonify({'success': True, 'url': final_url, 'public_id': public_id, 'upload': mobile_upload.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Mobile upload error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/mobile-uploads/<session_id>', methods=['GET'])
def get_mobile_uploads(session_id):
    uploads = MobileUpload.query.filter_by(session_uuid=session_id).order_by(MobileUpload.slot_index.asc().nullslast(), MobileUpload.created_at.asc()).all()
    return jsonify([item.to_dict() for item in uploads]), 200

@app.route('/api/network/ip', methods=['GET'])
def get_lan_ip():
    import socket
    try:
        # Create a dummy socket to connect to an external IP
        # This forces the OS to figure out the primary LAN IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return jsonify({'ip': ip}), 200
    except Exception as e:
        print(f"Could not determine LAN IP: {e}")
        # Fallback
        return jsonify({'ip': 'localhost'}), 200


@app.route('/api/upload/cloud', methods=['POST'])
@app.route('/api/upload-cloud', methods=['POST'])
def upload_cloud():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    def save_upload_locally():
        original_name = secure_filename(file.filename) or 'upload.bin'
        extension = os.path.splitext(original_name)[1] or '.bin'
        local_name = f"cloud_{uuid.uuid4().hex}{extension}"
        cloud_folder = os.path.join(os.getcwd(), 'uploads', 'cloud')
        os.makedirs(cloud_folder, exist_ok=True)
        try:
            file.stream.seek(0)
        except Exception:
            pass
        file.save(os.path.join(cloud_folder, local_name))
        return {
            'url': f"/uploads/cloud/{local_name}",
            'public_id': f"local:cloud/{local_name}",
            'storage': 'local'
        }

    cloudinary_ready = all([
        os.environ.get("CLOUDINARY_CLOUD_NAME"),
        os.environ.get("CLOUDINARY_API_KEY"),
        os.environ.get("CLOUDINARY_API_SECRET")
    ])

    if is_supabase_configured():
        try:
            supabase_result = upload_file_to_supabase(file, folder="photobooth")
            return jsonify(supabase_result), 200
        except Exception as e:
            logging.warning(f"Supabase upload failed; trying Cloudinary/local fallback: {e}")
            try:
                file.stream.seek(0)
            except Exception:
                pass

    if not cloudinary_ready:
        try:
            local_result = save_upload_locally()
            logging.warning("Cloudinary is not configured. Stored upload locally.")
            return jsonify(local_result), 200
        except Exception as e:
            logging.exception("Local upload fallback failed")
            return jsonify({'error': f'Local upload failed: {str(e)}'}), 500

    try:
        # Upload to Cloudinary
        upload_result = cloudinary.uploader.upload(file, resource_type="auto")
        
        return jsonify({
            'url': upload_result['secure_url'],
            'public_id': upload_result['public_id']
        }), 200
    except Exception as e:
        try:
            local_result = save_upload_locally()
            logging.warning(f"Cloudinary upload failed; stored upload locally instead: {e}")
            return jsonify(local_result), 200
        except Exception as local_error:
            logging.exception("Cloudinary and local upload both failed")
            return jsonify({'error': f'Upload failed: {str(e)}; local fallback failed: {str(local_error)}'}), 500


# --- Local PUT Upload Fallback for Branding ---
@app.route('/api/upload/local-put/<key>/<filename>', methods=['PUT'])
def local_put_upload(key, filename):
    try:
        branding_folder = os.path.join(app.config['UPLOAD_FOLDER'], '..', 'branding')
        os.makedirs(branding_folder, exist_ok=True)
        dest_path = os.path.join(branding_folder, f"branding_{key}-{filename}")
        
        if request.files:
            file = request.files.get('') or list(request.files.values())[0]
            file.save(dest_path)
        else:
            with open(dest_path, 'wb') as f:
                f.write(request.data)
                
        local_url = f"/uploads/branding/branding_{key}-{filename}"
        return jsonify({'success': True, 'url': local_url}), 200
    except Exception as e:
        logging.error(f"Local PUT upload failed: {e}")
        return jsonify({'error': str(e)}), 500


# --- Branding Upload API ---
@app.route('/api/upload/branding', methods=['POST'])
@app.route('/api/upload-branding', methods=['POST'])
def upload_branding():
    # If the request content-type is JSON (the Vercel-style prepare/finalize flow)
    if request.is_json:
        data = request.json
        action = data.get('action')
        key = data.get('key')
        if not key:
            return jsonify({'error': 'Missing config key'}), 400

        import re
        key = re.sub(r'[^a-zA-Z0-9_-]', '', str(key))[:80]

        if action == 'prepare':
            filename = secure_filename(data.get('filename', 'branding'))
            name, ext = os.path.splitext(filename)
            unique_id = uuid.uuid4().hex
            object_path = f"branding/{key}-{unique_id}{ext.lower()}"

            if is_supabase_configured():
                try:
                    from services.supabase_storage import _create_client
                    client = _create_client()
                    bucket = os.environ.get("SUPABASE_BUCKET")
                    res = client.storage.from_(bucket).create_signed_upload_url(object_path, {"expiresIn": 3600})
                    return jsonify({
                        'bucket': bucket,
                        'objectPath': object_path,
                        'signedUrl': res.get('signed_url') or res.get('signedUrl'),
                        'token': res.get('token')
                    }), 200
                except Exception as supabase_err:
                    logging.warning(f"Supabase signed URL creation failed, falling back to local: {supabase_err}")

            signed_url = f"{request.host_url.rstrip('/')}/api/upload/local-put/{key}/{unique_id}{ext.lower()}"
            return jsonify({
                'bucket': 'local',
                'objectPath': object_path,
                'signedUrl': signed_url,
                'token': 'local'
            }), 200

        elif action == 'finalize':
            object_path = data.get('objectPath', '')
            if not object_path or not object_path.startswith(f"branding/{key}-"):
                return jsonify({'error': 'Invalid object path'}), 400

            parts = object_path.split('/')
            filename = parts[-1]
            
            branding_folder = os.path.join(app.config['UPLOAD_FOLDER'], '..', 'branding')
            local_file_name = f"branding_{filename}"
            local_path = os.path.join(branding_folder, local_file_name)
            
            if os.path.exists(local_path):
                url = f"/uploads/branding/{local_file_name}"
            else:
                if is_supabase_configured():
                    bucket = os.environ.get("SUPABASE_BUCKET")
                    url = f"{os.environ.get('SUPABASE_URL').rstrip('/')}/storage/v1/object/public/{bucket}/{object_path}"
                else:
                    return jsonify({'error': 'Uploaded file not found'}), 404

            config = db.session.get(Config, key)
            if config:
                config.value = url
            else:
                db.session.add(Config(key=key, value=url))
            db.session.commit()

            return jsonify({
                'success': True,
                'key': key,
                'url': url
            }), 200

        return jsonify({'error': 'Invalid upload action'}), 400

    # Multipart form-data fallback (original logic)
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    key = request.form.get('key')
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if not key:
        return jsonify({'error': 'No key provided'}), 400

    try:
        filename = secure_filename(file.filename) or 'branding_upload'
        name, ext = os.path.splitext(filename)
        safe_name = name[:60].strip('._-') or 'branding_upload'
        unique_filename = f"branding_{uuid.uuid4().hex}_{safe_name}{ext.lower()}"
        
        branding_folder = os.path.join(app.config['UPLOAD_FOLDER'], '..', 'branding')
        os.makedirs(branding_folder, exist_ok=True)
        
        dest_path = os.path.join(branding_folder, unique_filename)
        file.save(dest_path)
        
        local_url = f"/uploads/branding/{unique_filename}"
        
        config = db.session.get(Config, key)
        if config:
            config.value = local_url
        else:
            db.session.add(Config(key=key, value=local_url))
            
        db.session.commit()
        
        return jsonify({'success': True, 'url': local_url, 'key': key}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Branding upload error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500

# --- Printing API ---
@app.route('/api/printers', methods=['GET'])
def list_printers():
    configured_name = get_config_value('printer_name', '')
    resolved_name, printers = resolve_printer_name(configured_name)
    return jsonify({
        'printers': printers,
        'configured_printer': configured_name,
        'resolved_printer': resolved_name
    }), 200


def check_internet_status():
    import socket
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2):
            return {"online": True, "message": "Online"}
    except Exception as exc:
        return {"online": False, "message": str(exc)}


def check_supabase_status():
    if not is_supabase_configured():
        return {"online": False, "configured": False, "message": "Chưa cấu hình Supabase"}

    try:
        import urllib.request

        url = os.environ.get("SUPABASE_URL", "").rstrip("/") + "/rest/v1/"
        req = urllib.request.Request(
            url,
            headers={
                "apikey": os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY"),
                "Authorization": "Bearer " + (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")),
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=4) as res:
            return {"online": 200 <= res.status < 500, "configured": True, "message": f"HTTP {res.status}"}
    except Exception as exc:
        return {"online": False, "configured": True, "message": str(exc)}


def get_camera_status():
    mode = get_config_value('camera_mode', 'webcam')
    hot_folder = get_config_value('hot_folder', 'C:/Photobooth_Input')

    if mode == 'hotfolder':
        exists = os.path.isdir(hot_folder)
        return {
            "online": exists,
            "mode": mode,
            "name": "Hot folder / EOS Utility",
            "message": "Đã sẵn sàng" if exists else f"Không tìm thấy thư mục {hot_folder}",
            "hot_folder": hot_folder,
            "browser_check_required": False,
        }

    if mode == 'canon':
        return {
            "online": None,
            "mode": mode,
            "name": "Canon middleware",
            "message": "Cần kiểm tra qua middleware Canon trên máy local",
            "browser_check_required": False,
        }

    return {
        "online": None,
        "mode": mode,
        "name": "Webcam / USB camera",
        "message": "Cần kiểm tra quyền camera trên trình duyệt",
        "browser_check_required": True,
    }


@app.route('/api/hardware/status', methods=['GET'])
def hardware_status():
    printer = get_printer_status(get_config_value('printer_name', ''))
    camera = get_camera_status()
    internet = check_internet_status()
    supabase = check_supabase_status()

    checks = {
        "printer": bool(printer.get("online")),
        "camera": camera.get("online") is True,
        "internet": bool(internet.get("online")),
        "supabase": bool(supabase.get("online")),
    }

    return jsonify({
        "ok": all(checks.values()),
        "checks": checks,
        "printer": printer,
        "camera": camera,
        "internet": internet,
        "supabase": supabase,
    }), 200


@app.route('/api/printer/test', methods=['POST'])
def test_printer():
    payload = request.json if request.is_json and request.json else {}
    configured_name = payload.get('printer_name')
    configured_name = configured_name or get_config_value('printer_name', '')
    printer_name, printers = resolve_printer_name(configured_name)

    if not printer_name:
        return jsonify({
            'error': 'Không tìm thấy máy in',
            'available_printers': printers,
            'configured_printer': configured_name
        }), 500

    try:
        print_folder = os.path.join(os.getcwd(), 'uploads', 'print_jobs')
        color_settings = {
            'print_brightness': payload.get('print_brightness', get_config_value('print_brightness', '0')),
            'print_contrast': payload.get('print_contrast', get_config_value('print_contrast', '0')),
            'print_saturation': payload.get('print_saturation', get_config_value('print_saturation', '0')),
            'print_warmth': payload.get('print_warmth', get_config_value('print_warmth', '2')),
        }
        test_path = create_test_print_image(print_folder, color_settings)
        
        scale_x = get_int_config('print_scale_x', 100)
        scale_y = get_int_config('print_scale_y', 100)
        offset_x = get_int_config('print_offset_x', 0)
        offset_y = get_int_config('print_offset_y', 0)

        result = print_image_file(
            test_path, 
            printer_name, 
            1, 
            scale_x=scale_x, 
            scale_y=scale_y, 
            offset_x=offset_x, 
            offset_y=offset_y
        )
        return jsonify({
            'success': True,
            'message': 'Đã gửi lệnh in thử.',
            **result
        }), 200
    except Exception as e:
        logging.exception("Test print failed")
        return jsonify({'error': str(e), 'printer': printer_name}), 500


@app.route('/api/print', methods=['POST'])
def print_photo():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    configured_name = request.form.get('printer_name') or get_config_value('printer_name', '')
    copies = request.form.get('copies', get_config_value('printer_copies', '1'))
    try:
        copies_int = max(1, min(int(copies or 1), 20))
    except (TypeError, ValueError):
        copies_int = 1
    print_mode = request.form.get('print_mode') or 'grid_4x6'
    cut_mode = request.form.get('cut_mode') or 'none'
    session_uuid = request.form.get('session_id') or request.form.get('session_uuid')
    printer_name, printers = resolve_printer_name(configured_name)
    print_job = PrintJob(
        session_uuid=session_uuid,
        printer_name=printer_name or configured_name,
        copies=copies_int,
        print_mode=print_mode,
        cut_mode=cut_mode,
        status='pending'
    )
    db.session.add(print_job)
    db.session.commit()

    if not printer_name:
        print_job.status = 'failed'
        print_job.error_message = 'Printer not found'
        db.session.commit()
        return jsonify({
            'error': 'Không tìm thấy máy in RX1HS trong Windows. Hãy cài driver DNP RX1HS và đặt tên printer có chứa RX1HS/DNP/RX1, hoặc cấu hình đúng printer_name.',
            'available_printers': printers,
            'configured_printer': configured_name,
            'job': print_job.to_dict()
        }), 500

    try:
        print_folder = os.path.join(os.getcwd(), 'uploads', 'print_jobs')
        saved_path = save_print_image(file, print_folder, sharpen=get_int_config('print_sharpen', 70))
        print_job.file_path = saved_path
        print_job.printer_name = printer_name
        db.session.commit()

        scale_x = get_int_config('print_scale_x', 100)
        scale_y = get_int_config('print_scale_y', 100)
        offset_x = get_int_config('print_offset_x', 0)
        offset_y = get_int_config('print_offset_y', 0)

        result = print_image_file(
            saved_path, 
            printer_name, 
            copies_int, 
            cut_mode=cut_mode,
            scale_x=scale_x,
            scale_y=scale_y,
            offset_x=offset_x,
            offset_y=offset_y
        )
        print_job.status = 'sent'
        db.session.commit()
        return jsonify({
            'success': True,
            'message': 'Đã gửi ảnh sang máy in.',
            'job': print_job.to_dict(),
            **result
        }), 200
    except Exception as e:
        logging.exception("Print job failed")
        print_job.status = 'failed'
        print_job.error_message = str(e)[:500]
        db.session.commit()
        return jsonify({'error': str(e), 'printer': printer_name, 'job': print_job.to_dict()}), 500


@app.route('/api/print/jobs', methods=['GET'])
def get_print_jobs():
    limit = min(int(request.args.get('limit', 50) or 50), 200)
    jobs = PrintJob.query.order_by(PrintJob.created_at.desc()).limit(limit).all()
    return jsonify([job.to_dict() for job in jobs])


def _session_display_id(session):
    created = session.created_at or datetime.datetime.now(UTC)
    return f"S{created.strftime('%y%m%d')}-{session.id:03d}"


def _latest_print_job(session_uuid):
    return PrintJob.query.filter_by(session_uuid=session_uuid).order_by(PrintJob.created_at.desc()).first()


def _resolve_print_source_job(identifier):
    """Tìm PrintJob local có file in theo session_uuid (trường hợp thường), nếu không có
    thì thử theo chính uuid của PrintJob (job lẻ không gắn session_uuid)."""
    job = (PrintJob.query
           .filter(PrintJob.session_uuid == identifier, PrintJob.file_path.isnot(None))
           .order_by(PrintJob.created_at.desc()).first())
    if not job:
        job = (PrintJob.query
               .filter(PrintJob.uuid == identifier, PrintJob.file_path.isnot(None))
               .order_by(PrintJob.created_at.desc()).first())
    return job


def _local_upload_path_from_url(url):
    if not url:
        return None

    parsed = urlparse(url)
    path = unquote(parsed.path if parsed.scheme else url)
    if not path.startswith('/uploads/'):
        return None

    candidate = os.path.abspath(os.path.join(os.getcwd(), path.lstrip('/').replace('/', os.sep)))
    upload_root = os.path.abspath(os.path.join(os.getcwd(), 'uploads'))
    if candidate.startswith(upload_root + os.sep) and os.path.exists(candidate):
        return candidate
    return None


def _materialize_session_print_file(session):
    if not session.composite_url:
        return None

    print_folder = os.path.join(os.getcwd(), 'uploads', 'print_jobs')
    os.makedirs(print_folder, exist_ok=True)

    local_source = _local_upload_path_from_url(session.composite_url)
    if local_source:
        image = Image.open(local_source)
    else:
        request_url = session.composite_url
        if request_url.startswith('//'):
            request_url = f"https:{request_url}"
        if not urlparse(request_url).scheme:
            raise RuntimeError('Session image URL is not valid for restore.')

        req = urllib.request.Request(request_url, headers={'User-Agent': 'TomatoPhotobooth/1.0'})
        with urllib.request.urlopen(req, timeout=20) as response:
            image = Image.open(response)
            image.load()

    image = ImageOps.exif_transpose(image).convert('RGB')
    filename = secure_filename(f"reprint_{session.uuid}_{uuid.uuid4().hex[:8]}.jpg")
    path = os.path.join(print_folder, filename)
    image.save(path, 'JPEG', quality=95, subsampling=0)
    return path


def _session_staff_dict(session):
    latest_job = _latest_print_job(session.uuid)
    meta = session.meta_data or {}
    final_path = latest_job.file_path if latest_job else None
    has_local_print_file = bool(final_path and os.path.exists(final_path))
    can_reprint = has_local_print_file or bool(session.composite_url)
    return {
        'id': session.id,
        'uuid': session.uuid,
        'sessionId': _session_display_id(session),
        'layout': session.layout_id,
        'amount': session.amount or 0,
        'paymentMethod': session.payment_method,
        'paymentStatus': 'paid' if session.payment_method else 'unknown',
        'printStatus': latest_job.status if latest_job else 'not_printed',
        'printError': latest_job.error_message if latest_job else None,
        'copies': latest_job.copies if latest_job else meta.get('printer_copies') or meta.get('print_quantity') or 1,
        'printMode': latest_job.print_mode if latest_job else meta.get('print_mode'),
        'cutMode': latest_job.cut_mode if latest_job else meta.get('cut_mode'),
        'finalImageUrl': session.composite_url,
        'finalImagePath': final_path,
        'previewUrl': session.composite_url or (f"/api/staff/sessions/{session.uuid}/print-image" if has_local_print_file else None),
        'canReprint': can_reprint,
        'createdAt': session.created_at.isoformat() if session.created_at else None,
    }


def _printjob_staff_dict(latest, file_job=None):
    """latest = job mới nhất của phiên (lấy trạng thái/thông số). file_job = job mới nhất
    CÓ file in của phiên đó (để in lại / preview); có thể khác latest nếu lần in cuối lỗi."""
    has_file = file_job is not None
    key = latest.session_uuid or latest.uuid  # khóa dùng cho in lại / xem preview
    created = latest.created_at or datetime.datetime.now(UTC)
    return {
        'id': latest.id,
        'uuid': key,
        'sessionId': f"S{created.strftime('%y%m%d')}-{latest.id:03d}",
        'layout': latest.print_mode,
        'amount': 0,
        'paymentMethod': None,
        'paymentStatus': 'unknown',
        'printStatus': latest.status or 'not_printed',
        'printError': latest.error_message,
        'copies': (file_job or latest).copies or 1,
        'printMode': (file_job or latest).print_mode,
        'cutMode': (file_job or latest).cut_mode,
        'finalImageUrl': None,
        'finalImagePath': file_job.file_path if file_job else None,
        'previewUrl': f"/api/staff/sessions/{key}/print-image" if has_file else None,
        'canReprint': has_file,
        'createdAt': created.isoformat(),
    }


@app.route('/api/staff/sessions/recent', methods=['GET'])
def staff_recent_sessions():
    limit = min(max(int(request.args.get('limit', 30) or 30), 1), 100)
    # In lại / In thêm chạy trên máy booth: phiên chụp được lưu lên CLOUD nên DB local
    # KHÔNG có Session. Mỗi lần in tạo 1 PrintJob LOCAL kèm file in + thông số, nên phải
    # đọc lịch sử in từ PrintJob mới đúng nguồn (trước đây query Session -> luôn rỗng).
    # Chỉ lấy job TRONG NGÀY hôm nay (giờ local). created_at lưu UTC -> quy nửa đêm local sang UTC.
    local_midnight = datetime.datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = local_midnight.astimezone(UTC).replace(tzinfo=None)
    jobs = (PrintJob.query
            .filter(PrintJob.created_at >= start_utc)
            .order_by(PrintJob.created_at.desc())
            .all())
    # Gộp theo phiên: giữ job mới nhất (trạng thái) + job mới nhất có file (để in lại).
    order = []
    agg = {}
    for job in jobs:
        key = job.session_uuid or job.uuid
        if key not in agg:
            agg[key] = {'latest': job, 'file_job': None}
            order.append(key)
        if agg[key]['file_job'] is None and job.file_path and os.path.exists(job.file_path):
            agg[key]['file_job'] = job
    items = [_printjob_staff_dict(agg[k]['latest'], agg[k]['file_job']) for k in order[:limit]]
    return jsonify(items)


@app.route('/api/staff/sessions/<session_uuid>/reprint', methods=['POST'])
def staff_reprint_session(session_uuid):
    data = request.get_json(silent=True) or {}
    try:
        copies = max(1, min(int(data.get('copies') or 1), 20))
    except (TypeError, ValueError):
        copies = 1

    # Phiên thường KHÔNG có ở DB local (chỉ lưu trên cloud) -> in lại dựa vào PrintJob local.
    session = Session.query.filter_by(uuid=session_uuid).first()

    source_job = _resolve_print_source_job(session_uuid)

    source_path = source_job.file_path if source_job and source_job.file_path and os.path.exists(source_job.file_path) else None
    if not source_path and session:
        # Không còn file in local nhưng có Session cloud -> tải lại ảnh ghép để dựng file in.
        try:
            source_path = _materialize_session_print_file(session)
        except Exception as e:
            logging.exception("Could not restore print file for staff reprint")
            return jsonify({'error': f'Khong tai lai duoc file in cua phien nay: {str(e)}'}), 404

    if not source_path or not os.path.exists(source_path):
        return jsonify({'error': 'Khong tim thay file in cua phien nay.'}), 404

    meta = (session.meta_data or {}) if session else {}
    print_mode = source_job.print_mode if source_job else (meta.get('print_mode') or (session.layout_id if session else None) or 'grid_4x6')
    cut_mode = source_job.cut_mode if source_job else (meta.get('cut_mode') or ('2x6' if str(print_mode) == 'double_strip' else 'none'))

    configured_name = data.get('printer_name') or get_config_value('printer_name', '')
    printer_name, printers = resolve_printer_name(configured_name)
    print_job = PrintJob(
        session_uuid=session_uuid,
        file_path=source_path,
        printer_name=printer_name or configured_name,
        copies=copies,
        print_mode=print_mode,
        cut_mode=cut_mode,
        status='pending'
    )
    db.session.add(print_job)
    db.session.commit()

    if not printer_name:
        print_job.status = 'failed'
        print_job.error_message = 'Printer not found'
        db.session.commit()
        return jsonify({
            'error': 'Khong tim thay may in RX1HS trong Windows.',
            'available_printers': printers,
            'job': print_job.to_dict()
        }), 500

    try:
        scale_x = get_int_config('print_scale_x', 100)
        scale_y = get_int_config('print_scale_y', 100)
        offset_x = get_int_config('print_offset_x', 0)
        offset_y = get_int_config('print_offset_y', 0)

        result = print_image_file(
            source_path, 
            printer_name, 
            copies, 
            cut_mode=cut_mode,
            scale_x=scale_x,
            scale_y=scale_y,
            offset_x=offset_x,
            offset_y=offset_y
        )
        print_job.status = 'sent'
        print_job.printer_name = printer_name
        db.session.commit()
        return jsonify({
            'success': True,
            'message': 'Da gui lenh in lai.',
            'job': print_job.to_dict(),
            **result
        }), 200
    except Exception as e:
        logging.exception("Staff reprint failed")
        print_job.status = 'failed'
        print_job.error_message = str(e)[:500]
        db.session.commit()
        return jsonify({'error': str(e), 'job': print_job.to_dict()}), 500

@app.route('/api/staff/sessions/<session_uuid>/print-image', methods=['GET'])
def staff_session_print_image(session_uuid):
    source_job = _resolve_print_source_job(session_uuid)

    if not source_job or not source_job.file_path:
        return jsonify({'error': 'Không tìm thấy file in local của phiên này.'}), 404

    path = os.path.abspath(source_job.file_path)
    upload_root = os.path.abspath(os.path.join(os.getcwd(), 'uploads'))
    if not path.startswith(upload_root + os.sep) or not os.path.exists(path):
        return jsonify({'error': 'File in local không tồn tại hoặc không hợp lệ.'}), 404

    return send_file(path, mimetype='image/jpeg')

# --- Payment Code APIs ---

@app.route('/api/codes/generate', methods=['POST'])
def generate_codes():
    data = request.json
    value = int(data.get('value', 0))
    quantity = int(data.get('quantity', 1))
    expires_at_str = data.get('expires_at')
    expires_at = None
    
    if expires_at_str:
        try:
            # Handle ISO format from frontend
            expires_at = datetime.datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
        except ValueError:
            pass # Invalid format, treat as no expiration? or error? Let's ignore for now.

    # Fallback to duration if needed, or just remove. Let's keep for backward compatibility or remove if desired.
    # But user asked to CHANGE it. So we prioritize expires_at.
    if not expires_at:
        duration_minutes = int(data.get('duration', 0))
        if duration_minutes > 0:
            expires_at = datetime.datetime.now(UTC) + datetime.timedelta(minutes=duration_minutes)

    generated = []
    
    try:
        for _ in range(quantity):
            # Generate unique 6 digit code
            attempts = 0
            while attempts < 10:
                code = ''.join(random.choices(string.digits, k=6))
                if not PaymentCode.query.filter_by(code=code).first():
                    new_code = PaymentCode(
                        code=code,
                        value=value,
                        expires_at=expires_at
                    )
                    db.session.add(new_code)
                    generated.append(new_code)
                    break
                attempts += 1
        
        db.session.commit()
        return jsonify([c.to_dict() for c in generated]), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/codes', methods=['GET'])
def get_codes():
    codes = PaymentCode.query.order_by(PaymentCode.created_at.desc()).all()
    return jsonify([c.to_dict() for c in codes])

@app.route('/api/codes/validate', methods=['POST'])
def validate_code():
    code_str = request.json.get('code')
    code = PaymentCode.query.filter_by(code=code_str).first()
    
    if not code:
        return jsonify({'valid': False, 'message': 'Invalid code'}), 404
        
    if code.is_used:
        return jsonify({'valid': False, 'message': 'Code already used'}), 400
        
    if code.expires_at:
        # expires_at lưu theo UTC (naive). So sánh với "now" UTC naive để tránh lệch 7h
        # (trước đây dùng datetime.now() local -> mã hết hạn sớm/muộn 7 tiếng).
        exp = code.expires_at.replace(tzinfo=None) if code.expires_at.tzinfo else code.expires_at
        if exp < datetime.datetime.now(UTC).replace(tzinfo=None):
            return jsonify({'valid': False, 'message': 'Code expired'}), 400
        
    return jsonify({'valid': True, 'value': code.value, 'id': code.id}), 200

@app.route('/api/codes/<int:id>/use', methods=['POST'])
def use_code(id):
    code = db.session.get(PaymentCode, id)
    if not code:
        return jsonify({'error': 'Code not found'}), 404
        
    code.is_used = True
    code.used_at = datetime.datetime.now(UTC)
    db.session.commit()
    return jsonify({'success': True}), 200

# --- SePay QR Payment APIs ---

def get_config_value(key, fallback=None):
    config = db.session.get(Config, key)
    return config.value if config and config.value else fallback

def generate_payment_order_code():
    for _ in range(20):
        code = f"PTB{''.join(random.choices(string.ascii_uppercase + string.digits, k=8))}"
        if not PaymentOrder.query.filter_by(code=code).first():
            return code
    raise RuntimeError('Could not generate unique payment code')

@app.route('/api/sepay/orders', methods=['POST'])
def create_sepay_order():
    data = request.get_json(silent=True) or {}
    amount = int(data.get('amount') or 0)
    if amount <= 0:
        return jsonify({'error': 'Invalid amount'}), 400

    bank = get_config_value('sepay_bank', os.environ.get('SEPAY_BANK'))
    account_number = get_config_value('sepay_account_number', os.environ.get('SEPAY_ACCOUNT_NUMBER'))
    template = get_config_value('sepay_template', os.environ.get('SEPAY_TEMPLATE', 'compact')) or 'compact'
    if not bank or not account_number:
        return jsonify({
            'error': 'Sepay bank/account is not configured',
            'required': ['SEPAY_BANK', 'SEPAY_ACCOUNT_NUMBER']
        }), 400

    code = generate_payment_order_code()
    expires_at = datetime.datetime.now(UTC) + datetime.timedelta(minutes=15)
    order = PaymentOrder(
        code=code,
        amount=amount,
        bank=bank,
        account_number=account_number,
        expires_at=expires_at
    )
    db.session.add(order)
    db.session.commit()

    qr_url = f"https://qr.sepay.vn/img?{urlencode({'acc': account_number, 'bank': bank, 'amount': amount, 'des': code, 'template': template})}"
    return jsonify({
        **order.to_dict(),
        'qr_url': qr_url,
        'content': code
    }), 201

@app.route('/api/sepay/orders/<code>/status', methods=['GET'])
def get_sepay_order_status(code):
    order = PaymentOrder.query.filter_by(code=code).first()
    if not order:
        return jsonify({'error': 'Order not found'}), 404

    if order.status == 'pending' and order.expires_at:
        # expires_at là naive UTC -> so với now UTC naive (tránh TypeError offset-naive vs aware).
        exp = order.expires_at.replace(tzinfo=None) if order.expires_at.tzinfo else order.expires_at
        if exp < datetime.datetime.now(UTC).replace(tzinfo=None):
            order.status = 'expired'
            db.session.commit()

    return jsonify(order.to_dict()), 200

@app.route('/webhook/sepay', methods=['POST'])
def sepay_webhook():
    import re
    expected_key = os.environ.get('SEPAY_API_KEY')
    # Fail-closed: chưa cấu hình key -> TỪ CHỐI, tránh bất kỳ ai cũng POST giả "đã thanh toán".
    if not expected_key:
        return jsonify({'success': False, 'message': 'Webhook not configured'}), 503
    expected_auth = f"Apikey {expected_key}"
    auth_header = request.headers.get('Authorization', '')
    if not hmac.compare_digest(auth_header, expected_auth):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    payload = request.get_json(silent=True) or {}
    content = str(payload.get('content') or '')
    webhook_code = str(payload.get('code') or '')
    transfer_type = str(payload.get('transferType') or '').lower()
    transfer_amount = int(payload.get('transferAmount') or 0)
    tx_ref = payload.get('referenceCode') or str(payload.get('id') or '')

    if transfer_type and transfer_type != 'in':
        return jsonify({'success': True, 'message': 'Ignored non-incoming transfer'}), 200

    # Idempotency: SePay có thể retry webhook trùng -> nếu transaction này đã xử lý thì bỏ qua.
    if tx_ref:
        already = PaymentOrder.query.filter_by(transaction_reference=tx_ref).first()
        if already:
            return jsonify({'success': True, 'message': 'Already processed'}), 200

    def _code_in_content(code, text):
        # Khớp code như một token độc lập (không phải substring) để tránh khớp nhầm đơn khác.
        return bool(code) and re.search(r'(?<![A-Za-z0-9])' + re.escape(code) + r'(?![A-Za-z0-9])', text) is not None

    order = None
    candidates = PaymentOrder.query.filter_by(status='pending').order_by(PaymentOrder.created_at.desc()).limit(50).all()
    for candidate in candidates:
        # Khớp CHÍNH XÁC theo field code; chỉ dò trong content (theo ranh giới token) khi
        # webhook không có field code. Bỏ hẳn kiểu `code in content` (substring) gây khớp nhầm.
        if (webhook_code and candidate.code == webhook_code) or (not webhook_code and _code_in_content(candidate.code, content)):
            order = candidate
            break

    if not order:
        return jsonify({'success': True, 'message': 'No matching pending order'}), 200

    if transfer_amount < order.amount:
        order.raw_payload = payload
        db.session.commit()
        return jsonify({'success': True, 'message': 'Transfer amount is lower than order amount'}), 200

    order.status = 'paid'
    order.paid_at = datetime.datetime.now(UTC)
    order.transaction_reference = tx_ref
    order.raw_payload = payload
    db.session.commit()

    socketio.emit('sepay_payment_success', {
        'order_code': order.code,
        'amount': order.amount,
        'transaction_reference': order.transaction_reference
    })

    return jsonify({'success': True}), 200

@app.route('/api/revenue/reset', methods=['POST'])
def reset_revenue():
    try:
        data = request.get_json(silent=True) or {}
        if data.get("code") != "8686":
            return jsonify({"error": "Unauthorized"}), 403

        # XÓA LỊCH SỬ GIAO DỊCH
        from models import Photo, Session
        Photo.query.delete()
        Session.query.delete()
        
        # reset tổng doanh thu nếu bạn có bảng stats
        db.session.commit()

        # Ep reload state server client
        socketio.emit("revenue_reset", {"status": "ok"}, broadcast=True)

        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/revenue', methods=['GET'])
def get_revenue():
    start_date_str = request.args.get('startDate')
    end_date_str = request.args.get('endDate')
    payment_method = request.args.get('paymentMethod')

    print(f"DEBUG: get_revenue params: start={start_date_str}, end={end_date_str}, method={payment_method}", flush=True)

    query = Session.query.filter_by(status='completed')

    if start_date_str:
        try:
            start_date = datetime.datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            query = query.filter(Session.created_at >= start_date)
        except ValueError:
            pass
            
    if end_date_str:
        try:
            end_date = datetime.datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            query = query.filter(Session.created_at <= end_date)
        except ValueError:
            pass

    if payment_method:
        query = query.filter(Session.payment_method == payment_method)

    sessions = query.order_by(Session.created_at.desc()).all()
    
    total_revenue = sum(s.amount for s in sessions)
    
    return jsonify({
        'totalRevenue': total_revenue,
        'transactions': [{
            'id': s.id,
            'code': s.payment_method, # Using code field for method/code
            'value': s.amount,
            'used_at': s.created_at.isoformat(),
            'status': s.status,
            'device_id': get_device_id()
        } for s in sessions]
    })

@app.route('/api/stats/top-frames', methods=['GET'])
def get_top_frames():
    try:
        limit = int(request.args.get('limit', 5))
        sessions = Session.query.filter_by(status='completed').all()

        frame_counts = {}
        for s in sessions:
            meta = s.meta_data
            if not meta:
                continue
            frame_url = meta.get('frame_url')
            if not frame_url:
                continue
            if frame_url not in frame_counts:
                frame_counts[frame_url] = {'count': 0, 'frame_url': frame_url, 'frame_name': None}
            frame_counts[frame_url]['count'] += 1

        # Try to enrich with frame name from DB
        for frame_url, data in frame_counts.items():
            # Extract filename from URL path
            filename = frame_url.rstrip('/').split('/')[-1]
            frame = Frame.query.filter_by(name=filename).first()
            if frame:
                data['frame_name'] = frame.name
                data['layout'] = frame.layout
            else:
                data['frame_name'] = filename

        sorted_frames = sorted(frame_counts.values(), key=lambda x: x['count'], reverse=True)
        return jsonify(sorted_frames[:limit])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- SocketIO Events ---

connected_devices = {} # Map device_id -> request.sid

# Sự kiện lắng nghe kết nối
@socketio.on('connect')
def test_connect(auth=None):
    device_id = auth.get("deviceId") if auth else None
    
    if device_id:
        connected_devices[device_id] = request.sid
        print(f'Client connected: deviceId={device_id}, sid={request.sid}')
        
        device = Device.query.filter_by(device_id=device_id).first()
        if not device:
            device = Device(device_id=device_id)
            db.session.add(device)
            
        device.last_active = datetime.datetime.now(UTC)
        db.session.commit()
    else:
        print(f'Client connected (No Auth): sid={request.sid}')

@socketio.on('disconnect')
def test_disconnect():
    print(f'Client disconnected: sid={request.sid}')
    for d_id, sid in list(connected_devices.items()):
        if sid == request.sid:
            del connected_devices[d_id]
            break
# Device Management
@app.route('/api/devices/heartbeat', methods=['POST'])
def device_heartbeat():
    data = request.json
    device_id = data.get('deviceId')
    
    if not device_id:
        return jsonify({'error': 'Device ID required'}), 400
        
    device = Device.query.filter_by(device_id=device_id).first()
    
    if not device:
        device = Device(device_id=device_id)
        db.session.add(device)
    
    # Update name if provided and not already set
    name = data.get('name')
    if name and not device.name:
        device.name = name
    
    device.last_active = datetime.datetime.now(UTC)
    db.session.commit()
    
    return jsonify({
        'mode': device.mode,
        'name': device.name or f"Máy {device.device_id[-6:].upper()}"
    })

@app.route('/api/devices', methods=['GET'])
def get_devices():
    devices = Device.query.order_by(Device.last_active.desc()).all()
    return jsonify([d.to_dict() for d in devices])

@app.route('/api/devices/<int:id>', methods=['PUT'])
def update_device(id):
    device = Device.query.get_or_404(id)
    data = request.json
    
    if 'name' in data:
        device.name = data['name']
    if 'mode' in data:
        device.mode = data['mode']
        
    db.session.commit()
    return jsonify(device.to_dict())

@app.route('/api/devices/<int:id>', methods=['DELETE'])
def delete_device(id):
    device = Device.query.get_or_404(id)
    db.session.delete(device)
    db.session.commit()
    return jsonify({'success': True})

# --- Config APIs ---

def init_configs():
    """Initialize default configurations if they don't exist"""
    defaults = [
        {'key': 'price', 'value': '60000'},
        {'key': 'session_timeout', 'value': '600'}, # 10 minutes
        {'key': 'countdown', 'value': '5'},
        {'key': 'hotfolder_capture_timeout', 'value': '30'},
        {'key': 'canon_capture_timeout', 'value': '30'},
        {'key': 'print_price', 'value': '20000'},
        {'key': 'mobile_price', 'value': '30000'},
        {'key': 'mobile_session_timeout', 'value': '300'}, # 5 minutes
        {'key': 'mobile_print_price', 'value': '10000'},
        {'key': 'price_schedule', 'value': '[]'},
        {'key': 'printer_name', 'value': os.environ.get('PRINTER_NAME', 'RX1HS')},
        {'key': 'printer_copies', 'value': '1'},
        {'key': 'print_brightness', 'value': '0'},
        {'key': 'print_contrast', 'value': '0'},
        {'key': 'print_saturation', 'value': '0'},
        {'key': 'print_pink', 'value': '8'},
        {'key': 'print_skin_whitening', 'value': '6'},
        {'key': 'print_warmth', 'value': '2'},
        {'key': 'print_sharpen', 'value': '70'}, # độ làm nét khi in (0 = tắt)
        {'key': 'camera_mode', 'value': 'webcam'}, # webcam, hotfolder
        {'key': 'hot_folder', 'value': 'C:/Photobooth_Input'},
        {'key': 'trigger_key', 'value': '{F8}'},
        {'key': 'staff_pin', 'value': os.environ.get('STAFF_PIN', '1310')},
        {'key': 'bill_port', 'value': 'COM3'},
        {'key': 'bill_baudrate', 'value': '9600'},
        {'key': 'bill_enabled', 'value': 'false'},
        {'key': 'bill_parity', 'value': 'EVEN'},
        {'key': 'bill_mapping', 'value': '{"40": 10000, "41": 20000, "42": 50000, "43": 100000, "44": 200000, "45": 500000}'},
        {'key': 'sepay_bank', 'value': os.environ.get('SEPAY_BANK', '')},
        {'key': 'sepay_account_number', 'value': os.environ.get('SEPAY_ACCOUNT_NUMBER', '')},
        {'key': 'sepay_template', 'value': os.environ.get('SEPAY_TEMPLATE', 'compact')},
        {'key': 'brand_text_primary', 'value': '#7B5E43'},
        {'key': 'brand_text_secondary', 'value': '#5E6B78'},
        {'key': 'print_scale_x', 'value': '100'},
        {'key': 'print_scale_y', 'value': '100'},
        {'key': 'print_offset_x', 'value': '0'},
        {'key': 'print_offset_y', 'value': '0'},
    ]
    
    for item in defaults:
        if not db.session.get(Config, item['key']):
            db.session.add(Config(key=item['key'], value=item['value']))
    
    try:
        db.session.commit()
        print("Config initialized.", flush=True)
    except Exception as e:
        print(f"Config init error: {e}", flush=True)
        db.session.rollback()

@app.route('/api/config/system', methods=['GET'])
def get_system_config():
    return jsonify({
        'device_id': get_device_id()
    })

@app.route('/api/config', methods=['GET'])
def get_configs():
    _apply_due_price_schedule()
    configs = Config.query.all()
    # Convert list to simple dict for easier frontend consumption
    config_dict = {c.key: c.value for c in configs}
    return jsonify(config_dict)

def _parse_schedule_time(value):
    if not value:
        return None
    try:
        text = str(value).replace('Z', '+00:00')
        scheduled = datetime.datetime.fromisoformat(text)
        now = datetime.datetime.now(scheduled.tzinfo) if scheduled.tzinfo else datetime.datetime.now()
        return scheduled, now
    except Exception:
        return None

def _set_config_value(key, value):
    config = db.session.get(Config, key)
    if config:
        config.value = str(value)
    else:
        db.session.add(Config(key=key, value=str(value)))

def _apply_due_price_schedule():
    schedule_config = db.session.get(Config, 'price_schedule')
    if not schedule_config or not schedule_config.value:
        return
    try:
        schedule = json.loads(schedule_config.value)
    except Exception:
        return
    if not isinstance(schedule, list):
        return

    changed = False
    for item in schedule:
        if not isinstance(item, dict) or item.get('applied'):
            continue
        parsed_time = _parse_schedule_time(item.get('run_at'))
        if not parsed_time:
            continue
        scheduled, now = parsed_time
        if scheduled > now:
            continue

        for key in ('price', 'print_price', 'mobile_price', 'mobile_print_price'):
            value = item.get(key)
            if value not in (None, ''):
                _set_config_value(key, value)
        item['applied'] = True
        item['applied_at'] = datetime.datetime.now(UTC).isoformat()
        changed = True

    if changed:
        schedule_config.value = json.dumps(schedule, ensure_ascii=False)
        db.session.commit()

def get_int_config(key, fallback, min_value=None, max_value=None):
    try:
        value = int(get_config_value(key, fallback))
    except (TypeError, ValueError):
        value = fallback
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value

@app.route('/api/config', methods=['POST'])
def update_configs():
    data = request.json
    try:
        for key, value in data.items():
            # Only update known keys or allow new ones? Let's allow flexible keys
            _set_config_value(key, value)
        
        db.session.commit()
        return jsonify({'message': 'Configuration updated'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# Chạy Server
# Chạy Server
# --- Camera / Hot Folder APIs ---

@app.route('/api/camera/capture', methods=['POST'])
def trigger_capture():
    # 1. Get Configs
    camera_mode = db.session.get(Config, 'camera_mode')
    mode = camera_mode.value if camera_mode else 'webcam'
    
    if mode != 'hotfolder':
        return jsonify({'error': 'Camera mode is not hotfolder'}), 400
        
    hot_folder_config = db.session.get(Config, 'hot_folder')
    hot_folder_path = hot_folder_config.value if hot_folder_config else 'C:/Photobooth_Input'
    
    if not os.path.exists(hot_folder_path):
        try:
            os.makedirs(hot_folder_path)
        except:
            return jsonify({'error': f'Hot folder does not exist: {hot_folder_path}'}), 500

    # --- TRIGGER KEYPRESS ---
    trigger_key_config = db.session.get(Config, 'trigger_key')
    trigger_key = trigger_key_config.value if trigger_key_config else '{F8}'
    
    import subprocess
    print(f"ðŸ“¸ Triggering Camera with key: {trigger_key} ...", flush=True)
    try:
        # PowerShell command to send keystroke
        # Try to focus EOS Utility first (Best effort), then send key.
        # Note: Window title might vary ("EOS Utility", "Remote Live View window", etc.)
        # We try generic "EOS Utility"
        ps_command = f"""
        $wshell = New-Object -ComObject WScript.Shell
        if ($wshell.AppActivate('EOS Utility')) {{
            Start-Sleep -m 100
        }}
        $wshell.SendKeys('{trigger_key}')
        """
        subprocess.run(["powershell", "-c", ps_command], timeout=2)
    except Exception as e:
        print(f"âš ï¸ Failed to send trigger key: {e}", flush=True)
    # ------------------------

    print(f" à¤µà¤¾à¤šing Hot Folder: {hot_folder_path} ...", flush=True)
    
    # 2. Watch for NEW file
    
    start_time = datetime.datetime.now().timestamp()
    timeout = get_int_config('hotfolder_capture_timeout', 30, min_value=5, max_value=180)
    
    existing_files = set(os.listdir(hot_folder_path))
    
    import time
    elapsed = 0
    poll_interval = 0.5
    
    captured_file = None
    
    while elapsed < timeout:
        current_files = set(os.listdir(hot_folder_path))
        new_files = current_files - existing_files
        
        # Filter for images
        valid_new_files = [f for f in new_files if f.lower().endswith(('.jpg', '.jpeg', '.png', '.cr2', '.cr3'))]
        
        if valid_new_files:
            # Sort to Prioritize JPG over RAW (if both appear)
            # We want to show the JPG. 
            # sorting logic: files ending in .jpg/.jpeg come first?
            def sort_priority(name):
                lower = name.lower()
                if lower.endswith(('.jpg', '.jpeg')): return 0
                return 1 # RAW etc
                
            valid_new_files.sort(key=sort_priority)
            
            filename = valid_new_files[0]
            
            # Wait a bit for file write to complete (EOS Utility might be writing)
            # Retry loop to ensuring file is readable?
            time.sleep(1.0) 
            
            captured_file = filename
            break
            
        time.sleep(poll_interval)
        elapsed += poll_interval
        
    if captured_file:
        try:
            # 3. Process & Copy to Uploads
            source_path = os.path.join(hot_folder_path, captured_file)
            
            # Generate local filename
            ext = os.path.splitext(captured_file)[1]
            new_filename = f"capture_{uuid.uuid4().hex}{ext}"
            dest_path = os.path.join("uploads", "temp", new_filename)
            os.makedirs(os.path.join("uploads", "temp"), exist_ok=True)
            
            import shutil
            shutil.copy2(source_path, dest_path)
            
            # Optional: Delete from hot folder to keep it clean?
            # os.remove(source_path) 
            
            # 4. Return URL
            # If it's RAW (CR2/CR3), we might need conversion. Assuming JPG for now.
            local_url = f"http://localhost:5000/uploads/temp/{new_filename}"
            return jsonify({'success': True, 'url': local_url})
            
        except Exception as e:
            print(f"Error processing hot folder file: {e}", flush=True)
            return jsonify({'error': str(e)}), 500
            
    return jsonify({'error': 'Timeout: No photo detected'}), 408


    return jsonify({'error': 'Timeout: No photo detected'}), 408


# --- DEVICE IDENTITY ---
DEVICE_ID = None

def get_device_id():
    global DEVICE_ID
    if DEVICE_ID: return DEVICE_ID
    
    # Try read from file
    if os.path.exists("device_id.txt"):
        with open("device_id.txt", "r") as f:
            DEVICE_ID = f.read().strip()
            
    # If not, generate new and save
    if not DEVICE_ID:
        DEVICE_ID = f"PHOTOBOOTH_{uuid.uuid4().hex[:6].upper()}"
        with open("device_id.txt", "w") as f:
            f.write(DEVICE_ID)
            
    print(f"DEVICE ID: {DEVICE_ID}", flush=True)
    return DEVICE_ID

# --- BILL VALIDATOR APIs (Device Aware) ---
from services.BillValidatorService import BillValidatorService


bill_service = None

# Init Service Function (Called in startup)
def init_bill_service():
    global bill_service
    device_id = get_device_id()
    
    if bill_service is None:
        bill_service = BillValidatorService(app, socketio, device_id)
        bill_service.start()

# Generic Device Config API
@app.route('/api/devices/<device_id>/config', methods=['GET', 'POST'])
def device_config_api(device_id):
    if request.method == 'GET':
        global_configs = {c.key: c.value for c in Config.query.all() if c.key.startswith('bill_')}
        device_configs = {c.key: c.value for c in DeviceConfig.query.filter_by(device_id=device_id).all()}
        return jsonify({**global_configs, **device_configs})
        
    if request.method == 'POST':
        data = request.json
        try:
            for key, value in data.items():
                cfg = DeviceConfig.query.filter_by(device_id=device_id, key=key).first()
                if cfg:
                    cfg.value = str(value)
                else:
                    db.session.add(DeviceConfig(device_id=device_id, key=key, value=str(value)))
            
            db.session.commit()
            
            # Notify device via Socket? 
            # Or if this is the device itself, update service directly.
            # Ideally, we emit a socket event to room 'device_id'.
            socketio.emit('config_updated', {'device_id': device_id}, room=str(device_id))
            socketio.emit('config_updated_global', {'device_id': device_id}) # For admin panels to refresh
            
            # If WE are the device being updated, apply changes immediately
            if device_id == get_device_id() and bill_service:
                # We need to re-read configs. 
                # Ideally, BillService observes DB or we pass new values.
                # Simplified: BillService.update_from_db()
                bill_service.reload_config()

            return jsonify({'message': 'Device config updated'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500

# Legacy/Current Device Bill Config (Redirect to DeviceConfig)
@app.route('/api/bill/config', methods=['POST'])
def update_bill_config():
    # Use current device ID
    device_id = get_device_id()
    return device_config_api(device_id)

@app.route('/api/bill/mapping', methods=['GET', 'POST'])
def bill_mapping_api():
    device_id = get_device_id()
    if request.method == 'GET':
        cfg = DeviceConfig.query.filter_by(device_id=device_id, key='bill_mapping').first()
        mapping = json.loads(cfg.value) if cfg and cfg.value else {}
        return jsonify(mapping)
        
    if request.method == 'POST':
        mapping = request.json
        try:
            val = json.dumps(mapping)
            cfg = DeviceConfig.query.filter_by(device_id=device_id, key='bill_mapping').first()
            if cfg: cfg.value = val
            else: db.session.add(DeviceConfig(device_id=device_id, key='bill_mapping', value=val))
            db.session.commit()
            
            if bill_service:
                bill_service.update_mapping(mapping)
                
            return jsonify({'message': 'Mapping updated'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500

@app.route('/api/bill/accept', methods=['POST'])
def set_bill_accept():
    # Bật/tắt nhận tiền mặt. Chỉ bật khi khách chọn tiền mặt ở bước thanh toán.
    data = request.json or {}
    accepting = bool(data.get('accepting', False))
    if not bill_service:
        return jsonify({'accepting': False, 'error': 'Bill service not initialized'}), 200
    bill_service.set_accepting(accepting)
    return jsonify({'accepting': bill_service.accepting}), 200

@app.route('/api/bill/status', methods=['GET'])
def get_bill_status():
    # If admin asks for specifics, they should use /api/devices/<id>/status
    # This endpoint returns LOCAL status
    if not bill_service:
        return jsonify({'enabled': False, 'status': 'Not Initialized'})
    
    status = 'connected' if (bill_service.serial_conn and bill_service.serial_conn.is_open) else 'disconnected'
    return jsonify({
        'enabled': bill_service.enabled,
        'running': bill_service.running, 
        'port': bill_service.port,
        'status': status,
        'device_id': get_device_id()
    })

@app.route('/api/bill/history', methods=['GET'])
def get_bill_history():
    device_id = request.args.get('device_id') or get_device_id()
    business_date = request.args.get('date') or datetime.datetime.now().strftime('%Y-%m-%d')

    entries = BillCashEntry.query.filter_by(
        device_id=device_id,
        business_date=business_date
    ).order_by(BillCashEntry.created_at.desc()).all()

    total = sum(entry.amount for entry in entries)
    return jsonify({
        'device_id': device_id,
        'date': business_date,
        'total': total,
        'count': len(entries),
        'entries': [entry.to_dict() for entry in entries]
    })

@app.route('/api/bill/ports', methods=['GET'])
def get_bill_ports():
    try:
        from serial.tools import list_ports
        ports = [
            {
                'device': port.device,
                'description': port.description,
                'hwid': port.hwid
            }
            for port in list_ports.comports()
        ]
        return jsonify(ports)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# API to list all known devices (for Dropdown)
@app.route('/api/devices', methods=['GET'])
def list_devices():
    # Get unique device IDs from DeviceConfig or Device table
    # Prefer Device table if we register there
    devices = Device.query.all() # Assuming we register devices on startup
    return jsonify([d.to_dict() for d in devices])

@app.route('/<path:path>')
def serve_frontend_app(path):
    if path.startswith(('api/', 'uploads/', 'socket.io/')):
        return jsonify({'error': 'Not found'}), 404

    dist_dir = get_frontend_dist_dir()
    if not dist_dir:
        return jsonify({'error': 'Frontend dist not found'}), 404

    asset_path = os.path.join(dist_dir, path)
    if os.path.isfile(asset_path):
        return send_from_directory(dist_dir, path)

    return send_from_directory(dist_dir, 'index.html')

def register_current_device():
    device_id = get_device_id()
    device = Device.query.filter_by(device_id=device_id).first()
    if not device:
        device = Device(device_id=device_id, name=f"Device {device_id}", mode='event')
        db.session.add(device)
    else:
        device.last_active = datetime.datetime.now(timezone.utc)
    db.session.commit()
    print(f"Registered Device: {device_id}", flush=True)


def push_booth_report_to_cloud():
    """Gửi báo cáo cuối ngày (giấy còn + tiền mặt HÔM QUA) lên cloud — gọi 1 lần lúc bật máy."""
    try:
        import requests
        cloud_url = (os.environ.get('CLOUD_API_URL') or 'https://tomatophotobooth.vercel.app').rstrip('/')
        device_id = get_device_id()
        try:
            from services.printer_media import get_remaining_sheets
            paper = get_remaining_sheets()
        except Exception:
            paper = None
        # Tiền mặt của NGÀY HÔM QUA (ngày vừa hoàn tất)
        yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        with app.app_context():
            entries = BillCashEntry.query.filter_by(device_id=device_id, business_date=yesterday).all()
            cash_total = sum(int(e.amount or 0) for e in entries)
            cash_count = len(entries)
        payload = {
            'action': 'report',
            'device_id': device_id,
            'paper_remaining': paper,
            'cash_total': cash_total,
            'cash_count': cash_count,
            'business_date': yesterday,
        }
        requests.post(f"{cloud_url}/api/devices", json=payload, timeout=15)
        print(f"[BoothReport] Sent: paper={paper}, cash {yesterday}={cash_total} ({cash_count})", flush=True)
    except Exception as e:
        print(f"[BoothReport] Report failed: {e}", flush=True)


# Startup Init
with app.app_context():
    print("APP: Creating DB...", flush=True)
    db.create_all()
    ensure_runtime_schema()
    print("APP: Running migration...", flush=True)
    migrate_data()
    print("APP: Initializing configs...", flush=True)
    init_configs()
    print("APP: Initializing Bill Service...", flush=True)
    register_current_device()
    # Chỉ mở COM (bill service) ở TIẾN TRÌNH CHÍNH. Khi đóng gói PyInstaller + multiprocessing
    # spawn, tiến trình con re-import module -> nếu mở COM ở con sẽ chiếm cổng, tiến trình chính
    # mở lại báo "Access is denied". (freeze_support đã chặn phần lớn; đây là guard phòng xa.)
    import multiprocessing as _mp
    if _mp.current_process().name == 'MainProcess':
        try:
            init_bill_service()
        except Exception as e:
            logging.exception("Bill service initialization failed")
            print(f"Bill service initialization failed: {e}", flush=True)
    else:
        print("APP: Skip bill service init (not MainProcess)", flush=True)
    print("APP: Startup routines done", flush=True)

# Gửi báo cáo giấy + tiền mặt lên cloud 1 lần lúc bật máy (chạy nền, không chặn khởi động)
import threading as _threading
import time as _time
_threading.Thread(target=lambda: (_time.sleep(8), push_booth_report_to_cloud()), daemon=True).start()


from routes.convert_motion import convert_motion_bp
app.register_blueprint(convert_motion_bp)


if __name__ == '__main__':
    logging.info("Starting SocketIO Run...")
    print("Starting SocketIO Run...", flush=True)
    try:
        with app.app_context():
            db.create_all()
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
    except Exception as e:
        logging.error(f"SocketIO Run Error: {e}")
        print(f"SocketIO Run Error: {e}", flush=True)
