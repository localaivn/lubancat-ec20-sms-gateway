import serial
import time
import threading
import logging
import re
from datetime import datetime

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO

SERIAL_PORT = "/dev/ttyUSB3"
BAUD = 115200
INBOX_POLL_INTERVAL = 15  # seconds

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

serial_lock = threading.Lock()
history_lock = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# In-memory stores
INBOX = []
OUTBOX = []
SENT = []
message_counter = 0


# ---------------------------------------------------------------------------
# Serial helpers
# ---------------------------------------------------------------------------

def _open_serial():
    return serial.Serial(SERIAL_PORT, BAUD, timeout=1)


def _read_available(ser, timeout=1.5):
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        chunk = ser.read_all()
        if chunk:
            buf += chunk
        time.sleep(0.1)
    return buf.decode(errors="ignore")


def send_at(cmd, delay=1):
    acquired = serial_lock.acquire(timeout=5)
    if not acquired:
        logger.error("send_at: could not acquire serial_lock (timeout)")
        return ""
    try:
        with _open_serial() as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write((cmd + "\r").encode())
            ser.flush()
            time.sleep(delay)
            return ser.read_all().decode(errors="ignore")
    except Exception as exc:
        logger.error("send_at error: %s", exc)
        return ""
    finally:
        serial_lock.release()


# ---------------------------------------------------------------------------
# SMS operations
# ---------------------------------------------------------------------------

def next_local_id():
    global message_counter
    with history_lock:
        message_counter += 1
        return message_counter


def send_sms(numbers, message):
    """Send SMS to one or more numbers immediately."""
    results = []
    for number in numbers:
        number = number.strip()
        if not number:
            continue
        try:
            acquired = serial_lock.acquire(timeout=10)
            if not acquired:
                logger.error("send_sms: serial_lock timeout for %s", number)
                results.append({"number": number, "response": "lock timeout"})
                continue
            try:
                with _open_serial() as ser:
                    time.sleep(0.2)
                    ser.reset_input_buffer()
                    ser.write(b"AT+CMGF=1\r")
                    ser.flush()
                    time.sleep(0.5)
                    ser.write(f'AT+CMGS="{number}"\r'.encode())
                    ser.flush()
                    time.sleep(0.5)
                    ser.write((message + "\x1A").encode())
                    ser.flush()
                    time.sleep(3)
                    resp = ser.read_all().decode(errors="ignore")
            finally:
                serial_lock.release()
            results.append({"number": number, "response": resp})
        except Exception as exc:
            logger.error("send_sms error for %s: %s", number, exc)
            results.append({"number": number, "response": str(exc)})
    return results


def parse_inbox(raw):
    """Parse AT+CMGL response into list of message dicts."""
    messages = []
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = re.match(r'\+CMGL:\s*(\d+),"([^"]+)","([^"]*)"[^,]*,?"?([^"]*)"?', line)
        if m:
            modem_index = int(m.group(1))
            status = m.group(2)
            number = m.group(3)
            timestamp = m.group(4)
            body_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("+CMGL:"):
                body_lines.append(lines[i])
                i += 1
            messages.append({
                "modem_index": modem_index,
                "status": status,
                "number": number,
                "timestamp": timestamp,
                "message": "\n".join(body_lines).strip(),
            })
        else:
            i += 1
    return messages


def read_sms():
    """Read all SMS from modem and return parsed list."""
    acquired = serial_lock.acquire(timeout=5)
    if not acquired:
        logger.error("read_sms: could not acquire serial_lock (timeout)")
        return []
    try:
        with _open_serial() as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write(b"AT+CMGF=1\r")
            ser.flush()
            _read_available(ser, timeout=0.8)
            ser.write(b'AT+CMGL="ALL"\r')
            ser.flush()
            raw = _read_available(ser, timeout=2.5)
    finally:
        serial_lock.release()
    return parse_inbox(raw)


def delete_sms(index):
    return send_at(f"AT+CMGD={index}")


def refresh_inbox():
    """Poll modem for inbox and push update via SocketIO."""
    global INBOX
    try:
        messages = read_sms()
        with history_lock:
            INBOX = messages
        socketio.emit("inbox", messages)
        logger.info("Inbox refreshed: %d messages", len(messages))
    except Exception as exc:
        logger.warning("refresh_inbox error: %s", exc)


# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------

def inbox_poller():
    """Periodically poll inbox every INBOX_POLL_INTERVAL seconds."""
    logger.info("Inbox poller started (interval=%ds)", INBOX_POLL_INTERVAL)
    time.sleep(2)  # let Flask finish starting up first
    while True:
        refresh_inbox()
        time.sleep(INBOX_POLL_INTERVAL)


def sms_listener():
    """Listen for +CMTI unsolicited notifications for instant inbox updates."""
    logger.info("SMS listener started on %s", SERIAL_PORT)
    time.sleep(2)
    try:
        send_at("AT+CNMI=2,1,0,0,0", 1)
    except Exception as exc:
        logger.warning("sms_listener: CNMI setup failed: %s", exc)

    while True:
        try:
            # Open serial, read one line, then immediately close — never hold lock while blocking
            with serial_lock:
                with _open_serial() as ser:
                    ser.timeout = 2  # short timeout so lock is released quickly
                    line = ser.readline().decode(errors="ignore")
            if "+CMTI:" in line:
                logger.info("New SMS notification: %s", line.strip())
                refresh_inbox()
        except Exception as exc:
            logger.warning("sms_listener error: %s", exc)
            time.sleep(2)


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/inbox")
def inbox():
    with history_lock:
        return jsonify(INBOX)


@app.route("/outbox")
def outbox():
    with history_lock:
        return jsonify(OUTBOX)


@app.route("/sent")
def sent():
    with history_lock:
        return jsonify(SENT)


@app.route("/queue", methods=["POST"])
def queue_sms():
    """Add messages to outbox queue without sending."""
    payload = request.get_json(silent=True) or {}
    numbers = payload.get("numbers", [])
    message = payload.get("message", "")

    if not isinstance(numbers, list) or not message:
        return jsonify({"status": "error", "error": "Invalid payload"}), 400

    queued = []
    with history_lock:
        for number in numbers:
            number = number.strip()
            if not number:
                continue
            entry = {
                "id": next_local_id(),
                "number": number,
                "message": message,
                "status": "queued",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            OUTBOX.append(entry)
            queued.append(entry)
            socketio.emit("outbox_update", entry)

    return jsonify({"status": "ok", "queued": queued})


@app.route("/send/<int:msg_id>", methods=["POST"])
def send_queued(msg_id):
    """Confirm and send a specific queued message by its local id."""
    with history_lock:
        entry = next((m for m in OUTBOX if m["id"] == msg_id and m["status"] == "queued"), None)

    if not entry:
        return jsonify({"status": "error", "error": "Message not found or already sent"}), 404

    results = send_sms([entry["number"]], entry["message"])
    resp = results[0]["response"] if results else ""
    success = "+CMGS:" in resp

    with history_lock:
        entry["status"] = "sent" if success else "failed"
        entry["sent_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry["modem_response"] = resp
        if success:
            SENT.append(entry)

    socketio.emit("outbox_update", entry)
    if success:
        socketio.emit("sent_update", entry)

    return jsonify({"status": "ok", "entry": entry})


@app.route("/send_all", methods=["POST"])
def send_all_queued():
    """Send all queued messages at once."""
    with history_lock:
        pending = [m for m in OUTBOX if m["status"] == "queued"]

    sent_entries = []
    for entry in pending:
        results = send_sms([entry["number"]], entry["message"])
        resp = results[0]["response"] if results else ""
        success = "+CMGS:" in resp
        with history_lock:
            entry["status"] = "sent" if success else "failed"
            entry["sent_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry["modem_response"] = resp
            if success:
                SENT.append(entry)
                sent_entries.append(entry)
        socketio.emit("outbox_update", entry)
        if success:
            socketio.emit("sent_update", entry)

    return jsonify({"status": "ok", "sent_count": len(sent_entries)})


@app.route("/delete/<int:index>")
def delete(index):
    resp = delete_sms(index)
    refresh_inbox()
    return jsonify({"status": "ok", "modem_response": resp})


# ---------------------------------------------------------------------------
# Start background threads + run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    threading.Thread(target=inbox_poller, daemon=True).start()
    threading.Thread(target=sms_listener, daemon=True).start()
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
