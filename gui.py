"""PlaToo Bridge GUI: a window around platoo_bridge.py, the bridge script from the PlaToo site (unchanged)."""

import ctypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from argparse import Namespace
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import platoo_bridge as pb

LEVELS = (("ง่าย / Easy", "easy"), ("ปานกลาง / Medium", "medium"), ("โปร / Pro", "pro"),
          ("โปรแม็กซ์ / Pro Max", "promax"))
COLORS = (("สลับ / Alternate", "alt"), ("ขาว / White", "w"), ("ดำ / Black", "b"), ("สุ่ม / Random", "random"))
MINUTES = (3, 5, 10, 20)
GAME_URL_RE = re.compile(r"https?://\S+/g/\S+")
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))  # PyInstaller unpacks bundled files to _MEIPASS
TOKEN_FILE = Path(os.environ.get("APPDATA", Path.home())) / "PlatooBridge" / "token.bin"
CAN_REMEMBER = sys.platform == "win32"
EDIT_KEYS = {65: "<<SelectAll>>", 67: "<<Copy>>", 86: "<<Paste>>", 88: "<<Cut>>"}  # Windows keycodes of A C V X


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    """Windows DPAPI: the result can only be decrypted by this Windows user on this computer."""
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows could not encrypt or decrypt the saved token")
    try:
        return ctypes.string_at(out.data, out.size)
    finally:
        ctypes.windll.kernel32.LocalFree(out.data)


def save_token(token):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_bytes(_dpapi(token.encode(), True))


def load_token():
    try:
        return _dpapi(TOKEN_FILE.read_bytes(), False).decode() if CAN_REMEMBER else ""
    except OSError:  # no saved token, or saved by another Windows user
        return ""


def forget_token():
    TOKEN_FILE.unlink(missing_ok=True)


# A windowed exe has no console: without this flag Windows opens one for the engine.
_popen = subprocess.Popen
pb.subprocess.Popen = lambda *a, **kw: _popen(*a, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kw)


def make_args(engine, options, level, minutes, color, games, evalfile="", threads=1, hash_mb=64, ponder=True,
              no_bonus=True):
    """Form values -> the argparse namespace pb.Bridge expects. ValueError carries the message for the user."""
    if not Path(engine).is_file():
        raise ValueError("เลือกไฟล์เอนจิน UCI ของคุณ / Pick your UCI engine file")
    if evalfile and not Path(evalfile).is_file():
        raise ValueError(f"ไม่พบไฟล์ EvalFile / EvalFile not found: {evalfile}")
    if not (1 <= threads <= 1024 and 1 <= hash_mb <= 65536):
        raise ValueError("Threads 1-1024, Hash 1-65536 MB")
    opts = [f"Threads={threads}", f"Hash={hash_mb}", f"Ponder={'true' if ponder else 'false'}"]
    if evalfile:  # empty: the engine keeps its own built-in net
        opts.append(f"EvalFile={evalfile}")
    extra = [line.strip() for line in options.splitlines() if line.strip()]
    for opt in extra:
        if "=" not in opt or not opt.partition("=")[0].strip():
            raise ValueError(f"ตัวเลือกต้องเป็น NAME=VALUE / Options must be NAME=VALUE: {opt}")
    if not 1 <= games <= 20:
        raise ValueError("จำนวนเกม 1-20 / Games must be 1-20")
    return Namespace(engine=engine, engine_arg=None, option=opts + extra, server=pb.DEFAULT_SERVER,
                     challenge=(level, minutes, color), games=games, no_bonus=no_bonus,
                     exit_after_series=True, overhead_ms=300, log=None, quiet=False)


def _is_bestmove(line):
    return line.startswith("bestmove")


