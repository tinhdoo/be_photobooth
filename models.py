from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
# Tạo biến UTC thủ công cho Python 3.9
UTC = timezone.utc
import uuid

db = SQLAlchemy()

class Frame(db.Model):
    __tablename__ = 'frames'
    
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, default=lambda: uuid.uuid4().hex)
    layout = db.Column(db.String(50), nullable=False) # e.g., 'strip', 'vertical'
    name = db.Column(db.String(100), nullable=False) # Original display name
    image_path = db.Column(db.String(255), nullable=False) # Relative path or filename
    icon_path = db.Column(db.String(255), nullable=True) # Relative path or filename for icon
    config = db.Column(db.JSON, default={}) # Stores boxes, borderRadius, etc.
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def to_dict(self):
        return {
            'id': self.id,
            'uuid': self.uuid,
            'name': self.name,
            'layout': self.layout,
            # Construct URL dynamically in app.py or here if we know the host
            # For now, we return relative path or let app.py handle URL construction
            'image_url': self.image_path, 
            'icon_url': self.icon_path,
            'config': self.config
        }

class Session(db.Model):
    __tablename__ = 'sessions'

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, default=lambda: uuid.uuid4().hex)
    layout_id = db.Column(db.String(50), nullable=True) # Which layout was used
    status = db.Column(db.String(20), default='pending') # pending, completed, printed
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    
    photos = db.relationship('Photo', backref='session', lazy=True)
    
    # New fields for Cloudinary URLs
    composite_url = db.Column(db.String(500), nullable=True)
    composite_public_id = db.Column(db.String(100), nullable=True) # Added for cleanup
    gif_url = db.Column(db.String(500), nullable=True)
    gif_public_id = db.Column(db.String(100), nullable=True) # Added for cleanup
    
    # Payment Info
    payment_method = db.Column(db.String(50), nullable=True) # cash, qr, code
    amount = db.Column(db.Integer, default=0)
    
    # Metadata for UI reconstruction (frames, boxes)
    meta_data = db.Column(db.JSON, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'uuid': self.uuid,
            'layout_id': self.layout_id,
            'status': self.status,
            'status': self.status,
            'created_at': self.created_at.isoformat(),
            'composite_url': self.composite_url,
            'composite_public_id': self.composite_public_id,
            'gif_url': self.gif_url,
            'gif_public_id': self.gif_public_id,
            'payment_method': self.payment_method,
            'amount': self.amount,
            'meta_data': self.meta_data,
            'photos': [p.to_dict() for p in self.photos]
        }

class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=True)
    mode = db.Column(db.String(20), default='payment') # 'payment' or 'event'
    last_active = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'name': self.name or f"Device {self.device_id[:6]}",
            'mode': self.mode,
            'last_active': self.last_active.isoformat() if self.last_active else None
        }

class Photo(db.Model):
    __tablename__ = 'photos'

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('sessions.id'), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    type = db.Column(db.String(20), default='raw') # raw (captured), result (composite)
    url = db.Column(db.String(500), nullable=True) # Cloudinary URL
    video_url = db.Column(db.String(500), nullable=True) # Motion Photo Video URL
    public_id = db.Column(db.String(100), nullable=True) # Added for cleanup
    video_public_id = db.Column(db.String(100), nullable=True) # Cloudinary ID for motion video cleanup
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def to_dict(self):
        return {
            'id': self.id,
            'session_id': self.session_id,
            'type': self.type,
            'url': self.url,
            'video_url': self.video_url,
            'public_id': self.public_id,
            'video_public_id': self.video_public_id,
            'created_at': self.created_at.isoformat()
        }


class MobileUpload(db.Model):
    __tablename__ = 'mobile_uploads'

    id = db.Column(db.Integer, primary_key=True)
    session_uuid = db.Column(db.String(64), index=True, nullable=False)
    url = db.Column(db.String(500), nullable=False)
    public_id = db.Column(db.String(150), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def to_dict(self):
        return {
            'id': self.id,
            'session_uuid': self.session_uuid,
            'url': self.url,
            'public_id': self.public_id,
            'created_at': self.created_at.isoformat()
        }


class PaymentCode(db.Model):
    __tablename__ = 'payment_codes'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(6), unique=True, nullable=False)
    value = db.Column(db.Integer, nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'value': self.value,
            'is_used': self.is_used,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'used_at': self.used_at.isoformat() if self.used_at else None,
            'created_at': self.created_at.isoformat()
        }

class PaymentOrder(db.Model):
    __tablename__ = 'payment_orders'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='pending') # pending, paid, expired
    bank = db.Column(db.String(50), nullable=True)
    account_number = db.Column(db.String(50), nullable=True)
    transaction_reference = db.Column(db.String(100), nullable=True)
    raw_payload = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    paid_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'amount': self.amount,
            'status': self.status,
            'bank': self.bank,
            'account_number': self.account_number,
            'transaction_reference': self.transaction_reference,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'paid_at': self.paid_at.isoformat() if self.paid_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None
        }

class Config(db.Model):
    __tablename__ = 'configs'
    
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    def to_dict(self):
        return {
            'key': self.key,
            'value': self.value,
            'updated_at': self.updated_at.isoformat()
        }

class DeviceConfig(db.Model):
    __tablename__ = 'device_configs'
    
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(50), nullable=False) # ID of the device (e.g., from file)
    key = db.Column(db.String(50), nullable=False)
    value = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        db.UniqueConstraint('device_id', 'key', name='unique_device_config'),
    )

    def to_dict(self):
        return {
            'device_id': self.device_id,
            'key': self.key,
            'value': self.value,
            'updated_at': self.updated_at.isoformat()
        }
