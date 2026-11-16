#!/usr/bin/env python3
"""PlaToo bridge: plays your own UCI Makruk engine against PlaToo on the PlaToo web site (AI vs AI).

Your engine runs on your computer; this script connects it to the site over HTTPS. Python 3.9+,
standard library only: no pip install needed.

    export PLATOO_BOT_TOKEN=ptb_...            # from the site's admin (shown once)
    python platoo-bridge.py --engine ./my-engine --challenge pro:5:alt --games 4

Moves use the Fairy-Stockfish makruk format (m = Met, s = Khon, promotion suffix m, e.g. g5g6m).
The server keeps the clocks and judges every move and the result by the Thai rules; your engine only
plays. Your clock runs from PlaToo's move until your move reaches the server, less a small network
allowance; set --overhead-ms so your engine keeps a margin.

What this script sends to your engine: uci, setoption (UCI_Variant makruk if offered, and your
--option lines), isready, ucinewgame, position fen ... moves ..., go wtime/btime/winc/binc, stop, quit.
Every FEN and move from the server is checked against a strict pattern first, so nothing else can
reach your engine.

What this script sends to the server: your engine's moves; its UCI "id name" / "id author" and this
script's version (once, at start); and with each move the depth, nodes, time, score and PV of the
engine's last info line (shown to the PlaToo admins only). Never your engine program, its NNUE or
other eval files, your --option values, or anything else from your computer.
"""

import argparse
import http.client
import json
import os
import queue
import re
import signal
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

VERSION = "1.0.3"
DEFAULT_SERVER = "https://play.platoomakruk.in.th"
MOVE_RE = re.compile(r"[a-h][1-8][a-h][1-8]m?")
FEN_RE = re.compile(r"[1-8A-Za-z~/]{15,90} [wb] [A-Za-z-]{1,4} [a-h1-8-]{1,2} \d{1,4} \d{1,4}")
LEVELS = ("easy", "medium", "pro", "promax")


