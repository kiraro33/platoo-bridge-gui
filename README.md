# PlaToo Bridge GUI

หน้าต่างเล็ก ๆ สำหรับนำเอนจินหมากรุกไทย (UCI) ของคุณมาแข่งกับ PlaToo ที่ <https://platoo.kiraro.dev/bots>
ทำงานเหมือน `platoo-bridge.py` ทุกอย่าง เพียงแต่ไม่ต้องพิมพ์คำสั่ง

A small window that plays your own UCI Makruk engine against PlaToo at <https://platoo.kiraro.dev/bots>.
It does exactly what `platoo-bridge.py` does, without the command line.

## ใช้งาน / Use

1. ขอบัญชีบอทและโทเคนที่หน้า AI ปะทะ AI / Get a bot account and token on the site's AI vs AI page.
2. เปิด `PlatooBridge.exe` (จาก Actions ของ repo นี้) หรือ `python gui.py` (Python 3.9+ ไม่ต้องติดตั้งอะไรเพิ่ม) /
   Run `PlatooBridge.exe` (from this repo's Actions builds) or `python gui.py` (Python 3.9+, nothing to install).
3. วางโทเคน เลือกไฟล์เอนจิน ระดับ เวลา สี และจำนวนเกม แล้วกด เริ่ม /
   Paste the token, pick your engine file, level, time, colour and number of games, then press Start.

ตัวเลือก UCI ใส่บรรทัดละตัว เช่น `EvalFile=my-net.nnue`, `Hash=256`, `Threads=4` /
UCI options go one per line.

เปิดหน้าต่างไว้ตลอดเกม ถ้าปิดระหว่างเกม บอทจะแพ้เพราะทิ้งเกมหลัง 60 วินาที /
Keep the window open during a game: a bot that is away for 60 seconds loses by abandonment.

## สิ่งที่ส่งและไม่ส่ง / What is and is not sent

`platoo_bridge.py` ใน repo นี้คือไฟล์เดียวกับที่ดาวน์โหลดจากเว็บไซต์ ไม่ได้แก้ไข และ `gui.py` เพียงเรียกใช้มัน /
`platoo_bridge.py` here is the file the site offers for download, unmodified; `gui.py` only drives it.

- ส่งไปยังเซิร์ฟเวอร์ / Sent to the server: ตาเดินของเอนจิน ชื่อเอนจินและผู้พัฒนา (UCI `id`) รุ่นของ bridge
  และข้อมูลการคิดของแต่ละตา (ความลึก โหนด เวลา คะแนน แนวการเดิน) / your engine's moves, its UCI `id name` and
  `id author`, the bridge version, and per-move depth, nodes, time, score and PV.
- ไม่ส่ง / Never sent: ตัวโปรแกรมเอนจิน ไฟล์ NNUE หรือ eval ค่าตัวเลือก UCI หรือสิ่งอื่นใดในเครื่องของคุณ /
  your engine program, its NNUE or eval files, your UCI option values, or anything else on your computer.
- โทเคนอยู่ในหน่วยความจำเท่านั้น ไม่ถูกบันทึกลงไฟล์หรือ log / The token is kept in memory only, never written to
  a file or the log.

## พัฒนา / Develop

```bash
pip install pytest
pytest -q
```

GPL-3.0, see `LICENSE`.
