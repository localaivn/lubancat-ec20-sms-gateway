#!/usr/bin/env python3
"""
Quectel EC25 - Web Dashboard
Backend: Flask + Flask-SocketIO + pyserial
Compatible: Python 3.8+
"""

from __future__ import annotations
from typing import List, Dict, Optional, Any

import serial
import time
import threading
import re
import json
import logging
from datetime import datetime

from flask import Flask, render_template_string, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit

# ─── Cấu hình ───────────────────────────────────────────
AT_PORT   = "/dev/ttyUSB2"   # AT Commands
GPS_PORT  = "/dev/ttyUSB1"   # NMEA sentences
BAUD      = 115200
WEB_PORT  = 5000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = "ec25_secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


# ─────────────────────────────────────────────────────────
# Modem AT
# ─────────────────────────────────────────────────────────
class EC25:
    def __init__(self, port: str = AT_PORT, baud: int = BAUD):
        self._lock = threading.Lock()
        self.ser: Optional[serial.Serial] = None
        self.connected = False
        self._connect(port, baud)

    def _connect(self, port: str, baud: int):
        try:
            self.ser = serial.Serial(port, baud, timeout=5)
            time.sleep(0.5)
            ok, _ = self.send_at("AT")
            self.connected = ok
            if ok:
                log.info(f"Modem kết nối thành công: {port}")
            else:
                log.warning("Modem không phản hồi AT")
        except serial.SerialException as e:
            log.error(f"Không mở được {port}: {e}")
            self.connected = False

    def send_at(self, cmd: str, wait: str = "OK", timeout: int = 5) -> tuple:
        if not self.ser or not self.ser.is_open:
            return False, ["PORT_CLOSED"]
        with self._lock:
            try:
                self.ser.reset_input_buffer()
                self.ser.write((cmd + "\r\n").encode())
                deadline = time.time() + timeout
                response = []
                while time.time() < deadline:
                    if self.ser.in_waiting:
                        line = self.ser.readline().decode(errors="ignore").strip()
                        if line:
                            response.append(line)
                            if wait in line or line in ("ERROR", "+CME ERROR", "+CMS ERROR"):
                                break
                success = any(wait in l for l in response)
                return success, response
            except Exception as e:
                log.error(f"send_at error: {e}")
                return False, [str(e)]

    def get_status(self) -> Dict[str, Any]:
        status: Dict[str, Any] = {"connected": self.connected}
        if not self.connected:
            return status

        _, r = self.send_at("AT+CSQ")
        for line in r:
            m = re.search(r'\+CSQ:\s*(\d+)', line)
            if m:
                raw = int(m.group(1))
                # Chuyển sang dBm: -113 + 2*rssi
                status["signal_raw"] = raw
                status["signal_dbm"] = -113 + 2 * raw if raw < 99 else None
                status["signal_pct"] = min(100, int(raw / 31 * 100)) if raw < 99 else 0

        _, r = self.send_at("AT+COPS?")
        for line in r:
            m = re.search(r'\+COPS:.*?"(.+?)"', line)
            if m:
                status["operator"] = m.group(1)

        _, r = self.send_at("AT+CREG?")
        for line in r:
            m = re.search(r'\+CREG:\s*\d+,(\d+)', line)
            if m:
                codes = {"0": "Không đăng ký", "1": "Đã đăng ký (home)",
                         "2": "Đang tìm mạng", "5": "Roaming"}
                status["network"] = codes.get(m.group(1), m.group(1))

        _, r = self.send_at("AT+CIMI")
        for line in r:
            if re.match(r'^\d{10,15}$', line):
                status["imsi"] = line

        return status

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()