def log_line(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


class BridgeError(Exception):
    pass


# ---- Checks on what goes to the engine (unit-tested) -----------------------------------------------

def check_fen(fen):
    if not isinstance(fen, str) or not FEN_RE.fullmatch(fen):
        raise BridgeError(f"refusing a bad FEN from the server: {fen!r}")
    return fen


def check_moves(moves):
    if not isinstance(moves, list) or len(moves) > 2000 or not all(
            isinstance(m, str) and MOVE_RE.fullmatch(m) for m in moves):
        raise BridgeError("refusing a bad move list from the server")
    return moves


def position_line(start_fen, moves):
    line = "position fen " + check_fen(start_fen)
    if check_moves(moves):
        line += " moves " + " ".join(moves)
    return line


def go_line(state, color, overhead_ms):
    """go wtime/btime/winc/binc from the server's clocks; our own clock less our overhead."""
    clocks = {c: max(0, int(state["clocks"][c])) for c in "wb"}
    clocks[color] = max(50, clocks[color] - overhead_ms)
    inc = state.get("inc") or {}
    parts = ["go", "wtime", str(clocks["w"]), "btime", str(clocks["b"])]
    for c, name in (("w", "winc"), ("b", "binc")):
        if int(inc.get(c) or 0) > 0:
            parts += [name, str(int(inc[c]))]
    return " ".join(parts)


LIVE_PATH_RE = re.compile(r"/bots/[A-Za-z0-9_-]{3,24}/live")


def live_url(server, hello, name):
    """The bot's permanent live page (always its newest game), from /bot/hello (server.liveUrl); servers without
    it get the same path built from the bot name. Only a path of that form is used: nothing else gets opened."""
    path = (hello.get("server") or {}).get("liveUrl") or f"/bots/{name}/live"
    return server.rstrip("/") + path if isinstance(path, str) and LIVE_PATH_RE.fullmatch(path) else None


def parse_challenge(text):
    """'pro:5:alt' -> (level, minutes, color)."""
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError("use LEVEL:MINUTES[:COLOR], e.g. pro:5:alt")
    level, minutes = parts[0].lower(), parts[1]
    color = (parts[2] if len(parts) == 3 else "random").lower()
    color = {"white": "w", "black": "b"}.get(color, color)
    if level not in LEVELS or not minutes.isdigit() or color not in ("w", "b", "alt", "random"):
        raise argparse.ArgumentTypeError("level easy|medium|pro|promax, minutes 3|5|10|20, color w|b|alt|random")
    return level, int(minutes), color


def parse_info(line):
    """Score/depth/nodes/time/pv of a UCI info line (for the server's admin view only)."""
    words = line.split()
    out = {}
    i = 1
    while i < len(words):
        w = words[i]
        if w in ("depth", "seldepth", "nodes", "time") and i + 1 < len(words) and words[i + 1].lstrip("-").isdigit():
            out["timeMs" if w == "time" else w] = int(words[i + 1])
            i += 2
        elif w == "score" and i + 2 < len(words) and words[i + 2].lstrip("-").isdigit():
            out["scoreCp" if words[i + 1] == "cp" else "scoreMate"] = int(words[i + 2])
            i += 3
        elif w == "pv":
            pv = [m for m in words[i + 1:i + 21] if MOVE_RE.fullmatch(m)]
            out["pv"] = " ".join(pv)
            break
        else:
            i += 1
    return out


# ---- The engine ---------------------------------------------------------------------------------------

class Engine:
    def __init__(self, cmd, options, log_file=None):
        self.cmd = cmd
        self.options = options
        self.log_file = log_file
        self.proc = None
        self.lines = queue.Queue()
        self.name = os.path.basename(cmd[0])
        self.author = ""

    def _log(self, direction, text):
        if self.log_file:
            self.log_file.write(f"{time.strftime('%H:%M:%S')} {direction} {text}\n")
            self.log_file.flush()

    def start(self):
        self.lines = queue.Queue()
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                     cwd=os.path.dirname(os.path.abspath(self.cmd[0])) or None)
        lines = self.lines

        def reader(stdout):
            for raw in stdout:
                lines.put(raw.rstrip("\r\n"))
            lines.put(None)

        threading.Thread(target=reader, args=(self.proc.stdout,), daemon=True).start()
        self.send("uci")
        names = set()
        for line in self.read_until(lambda l: l == "uciok", 15):
            if line.startswith("id name "):
                self.name = line[8:].strip()
            elif line.startswith("id author "):
                self.author = line[10:].strip()
            elif line.startswith("option name "):
                names.add(line[12:].split(" type ")[0].strip())
        if "UCI_Variant" in names:
            self.send("setoption name UCI_Variant value makruk")
        for opt in self.options:
            name, _, value = opt.partition("=")
            self.send(f"setoption name {name.strip()} value {value.strip()}")
        self.isready()
        return self

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def send(self, line):
        if "\n" in line or "\r" in line:
            raise BridgeError("refusing a multi-line engine command")
        self._log(">", line)
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise BridgeError(f"engine closed its input: {exc}")

    def read_until(self, done, timeout):
        out = []
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise BridgeError(f"engine did not answer in {timeout:.0f} s")
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                raise BridgeError("engine exited")
            self._log("<", line)
            out.append(line)
            if done(line):
                return out

    def isready(self, timeout=30):
        self.send("isready")
        self.read_until(lambda l: l == "readyok", timeout)

    def new_game(self):
        self.send("ucinewgame")
        self.isready()

    def think(self, start_fen, moves, go, timeout):
        self.send(position_line(start_fen, moves))
        self.send(go)
        try:
            lines = self.read_until(lambda l: l.startswith("bestmove"), timeout)
        except BridgeError as exc:
            if "did not answer" not in str(exc):
                raise
            self.send("stop")
            lines = self.read_until(lambda l: l.startswith("bestmove"), 3)
        best = lines[-1].split()
        move = best[1] if len(best) > 1 else ""
        info = {}
        for line in reversed(lines):
            if line.startswith("info ") and " score " in line and " pv " in line:
                info = parse_info(line)
                break
        return move, info

    def quit(self):
        if not self.alive:
            return
        try:
            self.send("quit")
            self.proc.wait(3)
        except Exception:
            self.proc.kill()


