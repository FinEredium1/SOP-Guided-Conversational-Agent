from dataclasses import dataclass
import re
from typing import Any

from apps import claims, verification
from apps.llm import ModelConfigurationError, ModelGateway, ModelRequestError
from apps.memory import ConversationSession, Phase
from apps.schemas import TurnAnalysis
from apps.tools import submit_identity_information


@dataclass
class AgentResult:
    message: str
    model_used: bool
    needs_api_key: bool = False


PII_LABELS = {
    "full_name": "full name",
    "dob": "date of birth",
    "phone": "phone number",
    "email": "email address",
    "id_last4": "ID or SSN last four digits",
}

GREETING_ONLY_PATTERN = re.compile(
    r"^\s*(?:hi|hello|hey|good\s+(?:morning|afternoon|evening))(?:\s+there)?[!.?,\s]*$",
    re.IGNORECASE,
)


class SOPAgent:
    def handle(
        self,
        session: ConversationSession,
        user_message: str,
        api_key: str | None = None,
        provider: str = "server",
        base_url: str | None = None,
        model: str | None = None,
    ) -> AgentResult:
        try:
            gateway = ModelGateway(
                api_key=api_key,
                provider=provider,
                base_url=base_url,
                model=model,
            )
        except ModelConfigurationError as exc:
            return AgentResult(message=str(exc), model_used=False, needs_api_key=True)
        if not gateway.is_configured:
            return AgentResult(
                message=(
                    "A model API key is required to start the conversation. Open Model "
                    "settings, choose your provider, enter its API key, and send your message again."
                ),
                model_used=False,
                needs_api_key=True,
            )

        session.add_message("user", user_message)
        try:
            analysis = gateway.analyze(user_message, session.phase)
        except ModelRequestError:
            return self._finish(
                session,
                (
                    f"I couldn’t reach the {gateway.provider} model. Please confirm the "
                    "provider, API key, and model in Model settings, then try again."
                ),
                False,
            )
        analysis_used_model = gateway.last_call_used_model
        self._remember(session, analysis)

        if analysis.requests_human:
            session.escalated_to_human = True
            reply = (
                "Of course. I’ll stop the automated workflow and arrange a human "
                "claims representative. In this demo, the transfer has been recorded."
            )
            return self._finish(session, reply, analysis_used_model)

        if not analysis.in_scope:
            session.irrelevant_attempts += 1
            if session.irrelevant_attempts >= 2:
                session.escalated_to_human = True
                reply = (
                    "I can only help with insurance customer-service questions. Since "
                    "we haven’t been able to get back to your claim, I can connect you "
                    "with a human representative. The demo transfer has been recorded."
                )
            else:
                reply = (
                    "I’m here specifically to help with insurance policies and claims, "
                    "so I can’t answer that question. We can continue with identity "
                    "verification or your claim whenever you’re ready."
                )
            return self._finish(session, reply, analysis_used_model)

        try:
            if session.phase == Phase.VERIFY_ID:
                reply = self._handle_verification(session, analysis, user_message, gateway)
            elif session.phase == Phase.RESOLVE_INTENT:
                reply = self._handle_resolution(session, analysis, user_message, gateway)
            elif session.phase == Phase.PROCESS_CASE:
                reply = self._handle_case(session, analysis, user_message, gateway)
            else:
                reply = self._handle_post_process(session, analysis, user_message, gateway)
        except ModelRequestError:
            reply = (
                f"The {gateway.provider} model couldn’t generate a response. Please "
                "confirm the provider, API key, and model in Model settings, then try again."
            )

        return self._finish(
            session,
            reply,
            analysis_used_model or gateway.last_call_used_model,
        )

    @staticmethod
    def _remember(session: ConversationSession, analysis: TurnAnalysis) -> None:
        if analysis.intent != "unknown":
            session.memory.intent_hint = analysis.intent
        if analysis.claim_type_hint:
            session.memory.claim_type_hint = analysis.claim_type_hint
        if analysis.date_hint:
            session.memory.date_hint = analysis.date_hint
        if analysis.status_hint:
            session.memory.status_hint = analysis.status_hint
        if analysis.case_id:
            session.memory.selected_claim_id = analysis.case_id

    def _handle_verification(
        self,
        session: ConversationSession,
        analysis: TurnAnalysis,
        user_message: str,
        gateway: ModelGateway,
    ) -> str:
        if GREETING_ONLY_PATTERN.fullmatch(user_message):
            return (
                "Hello! I’m happy to help with your insurance claim. To get started "
                "securely, please share any three of these identity details: your full "
                "name, date of birth, phone number, email address, or the last four "
                "digits of your ID or SSN."
            )

        result = submit_identity_information(
            session=session,
            policy_number=analysis.policy_number,
            full_name=analysis.full_name,
            dob=analysis.dob,
            phone=analysis.phone,
            email=analysis.email,
            id_last4=analysis.id_last4,
        )

        if result.verified:
            selected, available = claims.resolve_claim(session)
            if selected is not None:
                return self._claim_answer(
                    session,
                    selected,
                    analysis,
                    user_message,
                    gateway,
                    verification_just_completed=True,
                )
            if not available:
                session.phase = Phase.RESOLVE_INTENT
                fallback = (
                    "Thank you—your identity is verified. I don’t see a claim on the "
                    "available demo record, so a human representative should review it."
                )
            else:
                choices = "; ".join(claims.claim_choices(available))
                fallback = (
                    "Thank you—your identity is verified. Which claim are you calling "
                    f"about? I found: {choices}."
                )
            return gateway.compose(
                user_message,
                session.phase,
                {"required_action": "Confirm verification and ask which claim.", "claim_choices": claims.claim_choices(available)},
                fallback,
                analysis.emotion,
            )

        if analysis.refuses_verification:
            session.verification_refusals += 1
        if result.accepted_fields and len(session.provided_pii) >= 3:
            session.failed_verification_attempts += 1

        if session.verification_refusals >= 2 or session.failed_verification_attempts >= 3:
            session.escalated_to_human = True
            return (
                "I understand this is frustrating. I can’t disclose protected claim "
                "details without verification, and the automated check wasn’t completed. "
                "I’ll stop asking and record a transfer to a human representative."
            )

        remaining = [
            label
            for key, label in PII_LABELS.items()
            if key not in session.provided_pii
        ]
        if remaining:
            options = ", ".join(remaining)
            fallback = (
                "I know verification can feel repetitive, but it protects your claim "
                "information. I still need additional verification information. You "
                f"can provide any of these alternatives: {options}."
            )
        else:
            session.escalated_to_human = True
            fallback = (
                "I’m sorry, but I couldn’t complete the automated identity check. I "
                "haven’t accessed or disclosed any claim details. I’ll record a transfer "
                "to a human representative for help verifying your identity."
            )

        safe_context = {
            "required_action": "Explain verification and request alternate identity fields. Do not discuss any claim record.",
            "allowed_identity_options": remaining,
            "validation_errors": result.errors,
        }
        return gateway.compose(
            user_message,
            Phase.VERIFY_ID,
            safe_context,
            fallback,
            analysis.emotion,
        )

    def _handle_resolution(
        self,
        session: ConversationSession,
        analysis: TurnAnalysis,
        user_message: str,
        gateway: ModelGateway,
    ) -> str:
        selected, available = claims.resolve_claim(session)
        if selected is not None:
            return self._claim_answer(session, selected, analysis, user_message, gateway)
        if not available:
            return "I don’t see any claims in the available record. I can arrange human help."

        choices = claims.claim_choices(available)
        fallback = "Which claim do you mean? I found: " + "; ".join(choices) + "."
        return gateway.compose(
            user_message,
            Phase.RESOLVE_INTENT,
            {
                "required_action": "Ask the caller to choose one of the listed claims.",
                "claim_choices": choices,
            },
            fallback,
            analysis.emotion,
        )

    def _handle_case(
        self,
        session: ConversationSession,
        analysis: TurnAnalysis,
        user_message: str,
        gateway: ModelGateway,
    ) -> str:
        if analysis.case_complete:
            session.phase = Phase.POST_PROCESS
            return (
                "Before we finish, would you like me to send an email summary covering "
                "what we discussed, the claim status or outcome, and the next steps?"
            )

        selected = claims.get_selected_claim(session)
        if selected is None:
            session.phase = Phase.RESOLVE_INTENT
            return self._handle_resolution(session, analysis, user_message, gateway)
        return self._claim_answer(session, selected, analysis, user_message, gateway)

    def _claim_answer(
        self,
        session: ConversationSession,
        claim: dict[str, Any],
        analysis: TurnAnalysis,
        user_message: str,
        gateway: ModelGateway,
        verification_just_completed: bool = False,
    ) -> str:
        context = claims.grounded_claim_context(claim, user_message)
        context["required_action"] = (
            "Answer the caller using only the grounded claim and guidance facts. "
            "If verification_just_completed is true, briefly confirm verification first."
        )
        context["verification_just_completed"] = verification_just_completed
        fallback = self._fallback_claim_answer(claim, analysis.intent, verification_just_completed)
        return gateway.compose(
            user_message,
            Phase.PROCESS_CASE,
            context,
            fallback,
            analysis.emotion,
        )

    @staticmethod
    def _fallback_claim_answer(
        claim: dict[str, Any],
        intent: str,
        verification_just_completed: bool,
    ) -> str:
        prefix = "Thank you—your identity is verified. " if verification_just_completed else ""
        case_id = claim["case_id"]
        if intent == "denial_reason" and claim.get("denial_reason"):
            return prefix + f"Claim {case_id} was denied because {claim['denial_reason']}."
        if intent in {"required_documents", "document_submission", "next_steps"}:
            documents = claim.get("documents_needed", [])
            if documents:
                deadline = claim.get("appeal_deadline")
                deadline_text = f" The appeal deadline shown is {deadline}." if deadline else ""
                return prefix + f"For claim {case_id}, the requested documents are {', '.join(documents)}.{deadline_text}"
        if intent == "appeal":
            if claim.get("appeal_deadline"):
                return prefix + f"The appeal deadline shown for claim {case_id} is {claim['appeal_deadline']}."
            return prefix + f"The available record does not list an appeal deadline for claim {case_id}."
        if intent == "payment_amount":
            return (
                prefix
                + f"For claim {case_id}, the expected reimbursement is ${claim.get('expected_reimbursement_amount', 'not listed')} "
                + f"and the finalized net payment is ${claim.get('net_pay', 'not listed')}."
            )
        return prefix + f"I found claim {case_id}. Its current status is {claim['status']}: {claim['summary']}. What would you like to know about it?"

    def _handle_post_process(
        self,
        session: ConversationSession,
        analysis: TurnAnalysis,
        user_message: str,
        gateway: ModelGateway,
    ) -> str:
        if analysis.email_choice == "send":
            summary = self._build_email_summary(session)
            session.email_summary = summary
            session.email_summary_sent = True
            return "Done—the demo email summary has been recorded as sent. Thank you for calling."
        if analysis.email_choice == "skip":
            session.email_summary_skipped = True
            return "No problem—I’ve skipped the email summary. Thank you for calling."

        selected = claims.get_selected_claim(session)
        if analysis.intent != "unknown" and selected is not None:
            answer = self._claim_answer(session, selected, analysis, user_message, gateway)
            return answer + " Would you like an email summary before we finish?"
        return "Would you like me to send the conversation and next-step summary by email, or should I skip it?"

    @staticmethod
    def _build_email_summary(session: ConversationSession) -> str:
        claim = claims.get_selected_claim(session)
        if claim is None:
            return "Insurance support conversation completed. No individual claim was selected."

        lines = [
            f"Claim: {claim['case_id']} ({claim['case_type']})",
            f"Status/outcome: {claim['status']} — {claim['summary']}",
        ]
        if claim.get("denial_reason"):
            lines.append(f"Reason discussed: {claim['denial_reason']}")
        if claim.get("documents_needed"):
            lines.append("Next steps: provide " + ", ".join(claim["documents_needed"]))
        if claim.get("appeal_deadline"):
            lines.append(f"Appeal deadline: {claim['appeal_deadline']}")
        return "\n".join(lines)

    @staticmethod
    def _finish(session: ConversationSession, reply: str, model_used: bool) -> AgentResult:
        session.add_message("assistant", reply)
        return AgentResult(message=reply, model_used=model_used)
