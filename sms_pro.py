import serial
import time
import threading
import logging
import re
from datetime import datetime

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SERIAL_PORT = "/dev/ttyUSB3"
BAUD = 115200
INBOX_POLL_INTERVAL = 15  # seconds

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

serial_lock = threading.Lock()
history_lock = threading.Lock()

logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG for detailed logs
    format="%(asctime)s [%(levelname)s] %(funcName)s: %(message)s"
)
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
    """Open serial connection with error handling."""
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
        logger.debug(f"Serial port {SERIAL_PORT} opened successfully")
        return ser
    except Exception as exc:
        logger.error(f"Failed to open {SERIAL_PORT}: {exc}")
        raise


def _read_available(ser, timeout=1.5):
    """Read all available data from serial with timeout."""
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        chunk = ser.read_all()
        if chunk:
            buf += chunk
            logger.debug(f"Read chunk: {len(chunk)} bytes")
        time.sleep(0.1)
    decoded = buf.decode(errors="ignore")
    logger.debug(f"Total read: {len(decoded)} chars")
    return decoded


def send_at(cmd, delay=1):
    """Send AT command and return response."""
    logger.debug(f"send_at: acquiring lock for command: {cmd}")
    acquired = serial_lock.acquire(timeout=5)
    if not acquired:
        logger.error(f"send_at: LOCK TIMEOUT for command: {cmd}")
        return ""
    
    logger.debug(f"send_at: lock acquired, executing: {cmd}")
    try:
        with _open_serial() as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write((cmd + "\r").encode())
            ser.flush()
            logger.debug(f"send_at: command sent, waiting {delay}s")
            time.sleep(delay)
            resp = ser.read_all().decode(errors="ignore")
            logger.debug(f"send_at: response: {resp[:100]}")
            return resp
    except Exception as exc:
        logger.error(f"send_at error for '{cmd}': {exc}")
        return ""
    finally:
        serial_lock.release()
        logger.debug(f"send_at: lock released for: {cmd}")


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
    logger.info(f"send_sms: sending to {len(numbers)} number(s)")
    results = []
    
    for number in numbers:
        number = number.strip()
        if not number:
            continue
            
        logger.info(f"send_sms: processing {number}")
        
        try:
            logger.debug(f"send_sms: acquiring lock for {number}")
            acquired = serial_lock.acquire(timeout=10)
            if not acquired:
                logger.error(f"send_sms: LOCK TIMEOUT for {number}")
                results.append({"number": number, "response": "lock timeout"})
                continue
            
            logger.debug(f"send_sms: lock acquired for {number}")
            try:
                with _open_serial() as ser:
                    time.sleep(0.2)
                    ser.reset_input_buffer()
                    
                    # Set text mode
                    logger.debug(f"send_sms: setting text mode for {number}")
                    ser.write(b"AT+CMGF=1\r")
                    ser.flush()
                    time.sleep(0.5)
                    
                    # Send number
                    logger.debug(f"send_sms: sending AT+CMGS for {number}")
                    ser.write(f'AT+CMGS="{number}"\r'.encode())
                    ser.flush()
                    time.sleep(0.5)
                    
                    # Send message
                    logger.debug(f"send_sms: sending message body for {number}")
                    ser.write((message + "\x1A").encode())
                    ser.flush()
                    time.sleep(3)
                    
                    resp = ser.read_all().decode(errors="ignore")
                    logger.info(f"send_sms: response for {number}: {resp[:100]}")
            finally:
                serial_lock.release()
                logger.debug(f"send_sms: lock released for {number}")
                
            results.append({"number": number, "response": resp})
            
        except Exception as exc:
            logger.error(f"send_sms error for {number}: {exc}", exc_info=True)
            results.append({"number": number, "response": str(exc)})
    
    logger.info(f"send_sms: completed {len(results)} sends")
    return results


def parse_inbox(raw):
    """Parse AT+CMGL response into list of message dicts."""
    logger.debug(f"parse_inbox: parsing {len(raw)} chars")
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
            logger.debug(f"parse_inbox: found message from {number}")
        else:
            i += 1
    
    logger.info(f"parse_inbox: parsed {len(messages)} messages")
    return messages