# ─────────────────────────────────────────────────────────
# SMS
# ─────────────────────────────────────────────────────────
class SMS:
    def __init__(self, modem: EC25):
        self.modem = modem
        if modem.connected:
            self._init()

    def _init(self):
        self.modem.send_at('AT+CMGF=1')
        self.modem.send_at('AT+CSCS="GSM"')
        self.modem.send_at('AT+CNMI=2,2,0,0,0')
        log.info("SMS khởi tạo OK")

    def send(self, number: str, message: str) -> Dict[str, Any]:
        if not self.modem.connected:
            return {"success": False, "error": "Modem chưa kết nối"}

        ok, _ = self.modem.send_at(f'AT+CMGS="{number}"', wait=">", timeout=5)
        if not ok:
            return {"success": False, "error": "Không vào được chế độ nhập SMS"}

        with self.modem._lock:
            self.modem.ser.write((message + chr(26)).encode())

        deadline = time.time() + 15
        response = []
        while time.time() < deadline:
            if self.modem.ser.in_waiting:
                line = self.modem.ser.readline().decode(errors="ignore").strip()
                if line:
                    response.append(line)
                    if "+CMGS" in line or "ERROR" in line:
                        break

        success = any("+CMGS" in l for l in response)
        return {
            "success": success,
            "number": number,
            "message": message,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": None if success else str(response)
        }

    def read_all(self) -> List[Dict[str, Any]]:
        if not self.modem.connected:
            return []

        ok, resp = self.modem.send_at('AT+CMGL="ALL"', wait="OK", timeout=10)
        messages = []
        i = 0
        while i < len(resp):
            line = resp[i]
            if line.startswith("+CMGL:"):
                try:
                    # +CMGL: idx,"status","sender",,"date,time"
                    after = line[len("+CMGL:"):].strip()
                    parts = after.split(",")
                    idx    = parts[0].strip()
                    status = parts[1].strip().strip('"')
                    sender = parts[2].strip().strip('"')
                    # Timestamp nằm ở parts[4] và parts[5]
                    ts = ""
                    if len(parts) >= 6:
                        ts = f"{parts[4].strip().strip(chr(34))},{parts[5].strip().strip(chr(34))}"
                    body = resp[i + 1].strip() if i + 1 < len(resp) else ""
                    messages.append({
                        "index":     idx,
                        "status":    status,
                        "from":      sender,
                        "body":      body,
                        "timestamp": ts,
                        "unread":    status == "REC UNREAD"
                    })
                    i += 2
                except Exception:
                    i += 1
            else:
                i += 1
        return messages

    def delete(self, index: int) -> bool:
        ok, _ = self.modem.send_at(f"AT+CMGD={index}")
        return ok


# ─────────────────────────────────────────────────────────
# GPS
# ─────────────────────────────────────────────────────────
class GPS:
    def __init__(self, modem: EC25):
        self.modem = modem
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.last_fix: Optional[Dict[str, Any]] = None

    def enable(self) -> bool:
        ok, resp = self.modem.send_at("AT+QGPS=1", timeout=5)
        if not ok and any("already" in l.lower() for l in resp):
            ok = True  # Đã bật rồi
        log.info(f"GPS enable: {ok} | {resp}")
        return ok

    def disable(self):
        self.modem.send_at("AT+QGPSEND")

    def get_location(self) -> Optional[Dict[str, Any]]:
        ok, resp = self.modem.send_at("AT+QGPSLOC=0", timeout=10)
        for line in resp:
            if "+QGPSLOC:" in line:
                parts = line.split(":")[1].strip().split(",")
                if len(parts) >= 3:
                    try:
                        fix = {
                            "latitude":   float(parts[1]),
                            "longitude":  float(parts[2]),
                            "accuracy":   float(parts[3]) if len(parts) > 3 and parts[3] else None,
                            "altitude":   float(parts[4]) if len(parts) > 4 and parts[4] else None,
                            "satellites": parts[10].strip() if len(parts) > 10 else "?",
                            "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "fix":        True
                        }
                        self.last_fix = fix
                        return fix
                    except (ValueError, IndexError):
                        pass
        return None

    @staticmethod
    def parse_gprmc(sentence: str) -> Optional[Dict[str, Any]]:
        if not (sentence.startswith("$GPRMC") or sentence.startswith("$GNRMC")):
            return None
        parts = sentence.split(",")
        if len(parts) < 10 or parts[2] != "A":
            return None

        def to_decimal(raw: str, direction: str) -> Optional[float]:
            if not raw:
                return None
            try:
                dot = raw.index(".") - 2
                deg = float(raw[:dot])
                mins = float(raw[dot:])
                val = deg + mins / 60.0
                if direction in ("S", "W"):
                    val = -val
                return round(val, 6)
            except Exception:
                return None

        lat = to_decimal(parts[3], parts[4])
        lon = to_decimal(parts[5], parts[6])
        if lat is None or lon is None:
            return None
        return {
            "latitude":    lat,
            "longitude":   lon,
            "speed_knots": float(parts[7]) if parts[7] else 0.0,
            "time":        parts[1],
            "date":        parts[9],
            "fix":         True
        }

    def start_stream(self, interval: float = 2.0):
        """Phát GPS liên tục qua SocketIO"""
        if self._running:
            return
        self._running = True

        def _worker():
            # Thử NMEA port trước
            try:
                ser = serial.Serial(GPS_PORT, BAUD, timeout=1)
                log.info("GPS: Đọc NMEA từ " + GPS_PORT)
                while self._running:
                    try:
                        line = ser.readline().decode(errors="ignore").strip()
                        if line.startswith("$"):
                            fix = GPS.parse_gprmc(line)
                            if fix:
                                self.last_fix = fix
                                socketio.emit("gps_update", fix)
                    except Exception:
                        pass
                ser.close()
            except serial.SerialException:
                # Fallback: AT+QGPSLOC polling
                log.info("GPS: Dùng AT+QGPSLOC polling")
                self.enable()
                while self._running:
                    fix = self.get_location()
                    if fix:
                        socketio.emit("gps_update", fix)
                    else:
                        socketio.emit("gps_update", {"fix": False})
                    time.sleep(interval)

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    def stop_stream(self):
        self._running = False


