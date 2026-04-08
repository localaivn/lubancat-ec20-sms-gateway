import serial
import time
import threading
import logging
from datetime import datetime

from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO

SERIAL_PORT = "/dev/ttyUSB3"
BAUD = 115200
SERIAL_TIMEOUT = 0.5

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

lock = threading.Lock()
history_lock = threading.Lock()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
message_counter = 0
INBOX = []
OUTBOX = []
SENT = []


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def next_local_id():
    global message_counter
    with history_lock:
        message_counter += 1
        return message_counter


def _open_serial():
    return serial.Serial(SERIAL_PORT, BAUD, timeout=SERIAL_TIMEOUT)


def _read_available(ser, timeout=1.5):
    end = time.time() + timeout
    chunks = []
    while time.time() < end:
        waiting = ser.in_waiting
        if waiting:
            chunks.append(ser.read(waiting).decode(errors="ignore"))
            end = time.time() + 0.3
        else:
            time.sleep(0.05)
    return "".join(chunks)


def send_at(cmd, delay=1):
    with lock:
        with _open_serial() as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            if cmd:
                ser.write((cmd + "\r").encode())
                ser.flush()
            time.sleep(delay)
            return _read_available(ser, timeout=max(0.6, delay + 0.6))


def send_sms(numbers, message):
    result = []

    for number in numbers:
        number = number.strip()
        if not number:
            continue
        record = {
            "id": next_local_id(),
            "number": number,
            "message": message,
            "created_at": now_iso(),
            "status": "queued",
            "modem_response": "",
        }
        with history_lock:
            OUTBOX.insert(0, record)
        with lock:
            with _open_serial() as ser:
                time.sleep(0.2)
                ser.reset_input_buffer()
                ser.write(b'AT+CMGF=1\r')
                ser.flush()
                _read_available(ser, timeout=0.8)

                ser.write(f'AT+CMGS="{number}"\r'.encode())
                ser.flush()
                prompt = _read_available(ser, timeout=1.5)
                if ">" not in prompt:
                    record["modem_response"] = prompt
                    record["status"] = "failed"
                    result.append(record)
                    socketio.emit("send_error", record)
                    continue

                ser.write((message + "\x1A").encode())
                ser.flush()
                resp = _read_available(ser, timeout=8.0)
                record["modem_response"] = resp
                ok = "OK" in resp and "ERROR" not in resp
                record["status"] = "sent" if ok else "failed"
                result.append(record)

        if record["status"] == "sent":
            with history_lock:
                SENT.insert(0, record.copy())
            socketio.emit("sent", record)
        else:
            socketio.emit("send_error", record)

    return result


def read_sms():
    with lock:
        with _open_serial() as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write(b"AT+CMGF=1\r")
            ser.flush()
            _read_available(ser, timeout=0.8)
            ser.write(b'AT+CMGL="ALL"\r')
            ser.flush()
            return _read_available(ser, timeout=2.5)


def delete_sms(index):
    return send_at(f"AT+CMGD={index}")


def parse_cmgl(raw):
    messages = []
    if not raw:
        return messages

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("+CMGL:"):
            header = line
            body = lines[i + 1] if i + 1 < len(lines) else ""
            parts = [p.strip() for p in header.split(",")]
            index = -1
            status = "UNKNOWN"
            number = "UNKNOWN"
            timestamp = ""
            try:
                index = int(parts[0].split(":")[1].strip())
            except Exception:
                pass
            if len(parts) > 1:
                status = parts[1].strip('"')
            if len(parts) > 2:
                number = parts[2].strip('"')
            if len(parts) > 4:
                timestamp = parts[4].strip('"')

            messages.append(
                {
                    "modem_index": index,
                    "status": status,
                    "number": number,
                    "timestamp": timestamp,
                    "message": body,
                }
            )
            i += 2
            continue
        i += 1
    return messages


def refresh_inbox():
    raw = read_sms()
    parsed = parse_cmgl(raw)
    with history_lock:
        INBOX.clear()
        INBOX.extend(parsed)
    return parsed


def sms_listener():
    logger.info("SMS listener started on %s", SERIAL_PORT)
    refresh_inbox()

    while True:
        try:
            with _open_serial() as ser:
                with lock:
                    ser.reset_input_buffer()
                    ser.write(b"AT+CMGF=1\r")
                    ser.flush()
                    _read_available(ser, timeout=0.6)
                    ser.write(b"AT+CNMI=2,1,0,0,0\r")
                    ser.flush()
                    _read_available(ser, timeout=0.6)
                while True:
                    line = ser.readline().decode(errors="ignore").strip()
                    if "+CMTI:" in line or "+CMT:" in line:
                        socketio.emit("inbox", refresh_inbox())
        except Exception as exc:
            logger.warning("sms_listener error: %s", exc)
            time.sleep(1)


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/send", methods=["POST"])
def send():
    payload = request.get_json(silent=True) or {}
    numbers = payload.get("numbers", [])
    message = payload.get("message", "")

    if not isinstance(numbers, list) or not message:
        return jsonify({"status": "error", "error": "Invalid payload"}), 400

    response = send_sms(numbers, message)
    return jsonify({"status": "ok", "results": response})


@app.route("/inbox")
def inbox():
    return jsonify(refresh_inbox())


@app.route("/read")
def read():
    return jsonify(refresh_inbox())


@app.route("/outbox")
def outbox():
    with history_lock:
        return jsonify(OUTBOX)


@app.route("/sent")
def sent():
    with history_lock:
        return jsonify(SENT)


@app.route("/delete/<int:index>")
def delete(index):
    resp = delete_sms(index)
    refresh_inbox()
    return jsonify({"status": "ok", "modem_response": resp})


