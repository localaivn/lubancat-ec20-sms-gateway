import serial
import time
import threading
import logging
import re
from datetime import datetime

from flask import Flask, render_template_string, request, jsonify
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
    with serial_lock:
        with _open_serial() as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write((cmd + "\r").encode())
            ser.flush()
            time.sleep(delay)
            return ser.read_all().decode(errors="ignore")


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
            with serial_lock:
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
    with serial_lock:
        with _open_serial() as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write(b"AT+CMGF=1\r")
            ser.flush()
            _read_available(ser, timeout=0.8)
            ser.write(b'AT+CMGL="ALL"\r')
            ser.flush()
            raw = _read_available(ser, timeout=2.5)
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
    while True:
        refresh_inbox()
        time.sleep(INBOX_POLL_INTERVAL)


def sms_listener():
    """Listen for +CMTI unsolicited notifications for instant inbox updates."""
    logger.info("SMS listener started on %s", SERIAL_PORT)
    send_at("AT+CNMI=2,1,0,0,0", 1)
    while True:
        try:
            with serial_lock:
                with _open_serial() as ser:
                    line = ser.readline().decode(errors="ignore")
            if "+CMTI:" in line:
                logger.info("New SMS notification: %s", line.strip())
                refresh_inbox()
        except Exception as exc:
            logger.warning("sms_listener error: %s", exc)
            time.sleep(1)


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(HTML)


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
# HTML frontend
# ---------------------------------------------------------------------------

