# Orange Pi EC20 Minicom // SMS Gateway

> Phần mềm quản lý SMS qua modem Quectel EC20F trên Orange Pi, giao diện web realtime, hỗ trợ tiếng Việt đầy đủ.

Được tạo ra với ❤ bởi [Tony Trần](https://github.com/thanhtantran) · Copyright 2026 © · [Orange Pi Việt Nam](https://orangepi.vn/)

---

## Mục lục

- [Giới thiệu](#giới-thiệu)
- [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
- [Cài đặt](#cài-đặt)
- [Cấu hình](#cấu-hình)
- [Khởi động](#khởi-động)
- [Kiến trúc hoạt động](#kiến-trúc-hoạt-động)
- [Cách gửi SMS tiếng Việt — PDU mode](#cách-gửi-sms-tiếng-việt--pdu-mode)
- [REST API](#rest-api)
- [WebSocket Events](#websocket-events)
- [Giao diện Web](#giao-diện-web)
- [Cấu trúc dự án](#cấu-trúc-dự-án)
- [Gỡ lỗi thường gặp](#gỡ-lỗi-thường-gặp)

---

## Giới thiệu

**Orange Pi EC20 SMS Gateway** là ứng dụng web cho phép gửi và nhận SMS thông qua modem Quectel EC20F gắn trên board Orange Pi (hoặc bất kỳ máy Linux nào có USB modem).

**Tính năng chính:**

- 📨 **Nhận SMS realtime** — tin nhắn mới hiển thị ngay lập tức không cần reload
- ✉ **Gửi SMS tiếng Việt** — hỗ trợ đầy đủ Unicode/tiếng Việt có dấu qua PDU mode
- 🗑 **Xoá linh hoạt** — xoá từng tin, xoá nhiều tin đã chọn, hoặc xoá hàng loạt theo loại
- 📡 **Giám sát modem** — xem model, firmware, nhà mạng, cường độ tín hiệu, IMSI, số SIM
- 🌙 **Dark / Light mode** — chuyển đổi giao diện, tự lưu lựa chọn vào trình duyệt
- ⚡ **WebSocket** — toàn bộ cập nhật realtime, không cần polling từ frontend

**Phần cứng được kiểm tra:**

| Thiết bị | Trạng thái |
|----------|-----------|
| Quectel EC20F (`AT+GMM` → `EC20F`) | ✅ Hoạt động |
| USB ID `2c7c:0125` | ✅ |
| Orange Pi / Ubuntu 20.04 / Python 3.8 | ✅ |

---

## Yêu cầu hệ thống

- Python **3.8+**
- Modem Quectel EC20/EC25 kết nối USB
- SIM card đã kích hoạt dịch vụ SMS
- Hệ điều hành: Linux (Ubuntu / Debian / Armbian)

---

## Cài đặt

**1. Copy 2 file vào cùng một thư mục:**

```
ec20_server.py
ec20_ui.html
```

**2. Cài đặt thư viện Python:**

```bash
pip install flask flask-socketio pyserial
```

**3. Cấp quyền truy cập serial port:**

```bash
sudo usermod -aG dialout $USER
# Logout và login lại để có hiệu lực
```

**4. Kiểm tra modem đã nhận chưa:**

```bash
lsusb | grep Quectel
ls /dev/ttyUSB*
```

Kết quả mong đợi: `ttyUSB0`, `ttyUSB1`, `ttyUSB2`, `ttyUSB3`

| Port | Chức năng |
|------|-----------|
| `/dev/ttyUSB0` | DM / Diagnostics |
| `/dev/ttyUSB1` | NMEA (GPS — EC20F không có GPS) |
| `/dev/ttyUSB2` | **AT Commands ← dùng cái này** |
| `/dev/ttyUSB3` | PPP / Data |

---

## Cấu hình

Mở `ec20_server.py`, sửa các biến ở đầu file:

```python
AT_PORT  = "/dev/ttyUSB2"   # Cổng AT command của modem
BAUD     = 115200            # Tốc độ baud (mặc định EC20)
WEB_PORT = 5000              # Cổng web server
```

---

## Khởi động

```bash
python ec20_server.py
```

Mở trình duyệt: **`http://<IP-của-board>:5000`**

Ví dụ: `http://192.168.88.10:5000`

Để chạy nền (background):

```bash
nohup python ec20_server.py > sms_gateway.log 2>&1 &
```

---

## Kiến trúc hoạt động

```
┌──────────────────────────────────────────────────────────┐
│                    Trình duyệt (Web UI)                  │
│              ec20_ui.html — HTML / CSS / JS              │
│                                                          │
│   REST API calls ──────────────────► HTTP / JSON         │
│   Realtime updates ◄────────────── WebSocket (Socket.IO) │
└───────────────────────┬──────────────────────────────────┘
                        │ HTTP + WebSocket
┌───────────────────────▼──────────────────────────────────┐
│              ec20_server.py  (Flask + SocketIO)           │
│                                                           │
│  ┌──────────┐  ┌────────────────┐  ┌──────────────────┐  │
│  │  Modem   │  │   SMS class    │  │  Background      │  │
│  │  class   │  │                │  │  SMS Poller      │  │
│  │          │  │ send() → PDU   │  │  (mỗi 5 giây)   │  │
│  │ send_at()│  │ read_all()     │  │  phát new_sms    │  │
│  │ get_stat │  │ delete()       │  │  event qua WS    │  │
│  └─────┬────┘  └───────┬────────┘  └──────────────────┘  │
└────────┼───────────────┼───────────────────────────────┘
         │               │  AT Commands qua pyserial
┌────────▼───────────────▼───────────────────────────────┐
│              /dev/ttyUSB2  —  115200 baud               │
└────────────────────────┬───────────────────────────────┘
                         │ USB
               ┌─────────▼──────────┐
               │   Quectel EC20F    │
               └─────────┬──────────┘
                         │ RF
                   [ Mạng GSM / 4G ]
```

### Luồng khởi động

```
python ec20_server.py
  │
  ├── Modem.__init__()
  │     ├── Mở /dev/ttyUSB2 @ 115200 baud
  │     └── Gửi AT → OK  (xác nhận kết nối)
  │
  ├── SMS.__init__()
  │     ├── AT+CMGF=0          (PDU mode — dùng để gửi)
  │     ├── AT+CNMI=2,2,0,0,0  (bật thông báo SMS mới)
  │     ├── AT+CMGF=1          (text mode — dùng để đọc)
  │     └── AT+CSCS="GSM"
  │
  ├── Thread: _sms_poller()    (daemon, vòng lặp mỗi 5s)
  │
  └── Flask + SocketIO lắng nghe :5000
```

### Luồng nhận SMS mới

```
Mạng GSM gửi SMS vào SIM
  │
  └── Modem lưu vào bộ nhớ SIM
        │
        └── _sms_poller() mỗi 5 giây: AT+CMGL="ALL"
              │
              ├── So sánh với _known_sms (set đã thấy)
              │
              └── Nếu có tin mới + chưa đọc:
                    socketio.emit("new_sms", { index, from, body, ... })
                          │
                          └── Trình duyệt nhận → toast thông báo + cập nhật list
```

---

## Cách gửi SMS tiếng Việt — PDU mode

EC20F không hỗ trợ `AT+CSCS="UCS2"` ở text mode, nên phải dùng **PDU mode** (chuẩn GSM 03.40).

### Vì sao phải dùng PDU?

| Phương pháp | Kết quả |
|-------------|---------|
| Text mode + `CSCS=GSM` | Chỉ hỗ trợ ASCII 7-bit, tiếng Việt mất dấu |
| Text mode + `CSCS=UCS2` | EC20F báo `ERROR`, không hỗ trợ |
| **PDU mode** | ✅ Hoạt động, mang UCS2 bên trong PDU |

### Cấu trúc PDU gửi đi

```
 00      11    00     0B  91  8490XXXXXX  00  08  AA   XX   <UCS2 hex>
  │       │     │     └──────────┬──────┘  │   │   │    │        │
  │       │     │                │          │   │   │    │        └─ Nội dung (UTF-16BE hex)
  │       │     │                │          │   │   │    └────────── User Data Length (bytes)
  │       │     │                │          │   │   └─────────────── Validity Period: AA = 4 ngày
  │       │     │                │          │   └─────────────────── DCS: 08 = UCS2 encoding
  │       │     │                │          └─────────────────────── PID: 00
  │       │     │                └────────────────────────────────── Destination Address
  │       │     └───────────────────────────────────────────────────  Message Reference: 00
  │       └─────────────────────────────────────────────────────────  PDU Type: 11 = SMS-SUBMIT + VP
  └─────────────────────────────────────────────────────────────────  SMSC: 00 = dùng mặc định mạng
```

### Luồng AT command khi gửi

```
Acquire serial lock
  │
  ├── AT+CMGF=0\r\n              → OK     (switch PDU mode)
  │   [delay 400ms, flush buffer]
  │
  ├── AT+CMGS=<tpdu_length>\r\n  → ...    (số bytes TPDU, không tính SMSC)
  │   [đọc từng byte, chờ ký tự ">"]
  │
  ├── <PDU hex string>\x1A       → +CMGS: <mr>   (gửi PDU + Ctrl+Z)
  │   [chờ "+CMGS" hoặc "ERROR", timeout 20s]
  │
  └── AT+CMGF=1\r\n              → OK     (khôi phục text mode, luôn chạy)

Release serial lock
```

---

## REST API

Base URL: `http://<host>:5000`

---

### `GET /api/status`

Lấy trạng thái modem hiện tại.

**Response:**

```json
{
  "connected": true,
  "model": "EC20F",
  "firmware": "EC20CEHCLGR06A04M1G",
  "operator": "Viettel",
  "network": "Da dang ky (Home)",
  "signal_raw": 18,
  "signal_dbm": -77,
  "signal_pct": 58,
  "imsi": "452011234567890",
  "own_number": "+84901234567"
}
```

| Trường | Kiểu | Mô tả |
|--------|------|-------|
| `connected` | bool | Modem phản hồi AT không |
| `signal_raw` | int | Giá trị CSQ thô (0–31, 99 = không xác định) |
| `signal_dbm` | int\|null | Đổi sang dBm: `-113 + 2 × raw` |
| `signal_pct` | int | Phần trăm tín hiệu (0–100%) |

---

### `GET /api/sms/list`

Lấy tất cả SMS trong bộ nhớ SIM.

**Response:**

```json
[
  {
    "index": "1",
    "status": "REC UNREAD",
    "from": "+84901234567",
    "body": "Xin chào! Đây là tin nhắn tiếng Việt",
    "timestamp": "26/10/23,14:30:00+28",
    "unread": true
  },
  {
    "index": "2",
    "status": "REC READ",
    "from": "Viettel",
    "body": "Tai khoan con 50,000 VND",
    "timestamp": "26/10/23,10:00:00+28",
    "unread": false
  }
]
```

| Trường | Mô tả |
|--------|-------|
| `index` | Vị trí trong bộ nhớ SIM, dùng để xoá |
| `status` | `REC UNREAD` / `REC READ` / `STO SENT` |
| `from` | Số điện thoại hoặc tên người gửi |
| `body` | Nội dung (đã tự động decode UCS2 nếu cần) |
| `unread` | `true` nếu chưa đọc |

---

### `POST /api/sms/send`

Gửi SMS. Hỗ trợ tiếng Việt, emoji, mọi ký tự Unicode.

**Request body:**

```json
{
  "number": "+84901234567",
  "message": "Xin chào từ Orange Pi! 🍊"
}
```

**Response thành công:**

```json
{
  "success": true,
  "number": "+84901234567",
  "message": "Xin chào từ Orange Pi! 🍊",
  "timestamp": "16/05/2026 12:30:00",
  "error": null
}
```

**Response thất bại:**

```json
{
  "success": false,
  "number": "+84901234567",
  "message": "...",
  "timestamp": "16/05/2026 12:30:00",
  "error": "Khong co dau nhac '>': b'ERROR'"
}
```

**Curl example:**

```bash
curl -X POST http://localhost:5000/api/sms/send \
  -H "Content-Type: application/json" \
  -d '{"number":"+84901234567","message":"Xin chao tu EC20!"}'
```

---

### `DELETE /api/sms/delete/<index>`

Xoá một tin nhắn theo index SIM.

```bash
curl -X DELETE http://localhost:5000/api/sms/delete/3
```

**Response:**

```json
{ "success": true }
```

---

### `POST /api/sms/delete-bulk`

Xoá nhiều tin nhắn. Có 2 chế độ:

**Chế độ A — Xoá theo danh sách index:**

```json
{ "indices": ["1", "3", "5"] }
```

Response:

```json
{ "success": true, "count": 3 }
```

**Chế độ B — Xoá theo loại (`flag`):**

```json
{ "flag": 4 }
```

| `flag` | Xoá những tin nào |
|--------|-------------------|
| `1` | Tin đã đọc |
| `2` | Tin đã đọc + đã gửi |
| `3` | Tin đã đọc + đã gửi + chưa gửi |
| `4` | **Tất cả không ngoại lệ** |

Tương đương AT command: `AT+CMGD=1,<flag>`

Response:

```json
{ "success": true }
```

---

## WebSocket Events

Kết nối qua Socket.IO tại `ws://<host>:5000`.

### Server → Client

| Event | Payload | Khi nào |
|-------|---------|---------|
| `status_update` | Object (giống `/api/status`) | Ngay khi client connect |
| `sms_list` | Array SMS (giống `/api/sms/list`) | Sau khi client emit `request_sms` |
| `new_sms` | Object SMS đơn lẻ | Khi background poller phát hiện tin mới chưa đọc |
| `sent_sms` | Object kết quả gửi | Khi gửi SMS thành công |

### Client → Server

| Event | Mô tả |
|-------|-------|
| `request_sms` | Yêu cầu toàn bộ danh sách SMS |
| `request_status` | Yêu cầu trạng thái modem |

### Ví dụ kết nối từ JavaScript

```javascript
const socket = io("http://192.168.88.10:5000");

// Nhận SMS mới realtime
socket.on("new_sms", (msg) => {
  console.log(`Tin mới từ ${msg.from}: ${msg.body}`);
});

// Trạng thái modem
socket.on("status_update", (s) => {
  console.log(`Tín hiệu: ${s.signal_dbm} dBm — ${s.operator}`);
});

// Yêu cầu danh sách SMS
socket.emit("request_sms");
socket.on("sms_list", (list) => {
  console.log(`Tổng ${list.length} tin, ${list.filter(m => m.unread).length} chưa đọc`);
});
```

### Ví dụ gửi SMS từ Python client

```python
import socketio

sio = socketio.Client()
sio.connect("http://192.168.88.10:5000")

# Hoặc dùng REST API trực tiếp
import requests
r = requests.post("http://192.168.88.10:5000/api/sms/send", json={
    "number": "+84901234567",
    "message": "Xin chào từ Python client!"
})
print(r.json())
```

---

## Giao diện Web

### Tab 📨 SMS

| Tính năng | Mô tả |
|-----------|-------|
| Danh sách tin nhắn | Hiển thị người gửi, preview, thời gian. Badge xanh = chưa đọc |
| Tìm kiếm | Lọc realtime theo số điện thoại hoặc nội dung |
| Chọn nhiều | Checkbox chọn từng tin, "Chọn tất cả" |
| Xoá hàng loạt | Modal chọn loại tin cần xoá |
| Xem chi tiết | Click vào tin → xem nội dung đầy đủ |
| Trả lời nhanh | Nút "↩ Trả lời" → hiện ô soạn ngay bên dưới |
| Soạn mới | Nút "Soạn tin nhắn mới" → form với bộ đếm ký tự |
| Bộ đếm ký tự | Tự nhận diện GSM 7-bit (160 ký/SMS) vs UCS2 (70 ký/SMS) |

### Tab 📡 Modem

Hiển thị: Model, Firmware, Nhà mạng, Loại kết nối, Tín hiệu (dBm + %), IMSI, Số SIM.

### Phím tắt

| Phím | Tác dụng |
|------|----------|
| `Ctrl + Enter` | Gửi tin (trong form soạn hoặc trả lời) |
| `Esc` | Đóng modal / tắt khung trả lời |

---

## Cấu trúc dự án

```
.
├── ec20_server.py     # Backend: Flask + SocketIO + pyserial
├── ec20_ui.html       # Frontend: single-file HTML/CSS/JS
└── README.md          # Tài liệu này
```

### Các thành phần trong `ec20_server.py`

```
Modem
 ├── send_at(cmd, wait, timeout)  Gửi AT command, đọc response có timeout
 └── get_status()                 Gọi AT+GMM, QGMR, CSQ, COPS, CREG, CIMI, CNUM

SMS
 ├── _init()           AT+CMGF + CNMI + CSCS khi khởi động
 ├── send()            Gửi qua PDU mode, giữ lock suốt quá trình
 ├── read_all()        AT+CMGL="ALL", parse từng dòng, auto-decode UCS2
 ├── delete()          AT+CMGD=<n>
 └── delete_all()      AT+CMGD=1,<flag>

Helpers
 ├── _build_pdu()      Tạo PDU hex (SMSC + TPDU) từ số và nội dung
 ├── _encode_da()      Encode số điện thoại sang semi-octet GSM
 ├── _decode_ucs2()    Decode UCS2 hex → Python str (UTF-8)
 └── _is_ucs2_hex()    Phát hiện body tin có phải UCS2 hex không

Background
 └── _sms_poller()     Thread daemon, poll mỗi 5s, emit "new_sms" qua SocketIO
```

---

## Gỡ lỗi thường gặp

### Modem không nhận diện được

```bash
lsusb | grep Quectel
ls /dev/ttyUSB*
dmesg | tail -30
sudo chmod 666 /dev/ttyUSB*   # Cấp quyền tạm thời để test
```

### Kiểm tra nhanh bằng minicom

```bash
minicom -D /dev/ttyUSB2 -b 115200
```

Gõ lần lượt:

```
AT              → phải trả OK
AT+CREG?        → phải có 0,1 hoặc 0,5 (đã đăng ký mạng)
AT+CSQ          → giá trị đầu tiên > 10 là đủ tín hiệu
AT+CIMI         → trả về 15 chữ số = SIM đang hoạt động
```

### Lỗi gửi `+CMS ERROR: 305`

Số điện thoại bị encode sai. Đảm bảo truyền đúng định dạng quốc tế:

```json
{ "number": "+84901234567" }
```

### Tin nhắn nhận hiển thị ký tự lạ

Modem gửi body dạng UCS2 hex nhưng không được nhận diện. Kiểm tra log server xem `raw_body` có phải chuỗi hex thuần không.

### Port bị chiếm bởi tiến trình khác

```bash
fuser /dev/ttyUSB2      # Xem PID đang giữ port
kill <PID>              # Giải phóng
```

### Chạy song song minicom và server

**Không thể** — một lúc chỉ một tiến trình được giữ `/dev/ttyUSB2`. Tắt minicom trước khi chạy server.
