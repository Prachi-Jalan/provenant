"""The demo runner (what you show on stage) and the wallet-setup script (what teammates run once)."""
import importlib.util
import re
from pathlib import Path

import pytest
from eth_account import Account

from demo import run_scenarios

REPO = Path(__file__).resolve().parent.parent


# ---- demo runner --------------------------------------------------------------------------------------
def test_all_five_scenarios_behave_as_expected(capsys):
    assert run_scenarios.main([]) == 0
    out = capsys.readouterr().out
    assert "5/5 scenarios behaved as expected" in out and "DRY RUN" in out
    decisions = re.findall(r"decision\s+: (\w+)", out)
    assert decisions == ["BLOCK", "REPAIRED", "REPAIRED", "BLOCK", "ALLOW"]
    assert "(dry run, nothing was sent)" in out


def test_unprotected_mode_shows_the_agent_being_fooled(capsys):
    assert run_scenarios.main(["--unprotected"]) == 0
    out = capsys.readouterr().out
    assert out.count("FOOLED!") == 4 and out.count("ok (legit)") == 1


def test_only_runs_a_single_scenario(capsys):
    assert run_scenarios.main(["--only", "5"]) == 0
    out = capsys.readouterr().out
    assert "[5] Legit payment" in out and "[1]" not in out and "1/1 scenarios" in out


def test_scenarios_are_the_five_from_the_build_plan():
    sc = run_scenarios.build_scenarios()
    assert [s["key"] for s in sc] == ["grok_style", "pay_rahul", "invoice_fraud", "approval_drain", "legit"]
    assert sc[0]["requester"] != run_scenarios.OWNER and all(s["requester"] == run_scenarios.OWNER for s in sc[1:])


def test_demo_never_goes_live_without_the_flag(monkeypatch, capsys):
    monkeypatch.setenv("DRY_RUN", "0")
    run_scenarios.main(["--only", "5"])
    assert "DRY RUN" in capsys.readouterr().out   # main() forces dry run unless --live


# ---- scripts/setup_wallets.py ------------------------------------------------------------------------------
@pytest.fixture
def setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("setup_wallets", REPO / "scripts" / "setup_wallets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(mod, "ENV_EXAMPLE", REPO / ".env.example")
    monkeypatch.setattr("sys.argv", ["setup_wallets.py"])
    return mod


def read_env(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if re.match(r"^[A-Z_]+=", line))


def test_creates_three_wallets_whose_keys_match_their_addresses(setup, tmp_path, capsys):
    assert setup.main() == 0
    env = read_env(tmp_path / ".env")
    for label in ("OWNER", "VENDOR", "ATTACKER"):
        assert Account.from_key(env[f"{label}_PRIVATE_KEY"]).address == env[f"{label}_ADDRESS"]
    assert len({env["OWNER_ADDRESS"], env["VENDOR_ADDRESS"], env["ATTACKER_ADDRESS"]}) == 3
    assert "RPC_URL" in env and "DRY_RUN" in env          # the rest of .env.example is kept
    assert "faucet" in capsys.readouterr().out.lower()


def test_refuses_to_overwrite_an_existing_env(setup, tmp_path, capsys):
    (tmp_path / ".env").write_text("OWNER_PRIVATE_KEY=0xfunded\n")
    assert setup.main() == 1
    assert (tmp_path / ".env").read_text() == "OWNER_PRIVATE_KEY=0xfunded\n"
    assert "did not touch it" in capsys.readouterr().out


def test_force_overwrites(setup, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OWNER_PRIVATE_KEY=0xold\n")
    monkeypatch.setattr("sys.argv", ["setup_wallets.py", "--force"])
    assert setup.main() == 0
    assert read_env(tmp_path / ".env")["OWNER_PRIVATE_KEY"] != "0xold"


def test_never_touches_the_shared_seed_data(setup, tmp_path):
    before = {p: p.read_bytes() for p in (REPO / "data").glob("*.json")}
    setup.main()
    assert {p: p.read_bytes() for p in (REPO / "data").glob("*.json")} == before


def test_env_is_gitignored_and_example_has_no_secrets():
    ignored = (REPO / ".gitignore").read_text().splitlines()
    assert ".env" in [line.strip() for line in ignored]
    example = (REPO / ".env.example").read_text()
    assert not re.search(r"PRIVATE_KEY=0x[0-9a-fA-F]{64}", example)
