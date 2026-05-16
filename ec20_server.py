#!/usr/bin/env python3
"""
Orange Pi EC20 SMS Gateway - PDU mode
Compatible: Python 3.8+
"""
from __future__ import annotations
from typing import List, Dict, Optional, Any

import serial
import time
import threading
import re
import logging
from datetime import datetime

from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit

AT_PORT  = "/dev/ttyUSB2"
BAUD     = 115200
WEB_PORT = 5000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = "ec20_sms_gateway"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


# ===========================================================================
# PDU helpers
# ===========================================================================

def _decode_ucs2(hex_str: str) -> str:
    try:
        h = hex_str.strip().replace(" ", "")
        if len(h) % 4 != 0:
            return hex_str
        return bytes.fromhex(h).decode("utf-16-be")
    except Exception:
        return hex_str

def _is_ucs2_hex(s: str) -> bool:
    s = s.strip()
    if len(s) < 4 or len(s) % 4 != 0:
        return False
    try:
        decoded = bytes.fromhex(s).decode("utf-16-be")
        return any(ord(c) > 127 for c in decoded)
    except Exception:
        return False

def _encode_da(number: str) -> str:
    """Encode destination address sang semi-octet."""
    digits = number.lstrip("+")
    if len(digits) % 2 != 0:
        digits += "F"
    pairs = [digits[i:i+2] for i in range(0, len(digits), 2)]
    return "".join(p[1] + p[0] for p in pairs).upper()

def _build_pdu(number: str, message: str) -> tuple:
    """
    Xay dung PDU string de gui SMS UCS2.
    Tra ve (pdu_hex_string, tpdu_length_in_bytes).
    """
    # SMSC: dung mac dinh cua mang
    smsc = "00"

    # TP flags: SMS-SUBMIT (01) + VP relative (10) => 0x11
    pdu_type = "11"
    mr = "00"

    # Destination address
    number_clean = number.strip()
    if number_clean.startswith("+"):
        ton_npi = "91"
        digits  = number_clean[1:]
    else:
        ton_npi = "81"
        digits  = number_clean

    da_len    = format(len(digits), "02X")
    da_digits = _encode_da(number_clean)
    da        = da_len + ton_npi + da_digits

    pid = "00"       # Protocol ID
    dcs = "08"       # Data Coding Scheme: UCS2
    vp  = "AA"       # Validity period: 4 days

    # User data: UCS2
    ud_bytes = message.encode("utf-16-be")
    udl      = format(len(ud_bytes), "02X")
    ud_hex   = ud_bytes.hex().upper()

    tpdu     = pdu_type + mr + da + pid + dcs + vp + udl + ud_hex
    pdu      = (smsc + tpdu).upper()
    tpdu_len = len(tpdu) // 2

    log.info(f"PDU built: number={number_clean}, msg_len={len(message)}, tpdu_len={tpdu_len}")
    log.debug(f"PDU hex: {pdu}")

    return pdu, tpdu_len


# ===========================================================================
# Modem
# ===========================================================================

class Modem:
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
            log.info("Modem OK" if ok else "Modem khong phan hoi")
        except serial.SerialException as e:
            log.error(f"Khong mo duoc {port}: {e}")

    def send_at(self, cmd: str, wait: str = "OK", timeout: int = 5):
        if not self.ser or not self.ser.is_open:
            return False, ["PORT_CLOSED"]
        with self._lock:
            try:
                self.ser.reset_input_buffer()
                self.ser.write((cmd + "\r\n").encode())
                deadline = time.time() + timeout
                resp: List[str] = []
                while time.time() < deadline:
                    if self.ser.in_waiting:
                        line = self.ser.readline().decode(errors="ignore").strip()
                        if line:
                            resp.append(line)
                            if wait in line or line in ("ERROR", "+CME ERROR", "+CMS ERROR"):
                                break
                    else:
                        time.sleep(0.01)
                return any(wait in l for l in resp), resp
            except Exception as e:
                log.error(f"send_at error: {e}")
                return False, [str(e)]

    def get_status(self) -> Dict[str, Any]:
        st: Dict[str, Any] = {"connected": self.connected}
        if not self.connected:
            return st

        _, r = self.send_at("AT+GMM", timeout=3)
        for line in r:
            if line not in ("OK", "ERROR", "AT+GMM", ""):
                st["model"] = line.strip(); break

        _, r = self.send_at("AT+QGMR", timeout=3)
        for line in r:
            if line not in ("OK", "ERROR", "AT+QGMR", ""):
                st["firmware"] = line.strip(); break

        _, r = self.send_at("AT+CSQ")
        for line in r:
            m = re.search(r'\+CSQ:\s*(\d+)', line)
            if m:
                raw = int(m.group(1))
                st["signal_raw"] = raw
                st["signal_dbm"] = -113 + 2 * raw if raw < 99 else None
                st["signal_pct"] = min(100, int(raw / 31 * 100)) if raw < 99 else 0

        _, r = self.send_at("AT+COPS?")
        for line in r:
            m = re.search(r'\+COPS:.*?"(.+?)"', line)
            if m:
                st["operator"] = m.group(1)

        _, r = self.send_at("AT+CREG?")
        for line in r:
            m = re.search(r'\+CREG:\s*\d+,(\d+)', line)
            if m:
                codes = {"0": "Khong dang ky", "1": "Da dang ky (Home)",
                         "2": "Dang tim mang",  "5": "Roaming"}
                st["network"] = codes.get(m.group(1), m.group(1))

        _, r = self.send_at("AT+CIMI")
        for line in r:
            if re.match(r'^\d{10,15}$', line):
                st["imsi"] = line

        _, r = self.send_at("AT+CNUM")
        for line in r:
            m = re.search(r'\+CNUM:.*?"(\+?\d+)"', line)
            if m:
                st["own_number"] = m.group(1)

        return st

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()