# ---- The server ---------------------------------------------------------------------------------------

class Server:
    def __init__(self, base, token):
        u = urllib.parse.urlparse(base)
        local = u.hostname in ("localhost", "127.0.0.1", "::1")
        if u.scheme != "https" and not (u.scheme == "http" and local):
            raise BridgeError("the server must be https:// (http:// only for localhost)")
        self.base = base.rstrip("/")
        self.token = token
        self.ctx = ssl.create_default_context() if u.scheme == "https" else None

    def call(self, method, path, body=None, timeout=20):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "User-Agent": f"platoo-bridge/{VERSION}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                err = json.loads(exc.read()).get("error", {})
            except Exception:
                err = {}
            raise ApiError(exc.code, err.get("code", f"http_{exc.code}"), err)
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
            raise NetError(str(getattr(exc, "reason", exc)) or type(exc).__name__)

    def retry(self, method, path, body=None, until=None, timeout=20):
        """call() again on network errors and 5xx answers, with backoff, until `until` (monotonic s)."""
        wait = 0.5
        while True:
            try:
                return self.call(method, path, body, timeout)
            except (NetError, ApiError) as exc:
                if isinstance(exc, ApiError) and exc.status < 500:
                    raise
                if until is not None and time.monotonic() + wait > until:
                    raise
                log_line(f"{method} {path.split('?')[0]}: {exc}; retrying in {wait:.1f} s")
                time.sleep(wait)
                wait = min(wait * 2, 10)


class NetError(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status, code, detail=None):
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.detail = detail or {}


# ---- The bridge ---------------------------------------------------------------------------------------