class PonderEngine(pb.Engine):
    """pb.Engine plus pondering: after its move the engine keeps thinking on the reply it expects (go ponder);
    when PlaToo plays that reply the search goes on (ponderhit), otherwise it is stopped and started afresh."""

    ponder_on = None  # (start_fen, moves) of the position being pondered

    def start(self):
        self.ponder_on = None
        return super().start()

    def stop_ponder(self):
        if self.ponder_on and self.alive:
            self.ponder_on = None
            self.send("stop")
            self.read_until(_is_bestmove, 5)  # the pondering search's move, not wanted
        self.ponder_on = None

    def new_game(self):
        self.stop_ponder()
        super().new_game()

    def think(self, start_fen, moves, go, timeout):
        if self.ponder_on == (start_fen, moves):
            self.ponder_on = None
            self.send("ponderhit")
        else:
            self.stop_ponder()
            self.send(pb.position_line(start_fen, moves))
            self.send(go)
        try:
            lines = self.read_until(_is_bestmove, timeout)
        except pb.BridgeError as exc:  # as pb.Engine.think: stop a search that runs too long
            if "did not answer" not in str(exc):
                raise
            self.send("stop")
            lines = self.read_until(_is_bestmove, 3)
        best = lines[-1].split()
        move = best[1] if len(best) > 1 else ""
        info = next((pb.parse_info(l) for l in reversed(lines)
                     if l.startswith("info ") and " score " in l and " pv " in l), {})
        reply = best[3] if len(best) > 3 and best[2] == "ponder" else ""
        if "Ponder=true" in self.options and pb.MOVE_RE.fullmatch(move) and pb.MOVE_RE.fullmatch(reply):
            line = moves + [move, reply]
            self.send(pb.position_line(start_fen, line))
            self.send(go.replace("go", "go ponder", 1))  # PlaToo's clock runs now, ours does not
            self.ponder_on = (start_fen, line)
        return move, info


pb.Engine = PonderEngine  # pb.Bridge builds its engine from this name


class GuiBridge(pb.Bridge):
    def on_finish(self, ev):
        try:
            self.engine.stop_ponder()  # no pondering on a finished game
        except pb.BridgeError:
            pass  # a dead engine is restarted at the next move
        super().on_finish(ev)