def read_sms():
    """Read all SMS from modem and return parsed list."""
    logger.debug("read_sms: acquiring lock")
    acquired = serial_lock.acquire(timeout=5)
    if not acquired:
        logger.error("read_sms: LOCK TIMEOUT")
        return []
    
    logger.debug("read_sms: lock acquired, opening serial")
    try:
        with _open_serial() as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            
            logger.debug("read_sms: setting text mode")
            ser.write(b"AT+CMGF=1\r")
            ser.flush()
            _read_available(ser, timeout=0.8)
            
            logger.debug("read_sms: sending AT+CMGL")
            ser.write(b'AT+CMGL="ALL"\r')
            ser.flush()
            raw = _read_available(ser, timeout=2.5)
            
            logger.debug(f"read_sms: received {len(raw)} chars")
    finally:
        serial_lock.release()
        logger.debug("read_sms: lock released")
    
    return parse_inbox(raw)


def delete_sms(index):
    logger.info(f"delete_sms: deleting index {index}")
    return send_at(f"AT+CMGD={index}")


def refresh_inbox():
    """Poll modem for inbox and push update via SocketIO."""
    global INBOX
    logger.debug("refresh_inbox: starting")
    try:
        messages = read_sms()
        with history_lock:
            INBOX = messages
        socketio.emit("inbox", messages)
        logger.info(f"refresh_inbox: ✅ {len(messages)} messages")
    except Exception as exc:
        logger.warning(f"refresh_inbox: ❌ error: {exc}", exc_info=True)


# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------

def inbox_poller():
    """Periodically poll inbox every INBOX_POLL_INTERVAL seconds."""
    logger.info(f"inbox_poller: 🚀 STARTED (interval={INBOX_POLL_INTERVAL}s)")
    time.sleep(3)  # let Flask finish starting up first
    
    while True:
        try:
            logger.debug("inbox_poller: triggering refresh")
            refresh_inbox()
        except Exception as exc:
            logger.error(f"inbox_poller: error: {exc}", exc_info=True)
        time.sleep(INBOX_POLL_INTERVAL)


def sms_listener():
    """Listen for +CMTI unsolicited notifications - DISABLED to avoid lock issues."""
    logger.warning("sms_listener: ⚠️ DISABLED (causes lock contention)")
    logger.info("sms_listener: Using inbox_poller only for now")
    # This thread is intentionally disabled because readline() blocks the lock
    # Use inbox_poller instead which polls every 15 seconds
    return


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    logger.debug("route: / accessed")
    return render_template("index.html")


@app.route("/inbox")
def inbox():
    logger.debug("route: /inbox accessed")
    with history_lock:
        return jsonify(INBOX)


@app.route("/outbox")
def outbox():
    logger.debug("route: /outbox accessed")
    with history_lock:
        return jsonify(OUTBOX)


@app.route("/sent")
def sent():
    logger.debug("route: /sent accessed")
    with history_lock:
        return jsonify(SENT)


@app.route("/queue", methods=["POST"])
def queue_sms():
    """Add messages to outbox queue without sending."""
    logger.info("route: /queue POST")
    payload = request.get_json(silent=True) or {}
    numbers = payload.get("numbers", [])
    message = payload.get("message", "")
    
    logger.debug(f"queue_sms: numbers={numbers}, message_len={len(message)}")

    if not isinstance(numbers, list) or not message:
        logger.warning("queue_sms: invalid payload")
        return jsonify({"status": "error", "error": "Invalid payload"}), 400

    queued = []
    logger.debug(f"queue_sms: processing {len(numbers)} numbers")
    
    with history_lock:
        for idx, number in enumerate(numbers):
            logger.debug(f"queue_sms: [{idx}] raw number: '{number}' (type: {type(number).__name__})")
            number = number.strip()
            logger.debug(f"queue_sms: [{idx}] after strip: '{number}' (len: {len(number)})")
            
            if not number:
                logger.warning(f"queue_sms: [{idx}] skipping empty number")
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
            logger.info(f"queue_sms: ✅ queued #{entry['id']} to {number}")
            
            logger.debug(f"queue_sms: emitting outbox_update for #{entry['id']}")
            socketio.emit("outbox_update", entry)

    logger.info(f"queue_sms: ✅ DONE - queued {len(queued)}/{len(numbers)} messages")
    logger.debug(f"queue_sms: OUTBOX now has {len(OUTBOX)} total messages")
    return jsonify({"status": "ok", "queued": queued})


