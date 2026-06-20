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
        self.parity = 'EVEN'
        self.mapping = {}
        self.enabled = False
        # accepting: chỉ TRUE khi khách chọn tiền mặt ở bước thanh toán.
        # Khi FALSE -> không nhận tiền (LED tắt), mọi tờ tiền sẽ bị trả lại (0F).
        self.accepting = False
        self.enable_cmd = b'\x3E'   # Enable: bật nhận tiền + LED
        self.inhibit_cmd = b'\x5E'  # Inhibit: tắt nhận tiền + LED

    @staticmethod
    def _parse_cmd(val, default):
        if not val:
            return default
        try:
            return bytes([int(str(val).strip(), 16)])
        except Exception:
            return default

    def set_accepting(self, value):
        self.accepting = bool(value)
        print(f"[Bill] Accepting = {self.accepting}", flush=True)
        # Gửi lệnh ngay nếu serial đang mở (không cần đợi heartbeat power-up)
        try:
            if self.serial_conn and self.serial_conn.is_open:
                self.serial_conn.write(self.enable_cmd if self.accepting else self.inhibit_cmd)
        except Exception as e:
            print(f"[Bill] set_accepting write error: {e}", flush=True)
        return self.accepting

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
            c_parity = get_cfg('bill_parity')
            c_enable_cmd = get_cfg('bill_enable_cmd')
            c_inhibit_cmd = get_cfg('bill_inhibit_cmd')
            self.enable_cmd = self._parse_cmd(c_enable_cmd.value if c_enable_cmd else None, b'\x3E')
            self.inhibit_cmd = self._parse_cmd(c_inhibit_cmd.value if c_inhibit_cmd else None, b'\x5E')

            if c_port: self.port = c_port.value
            if c_baud: self.baudrate = int(c_baud.value)
            if c_parity:
                self.parity = c_parity.value
            else:
                self.parity = 'EVEN'
            if c_map: 
                try:
                    self.mapping = json.loads(c_map.value)
                except:
                    self.mapping = {}
            if c_enable:
                self.enabled = (c_enable.value.lower() == 'true')
                
            print(f"[Bill] Loaded Config for {self.device_id}: {self.port} @ {self.baudrate} ({self.parity}), Enabled: {self.enabled}", flush=True)

    def reload_config(self):
        # Called when config changes via API
        old_port = self.port
        old_baud = self.baudrate
        old_enabled = self.enabled
        old_parity = getattr(self, 'parity', 'EVEN')
        
        self.load_config()
        
        restart = (self.port != old_port or self.baudrate != old_baud or self.enabled != old_enabled or getattr(self, 'parity', 'EVEN') != old_parity)
        
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
                        serial_parity = serial.PARITY_EVEN
                        p_val = getattr(self, 'parity', 'EVEN').upper()
                        if p_val == 'NONE':
                            serial_parity = serial.PARITY_NONE
                        elif p_val == 'ODD':
                            serial_parity = serial.PARITY_ODD
                        elif p_val == 'EVEN':
                            serial_parity = serial.PARITY_EVEN

                        self.serial_conn = serial.Serial(
                            port=self.port,
                            baudrate=self.baudrate,
                            parity=serial_parity,
                            timeout=1
                        )
                        print(f"[Bill] Serial Connected: {self.port} ({p_val})", flush=True)
                        # Đặt trạng thái LED/nhận tiền theo accepting hiện tại (mặc định: tắt)
                        try:
                            self.serial_conn.write(self.enable_cmd if self.accepting else self.inhibit_cmd)
                        except Exception:
                            pass
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

                        # ICT Handshake (Power supply ON: 80H 8FH)
                        if hex_val in ('80', '8F'):
                            # Chỉ Enable (LED bật) khi đang ở chế độ nhận tiền, ngược lại Inhibit (LED tắt)
                            cmd = self.enable_cmd if self.accepting else self.inhibit_cmd
                            print(f"[Bill] Power up ({hex_val}). ACK (02) + {'Enable' if self.accepting else 'Inhibit'}...", flush=True)
                            try:
                                self.serial_conn.write(b'\x02')
                                time.sleep(0.1)
                                self.serial_conn.write(cmd)
                            except Exception as write_err:
                                print(f"[Bill] Failed to write serial handshake: {write_err}", flush=True)
                            continue

                        # Check mapping
                        if hex_val in self.mapping:
                            amount = int(self.mapping[hex_val])

                            # Chưa tới bước nhận tiền mặt -> trả lại tờ tiền (0F), không ghi nhận
                            if not self.accepting:
                                print(f"[Bill] Bill {amount} VND nhưng chưa ở chế độ nhận -> reject (0F)", flush=True)
                                try:
                                    self.serial_conn.write(b'\x0F')
                                except Exception as write_err:
                                    print(f"[Bill] Failed to send reject 0F: {write_err}", flush=True)
                                continue

                            print(f"[Bill] Valid Bill: {amount} VND. Sending ACK (02) to stack...", flush=True)

                            try:
                                self.serial_conn.write(b'\x02')
                            except Exception as write_err:
                                print(f"[Bill] Failed to send stack ACK: {write_err}", flush=True)

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
                            # If it looks like a bill value code (usually starts with 4x) but not mapped, reject it (0F)
                            if hex_val.startswith('4'):
                                print(f"[Bill] Bill value code {hex_val} not mapped or accepted. Rejecting with 0F...", flush=True)
                                try:
                                    self.serial_conn.write(b'\x0F')
                                except Exception as write_err:
                                    print(f"[Bill] Failed to send reject 0F: {write_err}", flush=True)
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
