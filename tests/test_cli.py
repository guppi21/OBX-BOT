import pytest
from typer.testing import CliRunner
from apps.obx_core.cli import app
from packages.database.base import Base

runner = CliRunner()


@pytest.fixture(autouse=True)
def setup_cli_db(monkeypatch, test_engine):
    """Ensure CLI uses test database engine and tables are clean."""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    from packages.database import session as db_session_module
    from sqlalchemy.orm import sessionmaker

    def test_get_session_factory(engine=None):
        return sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)

    monkeypatch.setattr(db_session_module, "get_session_factory", test_get_session_factory)
    monkeypatch.setattr(db_session_module, "get_engine", lambda: test_engine)
    yield


def test_cli_create_user():
    result = runner.invoke(app, ["create-user", "cli_user_1"])
    assert result.exit_code == 0
    assert "Successfully created user and wallet" in result.output

    # Duplicate call
    result_dup = runner.invoke(app, ["create-user", "cli_user_1"])
    assert result_dup.exit_code == 0
    assert "User already exists" in result_dup.output


def test_cli_credit_and_balance():
    user_id = "cli_user_credit"
    runner.invoke(app, ["create-user", user_id])

    res_cred = runner.invoke(app, ["credit", user_id, "1500", "--ref-type", "bonus"])
    assert res_cred.exit_code == 0
    assert "Credited 1500 OBX" in res_cred.output

    res_bal = runner.invoke(app, ["balance", user_id])
    assert res_bal.exit_code == 0
    assert "1,500 OBX" in res_bal.output


def test_cli_debit():
    user_id = "cli_user_debit"
    runner.invoke(app, ["create-user", user_id])
    runner.invoke(app, ["credit", user_id, "1000"])

    res_deb = runner.invoke(app, ["debit", user_id, "400"])
    assert res_deb.exit_code == 0
    assert "Debited 400 OBX" in res_deb.output

    res_bal = runner.invoke(app, ["balance", user_id])
    assert "600 OBX" in res_bal.output


def test_cli_transactions():
    user_id = "cli_user_tx"
    runner.invoke(app, ["create-user", user_id])
    runner.invoke(app, ["credit", user_id, "500", "--desc", "First Deposit"])
    runner.invoke(app, ["debit", user_id, "100", "--desc", "Item Purchase"])

    res_tx = runner.invoke(app, ["transactions", user_id])
    assert res_tx.exit_code == 0
    assert "First Deposit" in res_tx.output
    assert "Item Purchase" in res_tx.output


def test_cli_reconcile():
    user_id = "cli_user_recon"
    runner.invoke(app, ["create-user", user_id])
    runner.invoke(app, ["credit", user_id, "800"])

    # Reconcile specific user
    res_user = runner.invoke(app, ["reconcile", "--user", user_id])
    assert res_user.exit_code == 0
    assert "matches ledger perfectly" in res_user.output

    # System-wide reconcile
    res_sys = runner.invoke(app, ["reconcile"])
    assert res_sys.exit_code == 0
    assert "System-wide reconciliation PASSED" in res_sys.output
