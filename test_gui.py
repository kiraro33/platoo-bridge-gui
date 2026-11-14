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
    args = make_args(str(engine), "EvalFile=my.nnue\n\n Hash = 256 \n", "pro", 5, "alt", 4)
    assert args.option == ["EvalFile=my.nnue", "Hash = 256"]
    assert args.challenge == ("pro", 5, "alt") and args.games == 4

    for bad in ({"options": "Hash 256"}, {"options": "=1"}, {"games": 0}, {"games": 21},
                {"engine": str(tmp_path / "missing.exe")}):
        form = {"engine": str(engine), "options": "", "level": "pro", "minutes": 5, "color": "w", "games": 1, **bad}
        with pytest.raises(ValueError):
            make_args(**form)
