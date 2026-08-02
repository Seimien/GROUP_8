from unittest.mock import MagicMock, patch
import pytest
import payment_router as pr

VALID_PHONE = "+15551234567"

@pytest.fixture
def env_key(monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_API_KEY", "REAL_PROD_KEY_123")

@patch("payment_router.time.sleep", return_value=None)
def test_primary_504_falls_back_cleanly(mock_sleep, env_key):
    repo = MagicMock(spec=pr.DatabaseRepository)
    repo.get_transaction.return_value = None
    primary = MagicMock(spec=pr.PaymentGatewayClient)
    backup = MagicMock(spec=pr.PaymentGatewayClient)
    primary.process_payment.side_effect = Exception("504 Gateway Timeout")
    backup.process_payment.return_value = True

    router = pr.PaymentRouter(repo, primary, backup)
    result = router.execute_transaction("tx-chaos", 15.0, VALID_PHONE)
    assert result == "COMPLETED_BACKUP"