HTML = """
<!doctype html>
<html>
<head>
<title>LubanCat SMS Pro+</title>
<script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>
<style>
* { box-sizing: border-box; }
body { margin:0; font-family: Inter, Arial, sans-serif; background:#0f172a; color:#0f172a; }
.container { max-width:1200px; margin:24px auto; padding:0 16px; }
.hero { background:linear-gradient(135deg,#2563eb,#9333ea); color:#fff; padding:20px; border-radius:16px; margin-bottom:16px; display:flex; justify-content:space-between; align-items:center; }
.hero-right { font-size:13px; opacity:.85; }
.layout { display:grid; grid-template-columns: 1fr 1fr; gap:16px; }
.card { background:#fff; border-radius:14px; padding:16px; box-shadow:0 10px 20px rgba(2,6,23,.15); }
.tabs { display:flex; gap:8px; margin-bottom:10px; flex-wrap:wrap; }
.tab { border:none; border-radius:999px; padding:8px 14px; background:#e2e8f0; cursor:pointer; font-weight:600; font-size:13px; }
.tab.active { background:#1d4ed8; color:#fff; }
input, textarea { width:100%; border:1px solid #cbd5e1; border-radius:8px; padding:10px; margin-top:6px; font-size:14px; }
textarea { min-height:110px; resize:vertical; }
.btn { border:none; border-radius:8px; padding:9px 14px; background:#2563eb; color:#fff; font-weight:700; cursor:pointer; font-size:13px; }
.btn.success { background:#16a34a; }
.btn.danger { background:#dc2626; }
.btn.warn { background:#d97706; }
.btn-row { display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }
.list { max-height:420px; overflow:auto; display:grid; gap:8px; }
.item { border:1px solid #e2e8f0; border-radius:10px; padding:10px; background:#f8fafc; }
.meta { font-size:12px; color:#475569; margin-bottom:6px; display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; }
.status { font-size:12px; font-weight:700; padding:2px 8px; border-radius:999px; }
.status.sent { color:#166534; background:#dcfce7; }
.status.failed { color:#991b1b; background:#fee2e2; }
.status.queued { color:#1e3a8a; background:#dbeafe; }
.badge { display:inline-block; background:#ef4444; color:#fff; border-radius:999px; font-size:11px; font-weight:700; padding:1px 6px; margin-left:4px; }
.toast { position:fixed; bottom:20px; right:20px; background:#1e293b; color:#fff; padding:12px 18px; border-radius:10px; font-size:14px; opacity:0; transition:opacity .3s; pointer-events:none; z-index:999; }
.toast.show { opacity:1; }
@media (max-width:900px) { .layout { grid-template-columns:1fr; } }
</style>
</head>
<body>
<div class="container">
  <div class="hero">
    <div>
      <h2 style="margin:0;">📡 LubanCat SMS Pro+</h2>
      <div style="font-size:13px;opacity:.85;">Realtime Inbox / Outbox / Sent with modem control</div>
    </div>
    <div class="hero-right" id="pollStatus">⏳ Loading...</div>
  </div>

  <div class="layout">
    <!-- Compose -->
    <div class="card">
      <h3 style="margin-top:0;">✉️ Compose SMS</h3>
      <label>Numbers <span style="color:#94a3b8;font-size:12px;">(comma separated)</span></label>
      <input id="numbers" placeholder="0901111111, 0902222222">
      <label>Message</label>
      <textarea id="message" placeholder="Type your SMS..."></textarea>
      <div class="btn-row">
        <button class="btn" onclick="queueSMS()">📥 Add to Outbox</button>
        <button class="btn success" onclick="sendAllQueued()">🚀 Send All Queued</button>
      </div>
    </div>

    <!-- Message lists -->
    <div class="card">
      <div class="tabs">
        <button id="tabInbox" class="tab active" onclick="showTab('inbox')">
          📨 Inbox <span id="badgeInbox" class="badge" style="display:none">0</span>
        </button>
        <button id="tabOutbox" class="tab" onclick="showTab('outbox')">
          📤 Outbox <span id="badgeOutbox" class="badge" style="display:none">0</span>
        </button>
        <button id="tabSent" class="tab" onclick="showTab('sent')">✅ Sent</button>
      </div>
      <div id="list" class="list"></div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
var socket = io();
var currentTab = "inbox";
var store = { inbox: [], outbox: [], sent: [] };

// SocketIO events
socket.on("inbox", function(data){
  store.inbox = data || [];
  updateBadge("Inbox", store.inbox.length);
  document.getElementById("pollStatus").textContent = "🟢 Last sync: " + new Date().toLocaleTimeString();
  if(currentTab === "inbox") render();
});

socket.on("outbox_update", function(entry){
  var idx = store.outbox.findIndex(function(m){ return m.id === entry.id; });
  if(idx >= 0) store.outbox[idx] = entry;
  else store.outbox.unshift(entry);
  var queued = store.outbox.filter(function(m){ return m.status === "queued"; }).length;
  updateBadge("Outbox", queued);
  if(currentTab === "outbox") render();
});

socket.on("sent_update", function(entry){
  var idx = store.sent.findIndex(function(m){ return m.id === entry.id; });
  if(idx < 0) store.sent.unshift(entry);
  if(currentTab === "sent") render();
});

function updateBadge(name, count){
  var el = document.getElementById("badge" + name);
  if(count > 0){ el.textContent = count; el.style.display = ""; }
  else { el.style.display = "none"; }
}

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
    list.innerHTML = "<div class='item' style='color:#94a3b8;text-align:center;padding:24px;'>No messages</div>";
    return;
  }
  list.innerHTML = items.map(function(item){
    if(currentTab === "inbox"){
      return "<div class='item'>"
        + "<div class='meta'><span><b>"+esc(item.number)+"</b></span><span>"+esc(item.timestamp || "")+"</span></div>"
        + "<div style='white-space:pre-wrap'>"+esc(item.message)+"</div>"
        + "<div style='margin-top:8px'><button class='btn danger' onclick='deleteSMS("+item.modem_index+")'>🗑 Delete</button></div>"
      + "</div>";
    }
    var cls = "status " + esc(item.status || "queued");
    var actions = "";
    if(item.status === "queued"){
      actions = "<button class='btn success' style='margin-top:8px;' onclick='sendOne("+item.id+")'>🚀 Send</button>";
    }
    return "<div class='item'>"
      + "<div class='meta'><span><b>"+esc(item.number)+"</b></span><span>"+esc(item.created_at || "")+"</span></div>"
      + "<div style='white-space:pre-wrap'>"+esc(item.message)+"</div>"
      + "<div style='margin-top:6px;display:flex;align-items:center;gap:8px;'><span class='"+cls+"'>"+esc(item.status || "queued")+"</span>"
      + (item.sent_at ? "<span style='font-size:11px;color:#64748b;'>sent "+esc(item.sent_at)+"</span>" : "")
      + "</div>"
      + actions
    + "</div>";
  }).join("");
}

function toast(msg){
  var el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(function(){ el.classList.remove("show"); }, 3000);
}

function loadAll(){
  Promise.all([
    fetch('/inbox').then(function(r){ return r.json(); }),
    fetch('/outbox').then(function(r){ return r.json(); }),
    fetch('/sent').then(function(r){ return r.json(); })
  ]).then(function(values){
    store.inbox = values[0] || [];
    store.outbox = values[1] || [];
    store.sent = values[2] || [];
    var queued = store.outbox.filter(function(m){ return m.status === "queued"; }).length;
    updateBadge("Inbox", store.inbox.length);
    updateBadge("Outbox", queued);
    document.getElementById("pollStatus").textContent = "🟢 Last sync: " + new Date().toLocaleTimeString();
    render();
  });
}

function queueSMS(){
  var numbers = document.getElementById("numbers").value.split(",");
  var message = document.getElementById("message").value.trim();
  if(!message){ toast("⚠️ Message is empty"); return; }
  fetch('/queue', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({numbers:numbers, message:message})
  }).then(function(r){ return r.json(); }).then(function(data){
    if(data.status === "ok"){
      toast("📥 Added " + data.queued.length + " message(s) to outbox");
      document.getElementById("message").value = "";
      showTab("outbox");
    }
  });
}

function sendOne(id){
  fetch('/send/' + id, { method:'POST' })
    .then(function(r){ return r.json(); })
    .then(function(data){
      if(data.entry && data.entry.status === "sent") toast("✅ Sent to " + data.entry.number);
      else toast("❌ Failed to send");
      loadAll();
    });
}

function sendAllQueued(){
  fetch('/send_all', { method:'POST' })
    .then(function(r){ return r.json(); })
    .then(function(data){
      toast("✅ Sent " + data.sent_count + " message(s)");
      loadAll();
    });
}

function deleteSMS(index){
  if(!confirm("Delete this SMS from modem?")) return;
  fetch('/delete/' + index).then(function(r){ return r.json(); }).then(function(){ loadAll(); });
}

loadAll();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Start background threads
# ---------------------------------------------------------------------------

threading.Thread(target=inbox_poller, daemon=True).start()
threading.Thread(target=sms_listener, daemon=True).start()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
