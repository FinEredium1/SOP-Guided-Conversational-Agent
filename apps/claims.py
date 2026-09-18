import json
from datetime import datetime
from pathlib import Path
from typing import Any

from apps.memory import ConversationSession, Phase


FIXTURES_DIR = Path(__file__).parent / "insurance_claims" / "fixtures"
CLAIMS_PATH = FIXTURES_DIR / "claims.json"
GUIDANCE_PATH = FIXTURES_DIR / "required_document_guideline.json"


class ClaimAccessError(PermissionError):
    pass


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def load_claims() -> list[dict[str, Any]]:
    return _load_json(CLAIMS_PATH)


def get_verified_party_claims(
    session: ConversationSession,
    claim_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not session.is_verified:
        raise ClaimAccessError("Identity verification is required before claim access.")

    if claim_records is None:
        claim_records = load_claims()
    return [
        claim
        for claim in claim_records
        if claim["party_id"] == session.verified_party_id
    ]


def get_selected_claim(session: ConversationSession) -> dict[str, Any] | None:
    claim_id = session.memory.selected_claim_id
    if claim_id is None:
        return None
    return next(
        (
            claim
            for claim in get_verified_party_claims(session)
            if claim["case_id"] == claim_id
        ),
        None,
    )


def _date_hint_score(date_hint: str, created_at: str) -> int:
    hint = date_hint.casefold()
    created = datetime.strptime(created_at, "%Y-%m-%d")
    score = 0
    if str(created.year) in hint:
        score += 1
    if created.strftime("%B").casefold() in hint or created.strftime("%b").casefold() in hint:
        score += 2
    if created_at in hint:
        score += 4
    return score


def resolve_claim(session: ConversationSession) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    available = get_verified_party_claims(session)
    if not available:
        return None, []

    memory = session.memory
    if memory.selected_claim_id:
        exact = next(
            (claim for claim in available if claim["case_id"] == memory.selected_claim_id),
            None,
        )
        if exact is not None:
            session.phase = Phase.PROCESS_CASE
            return exact, available

    scored: list[tuple[int, dict[str, Any]]] = []
    for claim in available:
        score = 0
        if memory.claim_type_hint and claim["case_type"].casefold() == memory.claim_type_hint.casefold():
            score += 4
        if memory.status_hint and claim["status"].casefold() == memory.status_hint.casefold():
            score += 3
        if memory.date_hint:
            score += _date_hint_score(memory.date_hint, claim["created_at"])
        scored.append((score, claim))

    highest = max(score for score, _ in scored)
    best = [claim for score, claim in scored if score == highest]

    selected = None
    if highest > 0 and len(best) == 1:
        selected = best[0]
    elif len(available) == 1:
        selected = available[0]

    if selected is not None:
        memory.selected_claim_id = selected["case_id"]
        session.phase = Phase.PROCESS_CASE
    return selected, available


def claim_choices(claim_records: list[dict[str, Any]]) -> list[str]:
    return [
        f'{claim["case_id"]}: {claim["case_type"]}, opened {claim["created_at"]}, {claim["status"]}'
        for claim in claim_records
    ]


def grounded_claim_context(claim: dict[str, Any], user_message: str) -> dict[str, Any]:
    """Build the only claim facts the response model is allowed to use."""
    context = {
        "claim": {
            key: value
            for key, value in claim.items()
            if key != "party_id"
        }
    }

    guidance = _load_json(GUIDANCE_PATH)
    context["general_document_guidance"] = guidance["default_guidance"]["en"]
    case_guidance = guidance.get("case_type_guidance", {}).get(claim["case_type"])
    if case_guidance:
        context["case_type_document_guidance"] = case_guidance["en"]

    document_guidance: dict[str, str] = {}
    for requested_document in claim.get("documents_needed", []):
        for guideline_name, guideline in guidance.get("document_guidance", {}).items():
            if requested_document in guideline_name or guideline_name in requested_document:
                document_guidance[requested_document] = guideline["en"]
    if document_guidance:
        context["document_guidance"] = document_guidance

    lowered = user_message.casefold()
    for followup in guidance.get("claim_followup_guidance", []):
        if any(phrase in lowered for phrase in followup.get("match_any", [])):
            text = followup["en"].format(
                case_id=claim["case_id"],
                documents=", ".join(claim.get("documents_needed", [])),
                average_processing_time_after_submission=guidance[
                    "claim_followup_settings"
                ]["average_processing_time_after_submission"]["en"],
            )
            context.setdefault("matched_followup_guidance", []).append(text)

    return context
