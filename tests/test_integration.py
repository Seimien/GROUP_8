import os
import sqlite3
import threading
import pytest
import payment_router as pr

VALID_PHONE = "+15551234567"


class SqliteRepository(pr.DatabaseRepository):
    def __init__(self, db_path):
        self.db_path = db_path

    def get_transaction(self, tx_id):
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            row = conn.execute(
                "SELECT tx_id, amount, recipient, status, gateway FROM transactions WHERE tx_id = ?",
                (tx_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return {"tx_id": row[0], "amount": row[1], "recipient": row[2], "status": row[3], "gateway": row[4]}

    def record_transaction(self, tx_id, amount, recipient, status, gateway):
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            with conn:
                conn.execute(
                    """INSERT INTO transactions (tx_id, amount, recipient, status, gateway)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(tx_id) DO UPDATE SET status=excluded.status, gateway=excluded.gateway""",
                    (tx_id, amount, recipient, status, gateway),
                )
        finally:
            conn.close()


@pytest.fixture(scope="function")
def sqlite_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_API_KEY", "REAL_PROD_KEY_123")
    db_path = tmp_path / "test_transactions.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE transactions (
               tx_id TEXT PRIMARY KEY,
               amount REAL NOT NULL,
               recipient TEXT NOT NULL,
               status TEXT NOT NULL,
               gateway TEXT NOT NULL
           )"""
    )
    conn.commit()
    conn.close()

    repo = SqliteRepository(str(db_path))
    yield repo
    if os.path.exists(db_path):
        os.remove(db_path)


class AlwaysSucceedGateway(pr.PaymentGatewayClient):
    def process_payment(self, tx_id, amount, recipient):
        return True


def test_end_to_end_success_writes_to_real_db(sqlite_repo):
    router = pr.PaymentRouter(sqlite_repo, AlwaysSucceedGateway(), AlwaysSucceedGateway())
    result = router.execute_transaction("tx-100", 25.0, VALID_PHONE)
    assert result == "COMPLETED_PRIMARY"
    stored = sqlite_repo.get_transaction("tx-100")
    assert stored["status"] == "SUCCESS"
    assert stored["gateway"] == "PRIMARY"


def test_concurrent_duplicate_tx_id_idempotent(sqlite_repo):
    router = pr.PaymentRouter(sqlite_repo, AlwaysSucceedGateway(), AlwaysSucceedGateway())
    results = []

    def worker():
        try:
            results.append(router.execute_transaction("tx-race", 10.0, VALID_PHONE))
        except Exception as e:
            results.append(str(e))

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert "COMPLETED_PRIMARY" in results or "ALREADY_PROCESSED" in results
    verify_conn = sqlite3.connect(sqlite_repo.db_path)
    try:
        count = verify_conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE tx_id='tx-race'"
        ).fetchone()[0]
    finally:
        verify_conn.close()
    assert count == 1  # PRIMARY KEY constraint prevents duplicate rows


# --- "Mock Lie" proof ---
class BrokenSqliteRepository(SqliteRepository):
    """Deliberately writes to a table name that does not exist."""
    def record_transaction(self, tx_id, amount, recipient, status, gateway):
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            with conn:
                # BUG: 'tx_history' doesn't exist -- only 'transactions' does.
                conn.execute(
                    "INSERT INTO tx_history (tx_id, amount, recipient, status, gateway) VALUES (?, ?, ?, ?, ?)",
                    (tx_id, amount, recipient, status, gateway),
                )
        finally:
            conn.close()


def test_mock_lie_proof(sqlite_repo, monkeypatch):
    """
    WHY THIS MATTERS (written proof):
    In test_unit.py, `repo` is a MagicMock -- record_transaction() is a fake
    that always "succeeds" no matter what SQL string a real implementation
    would run. So 100% of unit tests pass even if the real repo writes to
    a nonexistent table, because the mock never touches a real database.

    This integration test uses a REAL sqlite3 connection against a REAL
    schema. When record_transaction() runs invalid SQL (INSERT INTO
    tx_history instead of transactions), sqlite3 raises OperationalError
    ("no such table: tx_history") -- a bug the unit suite is structurally
    incapable of catching, because mocks only verify calls, not schema.
    """
    broken_repo = BrokenSqliteRepository(sqlite_repo.db_path)
    router = pr.PaymentRouter(broken_repo, AlwaysSucceedGateway(), AlwaysSucceedGateway())
    with pytest.raises(sqlite3.OperationalError):
        router.execute_transaction("tx-broken", 5.0, VALID_PHONE)
