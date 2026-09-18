import json
from pathlib import Path
from typing import Any

from apps import memory


POLICYHOLDERS_PATH = (
    Path(__file__).parent
    / "insurance_claims"
    / "fixtures"
    / "policyholders.json"
)

FIELD_MAPPING = {
    "full_name": "name",
    "dob": "dob",
    "phone": "phone",
    "email": "email",
    "id_last4": "id_last4",
}

ALIAS_MAPPING = {
    "full_name": "name_aliases",
    "phone": "phone_aliases",
    "email": "email_aliases",
}


def load_policyholders() -> list[dict[str, Any]]:
    with POLICYHOLDERS_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def normalize_for_comparison(field_name: str, value: str) -> str:
    value = value.strip()

    if field_name == "full_name":
        return " ".join(value.casefold().split())
    if field_name == "email":
        return value.casefold()
    if field_name == "phone":
        return "".join(character for character in value if character.isdigit())

    return value


def field_matches(
    field_name: str,
    provided_value: str,
    policyholder: dict[str, Any],
) -> bool:
    record_field = FIELD_MAPPING[field_name]
    accepted_values = [policyholder[record_field]]

    alias_field = ALIAS_MAPPING.get(field_name)
    if alias_field:
        accepted_values.extend(policyholder.get(alias_field, []))

    normalized_provided = normalize_for_comparison(field_name, provided_value)
    return any(
        normalized_provided == normalize_for_comparison(field_name, accepted_value)
        for accepted_value in accepted_values
    )


def find_policyholder(
    session: memory.ConversationSession,
    policyholders: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if session.policy_number is not None:
        for policyholder in policyholders:
            if policyholder["policy_number"] == session.policy_number:
                return policyholder
        return None

    # A policy number is helpful but not mandatory. If the supplied PII points
    # to exactly one best-matching record, use that record as the candidate.
    scored_policyholders = []
    for policyholder in policyholders:
        score = sum(
            field_matches(field_name, provided_value, policyholder)
            for field_name, provided_value in session.provided_pii.items()
        )
        if score:
            scored_policyholders.append((score, policyholder))

    if not scored_policyholders:
        return None

    best_score = max(score for score, _ in scored_policyholders)
    best_matches = [
        policyholder
        for score, policyholder in scored_policyholders
        if score == best_score
    ]
    return best_matches[0] if len(best_matches) == 1 else None


def verify_identity(
    session: memory.ConversationSession,
    policyholders: list[dict[str, Any]] | None = None,
) -> bool:
    if session.is_verified:
        return True

    if policyholders is None:
        policyholders = load_policyholders()
    candidate = find_policyholder(session, policyholders)

    if candidate is None:
        session.candidate_party_id = None
        session.matched_pii.clear()
        return False

    session.candidate_party_id = candidate["party_id"]
    session.matched_pii = {
        field_name
        for field_name, provided_value in session.provided_pii.items()
        if field_matches(field_name, provided_value, candidate)
    }

    if len(session.matched_pii) >= 3:
        session.verified_party_id = candidate["party_id"]
        session.phase = memory.Phase.RESOLVE_INTENT
        return True

    return False