# ===========================================================================
# SMS
# ===========================================================================

class SMS:
    def __init__(self, modem: Modem):
        self.modem = modem
        if modem.connected:
            self._init()

    def _init(self):
        # PDU mode + CNMI notification
        self.modem.send_at("AT+CMGF=0")           # PDU mode
        self.modem.send_at("AT+CNMI=2,2,0,0,0")   # SMS notification
        # Text mode chi de doc - chuyen lai text mode de CMGL hoat dong de hon
        self.modem.send_at("AT+CMGF=1")
        self.modem.send_at('AT+CSCS="GSM"')
        log.info("SMS init OK (PDU send / Text read)")

    def send(self, number: str, message: str) -> Dict[str, Any]:
        """Gui SMS bang PDU mode - ho tro moi ngon ngu."""
        if not self.modem.connected:
            return {"success": False, "error": "Modem chua ket noi"}

        pdu, tpdu_len = _build_pdu(number, message)
        ser = self.modem.ser

        with self.modem._lock:
            try:
                ser.reset_input_buffer()

                # Buoc 1: Chuyen sang PDU mode
                ser.write(b"AT+CMGF=0\r\n")
                time.sleep(0.4)
                ser.reset_input_buffer()

                # Buoc 2: Gui AT+CMGS=<tpdu_length>
                cmd = f"AT+CMGS={tpdu_len}\r\n"
                log.info(f"Sending: {cmd.strip()}")
                ser.write(cmd.encode("ascii"))

                # Buoc 3: Doi dau nhac ">" - doc tung byte
                buf = b""
                deadline = time.time() + 8
                prompt_found = False
                while time.time() < deadline:
                    if ser.in_waiting:
                        ch = ser.read(1)
                        buf += ch
                        if b">" in buf:
                            prompt_found = True
                            break
                        if b"ERROR" in buf:
                            break
                    else:
                        time.sleep(0.02)

                log.info(f"Prompt buf: {repr(buf)}, found={prompt_found}")

                if not prompt_found:
                    ser.write(bytes([27]))  # ESC
                    time.sleep(0.3)
                    # Khoi phuc text mode
                    ser.reset_input_buffer()
                    ser.write(b"AT+CMGF=1\r\n")
                    time.sleep(0.3)
                    ser.reset_input_buffer()
                    return {
                        "success": False,
                        "error": f"Khong co dau nhac '>': {repr(buf)}"
                    }

                # Buoc 4: Gui PDU hex + Ctrl+Z (0x1A)
                time.sleep(0.05)
                payload = (pdu + chr(26)).encode("ascii")
                log.info(f"Sending PDU ({len(pdu)} chars) + CTRL+Z")
                ser.write(payload)

                # Buoc 5: Doc ket qua
                resp_lines: List[str] = []
                deadline = time.time() + 20
                while time.time() < deadline:
                    if ser.in_waiting:
                        line = ser.readline().decode(errors="ignore").strip()
                        if line:
                            resp_lines.append(line)
                            log.info(f"PDU resp: {line}")
                            if "+CMGS" in line or "ERROR" in line:
                                break
                    else:
                        time.sleep(0.02)

            finally:
                # Luon khoi phuc text mode
                time.sleep(0.2)
                ser.reset_input_buffer()
                ser.write(b"AT+CMGF=1\r\n")
                time.sleep(0.3)
                ser.reset_input_buffer()

        ok = any("+CMGS" in l for l in resp_lines)
        return {
            "success": ok,
            "number": number,
            "message": message,
            "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "error": None if ok else str(resp_lines),
        }

    def read_all(self) -> List[Dict[str, Any]]:
        if not self.modem.connected:
            return []
        _, resp = self.modem.send_at('AT+CMGL="ALL"', wait="OK", timeout=10)
        messages: List[Dict[str, Any]] = []
        i = 0
        while i < len(resp):
            line = resp[i]
            if line.startswith("+CMGL:"):
                try:
                    parts = line[len("+CMGL:"):].strip().split(",")
                    idx    = parts[0].strip()
                    status = parts[1].strip().strip('"')
                    raw_s  = parts[2].strip().strip('"')
                    sender = _decode_ucs2(raw_s) if _is_ucs2_hex(raw_s) else raw_s
                    ts = ""
                    if len(parts) >= 6:
                        ts = (parts[4].strip().strip('"') + ","
                              + parts[5].strip().strip('"'))
                    raw_body = resp[i + 1].strip() if i + 1 < len(resp) else ""
                    body = _decode_ucs2(raw_body) if _is_ucs2_hex(raw_body) else raw_body
                    messages.append({
                        "index":   idx,
                        "status":  status,
                        "from":    sender,
                        "body":    body,
                        "timestamp": ts,
                        "unread":  status == "REC UNREAD",
                    })
                    i += 2
                except Exception as e:
                    log.warning(f"Parse SMS error line {i}: {e}")
                    i += 1
            else:
                i += 1
        return messages

    def delete(self, index: int) -> bool:
        ok, _ = self.modem.send_at(f"AT+CMGD={index}")
        return ok

    def delete_all(self, flag: int = 4) -> bool:
        ok, _ = self.modem.send_at(f"AT+CMGD=1,{flag}", timeout=15)
        return ok


