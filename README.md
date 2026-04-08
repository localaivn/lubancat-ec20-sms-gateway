# LubanCat SMS Pro Gateway

SMS Gateway chuyên nghiệp sử dụng **LubanCat / Linux SBC** và **Quectel EC20/EC25** với giao diện Web realtime.

---

# 🚀 Tính năng

* ✅ Gửi SMS
* ✅ Gửi nhiều số cùng lúc
* ✅ Nhận SMS realtime (WebSocket)
* ✅ Xóa SMS
* ✅ Inbox trực quan
* ✅ Giao diện Web đẹp
* ✅ REST API
* ✅ Hoạt động realtime
* ✅ Tương thích nhiều module 4G

---

# 🧰 Phần cứng hỗ trợ

* LubanCat 1 / LubanCat 1N / LubanCat 1H
* Orange Pi / Raspberry Pi
* Linux SBC bất kỳ

Module 4G hỗ trợ:

* Quectel EC20
* Quectel EC20F
* Quectel EC25
* SIM7600
* SIMCOM 4G modules

---

# 🏗️ Kiến trúc hệ thống

```
Browser UI
     │
     ▼
Flask Web Server
     │
     ▼
WebSocket (Realtime)
     │
     ▼
SMS Service (Python)
     │
     ▼
Serial (ttyUSB3)
     │
     ▼
4G Module (EC20F)
     │
     ▼
SMS Network
```

---

# 🔄 Flow hoạt động

## Gửi SMS

```
User nhập nội dung
     │
     ▼
Flask nhận request
     │
     ▼
Python gửi AT Command
     │
     ▼
EC20F gửi SMS
     │
     ▼
Network gửi SMS
```

---

## Nhận SMS

```
SMS gửi tới SIM
     │
     ▼
EC20F nhận SMS
     │
     ▼
+CMTI event
     │
     ▼
Python listener
     │
     ▼
WebSocket emit
     │
     ▼
UI realtime update
```

---

# ⚙️ Công nghệ sử dụng

## Backend

* Python 3
* Flask
* Flask-SocketIO
* PySerial
* Threading

## Frontend

* HTML
* CSS
* JavaScript
* Socket.IO

## Giao tiếp modem

* AT Command
* Serial /dev/ttyUSB3

---

# 📦 Cài đặt

## 1. Clone repository

```
git clone https://github.com/yourrepo/lubancat-sms-pro.git
cd lubancat-sms-pro
```

---

## 2. Cài dependency

```
pip3 install flask flask-socketio pyserial eventlet
```

---

## 3. Cấu hình port modem

Mở file:

```
sms_pro.py
```

Sửa:

```
SERIAL_PORT = "/dev/ttyUSB3"
```

---

## 4. Chạy server

```
python3 sms_pro.py
```

---

## 5. Truy cập Web

```
http://IP_LUBANCAT:5000
```

---

# 📱 Giao diện

## Send SMS

* Nhập số điện thoại
* Nhập nội dung
* Gửi nhiều số

Ví dụ:

```
0901111111,0902222222
```

---

## Inbox

* Hiển thị SMS
* Realtime update
* Xóa SMS

---

# 🔌 API

## Gửi SMS

```
POST /send
```

Body:

```
{
  "numbers":["0901111111"],
  "message":"Hello"
}
```

---

## Đọc SMS

```
GET /read
```

---

## Xóa SMS

```
GET /delete/<index>
```

---

# 📡 Serial Port Mapping

Thông thường:

```
ttyUSB0 - AT command
ttyUSB1 - GPS NMEA
ttyUSB2 - AT secondary
ttyUSB3 - SMS / Modem
```

---

# 🔒 Thread-safe

Script sử dụng:

```
threading.Lock()
```

Đảm bảo:

* Không treo modem
* Không conflict serial
* Hoạt động ổn định

---

# ⚡ Hiệu năng

* RAM: ~20MB
* CPU: rất thấp
* Realtime WebSocket

---

# 🔧 Troubleshooting

## Permission denied

```
sudo usermod -aG dialout $USER
sudo reboot
```

---

## Port busy

```
sudo systemctl stop ModemManager
sudo systemctl disable ModemManager
```

---

# 🧪 Test AT Command

```
AT
AT+CMGF=1
AT+CMGL="ALL"
```

---

# 🎯 Use cases

* SMS Gateway
* IoT SMS alert
* Alarm system
* Remote control
* Backup notification
* Home Assistant integration

---

# 🔮 Roadmap

* Login authentication
* SMS scheduling
* REST API full
* Docker support
* Home Assistant integration
* Telegram bridge

---

# 📄 License

MIT License

---

# 👨‍💻 Author

LubanCat SMS Pro
Open Source SMS Gateway for SBC

---

# ⭐ Nếu thấy hữu ích

Hãy star repo để ủng hộ dự án.