def _stopped(*_args, **_kw):
    raise pb.BridgeError("stopped")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PlaToo Bridge")
        self.lines = queue.Queue()
        self.bridge = None
        self.thread = None
        self.url = None
        pb.log_line = lambda msg: self.lines.put(time.strftime("%H:%M:%S ") + msg)

        self.logo = tk.PhotoImage(file=str(HERE / "logo.png"))
        self.iconphoto(True, self.logo)
        self.small_logo = self.logo.subsample(4)
        ttk.Label(self, image=self.small_logo).pack(pady=(10, 0))
        self.bind_all("<Control-KeyPress>", self.edit_key)
        self.menu = tk.Menu(self, tearoff=0)
        for text, ev in (("ตัด / Cut", "<<Cut>>"), ("คัดลอก / Copy", "<<Copy>>"), ("วาง / Paste", "<<Paste>>")):
            self.menu.add_command(label=text, command=lambda ev=ev: self.focus_get().event_generate(ev))
        for cls in ("TEntry", "Text"):
            self.bind_class(cls, "<Button-3>", self.context_menu, add=True)

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(11, weight=1)

        def label(row, text):
            ttk.Label(f, text=text).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)

        label(0, "โทเคนบอท / Bot token")
        self.token = ttk.Entry(f, show="•")
        self.token.grid(row=0, column=1, sticky="ew")
        saved = load_token()
        self.token.insert(0, saved)
        checks = ttk.Frame(f)
        checks.grid(row=0, column=2, sticky="w", padx=(6, 0))
        self.show = tk.BooleanVar(value=False)
        ttk.Checkbutton(checks, text="แสดง / Show", variable=self.show,
                        command=lambda: self.token.configure(show="" if self.show.get() else "•")).pack(side="left")
        self.remember = tk.BooleanVar(value=bool(saved))
        ttk.Checkbutton(checks, text="จำไว้ / Remember", variable=self.remember, command=self.remember_changed,
                        state="normal" if CAN_REMEMBER else "disabled").pack(side="left", padx=(6, 0))

        label(1, "เอนจิน / Engine")
        self.engine = ttk.Entry(f)
        self.engine.grid(row=1, column=1, sticky="ew")
        ttk.Button(f, text="เลือก… / Browse…", command=lambda: self.browse(self.engine)).grid(row=1, column=2, padx=(6, 0))

        label(2, "EvalFile (NNUE)\nเว้นว่าง = ใช้ไฟล์ในตัวเอนจิน / empty = engine's built-in")
        self.evalfile = ttk.Entry(f)
        self.evalfile.grid(row=2, column=1, sticky="ew")
        ttk.Button(f, text="เลือก… / Browse…", command=lambda: self.browse(self.evalfile, "*.nnue")).grid(
            row=2, column=2, padx=(6, 0))

        label(3, "Threads / Hash (MB)")
        sizes = ttk.Frame(f)
        sizes.grid(row=3, column=1, columnspan=2, sticky="w")
        self.threads = ttk.Spinbox(sizes, from_=1, to=os.cpu_count() or 64, width=6)
        self.threads.set(1)
        self.hash = ttk.Spinbox(sizes, values=(16, 32, 64, 128, 256, 512, 1024, 2048, 4096), width=8)
        self.hash.set(64)
        self.threads.pack(side="left")
        self.hash.pack(side="left", padx=(12, 0))

        ticks = ttk.Frame(f)
        ticks.grid(row=4, column=1, columnspan=2, sticky="w", pady=3)
        self.ponder = tk.BooleanVar(value=True)
        self.no_bonus = tk.BooleanVar(value=True)
        ttk.Checkbutton(ticks, text="คิดล่วงหน้าตอนตาคู่แข่ง / Ponder", variable=self.ponder).pack(side="left")
        ttk.Checkbutton(ticks, text="ไม่ใช้โบนัสเวลาเมื่อเหลือน้อย / No low-time bonus",
                        variable=self.no_bonus).pack(side="left", padx=(12, 0))

        label(5, "ตัวเลือกอื่น / Other UCI options\nNAME=VALUE (บรรทัดละตัว / one per line)")
        self.options = tk.Text(f, height=2, width=40)
        self.options.grid(row=5, column=1, columnspan=2, sticky="ew", pady=3)

        def combo(row, text, values):
            label(row, text)
            box = ttk.Combobox(f, values=values, state="readonly")
            box.current(0)
            box.grid(row=row, column=1, columnspan=2, sticky="ew")
            return box

        self.level = combo(6, "ระดับ / Level", [name for name, _ in LEVELS])
        self.level.current(2)
        self.minutes = combo(7, "เวลา (นาที) / Time (minutes)", MINUTES)
        self.minutes.current(1)
        self.color = combo(8, "สีของบอทคุณ / Your bot's colour", [name for name, _ in COLORS])
        label(9, "จำนวนเกม / Games")
        self.games = ttk.Spinbox(f, from_=1, to=20)
        self.games.set(4)
        self.games.grid(row=9, column=1, columnspan=2, sticky="ew")

        bar = ttk.Frame(f)
        bar.grid(row=10, column=0, columnspan=3, sticky="ew", pady=8)
        self.start_btn = ttk.Button(bar, text="เริ่ม / Start", command=self.start)
        self.stop_btn = ttk.Button(bar, text="หยุด / Stop", command=self.stop)
        self.watch_btn = ttk.Button(bar, text="ดูเกมสด / Watch live", command=lambda: webbrowser.open(self.url))
        self.score = ttk.Label(bar)
        for w in (self.start_btn, self.stop_btn, self.watch_btn, self.score):
            w.pack(side="left", padx=(0, 6))

        self.log = scrolledtext.ScrolledText(f, height=14, state="disabled")
        self.log.grid(row=11, column=0, columnspan=3, sticky="nsew")

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll()

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def edit_key(self, e):
        # Tk binds Ctrl+C/V/X/A by letter, so they do nothing under the Thai layout: map the physical key instead.
        ev = EDIT_KEYS.get(e.keycode)
        if ev and e.keysym.lower() not in ("a", "c", "v", "x"):
            e.widget.event_generate(ev)
            return "break"

    def context_menu(self, e):
        e.widget.focus_set()
        self.menu.tk_popup(e.x_root, e.y_root)

    def remember_changed(self):
        try:
            if self.remember.get() and self.token.get().strip():
                save_token(self.token.get().strip())
            elif not self.remember.get():
                forget_token()
        except OSError as exc:
            messagebox.showerror("PlaToo Bridge", str(exc))

    @staticmethod
    def browse(entry, pattern=None):
        path = filedialog.askopenfilename(filetypes=[(pattern, pattern), ("*", "*")] if pattern else [])
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)

    def start(self):
        token = self.token.get().strip()
        try:
            if not token:
                raise ValueError("วางโทเคนบอทก่อน / Paste your bot token first")
            if not all(box.get().strip().isdigit() for box in (self.games, self.threads, self.hash)):
                raise ValueError("จำนวนเกม, Threads และ Hash ต้องเป็นตัวเลข / Games, Threads and Hash must be numbers")
            args = make_args(self.engine.get().strip(), self.options.get("1.0", "end"),
                             LEVELS[self.level.current()][1], MINUTES[self.minutes.current()],
                             COLORS[self.color.current()][1], int(self.games.get()),
                             evalfile=self.evalfile.get().strip(), threads=int(self.threads.get()),
                             hash_mb=int(self.hash.get()), ponder=self.ponder.get(), no_bonus=self.no_bonus.get())
            self.bridge = GuiBridge(args, token)
        except (ValueError, pb.BridgeError) as exc:
            messagebox.showerror("PlaToo Bridge", str(exc))
            return
        self.remember_changed()  # saves the token as now typed
        self.thread =threading.Thread(target=self.work, args=(self.bridge,), daemon=True)
        self.thread.start()

    @staticmethod
    def work(bridge):
        try:
            bridge.run()
        except Exception as exc:  # anything the script raises ends the run and goes to the log
            pb.log_line("stopped" if bridge.stop.is_set() else f"error: {exc}")
        finally:
            bridge.close()

    def stop(self, ask=True):
        if not self.running:
            return True
        if ask and not messagebox.askokcancel("PlaToo Bridge", (
                "หยุด bridge? ถ้ามีเกมค้างอยู่ บอทจะแพ้เพราะทิ้งเกมหลัง 60 วินาที\n\n"
                "Stop the bridge? A game in progress is lost by abandonment after 60 seconds.")):
            return False
        b = self.bridge
        b.stop.set()
        b.engine.start = _stopped  # the script restarts a dead engine; not after Stop
        call = b.server.call  # ends the script's network retries; its challenge withdrawal (DELETE) still goes out
        b.server.call = lambda method, *a, **kw: call(method, *a, **kw) if method == "DELETE" else _stopped()
        b.events.put({"type": "fatal", "message": "stopped"})
        if b.engine.alive:
            b.engine.proc.kill()  # ends a search in progress so the bridge thread can leave
        return True

    def on_close(self):
        if self.stop():
            if self.thread:
                self.thread.join(5)  # lets the bridge withdraw its challenge
            self.destroy()

    def poll(self):
        try:
            while True:
                line = self.lines.get_nowait()
                found = GAME_URL_RE.search(line)
                if found:
                    self.url = found.group()
                self.log.configure(state="normal")
                self.log.insert("end", line + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.start_btn.configure(state="disabled" if self.running else "normal")
        self.stop_btn.configure(state="normal" if self.running else "disabled")
        self.watch_btn.configure(state="normal" if self.url else "disabled")
        if self.bridge:
            s = self.bridge.score
            self.score.configure(text=f"ชนะ / Win {s['win']}   เสมอ / Draw {s['draw']}   แพ้ / Loss {s['loss']}")
        self.after(100, self.poll)


if __name__ == "__main__":
    App().mainloop()
