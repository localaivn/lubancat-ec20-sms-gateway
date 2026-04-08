import serial
import time
import threading

from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO

SERIAL_PORT = "/dev/ttyUSB3"
BAUD = 115200

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

lock = threading.Lock()


def send_at(cmd, delay=1):
    with lock:
        ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
        time.sleep(0.5)
        ser.write((cmd + "\r").encode())
        time.sleep(delay)
        resp = ser.read_all().decode(errors="ignore")
        ser.close()
        return resp


def send_sms(numbers, message):
    result = []

    for number in numbers:
        with lock:
            ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
            time.sleep(0.5)

            ser.write(b'AT+CMGF=1\r')
            time.sleep(1)

            ser.write(f'AT+CMGS="{number}"\r'.encode())
            time.sleep(1)

            ser.write((message + "\x1A").encode())
            time.sleep(3)

            resp = ser.read_all().decode(errors="ignore")
            ser.close()

            result.append(resp)

    return "\n".join(result)


def read_sms():
    return send_at('AT+CMGL="ALL"', 2)


def delete_sms(index):
    return send_at(f"AT+CMGD={index}")


def sms_listener():
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)

    send_at("AT+CNMI=2,1,0,0,0")

    while True:
        try:
            line = ser.readline().decode(errors="ignore")

            if "+CMTI:" in line:
                sms = read_sms()
                socketio.emit("sms", sms)

        except:
            pass


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/send", methods=["POST"])
def send():
    numbers = request.json["numbers"]
    message = request.json["message"]

    send_sms(numbers, message)

    return jsonify({"status": "ok"})


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
    socketio.run(app, host="0.0.0.0", port=5000)