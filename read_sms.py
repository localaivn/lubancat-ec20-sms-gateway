import serial
import time

SERIAL_PORT = "/dev/ttyUSB3"
BAUD = 115200

try:
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
    print(f"✅ Đã kết nối {SERIAL_PORT}")
except Exception as e:
    print(f"❌ Không thể mở {SERIAL_PORT}: {e}")
    print("Kiểm tra:")
    print("  1. Modem đã cắm chưa?")
    print("  2. Port đúng chưa? (ls /dev/ttyUSB*)")
    print("  3. Permission: sudo usermod -aG dialout $USER")
    exit(1)

time.sleep(1)

def send(cmd, delay=1):
    ser.reset_input_buffer()  # xóa data cũ trong buffer
    ser.write((cmd + "\r").encode())
    ser.flush()  # đảm bảo data được gửi đi
    time.sleep(delay)
    
    # Đọc nhiều lần để đảm bảo nhận hết response
    resp = b""
    deadline = time.time() + delay
    while time.time() < deadline:
        chunk = ser.read_all()
        if chunk:
            resp += chunk
        time.sleep(0.1)
    
    decoded = resp.decode(errors="ignore")
    print(decoded)
    return decoded

# Test kết nối
send("AT")

# Text mode
send("AT+CMGF=1")

# Đọc tất cả SMS
print("\n📨 Đọc tất cả SMS từ modem...")
response = send('AT+CMGL="ALL"', 2)

# Parse và hiển thị đẹp hơn
print("\n" + "="*60)
lines = response.splitlines()
for i, line in enumerate(lines):
    if line.startswith("+CMGL:"):
        print(f"\n📩 {line}")
        # Dòng tiếp theo thường là nội dung SMS
        if i + 1 < len(lines):
            print(f"   💬 {lines[i+1]}")
print("="*60)

ser.close()