import serial
import time
from flask import Flask, render_template_string, request, redirect

SERIAL_PORT = "/dev/ttyUSB3"
BAUD = 115200

app = Flask(__name__)

def send_at(cmd, delay=1):
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
    time.sleep(0.5)
    ser.write((cmd + "\r").encode())
    time.sleep(delay)
    response = ser.read_all().decode(errors="ignore")
    ser.close()
    return response

def send_sms(number, message):
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
    time.sleep(0.5)

    ser.write(b'AT+CMGF=1\r')
    time.sleep(1)

    ser.write(f'AT+CMGS="{number}"\r'.encode())
    time.sleep(1)

    ser.write((message + "\x1A").encode())
    time.sleep(3)

    response = ser.read_all().decode(errors="ignore")
    ser.close()

    return response

def read_sms():
    return send_at('AT+CMGL="ALL"', 2)

HTML = """
<!doctype html>
<html>
<head>
<title>LubanCat SMS</title>
<style>
body { font-family: Arial; margin: 40px; }
textarea { width: 100%; height: 200px; }
input[type=text] { width: 100%; }
button { padding: 10px; }
</style>
</head>

<body>

<h2>📩 Send SMS</h2>

<form method="post" action="/send">
Number:<br>
<input type="text" name="number"><br><br>

Message:<br>
<textarea name="message"></textarea><br><br>

<button type="submit">Send</button>

</form>

<hr>

<h2>📥 Inbox</h2>

<pre>{{sms}}</pre>

<br>
<a href="/">Refresh</a>

</body>
</html>
"""

@app.route("/")
def index():
    sms = read_sms()
    return render_template_string(HTML, sms=sms)

@app.route("/send", methods=["POST"])
def send():
    number = request.form["number"]
    message = request.form["message"]
    send_sms(number, message)
    return redirect("/")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
