# EC20 Web Dashboard

## Cài đặt

```bash
pip install flask flask-socketio pyserial
```

## Chạy

```bash
python ec20_server.py
```

Mở trình duyệt: http://localhost:5000

## Cấu hình (trong ec20_server.py)

```python
AT_PORT  = "/dev/ttyUSB2"   # Đổi nếu khác
GPS_PORT = "/dev/ttyUSB1"   # Cổng NMEA
BAUD     = 115200
WEB_PORT = 5000
```

## Kiểm tra cổng

```bash
ls /dev/ttyUSB*
# Thường: ttyUSB0-DM, ttyUSB1-GPS, ttyUSB2-AT, ttyUSB3-PPP
```

## Quyền truy cập

```bash
sudo usermod -aG dialout $USER
# Logout và login lại
```
