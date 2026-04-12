# LubanCat SMS Pro Gateway

SMS Gateway chuyên nghiệp sử dụng **LubanCat / Linux SBC** và **Quectel EC20/EC25** với giao diện Web realtime.

---

# 🚀 Tính năng

* ✅ Soạn SMS và đưa vào hàng đợi Outbox trước khi gửi
* ✅ Xác nhận gửi từng tin hoặc gửi tất cả cùng lúc
* ✅ Gửi nhiều số cùng lúc (comma separated)
* ✅ Nhận SMS realtime (WebSocket + polling định kỳ)
* ✅ Inbox tự động cập nhật mỗi 15 giây
* ✅ Thông báo tức thì khi có SMS mới (+CMTI)
* ✅ Xóa SMS khỏi modem
* ✅ Inbox / Outbox / Sent với badge đếm tin chưa đọc / chưa gửi
* ✅ Toast notification phản hồi thao tác
* ✅ Giao diện Web đẹp (tabs + dashboard)
* ✅ REST API đầy đủ
* ✅ Thread-safe serial access
* ✅ Tương thích nhiều module 4G

---

# 🧰 Phần cứng hỗ trợ

* LubanCat 1 / LubanCat 1N / LubanCat 1H
* Orange Pi / Raspberry Pi
* Linux SBC bất kỳ

Module 4G hỗ trợ:

* Quectel EC20 / EC20F
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
     ├── REST API (/queue, /send, /send_all, /inbox, /outbox, /sent, /delete)
     │
     ▼
WebSocket (Flask-SocketIO)
     │
     ├── inbox_poller  (polling mỗi 15s)
     └── sms_listener  (realtime +CMTI event)
          │
          ▼
     Serial Lock (thread-safe)
          │
          ▼
     Serial /dev/ttyUSB3
          │
          ▼
     4G Module (EC20F)
          │
          ▼
     SMS Network
```

---

# 🔄 Flow hoạt động

## Soạn & Gửi SMS (Outbox Queue)

```
User soạn tin + nhập số
        │
        ▼
POST /queue  →  Tin vào OUTBOX (status: queued)
        │
        ▼
User xem Outbox, bấm "🚀 Send" (từng tin)
   hoặc bấm "🚀 Send All Queued" (tất cả)
        │
        ▼
POST /send/<id>  hoặc  POST /send_all
        │
        ▼
Python gửi AT+CMGS qua serial
        │
        ▼
EC20F gửi SMS lên mạng
        │
        ▼
Tin chuyển sang SENT (status: sent / failed)
        │
        ▼
WebSocket emit → UI cập nhật realtime
```

---

## Nhận SMS

```
SMS gửi tới SIM
        │
        ▼
EC20F nhận SMS
        │
        ├── +CMTI event  →  sms_listener  →  refresh_inbox()  (tức thì)
        └── inbox_poller mỗi 15s           →  refresh_inbox()  (định kỳ)
                                                    │
                                                    ▼
                                           WebSocket emit "inbox"
                                                    │
                                                    ▼
                                           UI Inbox cập nhật realtime