# ─────────────────────────────────────────────────────────
# Khởi tạo global
# ─────────────────────────────────────────────────────────
modem = EC25(AT_PORT)
sms   = SMS(modem)
gps   = GPS(modem)

# Background SMS poller — phát event khi có tin nhắn mới
_known_sms: set = set()

def _sms_poller():
    global _known_sms
    time.sleep(3)
    while True:
        try:
            msgs = sms.read_all()
            for m in msgs:
                key = (m["index"], m["from"])
                if key not in _known_sms and m["unread"]:
                    _known_sms.add(key)
                    socketio.emit("new_sms", m)
                    log.info(f"SMS mới từ {m['from']}")
        except Exception as e:
            log.error(f"SMS poller error: {e}")
        time.sleep(5)

threading.Thread(target=_sms_poller, daemon=True).start()


# ─────────────────────────────────────────────────────────
# HTML Template (được serve inline)
# ─────────────────────────────────────────────────────────
HTML = open("ec25_ui.html").read()


# ─────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/status")
def api_status():
    return jsonify(modem.get_status())

@app.route("/api/sms/list")
def api_sms_list():
    return jsonify(sms.read_all())

@app.route("/api/sms/send", methods=["POST"])
def api_sms_send():
    data = request.get_json()
    number  = data.get("number", "").strip()
    message = data.get("message", "").strip()
    if not number or not message:
        return jsonify({"success": False, "error": "Thiếu số điện thoại hoặc nội dung"})
    result = sms.send(number, message)
    if result["success"]:
        socketio.emit("sent_sms", result)
    return jsonify(result)

@app.route("/api/sms/delete/<int:idx>", methods=["DELETE"])
def api_sms_delete(idx):
    ok = sms.delete(idx)
    return jsonify({"success": ok})

@app.route("/api/gps/start", methods=["POST"])
def api_gps_start():
    if modem.connected:
        gps.enable()
        gps.start_stream()
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Modem chưa kết nối"})

@app.route("/api/gps/stop", methods=["POST"])
def api_gps_stop():
    gps.stop_stream()
    return jsonify({"success": True})

@app.route("/api/gps/location")
def api_gps_location():
    if not modem.connected:
        return jsonify({"fix": False, "error": "Modem chưa kết nối"})
    gps.enable()
    loc = gps.get_location()
    if loc:
        return jsonify(loc)
    return jsonify({"fix": False, "message": "Chưa có tín hiệu GPS"})


# ─────────────────────────────────────────────────────────
# SocketIO events
# ─────────────────────────────────────────────────────────
@socketio.on("connect")
def on_connect():
    log.info("Client kết nối WebSocket")
    emit("status_update", modem.get_status())
    if gps.last_fix:
        emit("gps_update", gps.last_fix)

@socketio.on("request_sms")
def on_request_sms():
    emit("sms_list", sms.read_all())

@socketio.on("request_status")
def on_request_status():
    emit("status_update", modem.get_status())


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════╗
║   Quectel EC20 Web Dashboard         ║
║   http://localhost:{WEB_PORT}              ║
╚══════════════════════════════════════╝
""")
    socketio.run(app, host="0.0.0.0", port=WEB_PORT, debug=False)
