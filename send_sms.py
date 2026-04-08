import serial
import time

SERIAL_PORT = "/dev/ttyUSB3"
PHONE_NUMBER = "+84904449899"
MESSAGE = "Hello from LubanCat EC20F"

ser = serial.Serial(SERIAL_PORT, 115200, timeout=1)

time.sleep(1)

def send_at(cmd, delay=1):
    ser.write((cmd + "\r").encode())
    time.sleep(delay)
    response = ser.read_all().decode(errors="ignore")
    print(response)
    return response

# Test
send_at("AT")

# Text mode
send_at("AT+CMGF=1")

# Send SMS
ser.write(f'AT+CMGS="{PHONE_NUMBER}"\r'.encode())
time.sleep(1)

ser.write((MESSAGE + "\x1A").encode())

time.sleep(5)

print(ser.read_all().decode(errors="ignore"))

ser.close()
