import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from starlette.requests import Request

from src.backend.api.schemas import EvidenceReviewRequest
from src.backend.api.staff_auth import authenticate_staff, require_roles
from src.backend.repositories.decision_lifecycle_repository import validate_human_decision


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/staff/me", "headers": []})


class StaffAuthenticationTests(unittest.TestCase):
    CONFIG = '{"secret-reviewer":{"staff_id":"agent_7","roles":["reviewer"]}}'

    @patch.dict(os.environ, {"STAFF_API_KEYS": CONFIG}, clear=False)
    def test_token_resolves_server_controlled_identity(self) -> None:
        principal = authenticate_staff(
            _request(), HTTPAuthorizationCredentials(scheme="Bearer", credentials="secret-reviewer")
        )
        self.assertEqual(principal.staff_id, "agent_7")
        self.assertEqual(principal.roles, {"reviewer"})

    @patch.dict(os.environ, {"STAFF_API_KEYS": CONFIG}, clear=False)
    def test_invalid_token_is_unauthorized(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            authenticate_staff(
                _request(), HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
            )
        self.assertEqual(caught.exception.status_code, 401)

    @patch.dict(os.environ, {"STAFF_API_KEYS": CONFIG}, clear=False)
    def test_role_dependency_rejects_insufficient_role(self) -> None:
        principal = authenticate_staff(
            _request(), HTTPAuthorizationCredentials(scheme="Bearer", credentials="secret-reviewer")
        )
        dependency = require_roles("admin")
        with self.assertRaises(HTTPException) as caught:
            dependency(principal)
        self.assertEqual(caught.exception.status_code, 403)


class HumanDecisionValidationTests(unittest.TestCase):
    def test_refund_approval_requires_amount(self) -> None:
        with self.assertRaisesRegex(ValueError, "批准金额"):
            validate_human_decision(
                request_type="Refund", requested_quantity=2, decision="Approve",
                approved_quantity=1, approved_amount=None,
            )

    def test_partial_approval_must_be_less_than_requested(self) -> None:
        with self.assertRaisesRegex(ValueError, "小于申请数量"):
            validate_human_decision(
                request_type="PartialRefund", requested_quantity=2,
                decision="PartialApprove", approved_quantity=2, approved_amount=10,
            )

    def test_rejection_cannot_carry_approved_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能填写"):
            validate_human_decision(
                request_type="Refund", requested_quantity=1, decision="Reject",
                approved_quantity=1, approved_amount=10,
            )


class EvidenceReviewValidationTests(unittest.TestCase):
    def test_rejected_evidence_requires_customer_visible_reason(self) -> None:
        with self.assertRaises(ValidationError):
            EvidenceReviewRequest(
                verification_status="Rejected",
                review_notes="   ",
            )

    def test_verified_evidence_notes_remain_optional(self) -> None:
        payload = EvidenceReviewRequest(verification_status="Verified")
        self.assertIsNone(payload.review_notes)


if __name__ == "__main__":
    unittest.main()
