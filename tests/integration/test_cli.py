from itwin.cli import main


def test_check_network_default(capsys):
    assert main(["check-network"]) == 0
    assert "ok: Demo elevated expressway" in capsys.readouterr().out


def test_check_network_invalid(tmp_path, capsys):
    p = tmp_path / "bad.toml"
    p.write_text('name = "x"\n')
    assert main(["check-network", str(p)]) == 1
    assert "invalid" in capsys.readouterr().err


def test_sizing(capsys):
    assert main(["sizing", "--arrivals", "1850", "--service", "350", "--target-wait", "60"]) == 0
    assert capsys.readouterr().out.startswith("6 lanes")


def test_demo_runs_and_finds_the_incident(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "ALARM" in out and "D13 and D16" in out
    assert "Detector D10: stuck" in out
    assert "= 0.000000 veh" in out


def test_serve_requires_keys(monkeypatch, capsys):
    monkeypatch.delenv("ITWIN_API_KEYS", raising=False)
    assert main(["serve"]) == 2
    assert "ITWIN_API_KEYS" in capsys.readouterr().err
