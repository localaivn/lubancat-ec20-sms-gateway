import serial
import time
import threading
import logging

from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO

SERIAL_PORT = "/dev/ttyUSB3"
BAUD = 115200

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

lock = threading.Lock()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _open_serial():
    return serial.Serial(SERIAL_PORT, BAUD, timeout=1)


def send_at(cmd, delay=1):
    with lock:
        with _open_serial() as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write((cmd + "\r").encode())
            ser.flush()
            time.sleep(delay)
            return ser.read_all().decode(errors="ignore")


def send_sms(numbers, message):
    result = []

    for number in numbers:
        number = number.strip()
        if not number:
            continue
        with lock:
            with _open_serial() as ser:
                time.sleep(0.2)
                ser.reset_input_buffer()
                ser.write(b'AT+CMGF=1\r')
                ser.flush()
                time.sleep(0.5)

                ser.write(f'AT+CMGS="{number}"\r'.encode())
                ser.flush()
                time.sleep(0.5)

                ser.write((message + "\x1A").encode())
                ser.flush()
                time.sleep(3)

                resp = ser.read_all().decode(errors="ignore")
                result.append(f"=== {number} ===\n{resp}")

    return "\n".join(result)


def read_sms():
    return send_at('AT+CMGL="ALL"', 2)


def delete_sms(index):
    return send_at(f"AT+CMGD={index}")


def sms_listener():
    logger.info("SMS listener started on %s", SERIAL_PORT)
    send_at("AT+CNMI=2,1,0,0,0", 1)

    while True:
        try:
            with lock:
                with _open_serial() as ser:
                    line = ser.readline().decode(errors="ignore")

            if "+CMTI:" in line:
                sms = read_sms()
                socketio.emit("sms", sms)
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
    return jsonify({"status": "ok", "modem_response": response})


@app.route("/read")
def read():
    return read_sms()


@app.route("/delete/<int:index>")
def delete(index):
    delete_sms(index)
    return "OK"


HTML = """
<!doctype html>
<html>
<head>

<title>LubanCat SMS Pro</title>

<script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>

<style>
body {
font-family: Arial;
background: #f5f5f5;
padding: 20px;
}

.card {
background: white;
padding: 20px;
border-radius: 10px;
margin-bottom: 20px;
box-shadow: 0 2px 10px rgba(0,0,0,0.1);
}

textarea {
width: 100%;
height: 100px;
}

input {
width: 100%;
padding: 10px;
}

button {
padding: 10px;
background: #2196F3;
color: white;
border: none;
border-radius: 5px;
}

</style>

</head>

<body>

<h2>LubanCat SMS Pro</h2>

<div class="card">
<h3>Send SMS</h3>

Numbers (comma separated)
<input id="numbers">

Message
<textarea id="message"></textarea>

<br><br>

<button onclick="send()">Send</button>

</div>

<div class="card">
<h3>Inbox</h3>

<pre id="sms"></pre>

</div>

<script>

var socket = io();

socket.on("sms", function(data){

document.getElementById("sms").innerText = data;

});

function load(){
fetch('/read')
.then(r=>r.text())
.then(t=>{

sms.innerText = t

})
}

function send(){

var numbers = document.getElementById("numbers").value.split(",")
var message = document.getElementById("message").value

fetch('/send',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({numbers:numbers,message:message})

})

}

load()

</script>

</body>
</html>
"""


threading.Thread(target=sms_listener, daemon=True).start()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
