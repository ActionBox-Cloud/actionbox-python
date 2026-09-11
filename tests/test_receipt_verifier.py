import base64
import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from actionbox import Actionbox, ReceiptVerificationError, verify_decision_receipt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

FIXTURES = Path(__file__).parent / "fixtures"
if not FIXTURES.is_dir():
    FIXTURES = Path(__file__).parents[3] / "test-fixtures"
CONTRACT_FIXTURE = FIXTURES / "decision-receipt-v1.json"
APPROVAL_FIXTURE = FIXTURES / "decision-receipt-v2.json"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class ReceiptVerifierTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.priv = Ed25519PrivateKey.generate()
        cls.pub = cls.priv.public_key()
        cls.raw_pub = cls.pub.public_bytes_raw()
        cls.kid = "test-key-1"

    def _make_receipt(self, payload_overrides=None, kid="test-key-1", tamper_sig=False):
        header = {
            "alg": "EdDSA",
            "typ": "actionbox-decision-receipt",
            "v": 1,
            "kid": kid,
        }
        payload = {
            "iss": "actionbox",
            "receipt_version": 1,
            "action_id": "act_12345",
            "environment": "live",
            "version": 1,
            "action_version": 1,
            "fingerprint": "fp_abcdef",
            "status": "resolved",
            "resolved_at": datetime.now(UTC).isoformat(),
        }
        if payload_overrides:
            payload.update(payload_overrides)

        encoded_h = _b64url(json.dumps(header).encode("utf-8"))
        encoded_p = _b64url(json.dumps(payload).encode("utf-8"))
        signing_input = f"{encoded_h}.{encoded_p}".encode("ascii")
        sig = self.priv.sign(signing_input)
        if tamper_sig:
            sig = bytes([sig[0] ^ 0xFF]) + sig[1:]
        encoded_s = _b64url(sig)
        return f"{encoded_h}.{encoded_p}.{encoded_s}"

    def test_verify_valid_receipt(self):
        fixture = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
        verified = verify_decision_receipt(
            fixture["receipt"],
            fixture["jwks"],
            expected_action_id="act_contract_fixture",
            expected_environment="live",
            expected_fingerprint="sha256:" + "a" * 64,
        )
        for key, value in fixture["claims"].items():
            self.assertEqual(verified[key], value)

    def test_client_verify_receipt_delegates_to_public_verifier(self):
        fixture = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
        client = Actionbox("axb_test")
        try:
            verified = client.verify_receipt(
                fixture["receipt"],
                fixture["jwks"],
                expected_action_id="act_contract_fixture",
            )
        finally:
            client.close()
        self.assertEqual(verified["action_id"], "act_contract_fixture")

    def test_verify_approval_policy_v2_fixture(self):
        fixture = json.loads(APPROVAL_FIXTURE.read_text(encoding="utf-8"))
        verified = verify_decision_receipt(
            fixture["receipt"], fixture["jwks"], expected_action_id="act_approval_fixture"
        )
        self.assertEqual(verified["receipt_version"], 2)
        self.assertEqual(verified["resolution_method"], "approval_policy")
        self.assertEqual(len(verified["approval_progress"]["votes"]), 2)

    def test_verify_tampered_signature_fails(self):
        receipt = self._make_receipt(tamper_sig=True)
        public_keys = {self.kid: self.raw_pub}
        with self.assertRaises(ReceiptVerificationError):
            verify_decision_receipt(receipt, public_keys)

    def test_verify_mismatched_action_id_fails(self):
        receipt = self._make_receipt()
        public_keys = {self.kid: self.raw_pub}
        with self.assertRaises(ReceiptVerificationError):
            verify_decision_receipt(receipt, public_keys, expected_action_id="wrong_id")

    def test_verify_expired_receipt_fails(self):
        old_time = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        receipt = self._make_receipt(payload_overrides={"resolved_at": old_time})
        public_keys = {self.kid: self.raw_pub}
        with self.assertRaises(ReceiptVerificationError):
            verify_decision_receipt(receipt, public_keys, max_age_seconds=30)

    def test_verify_future_receipt_fails(self):
        future_time = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        receipt = self._make_receipt(payload_overrides={"resolved_at": future_time})
        with self.assertRaises(ReceiptVerificationError):
            verify_decision_receipt(
                receipt,
                {self.kid: self.raw_pub},
                max_age_seconds=30,
                max_future_skew_seconds=10,
            )

    def test_verify_future_receipt_fails_without_max_age(self):
        future_time = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        receipt = self._make_receipt(payload_overrides={"resolved_at": future_time})
        with self.assertRaises(ReceiptVerificationError):
            verify_decision_receipt(
                receipt,
                {self.kid: self.raw_pub},
                max_future_skew_seconds=10,
            )

    def test_verify_invalid_timestamp_fails_without_max_age(self):
        receipt = self._make_receipt(payload_overrides={"resolved_at": "not-a-timestamp"})
        with self.assertRaises(ReceiptVerificationError):
            verify_decision_receipt(receipt, {self.kid: self.raw_pub})

    def test_verify_unknown_key_id_fails_closed(self):
        receipt = self._make_receipt(kid="unknown-key")
        with self.assertRaises(ReceiptVerificationError):
            verify_decision_receipt(receipt, {self.kid: self.raw_pub})


if __name__ == "__main__":
    unittest.main()
