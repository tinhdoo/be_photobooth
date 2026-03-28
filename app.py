import logging
logging.basicConfig(filename='app.log', level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logging.info("APP: STARTING UP...")

print("APP: Importing libs...", flush=True)
from flask import Flask, request, jsonify, send_from_directory
# ... (rest of imports)

# ... (inside __main__)

from flask_socketio import SocketIO
from flask_cors import CORS
print("APP: Libs imported", flush=True)
import datetime
from datetime import timezone
UTC = timezone.utc
import os
import uuid
import json
import cv2
from werkzeug.utils import secure_filename
import pillow_heif
pillow_heif.register_heif_opener()
from PIL import Image, ImageOps
from models import db, Session, Photo, Frame, PaymentCode, Device, Config, DeviceConfig
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
from services.cleanup_manager import cleanup_expired_sessions, cleanup_old_payment_codes

# Khởi tạo App
print("APP: Initializing Flask...", flush=True)
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
app.config['SECRET_KEY'] = 'secret!'
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads', 'frames')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///photobooth_v2.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024 # Limit upload size to 15MB

# Cloudinary Config
# Cloudinary Config
cloudinary.config( 
    cloud_name = "dmgcwlt88", 
    api_key = "737347737339893", 
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "J2E32NdSb-awkZZ9I_ffwOAiXT0"), # Replace with actual secret
    secure=True
)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)
# Initialize SocketIO correctly
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize Scheduler
def run_with_context(fn):
    """Wrapper to run a function inside Flask app context (required for background threads)."""
    def wrapper():
        with app.app_context():
            fn()
    return wrapper

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(func=run_with_context(cleanup_expired_sessions), trigger="interval", hours=1, id="cleanup_sessions", replace_existing=True)
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



@app.route('/')
def index():
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
        
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(config_path):
            os.remove(config_path)
            
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
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if not session_id:
        return jsonify({'error': 'No session_id provided'}), 400

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
        img.thumbnail((1800, 1800))
        
        # Save as optimized JPEG
        img.save(dest_path, "JPEG", quality=90)
        
        # URL tương đối để proxy của Vite tự forward tải ảnh đúng IP
        local_url = f"/uploads/temp/{new_filename}"
        
        # Emit to the specific session room or broadcast for now (let's broadcast and FE filters by session_id, or use rooms)
        # Using socketio.emit to all clients, FE will match session_id
        socketio.emit('mobile_photo_uploaded', {'session_id': session_id, 'url': local_url})
        
        return jsonify({'success': True, 'url': local_url}), 200
    except Exception as e:
        print(f"Mobile upload error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500

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
def upload_cloud():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    try:
        # Upload to Cloudinary
        upload_result = cloudinary.uploader.upload(file, resource_type="auto")
        
        return jsonify({
            'url': upload_result['secure_url'],
            'public_id': upload_result['public_id']
        }), 200
    except Exception as e:
        print(f"Cloudinary upload error: {e}")
        return jsonify({'error': str(e)}), 500

# --- Branding Upload API ---
@app.route('/api/upload/branding', methods=['POST'])
def upload_branding():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    key = request.form.get('key')
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if not key:
        return jsonify({'error': 'No key provided'}), 400

    try:
        filename = secure_filename(file.filename)
        unique_filename = f"branding_{uuid.uuid4().hex}_{filename}"
        
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
        
    if code.expires_at and code.expires_at < datetime.datetime.now():
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
            'status': s.status
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
        {'key': 'print_price', 'value': '20000'},
        {'key': 'mobile_price', 'value': '30000'},
        {'key': 'mobile_session_timeout', 'value': '300'}, # 5 minutes
        {'key': 'mobile_print_price', 'value': '10000'},
        {'key': 'camera_mode', 'value': 'webcam'}, # webcam, hotfolder
        {'key': 'hot_folder', 'value': 'C:/Photobooth_Input'},
        {'key': 'trigger_key', 'value': '{F8}'},
        {'key': 'bill_port', 'value': 'COM3'},
        {'key': 'bill_baudrate', 'value': '9600'},
        {'key': 'bill_enabled', 'value': 'false'},
        {'key': 'bill_mapping', 'value': '{"40": 10000, "41": 20000, "42": 50000, "43": 100000, "44": 200000, "45": 500000}'},
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

@app.route('/api/config', methods=['GET'])
def get_configs():
    configs = Config.query.all()
    # Convert list to simple dict for easier frontend consumption
    config_dict = {c.key: c.value for c in configs}
    return jsonify(config_dict)

@app.route('/api/config', methods=['POST'])
def update_configs():
    data = request.json
    try:
        for key, value in data.items():
            # Only update known keys or allow new ones? Let's allow flexible keys
            config = db.session.get(Config, key)
            if config:
                config.value = str(value)
            else:
                db.session.add(Config(key=key, value=str(value)))
        
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
    print(f"📸 Triggering Camera with key: {trigger_key} ...", flush=True)
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
        print(f"⚠️ Failed to send trigger key: {e}", flush=True)
    # ------------------------

    print(f" वाचing Hot Folder: {hot_folder_path} ...", flush=True)
    
    # 2. Watch for NEW file
    
    start_time = datetime.datetime.now().timestamp()
    timeout = 30 # seconds
    
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
            
    print(f"🆔 DEVICE ID: {DEVICE_ID}", flush=True)
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
        configs = DeviceConfig.query.filter_by(device_id=device_id).all()
        return jsonify({c.key: c.value for c in configs})
        
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

# API to list all known devices (for Dropdown)
@app.route('/api/devices', methods=['GET'])
def list_devices():
    # Get unique device IDs from DeviceConfig or Device table
    # Prefer Device table if we register there
    devices = Device.query.all() # Assuming we register devices on startup
    return jsonify([d.to_dict() for d in devices])

def register_current_device():
    device_id = get_device_id()
    device = Device.query.filter_by(device_id=device_id).first()
    if not device:
        device = Device(device_id=device_id, name=f"Device {device_id}", mode='event')
        db.session.add(device)
    else:
        device.last_active = datetime.datetime.now(timezone.utc)
    db.session.commit()
    print(f"✅ Registered Device: {device_id}", flush=True)


# Startup Init
with app.app_context():
    print("APP: Creating DB...", flush=True)
    db.create_all()
    print("APP: Running migration...", flush=True)
    migrate_data()
    print("APP: Initializing configs...", flush=True)
    init_configs()
    print("APP: Initializing Bill Service...", flush=True)
    register_current_device()
    init_bill_service()
    print("APP: Startup routines done", flush=True)


from routes.convert_motion import convert_motion_bp
app.register_blueprint(convert_motion_bp)


if __name__ == '__main__':
    logging.info("Starting SocketIO Run...")
    print("Starting SocketIO Run...", flush=True)
    try:
        with app.app_context():
            db.create_all()
        socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=False)
    except Exception as e:
        logging.error(f"SocketIO Run Error: {e}")
        print(f"SocketIO Run Error: {e}", flush=True)