class Bridge:
    def __init__(self, args, token):
        self.args = args
        self.server = Server(args.server, token)
        log_file = open(args.log, "a", encoding="utf-8") if args.log else None
        self.engine = Engine([args.engine] + (args.engine_arg or []), args.option or [], log_file)
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.answered = {}  # gameId -> last ply we posted a move for
        self.colors = {}
        self.scores = {}  # gameId -> our engine's last scores (cp, None = mate or none), for draw offers
        self.draw_offered = {}  # gameId -> ply of our last draw offer
        self.challenge_id = None
        self.score = {"win": 0, "draw": 0, "loss": 0}
        self.live_url = None  # the bot's live page, from hello (1.0.3)

    def poll_loop(self, after):
        backoff = 1
        while not self.stop.is_set():
            try:
                out = self.server.call("GET", f"/api/v1/bot/events?after={urllib.parse.quote(after)}&wait=25",
                                       timeout=45)
                for ev in out.get("events", []):
                    self.events.put(ev)
                after = out.get("last", after)
                backoff = 1
            except ApiError as exc:
                if exc.status in (401, 403):
                    self.events.put({"type": "fatal", "message": f"server refused the token or bots are off ({exc.code})"})
                    return
                log_line(f"server: {exc.code}, retrying in {max(backoff, 5)} s")
                self.stop.wait(max(backoff, 5))
                backoff = min(backoff * 2, 30)
            except NetError as exc:
                log_line(f"network: {exc}; retrying in {backoff} s")
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 30)

    def hello(self):
        out = self.server.retry("POST", "/api/v1/bot/hello", {
            "engineName": self.engine.name, "engineAuthor": self.engine.author, "bridgeVersion": VERSION},
            until=time.monotonic() + 300)
        log_line(f"connected as bot {out['bot']['name']} ({self.engine.name})")
        self.live_url = live_url(self.args.server, out, out["bot"]["name"])
        if self.live_url:
            log_line(f"watch live: {self.live_url}")
            if getattr(self.args, "open_live", False):
                webbrowser.open(self.live_url)
        return out

    def challenge(self):
        level, minutes, color = self.args.challenge
        out = self.server.call("POST", "/api/v1/bot/challenge", {
            "level": level, "minutes": minutes, "color": color, "lowTimeBonus": not self.args.no_bonus,
            "games": self.args.games})
        self.challenge_id = out["challengeId"]
        log_line(f"challenge {self.challenge_id}: {self.args.games} game(s) vs PlaToo {level}, {minutes} min, colour {color}")

    def on_state(self, st):
        gid = st["gameId"]
        self.colors[gid] = st["color"]
        if st["status"] != "running" or st["sideToMove"] != st["color"]:
            return
        if self.answered.get(gid) == st["ply"]:
            return
        check_moves(st["moves"])
        left = int(st["clocks"][st["color"]])
        go = go_line(st, st["color"], self.args.overhead_ms)
        for attempt in (1, 2):
            try:
                if not self.engine.alive:
                    log_line("engine is not running; restarting it")
                    self.engine.start().new_game()
                t0 = time.monotonic()
                move, info = self.engine.think(st["startFen"], st["moves"], go, left / 1000 + 5)
                info["timeMs"] = int((time.monotonic() - t0) * 1000)
                break
            except BridgeError as exc:
                log_line(f"engine error: {exc}")
                if attempt == 2:
                    return
        if not MOVE_RE.fullmatch(move or ""):
            log_line(f"engine gave no usable move ({move!r}); resigning game {gid}")
            self.resign(gid)
            return
        self.answered[gid] = st["ply"]
        try:
            # Retried while our clock lasts: the ply number makes a repeat harmless (stale_ply).
            self.server.retry("POST", f"/api/v1/bot/games/{gid}/move", {"uci": move, "ply": st["ply"], "info": info},
                              until=time.monotonic() + max(1.0, (left - info["timeMs"]) / 1000))
            if not self.args.quiet:
                log_line(f"  ply {st['ply'] + 1}: {move}  ({info['timeMs']} ms, {left / 1000:.1f} s left)")
            self.maybe_offer_draw(gid, st["ply"] + 1, info)
        except ApiError as exc:
            if exc.code == "illegal_move":
                log_line(f"server refused {move} as illegal in game {gid}; resigning")
                self.resign(gid)
            elif exc.code not in ("stale_ply", "not_your_turn", "game_over"):
                log_line(f"move {move} not accepted: {exc.code}")
                self.answered.pop(gid, None)
        except NetError as exc:
            log_line(f"move {move} could not be sent: {exc}")
            self.answered.pop(gid, None)

    def maybe_offer_draw(self, gid, ply, info):
        """Offers a draw when our engine has called the position level for a while (the server may
        also end a level game by itself, DESIGN §6.12.14). PlaToo accepts or declines at once."""
        a = self.args
        score = info.get("scoreCp") if info.get("scoreMate") is None else None
        scores = self.scores.setdefault(gid, [])
        scores.append(score)
        del scores[:-a.draw_moves]
        if a.no_draw_offer or ply < a.draw_after or len(scores) < a.draw_moves:
            return
        if any(s is None or abs(s) > a.draw_cp for s in scores) or ply - self.draw_offered.get(gid, -999) < 10:
            return
        self.draw_offered[gid] = ply
        try:
            out = self.server.call("POST", f"/api/v1/bot/games/{gid}/draw")
            log_line(f"game {gid[:8]}: draw offered at ply {ply}: {'accepted' if out.get('accepted') else 'declined'}")
        except (ApiError, NetError) as exc:
            if getattr(exc, "code", "") not in ("draw_offer_wait", "draw_offer_off", "game_over"):
                log_line(f"game {gid[:8]}: draw offer failed: {exc}")

    def resign(self, gid):
        try:
            self.server.call("POST", f"/api/v1/bot/games/{gid}/resign")
        except (ApiError, NetError):
            pass

    def on_finish(self, ev):
        color = self.colors.get(ev["gameId"])
        res = ev.get("result")
        if color and res in ("1-0", "0-1", "1/2-1/2"):
            side = "draw" if res == "1/2-1/2" else ("win" if (res == "1-0") == (color == "w") else "loss")
            self.score[side] += 1
        s = self.score
        log_line(f"game {ev['gameId'][:8]} over: {res} {ev.get('reason')}  (you: +{s['win']} ={s['draw']} -{s['loss']})")

    def run(self):
        self.engine.start()
        hello = self.hello()
        threading.Thread(target=self.poll_loop, args=(hello["last"],), daemon=True).start()
        for st in hello.get("games", []):
            log_line(f"resuming game {self.args.server.rstrip('/')}/g/{st['slug']}")
            self.engine.new_game()
            self.events.put({"type": "gameState", **st})
        if self.args.challenge and not hello.get("games"):
            try:
                self.challenge()
            except ApiError as exc:
                log_line(f"challenge not started: {exc.code}; waiting for games anyway")
        while True:
            ev = self.events.get()
            kind = ev.get("type")
            if kind == "fatal":
                raise BridgeError(ev["message"])
            if kind == "gameStart":
                self.colors[ev["gameId"]] = ev["color"]
                log_line(f"new game: you play {'White' if ev['color'] == 'w' else 'Black'} vs PlaToo {ev['level']}, "
                         f"{ev['minutes']} min  {self.args.server.rstrip('/')}/g/{ev['slug']}")
                self.engine.new_game()
            elif kind == "gameState":
                try:
                    self.on_state(ev)
                except (BridgeError, ApiError, NetError) as exc:
                    log_line(f"game {ev['gameId'][:8]}: {exc}")
            elif kind == "gameFinish":
                self.on_finish(ev)
            elif kind == "challengeDone":
                log_line(f"series {ev['challengeId']} done ({ev.get('reason')}, {ev.get('games')} game(s))")
                if self.args.exit_after_series and ev["challengeId"] == self.challenge_id:
                    return

    def close(self):
        self.stop.set()
        if self.challenge_id:
            try:
                self.server.call("DELETE", f"/api/v1/bot/challenge/{self.challenge_id}", timeout=5)
            except Exception:
                pass
        self.engine.quit()


