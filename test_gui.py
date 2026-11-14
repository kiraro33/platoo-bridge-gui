import pytest

from gui import make_args


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
