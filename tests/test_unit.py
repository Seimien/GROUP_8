import pytest
from unittest.mock import MagicMock, patch
import payment_router as pr

VALID_PHONE = "+15551234567"

@pytest.fixture
def repo():
    return MagicMock(spec=pr.DatabaseRepository)

@pytest.fixture
def primary_gw():
    return MagicMock(spec=pr.PaymentGatewayClient)

@pytest.fixture
def backup_gw():
    return MagicMock(spec=pr.PaymentGatewayClient)

@pytest.fixture
def router(repo, primary_gw, backup_gw, monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_API_KEY", "REAL_PROD_KEY_123")
    repo.get_transaction.return_value = None
    return pr.PaymentRouter(repo, primary_gw, backup_gw)


class TestSecurityGuardrail:
    def test_missing_api_key_raises(self, repo, primary_gw, backup_gw, monkeypatch):
        monkeypatch.delenv("PAYMENT_GATEWAY_API_KEY", raising=False)
        router = pr.PaymentRouter(repo, primary_gw, backup_gw)
        with pytest.raises(PermissionError):
            router.execute_transaction("tx1", 10.0, VALID_PHONE)

    def test_debug_mode_key_raises(self, repo, primary_gw, backup_gw, monkeypatch):
        monkeypatch.setenv("PAYMENT_GATEWAY_API_KEY", "DEBUG_MODE_KEY")
        router = pr.PaymentRouter(repo, primary_gw, backup_gw)
        with pytest.raises(PermissionError):
            router.execute_transaction("tx1", 10.0, VALID_PHONE)


class TestValidation:
    def test_negative_amount_raises(self, router):
        with pytest.raises(ValueError):
            router.execute_transaction("tx1", -5, VALID_PHONE)

    def test_invalid_phone_raises(self, router):
        with pytest.raises(ValueError):
            router.execute_transaction("tx1", 10.0, "0123-bad")


class TestIdempotency:
    def test_already_processed_short_circuits(self, router, repo, primary_gw, backup_gw):
        repo.get_transaction.return_value = {"status": "SUCCESS"}
        result = router.execute_transaction("tx1", 10.0, VALID_PHONE)
        assert result == "ALREADY_PROCESSED"
        primary_gw.process_payment.assert_not_called()
        backup_gw.process_payment.assert_not_called()


class TestRetryLogic:
    @patch("payment_router.time.sleep", return_value=None)
    def test_primary_retries_then_succeeds(self, mock_sleep, router, repo, primary_gw):
        primary_gw.process_payment.side_effect = [Exception("timeout"), True]
        result = router.execute_transaction("tx1", 10.0, VALID_PHONE)
        assert result == "COMPLETED_PRIMARY"
        assert primary_gw.process_payment.call_count == 2
        mock_sleep.assert_called_once()
        repo.record_transaction.assert_called_with("tx1", 10.0, VALID_PHONE, "SUCCESS", "PRIMARY")

    @patch("payment_router.time.sleep", return_value=None)
    def test_falls_back_to_backup(self, mock_sleep, router, repo, primary_gw, backup_gw):
        primary_gw.process_payment.side_effect = Exception("HTTP 500")
        backup_gw.process_payment.return_value = True
        result = router.execute_transaction("tx1", 10.0, VALID_PHONE)
        assert result == "COMPLETED_BACKUP"
        repo.record_transaction.assert_called_with("tx1", 10.0, VALID_PHONE, "SUCCESS", "BACKUP")


class TestCircuitBreak:
    @patch("payment_router.time.sleep", return_value=None)
    def test_both_gateways_fail(self, mock_sleep, router, repo, primary_gw, backup_gw):
        primary_gw.process_payment.side_effect = Exception("HTTP 500")
        backup_gw.process_payment.side_effect = Exception("HTTP 500")
        with pytest.raises(RuntimeError):
            router.execute_transaction("tx1", 10.0, VALID_PHONE)
        repo.record_transaction.assert_called_with("tx1", 10.0, VALID_PHONE, "FAILED", "NONE")