def main(argv=None):
    p = argparse.ArgumentParser(description="Play your UCI Makruk engine against PlaToo (AI vs AI).")
    p.add_argument("--engine", required=True, help="path to your UCI engine")
    p.add_argument("--engine-arg", action="append", help="argument for the engine (repeatable)")
    p.add_argument("--option", action="append", metavar="NAME=VALUE", help="UCI option to set (repeatable)")
    p.add_argument("--server", default=os.environ.get("PLATOO_SERVER", DEFAULT_SERVER))
    p.add_argument("--token-file", help="file with the bot token (else env PLATOO_BOT_TOKEN)")
    p.add_argument("--challenge", type=parse_challenge, metavar="LEVEL:MINUTES[:COLOR]",
                   help="start a series: level easy|medium|pro|promax, minutes 3|5|10|20, colour w|b|alt|random")
    p.add_argument("--games", type=int, default=1, help="games in the series (1-20)")
    p.add_argument("--no-bonus", action="store_true", help="no low-time bonus in the challenged games")
    p.add_argument("--exit-after-series", action="store_true", help="exit when the series is over")
    p.add_argument("--overhead-ms", type=int, default=300, help="time kept back from your engine per move")
    p.add_argument("--no-draw-offer", action="store_true", help="never offer PlaToo a draw")
    p.add_argument("--draw-after", type=int, default=250, help="offer draws only from this ply on (default 250)")
    p.add_argument("--draw-cp", type=int, default=15, help="level = engine score within +-this many cp (default 15)")
    p.add_argument("--draw-moves", type=int, default=8, help="offer after this many level moves of ours in a row (default 8)")
    p.add_argument("--open-live", action="store_true", help="open the bot's live page (its newest game) in the browser")
    p.add_argument("--log", help="write the whole UCI dialogue to this file")
    p.add_argument("--quiet", action="store_true", help="no line per move")
    p.add_argument("--version", action="version", version=f"platoo-bridge {VERSION}")
    args = p.parse_args(argv)
    if args.token_file:
        with open(args.token_file, encoding="utf-8") as f:
            token = f.read().strip()
    else:
        token = os.environ.get("PLATOO_BOT_TOKEN", "").strip()
    if not token:
        p.error("no bot token: set PLATOO_BOT_TOKEN or use --token-file (not on the command line)")
    bridge = Bridge(args, token)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        bridge.run()
    except KeyboardInterrupt:
        log_line("stopped")
    except (BridgeError, ApiError, NetError) as exc:
        log_line(f"error: {exc}")
        return 1
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
