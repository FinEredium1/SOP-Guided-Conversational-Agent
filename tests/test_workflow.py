import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps import claims
from apps.agent import SOPAgent
from apps.main import app
from apps.llm import ModelConfigurationError, ModelGateway
from apps.memory import ConversationSession, Phase
from apps.schemas import TurnAnalysis
from apps.tools import submit_identity_information


SAMPLE_CALL = (
    "I'm the policyholder. My name is Margaret Chen, policy POL-9921. "
    "I'm calling about my denied healthcare claim from January. "
    "DOB is 1985-03-15, SSN last four is 4472."
)


def fake_analysis(_gateway, message: str, phase: Phase) -> TurnAnalysis:
    lowered = message.casefold()
    is_sample_person = "margaret chen" in lowered
    return TurnAnalysis(
        policy_number="POL-9921" if "pol-9921" in lowered else None,
        full_name="Margaret Chen" if is_sample_person else None,
        dob="1985-03-15" if "1985-03-15" in lowered else None,
        phone=None,
        email=None,
        id_last4="4472" if "4472" in lowered else ("0000" if "0000" in lowered else None),
        case_id=None,
        intent="denial_reason" if "why" in lowered else (
            "general_claim_help" if any(word in lowered for word in ("claim", "insurance", "policy")) else "unknown"
        ),
        claim_type_hint="healthcare" if "healthcare" in lowered else None,
        date_hint="january" if "january" in lowered else None,
        status_hint="denied" if "denied" in lowered else None,
        emotion="neutral",
        refuses_verification=False,
        requests_human=False,
        in_scope=not any(term in lowered for term in ("reinforcement learning", "what is rl")),
        email_choice=(
            "send"
            if phase == Phase.POST_PROCESS and any(term in lowered for term in ("yes", "send it"))
            else "unclear"
        ),
        case_complete=any(term in lowered for term in ("that's all", "that is all")),
    )


def fake_compose(
    gateway,
    user_message,
    phase,
    safe_context,
    fallback,
    emotional_tone="neutral",
):
    gateway.last_call_used_model = True
    return fallback


