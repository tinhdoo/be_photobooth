import serial
import threading
import time
import json
from datetime import datetime
from models import db, BillCashEntry, Config, DeviceConfig

class BillValidatorService:
    def __init__(self, app, socketio, device_id):
        self.app = app
        self.socketio = socketio
        self.device_id = device_id
        self.serial_conn = None
        self.running = False
        self.thread = None
        self.port = 'COM3' # Default
        self.baudrate = 9600
        self.mapping = {}
        self.enabled = False

    def load_config(self):
        with self.app.app_context():
            # Load config from DeviceConfig table
            def get_cfg(key):
                device_cfg = DeviceConfig.query.filter_by(device_id=self.device_id, key=key).first()
                return device_cfg or Config.query.filter_by(key=key).first()

            c_port = get_cfg('bill_port')
            c_baud = get_cfg('bill_baudrate')
            c_map = get_cfg('bill_mapping')
            c_enable = get_cfg('bill_enabled')

            if c_port: self.port = c_port.value
            if c_baud: self.baudrate = int(c_baud.value)
            if c_map: 
                try:
                    self.mapping = json.loads(c_map.value)
                except:
                    self.mapping = {}
            if c_enable:
                self.enabled = (c_enable.value.lower() == 'true')
                
            print(f"[Bill] Loaded Config for {self.device_id}: {self.port} @ {self.baudrate}, Enabled: {self.enabled}", flush=True)

    def reload_config(self):
        # Called when config changes via API
        old_port = self.port
        old_baud = self.baudrate
        old_enabled = self.enabled
        
        self.load_config()
        
        restart = (self.port != old_port or self.baudrate != old_baud or self.enabled != old_enabled)
        
        if restart:
            self.stop()
            if self.enabled:
                self.start()

    def start(self):
        self.load_config()
        if self.enabled and not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.thread.start()
            print(f"[Bill] BillValidatorService: Started on {self.port} @ {self.baudrate}", flush=True)

    def stop(self):
        self.running = False
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except:
                pass
        self.serial_conn = None
        print("[Bill] BillValidatorService: Stopped", flush=True)

    def update_config(self, port, baudrate, enabled):
        # Legacy method support, now redirects to reload
        pass 

    def update_mapping(self, new_mapping):
        self.mapping = new_mapping
        print(f"[Bill] Bill Mapping updated: {self.mapping}", flush=True)

    def record_cash_entry(self, amount, hex_code):
        with self.app.app_context():
            entry = BillCashEntry(
                device_id=self.device_id,
                amount=int(amount),
                hex_code=hex_code,
                business_date=datetime.now().strftime('%Y-%m-%d')
            )
            db.session.add(entry)
            db.session.commit()
            return entry.to_dict()

    def _listen_loop(self):
        retry_count = 0
        while self.running:
            try:
                if not self.serial_conn or not self.serial_conn.is_open:
                    try:
                        self.serial_conn = serial.Serial(
                            port=self.port,
                            baudrate=self.baudrate,
                            timeout=1
                        )
                        print(f"[Bill] Serial Connected: {self.port}", flush=True)
                        self.socketio.emit('bill_status', {
                            'device_id': self.device_id,
                            'status': 'connected',
                            'port': self.port
                        })
                        retry_count = 0
                    except Exception as e:
                        if retry_count % 10 == 0: # Log every 10th try to avoid spam
                             print(f"[Bill] Serial Connection Error: {e}", flush=True)
                             self.socketio.emit('bill_status', {
                                 'device_id': self.device_id,
                                 'status': 'error',
                                 'message': str(e),
                                 'port': self.port
                             })
                        retry_count += 1
                        time.sleep(2)
                        continue

                # Read byte
                if self.serial_conn.in_waiting > 0:
                    byte = self.serial_conn.read(1)
                    if byte:
                        hex_val = byte.hex().upper() # e.g. '40'
                        
                        # Emit debug info
                        self.socketio.emit('bill_debug', {'device_id': self.device_id, 'hex': hex_val})
                        print(f"[Bill] Received Hex: {hex_val}", flush=True)

                        # Check mapping
                        if hex_val in self.mapping:
                            amount = int(self.mapping[hex_val])
                            print(f"[Bill] Valid Bill: {amount} VND", flush=True)
                            entry = None
                            try:
                                entry = self.record_cash_entry(amount, hex_val)
                            except Exception as log_error:
                                print(f"[Bill] Cash history write failed: {log_error}", flush=True)
                            self.socketio.emit('money_inserted', {
                                'device_id': self.device_id,
                                'amount': amount,
                                'hex': hex_val,
                                'entry': entry
                            })
                        else:
                            print(f"[Bill] Unknown Hex: {hex_val}", flush=True)

            except Exception as e:
                print(f"[Bill] Serial Loop Error: {e}", flush=True)
                self.socketio.emit('bill_status', {
                    'device_id': self.device_id,
                    'status': 'error',
                    'message': str(e),
                    'port': self.port
                })
                if self.serial_conn:
                    self.serial_conn.close()
                self.serial_conn = None
                time.sleep(2)