HTML = """
<!doctype html>
<html>
<head>

<title>LubanCat SMS Pro+</title>

<script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>

<style>
* { box-sizing: border-box; }
body { margin:0; font-family: Inter, Arial, sans-serif; background:#0f172a; color:#0f172a; }
.container { max-width:1100px; margin:24px auto; padding:0 16px; }
.hero { background:linear-gradient(135deg,#2563eb,#9333ea); color:#fff; padding:20px; border-radius:16px; margin-bottom:16px; }
.layout { display:grid; grid-template-columns: 1fr 1fr; gap:16px; }
.card { background:#fff; border-radius:14px; padding:16px; box-shadow:0 10px 20px rgba(2,6,23,.15); }
.tabs { display:flex; gap:8px; margin-bottom:10px; }
.tab { border:none; border-radius:999px; padding:8px 12px; background:#e2e8f0; cursor:pointer; font-weight:600; }
.tab.active { background:#1d4ed8; color:#fff; }
input, textarea { width:100%; border:1px solid #cbd5e1; border-radius:8px; padding:10px; margin-top:6px; }
textarea { min-height:110px; resize:vertical; }
.btn { border:none; border-radius:8px; padding:10px 14px; background:#2563eb; color:#fff; font-weight:700; cursor:pointer; }
.btn.danger { background:#dc2626; }
.list { max-height:400px; overflow:auto; display:grid; gap:8px; }
.item { border:1px solid #e2e8f0; border-radius:10px; padding:10px; background:#f8fafc; }
.meta { font-size:12px; color:#475569; margin-bottom:6px; display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; }
.status { font-size:12px; font-weight:700; padding:2px 8px; border-radius:999px; }
.status.sent { color:#166534; background:#dcfce7; }
.status.failed { color:#991b1b; background:#fee2e2; }
.status.queued { color:#1e3a8a; background:#dbeafe; }
@media (max-width:900px) { .layout { grid-template-columns:1fr; } }

</style>

</head>

<body>

<div class="container">
  <div class="hero">
    <h2 style="margin:0;">📡 LubanCat SMS Pro+</h2>
    <div>Realtime Inbox / Outbox / Sent with modem control</div>
  </div>

  <div class="layout">
    <div class="card">
      <h3>✉️ Send SMS</h3>
      <label>Numbers (comma separated)</label>
      <input id="numbers" placeholder="0901111111,0902222222">
      <label>Message</label>
      <textarea id="message" placeholder="Type your SMS..."></textarea>
      <div style="margin-top:10px;">
        <button class="btn" onclick="sendSMS()">Send</button>
      </div>
    </div>

    <div class="card">
      <div class="tabs">
        <button id="tabInbox" class="tab active" onclick="showTab('inbox')">Inbox</button>
        <button id="tabOutbox" class="tab" onclick="showTab('outbox')">Outbox</button>
        <button id="tabSent" class="tab" onclick="showTab('sent')">Sent</button>
      </div>
      <div id="list" class="list"></div>
    </div>
  </div>
</div>

<script>

var socket = io();
var currentTab = "inbox";
var store = { inbox: [], outbox: [], sent: [] };

socket.on("inbox", function(data){ store.inbox = data || []; render(); });
socket.on("sent", function(msg){ store.sent.unshift(msg); store.outbox.unshift(msg); render(); });
socket.on("send_error", function(msg){ store.outbox.unshift(msg); render(); });

function showTab(tab){
  currentTab = tab;
  ["Inbox","Outbox","Sent"].forEach(function(name){
    document.getElementById("tab"+name).classList.toggle("active", name.toLowerCase() === tab);
  });
  render();
}

function esc(s){
  return (s || "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}

function render(){
  var list = document.getElementById("list");
  var items = store[currentTab] || [];
  if(!items.length){
    list.innerHTML = "<div class='item'>No messages</div>";
    return;
  }
  list.innerHTML = items.map(function(item){
    if(currentTab === "inbox"){
      return "<div class='item'>"
        + "<div class='meta'><span><b>"+esc(item.number)+"</b></span><span>"+esc(item.timestamp || "")+"</span></div>"
        + "<div style='white-space:pre-wrap'>"+esc(item.message)+"</div>"
        + "<div style='margin-top:8px'><button class='btn danger' onclick='deleteSMS("+item.modem_index+")'>Delete</button></div>"
      + "</div>";
    }
    var cls = "status " + esc(item.status || "queued");
    return "<div class='item'>"
      + "<div class='meta'><span><b>"+esc(item.number)+"</b></span><span>"+esc(item.created_at || "")+"</span></div>"
      + "<div style='white-space:pre-wrap'>"+esc(item.message)+"</div>"
      + "<div style='margin-top:6px'><span class='"+cls+"'>"+esc(item.status || "queued")+"</span></div>"
    + "</div>";
  }).join("");
}

function loadAll(){
  Promise.all([
    fetch('/inbox').then(r=>r.json()),
    fetch('/outbox').then(r=>r.json()),
    fetch('/sent').then(r=>r.json())
  ]).then(function(values){
    store.inbox = values[0] || [];
    store.outbox = values[1] || [];
    store.sent = values[2] || [];
    render();
  });
}

function sendSMS(){
  var numbers = document.getElementById("numbers").value.split(",");
  var message = document.getElementById("message").value;
  fetch('/send', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({numbers:numbers, message:message})
  }).then(r=>r.json()).then(function(){
    loadAll();
  });
}

function deleteSMS(index){
  fetch('/delete/' + index).then(r=>r.json()).then(function(){ loadAll(); });
}

loadAll();

</script>

</body>
</html>
"""


threading.Thread(target=sms_listener, daemon=True).start()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