class VerificationTests(unittest.TestCase):
    def test_claims_are_blocked_before_verification(self):
        with self.assertRaises(claims.ClaimAccessError):
            claims.get_verified_party_claims(ConversationSession())

    def test_two_matches_do_not_verify(self):
        session = ConversationSession()
        result = submit_identity_information(
            session,
            policy_number="POL-9921",
            full_name="Margaret Chen",
            dob="1985-03-15",
        )
        self.assertFalse(result.verified)
        self.assertEqual(session.phase, Phase.VERIFY_ID)

    def test_three_matches_verify_and_advance(self):
        session = ConversationSession()
        result = submit_identity_information(
            session,
            policy_number="pol-9921",
            full_name="  Margaret   Chen ",
            dob="1985-03-15",
            phone="(650) 521-2836",
        )
        self.assertTrue(result.verified)
        self.assertEqual(session.verified_party_id, "P9")
        self.assertEqual(session.phase, Phase.RESOLVE_INTENT)

    def test_repeated_field_cannot_count_multiple_times(self):
        session = ConversationSession()
        submit_identity_information(
            session,
            policy_number="POL-9921",
            full_name="Margaret Chen",
        )
        submit_identity_information(session, full_name="Margaret Chen")
        result = submit_identity_information(session, full_name="Margaret Chen")
        self.assertFalse(result.verified)
        self.assertEqual(session.matched_pii, {"full_name"})


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.analyze_patch = patch("apps.agent.ModelGateway.analyze", new=fake_analysis)
        self.compose_patch = patch("apps.agent.ModelGateway.compose", new=fake_compose)
        self.analyze_patch.start()
        self.compose_patch.start()
        self.addCleanup(self.analyze_patch.stop)
        self.addCleanup(self.compose_patch.stop)
        self.agent = SOPAgent()
        self.session = ConversationSession()

    def send(self, message: str) -> str:
        return self.agent.handle(self.session, message, api_key="test-key").message

    def test_sample_call_verifies_and_uses_early_claim_hint(self):
        reply = self.send(SAMPLE_CALL)
        self.assertTrue(self.session.is_verified)
        self.assertEqual(self.session.phase, Phase.PROCESS_CASE)
        self.assertEqual(self.session.memory.selected_claim_id, "CL-2048")
        self.assertIn("CL-2048", reply)

    def test_early_hint_is_remembered_across_turns(self):
        self.send(
            "My name is Margaret Chen, policy POL-9921. I'm calling about my "
            "denied healthcare claim from January."
        )
        self.assertEqual(self.session.phase, Phase.VERIFY_ID)
        self.assertEqual(self.session.memory.claim_type_hint, "healthcare")
        self.assertEqual(self.session.memory.status_hint, "denied")
        self.assertEqual(self.session.memory.date_hint, "january")

        self.send("My DOB is 1985-03-15 and my SSN last four is 4472.")
        self.assertEqual(self.session.phase, Phase.PROCESS_CASE)
        self.assertEqual(self.session.memory.selected_claim_id, "CL-2048")

    def test_wrong_third_value_does_not_verify(self):
        self.send(
            "My name is Margaret Chen, policy POL-9921, DOB is 1985-03-15, "
            "and SSN last four is 0000."
        )
        self.assertFalse(self.session.is_verified)
        self.assertEqual(self.session.phase, Phase.VERIFY_ID)

    def test_greeting_is_warm_and_requests_three_identity_details(self):
        reply = self.send("hello")
        self.assertIn("hello", reply.casefold())
        self.assertIn("any three", reply.casefold())
        self.assertNotIn("repetitive", reply.casefold())
        self.assertFalse(self.session.is_verified)
        self.assertEqual(self.session.phase, Phase.VERIFY_ID)

    def test_out_of_scope_retries_escalate(self):
        first = self.send("What is reinforcement learning?")
        self.assertIn("insurance", first.casefold())
        second = self.send("No really, what is RL?")
        self.assertTrue(self.session.escalated_to_human)
        self.assertIn("human", second.casefold())

    def test_post_process_consent(self):
        self.send(SAMPLE_CALL)
        offer = self.send("That's all, thanks.")
        self.assertEqual(self.session.phase, Phase.POST_PROCESS)
        self.assertIn("email", offer.casefold())
        confirmation = self.send("Yes, please send it.")
        self.assertTrue(self.session.email_summary_sent)
        self.assertIsNotNone(self.session.email_summary)
        self.assertIn("recorded as sent", confirmation.casefold())

    def test_public_state_does_not_expose_pii(self):
        self.send(SAMPLE_CALL)
        public = self.session.public_state()
        rendered = str(public)
        self.assertNotIn("4472", rendered)
        self.assertNotIn("1985-03-15", rendered)
        self.assertNotIn("Margaret Chen", rendered)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_browser_and_chat_api(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Claims Guide", page.text)

        created = self.client.post("/api/sessions")
        self.assertEqual(created.status_code, 200)
        session_id = created.json()["session_id"]

        missing_key = self.client.post(
            "/api/chat",
            json={"session_id": session_id, "message": SAMPLE_CALL},
        )
        self.assertEqual(missing_key.status_code, 200)
        self.assertTrue(missing_key.json()["needs_api_key"])
        self.assertEqual(missing_key.json()["state"]["phase"], "VERIFY_ID")

        with patch("apps.agent.ModelGateway.analyze", new=fake_analysis), patch(
            "apps.agent.ModelGateway.compose", new=fake_compose
        ):
            reply = self.client.post(
                "/api/chat",
                json={
                    "session_id": session_id,
                    "message": SAMPLE_CALL,
                    "api_key": "test-key",
                },
            )
        self.assertEqual(reply.status_code, 200)
        payload = reply.json()
        self.assertEqual(payload["state"]["phase"], "PROCESS_CASE")
        self.assertTrue(payload["state"]["is_verified"])
        self.assertNotIn("4472", str(payload["state"]))

    def test_agent_without_key_does_not_change_conversation(self):
        session = ConversationSession()
        result = SOPAgent().handle(session, SAMPLE_CALL)
        self.assertTrue(result.needs_api_key)
        self.assertEqual(session.phase, Phase.VERIFY_ID)
        self.assertEqual(session.history, [])
        self.assertIn("API key", result.message)

    def test_deepseek_configuration_uses_compatible_endpoint(self):
        gateway = ModelGateway(
            api_key="deepseek-test-key",
            provider="deepseek",
        )
        self.assertEqual(gateway.base_url, "https://api.deepseek.com")
        self.assertEqual(gateway.model, "deepseek-flash")
        self.assertTrue(gateway.is_configured)

    def test_custom_provider_requires_safe_https_url(self):
        with self.assertRaises(ModelConfigurationError):
            ModelGateway(
                api_key="test-key",
                provider="custom",
                base_url="http://localhost:9000",
                model="custom-model",
            )


if __name__ == "__main__":
    unittest.main()
