import sys

import pytest

import gui
from gui import make_args


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_token_saved_encrypted(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "TOKEN_FILE", tmp_path / "PlatooBridge" / "token.bin")
    assert gui.load_token() == ""
    gui.save_token("ptb_secret")
    assert b"ptb_secret" not in gui.TOKEN_FILE.read_bytes()
    assert gui.load_token() == "ptb_secret"
    gui.forget_token()
    assert gui.load_token() == ""


def test_make_args(tmp_path):
    engine = tmp_path / "engine.exe"
    engine.write_bytes(b"")
    args = make_args(str(engine), "Foo=1\n\n Bar = x \n", "pro", 5, "alt", 4)
    assert args.option == ["Threads=1", "Hash=64", "Ponder=true", "Foo=1", "Bar = x"]  # no EvalFile: built-in net
    assert args.challenge == ("pro", 5, "alt") and args.games == 4 and args.no_bonus

    args = make_args(str(engine), "", "pro", 5, "w", 1, evalfile=str(engine), threads=4, hash_mb=256,
                     ponder=False, no_bonus=False)
    assert args.option == ["Threads=4", "Hash=256", "Ponder=false", f"EvalFile={engine}"] and not args.no_bonus
    # draw offers on, with the bridge's defaults (1.0.2: from ply 250)
    assert not args.no_draw_offer and (args.draw_after, args.draw_cp, args.draw_moves) == (250, 15, 8)

    for bad in ({"options": "Hash 256"}, {"options": "=1"}, {"games": 0}, {"games": 21}, {"threads": 0},
                {"evalfile": str(tmp_path / "missing.nnue")}, {"engine": str(tmp_path / "missing.exe")}):
        form = {"engine": str(engine), "options": "", "level": "pro", "minutes": 5, "color": "w", "games": 1, **bad}
        with pytest.raises(ValueError):
            make_args(**form)


FAKE_ENGINE = r"""
import sys
pondering = False
for line in sys.stdin:
    cmd = line.split()
    if not cmd:
        continue
    if cmd[0] == "uci":
        print("id name Fake\noption name Ponder type check default false\nuciok", flush=True)
    elif cmd[0] == "isready":
        print("readyok", flush=True)
    elif cmd[0] == "go" and "ponder" in cmd:
        pondering = True  # UCI: no bestmove until ponderhit or stop
    elif cmd[0] == "go":
        print("info depth 1 score cp 10 pv e3e4\nbestmove e3e4 ponder e6e5", flush=True)
    elif cmd[0] in ("ponderhit", "stop") and pondering:
        pondering = False
        print("info depth 9 score cp 20 pv a3a4\nbestmove a3a4 ponder a6a5", flush=True)
    elif cmd[0] == "quit":
        break
"""


def run_engine(tmp_path, ponder):
    script = tmp_path / "fake_engine.py"
    script.write_text(FAKE_ENGINE)
    log = open(tmp_path / "uci.log", "w+", encoding="utf-8")
    eng = gui.PonderEngine([sys.executable, str(script)], [f"Ponder={'true' if ponder else 'false'}"], log)
    return eng.start(), log


def sent(log):
    log.seek(0)
    return [line.split(" ", 2)[2].strip() for line in log if line.split(" ", 2)[1] == ">"]


def test_ponder_hit_miss_and_new_game(tmp_path):
    eng, log = run_engine(tmp_path, True)
    fen, go = gui.pb.check_fen("rnsmksnr/8/pppppppp/8/8/PPPPPPPP/8/RNSKMSNR w - - 0 1"), "go wtime 1000 btime 1000"
    try:
        assert eng.think(fen, [], go, 5)[0] == "e3e4"
        assert eng.ponder_on == (fen, ["e3e4", "e6e5"])
        mark = len(sent(log))
        move, info = eng.think(fen, ["e3e4", "e6e5"], go, 5)  # PlaToo played the expected reply
        assert move == "a3a4" and info["depth"] == 9 and sent(log)[mark] == "ponderhit"
        assert eng.ponder_on == (fen, ["e3e4", "e6e5", "a3a4", "a6a5"])
        mark = len(sent(log))
        assert eng.think(fen, ["e3e4", "e6e5", "a3a4", "b6b5"], go, 5)[0] == "e3e4"  # a different reply
        assert sent(log)[mark:mark + 3] == ["stop", "position fen " + fen + " moves e3e4 e6e5 a3a4 b6b5", go]
        assert "go ponder wtime 1000 btime 1000" in sent(log)
        eng.new_game()
        assert eng.ponder_on is None and sent(log)[-3:] == ["stop", "ucinewgame", "isready"]
    finally:
        eng.quit()


def test_no_ponder_when_off(tmp_path):
    eng, log = run_engine(tmp_path, False)
    try:
        eng.think(gui.pb.check_fen("rnsmksnr/8/pppppppp/8/8/PPPPPPPP/8/RNSKMSNR w - - 0 1"), [], "go wtime 1000 btime 1000", 5)
        assert eng.ponder_on is None and not any("ponder" in s for s in sent(log))
    finally:
        eng.quit()
