<p align="center"><img src="logo.png" alt="PlaToo makruk engine" width="200"></p>

# PlaToo Bridge GUI

หน้าต่างเล็ก ๆ สำหรับนำเอนจินหมากรุกไทย (UCI) ของคุณมาแข่งกับ PlaToo ที่ <https://play.platoomakruk.in.th/bots>
ทำงานเหมือน `platoo-bridge.py` ทุกอย่าง เพียงแต่ไม่ต้องพิมพ์คำสั่ง

A small window that plays your own UCI Makruk engine against PlaToo at <https://play.platoomakruk.in.th/bots>.
It does exactly what `platoo-bridge.py` does, without the command line.

## ใช้งาน / Use

1. ขอบัญชีบอทและโทเคนที่หน้า AI ปะทะ AI / Get a bot account and token on the site's AI vs AI page.
2. ดาวน์โหลด `PlatooBridge.exe` จากหน้า [Releases](https://github.com/kiraro33/platoo-bridge-gui/releases/latest)
   แล้วเปิด หรือรัน `python gui.py` (Python 3.9+ ไม่ต้องติดตั้งอะไรเพิ่ม) /
   Download `PlatooBridge.exe` from [Releases](https://github.com/kiraro33/platoo-bridge-gui/releases/latest),
   or run `python gui.py` (Python 3.9+, nothing to install).
3. วางโทเคน เลือกไฟล์เอนจิน ระดับ เวลา สี และจำนวนเกม แล้วกด เริ่ม /
   Paste the token, pick your engine file, level, time, colour and number of games, then press Start.

ตัวเลือกมาตรฐาน / Standard options:

- EvalFile: ไฟล์ NNUE ของคุณ เว้นว่างเพื่อใช้ไฟล์ในตัวเอนจิน / your NNUE file; leave empty for the engine's built-in net.
- Threads (ค่าเริ่มต้น 1) และ Hash (ค่าเริ่มต้น 64 MB) / Threads (default 1) and Hash (default 64 MB).
- คิดล่วงหน้าตอนตาคู่แข่ง (เปิดเป็นค่าเริ่มต้น): เอนจินคิดต่อระหว่างที่ PlaToo คิด (`go ponder` / `ponderhit`) /
  Ponder (on by default): your engine keeps thinking while PlaToo thinks.
- ไม่ใช้โบนัสเวลาเมื่อเหลือน้อย (เปิดเป็นค่าเริ่มต้น) / No low-time bonus (on by default).

ตัวเลือก UCI อื่น ๆ ใส่บรรทัดละตัวแบบ `NAME=VALUE` / Other UCI options go one per line as `NAME=VALUE`.

เปิดหน้าต่างไว้ตลอดเกม ถ้าปิดระหว่างเกม บอทจะแพ้เพราะทิ้งเกมหลัง 60 วินาที /
Keep the window open during a game: a bot that is away for 60 seconds loses by abandonment.

## คำเตือนตอนเปิดครั้งแรก / First-run warning

ไฟล์ exe ไม่ได้เซ็นรับรอง (code signing) Windows จึงอาจขึ้นหน้าจอ "Windows protected your PC"
ให้กด **More info** แล้วกด **Run anyway** โปรแกรมบางตัวอาจแจ้งว่าเป็นไวรัสโดยเข้าใจผิด เพราะ exe ที่สร้างด้วย
PyInstaller มักโดนแบบนี้

ตรวจได้ว่าไฟล์ไม่ถูกแก้: exe ทุกตัวในหน้า Releases build โดย GitHub Actions จากโค้ดใน repo นี้ และมีค่า SHA-256 กำกับ
ตรวจในเครื่องด้วย PowerShell: `Get-FileHash .\PlatooBridge.exe` ถ้าไม่อยากใช้ exe ให้รัน `python gui.py` แทน

The exe is not code-signed, so Windows SmartScreen may say "Windows protected your PC": click **More info**, then
**Run anyway**. Every release exe is built by GitHub Actions from this repo and lists its SHA-256
(`Get-FileHash .\PlatooBridge.exe`). Or run `python gui.py` instead.

## สิ่งที่ส่งและไม่ส่ง / What is and is not sent

`platoo_bridge.py` ใน repo นี้คือไฟล์เดียวกับที่ดาวน์โหลดจากเว็บไซต์ ไม่ได้แก้ไข และ `gui.py` เพียงเรียกใช้มัน /
`platoo_bridge.py` here is the file the site offers for download, unmodified; `gui.py` only drives it.

- ส่งไปยังเซิร์ฟเวอร์ / Sent to the server: ตาเดินของเอนจิน ชื่อเอนจินและผู้พัฒนา (UCI `id`) รุ่นของ bridge
  และข้อมูลการคิดของแต่ละตา (ความลึก โหนด เวลา คะแนน แนวการเดิน) / your engine's moves, its UCI `id name` and
  `id author`, the bridge version, and per-move depth, nodes, time, score and PV.
- ไม่ส่ง / Never sent: ตัวโปรแกรมเอนจิน ไฟล์ NNUE หรือ eval ค่าตัวเลือก UCI หรือสิ่งอื่นใดในเครื่องของคุณ /
  your engine program, its NNUE or eval files, your UCI option values, or anything else on your computer.
- โทเคนไม่ถูกเขียนลง log ถ้าติ๊ก "จำไว้" จะบันทึกแบบเข้ารหัสด้วย Windows (DPAPI) ที่
  `%APPDATA%\PlatooBridge\token.bin` ซึ่งถอดได้เฉพาะผู้ใช้ Windows คนเดียวกันบนเครื่องนี้ เอาติ๊กออกเพื่อลบ /
  The token never goes to the log. With "Remember" ticked it is saved encrypted by Windows (DPAPI) in
  `%APPDATA%\PlatooBridge\token.bin`, readable only by the same Windows user on this computer; untick to delete it.

## รันจาก source (ไม่ใช้ exe) / Run from source

ไม่มีคำเตือน SmartScreen และอ่านโค้ดได้ทุกบรรทัด ต้องมี Python 3.9 ขึ้นไป ไม่ต้อง `pip install` อะไรเพิ่ม /
No SmartScreen warning and every line is readable. Needs Python 3.9+, nothing to install.

```bash
git clone https://github.com/kiraro33/platoo-bridge-gui.git
cd platoo-bridge-gui
python gui.py
```

ไม่มี git ให้กด **Code → Download ZIP** แล้วแตกไฟล์ / No git: **Code → Download ZIP**.

- Windows: Python จาก python.org มี tkinter มาให้แล้ว / tkinter ships with Python from python.org.
- Linux: ติดตั้ง tkinter ก่อน เช่น `sudo apt install python3-tk` และปุ่ม "จำไว้" ใช้ได้เฉพาะ Windows /
  install tkinter first; "Remember" works on Windows only.

## ทดสอบ / Tests

```bash
pip install pytest
pytest -q
```

GPL-3.0, see `LICENSE`.