# ===========================================================================
# Global instances
# ===========================================================================

modem = Modem(AT_PORT)
sms   = SMS(modem)

_known_sms: set = set()

def _sms_poller():
    time.sleep(3)
    while True:
        try:
            msgs = sms.read_all()
            for m in msgs:
                key = (m["index"], m["from"])
                if key not in _known_sms and m["unread"]:
                    _known_sms.add(key)
                    socketio.emit("new_sms", m)
                    log.info(f"SMS moi tu {m['from']}: {m['body'][:40]}")
        except Exception as e:
            log.error(f"Poller error: {e}")
        time.sleep(5)

threading.Thread(target=_sms_poller, daemon=True).start()

HTML = open("ec20_ui.html").read()


# ===========================================================================
# Routes
# ===========================================================================

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
    data    = request.get_json() or {}
    number  = data.get("number", "").strip()
    message = data.get("message", "").strip()
    if not number or not message:
        return jsonify({"success": False, "error": "Thieu so hoac noi dung"})
    result = sms.send(number, message)
    if result["success"]:
        socketio.emit("sent_sms", result)
    return jsonify(result)

@app.route("/api/sms/delete/<int:idx>", methods=["DELETE"])
def api_sms_delete(idx):
    return jsonify({"success": sms.delete(idx)})

@app.route("/api/sms/delete-bulk", methods=["POST"])
def api_sms_delete_bulk():
    data    = request.get_json() or {}
    indices = data.get("indices", [])
    flag    = data.get("flag", None)
    if flag is not None:
        ok = sms.delete_all(flag)
        return jsonify({"success": ok})
    results = [sms.delete(int(i)) for i in indices]
    return jsonify({"success": all(results), "count": sum(results)})


# ===========================================================================
# SocketIO
# ===========================================================================

@socketio.on("connect")
def on_connect():
    emit("status_update", modem.get_status())

@socketio.on("request_sms")
def on_request_sms():
    emit("sms_list", sms.read_all())

@socketio.on("request_status")
def on_request_status():
    emit("status_update", modem.get_status())


# ===========================================================================
# Main
# ===========================================================================

if __name__ == "__main__":
    print("""
+----------------------------------------------+
|   Orange Pi EC20 // SMS Gateway              |
|   http://0.0.0.0:5000                        |
+----------------------------------------------+
""")
    socketio.run(app, host="0.0.0.0", port=WEB_PORT, debug=False)