```

---

# ⚙️ Công nghệ sử dụng

## Backend

* Python 3
* Flask
* Flask-SocketIO
* PySerial
* Threading (Lock, daemon threads)

## Frontend

* HTML / CSS / JavaScript
* Socket.IO 4.x

## Giao tiếp modem

* AT Command (AT+CMGF, AT+CMGS, AT+CMGL, AT+CMGD, AT+CNMI)
* Serial `/dev/ttyUSB3`

---

# 📦 Cài đặt

## 1. Clone repository

```bash
git clone https://github.com/yourrepo/lubancat-sms-pro.git
cd lubancat-sms-pro
```

## 2. Cài dependency

```bash
pip3 install flask flask-socketio pyserial
```

## 3. Cấu hình

Mở `sms_pro.py` và chỉnh các hằng số đầu file:

```python
SERIAL_PORT = "/dev/ttyUSB3"   # cổng serial của modem
BAUD = 115200
INBOX_POLL_INTERVAL = 15       # giây giữa các lần poll inbox
```

## 4. Chạy server

```bash
python3 sms_pro.py
```

## 5. Truy cập Web

```
http://IP_LUBANCAT:5000
```

---

# 📱 Giao diện

## ✉️ Compose SMS (trái)

* Nhập số điện thoại (nhiều số cách nhau bằng dấu phẩy)
* Nhập nội dung tin nhắn
* **"📥 Add to Outbox"** — đưa vào hàng đợi, chưa gửi
* **"🚀 Send All Queued"** — gửi tất cả tin đang chờ

## 📨 Inbox

* Hiển thị tất cả SMS trên modem
* Tự động cập nhật mỗi 15 giây và khi có tin mới
* Badge đỏ hiển thị số tin trong inbox
* Nút **🗑 Delete** xóa tin khỏi modem

## 📤 Outbox

* Danh sách tin đã soạn, chờ gửi (status: `queued`)
* Nút **🚀 Send** để xác nhận gửi từng tin
* Badge đỏ hiển thị số tin đang chờ
* Sau khi gửi, status chuyển thành `sent` hoặc `failed`

## ✅ Sent

* Lịch sử các tin đã gửi thành công
* Hiển thị thời gian gửi và trạng thái

---

# 🔌 REST API

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/inbox` | Lấy danh sách SMS trong inbox |
| `GET` | `/outbox` | Lấy danh sách tin trong outbox |
| `GET` | `/sent` | Lấy danh sách tin đã gửi |
| `POST` | `/queue` | Thêm tin vào outbox (chưa gửi) |
| `POST` | `/send/<id>` | Gửi một tin cụ thể theo id |
| `POST` | `/send_all` | Gửi tất cả tin đang queued |
| `GET` | `/delete/<index>` | Xóa SMS khỏi modem theo modem index |

### POST /queue

```json
{
  "numbers": ["0901111111", "0902222222"],
  "message": "Hello from LubanCat"
}
```

Response:

```json
{
  "status": "ok",
  "queued": [
    { "id": 1, "number": "0901111111", "message": "...", "status": "queued", "created_at": "..." }
  ]
}
```

### POST /send/<id>

Gửi tin có `id` tương ứng trong outbox.

Response:

```json
{
  "status": "ok",
  "entry": { "id": 1, "number": "0901111111", "status": "sent", "sent_at": "..." }
}
```

### POST /send_all

Gửi tất cả tin đang `queued`.

Response:

```json
{ "status": "ok", "sent_count": 2 }
```

---

# 📡 Serial Port Mapping (EC20F)

```
ttyUSB0 - AT command (chính)
ttyUSB1 - GPS NMEA
ttyUSB2 - AT secondary
ttyUSB3 - SMS / Modem  ← dùng cổng này
```

---

# 🔒 Thread Safety

Tất cả thao tác serial đều dùng `threading.Lock()`:

* Không conflict khi nhiều request đồng thời
* `inbox_poller` và `sms_listener` chạy song song an toàn
* Mỗi thao tác mở/đóng serial riêng biệt

---

# 🔧 Troubleshooting

## Permission denied trên serial port

```bash
sudo usermod -aG dialout $USER
sudo reboot
```

## Port bị chiếm bởi ModemManager

```bash
sudo systemctl stop ModemManager
sudo systemctl disable ModemManager
```

## Test AT Command thủ công

```
AT
AT+CMGF=1
AT+CMGL="ALL"
AT+CNMI=2,1,0,0,0
```

---

# 🎯 Use cases

* SMS Gateway cho IoT / cảnh báo hệ thống
* Hệ thống báo động
* Điều khiển từ xa qua SMS
* Thông báo backup / monitoring
* Tích hợp Home Assistant

---

# 🔮 Roadmap

* Login authentication
* SMS scheduling (gửi theo lịch)
* Docker support
* Home Assistant integration
* Telegram bridge
* Lưu lịch sử vào SQLite

---

# 📄 License

MIT License

---

# 👨‍💻 Author

LubanCat SMS Pro — Open Source SMS Gateway for SBC