@app.route("/send/<int:msg_id>", methods=["POST"])
def send_queued(msg_id):
    """Confirm and send a specific queued message by its local id."""
    logger.info(f"route: /send/{msg_id} POST")
    
    with history_lock:
        entry = next((m for m in OUTBOX if m["id"] == msg_id and m["status"] == "queued"), None)

    if not entry:
        logger.warning(f"send_queued: message {msg_id} not found")
        return jsonify({"status": "error", "error": "Message not found or already sent"}), 404

    logger.info(f"send_queued: sending #{msg_id} to {entry['number']}")
    results = send_sms([entry["number"]], entry["message"])
    resp = results[0]["response"] if results else ""
    success = "+CMGS:" in resp
    
    logger.info(f"send_queued: #{msg_id} {'✅ SUCCESS' if success else '❌ FAILED'}")

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
    logger.info("route: /send_all POST")
    
    with history_lock:
        pending = [m for m in OUTBOX if m["status"] == "queued"]
        logger.debug(f"send_all_queued: OUTBOX has {len(OUTBOX)} total, {len(pending)} queued")
    
    if not pending:
        logger.warning("send_all_queued: ⚠️ No pending messages to send")
        return jsonify({"status": "ok", "sent_count": 0})
    
    logger.info(f"send_all_queued: found {len(pending)} pending messages")

    sent_entries = []
    for entry in pending:
        logger.info(f"send_all_queued: sending #{entry['id']} to {entry['number']}")
        results = send_sms([entry["number"]], entry["message"])
        resp = results[0]["response"] if results else ""
        success = "+CMGS:" in resp
        
        logger.debug(f"send_all_queued: #{entry['id']} response: {resp[:100]}")
        logger.info(f"send_all_queued: #{entry['id']} {'✅ SUCCESS' if success else '❌ FAILED'}")
        
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

    logger.info(f"send_all_queued: ✅ DONE - sent {len(sent_entries)}/{len(pending)} messages")
    return jsonify({"status": "ok", "sent_count": len(sent_entries)})


@app.route("/delete/<int:index>")
def delete(index):
    logger.info(f"route: /delete/{index}")
    resp = delete_sms(index)
    refresh_inbox()
    return jsonify({"status": "ok", "modem_response": resp})


# ---------------------------------------------------------------------------
# Start background threads + run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("="*70)
    logger.info("🚀 LubanCat SMS Pro+ Starting...")
    logger.info(f"📡 Serial Port: {SERIAL_PORT}")
    logger.info(f"⏱️  Inbox Poll Interval: {INBOX_POLL_INTERVAL}s")
    logger.info("="*70)
    
    # Test serial connection before starting
    try:
        logger.info("🔌 Testing serial connection...")
        test_ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
        test_ser.close()
        logger.info("✅ Serial port OK")
    except Exception as exc:
        logger.error(f"❌ Serial port test FAILED: {exc}")
        logger.error("Check:")
        logger.error("  1. Modem connected?")
        logger.error("  2. Correct port? (ls /dev/ttyUSB*)")
        logger.error("  3. Permission: sudo usermod -aG dialout $USER")
        exit(1)
    
    # Start background threads
    threading.Thread(target=inbox_poller, daemon=True, name="InboxPoller").start()
    # sms_listener is disabled - causes lock contention
    
    logger.info("🌐 Starting Flask server on 0.0.0.0:5000")
    logger.info("="*70)
    
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
