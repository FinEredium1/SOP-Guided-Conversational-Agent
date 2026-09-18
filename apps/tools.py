import re
from dataclasses import dataclass, field
from datetime import date

from apps import memory, verification


@dataclass
class IdentitySubmissionResult:
    verified: bool
    errors: list[str] = field(default_factory=list)
    accepted_fields: list[str] = field(default_factory=list)


def submit_identity_information(
    session: memory.ConversationSession,
    policy_number: str | None = None,
    full_name: str | None = None,
    dob: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    id_last4: str | None = None,
    ssn_last4: str | None = None,
) -> IdentitySubmissionResult:
    """Validate and store identity inputs, then verify them in application code."""
    if session.is_verified:
        return IdentitySubmissionResult(verified=True)

    errors: list[str] = []
    accepted_fields: list[str] = []

    if policy_number is not None:
        policy_number_norm = policy_number.strip().upper()
        if re.fullmatch(r"POL-\d+", policy_number_norm):
            session.policy_number = policy_number_norm
        else:
            errors.append("The policy number format is invalid.")

    if full_name is not None:
        name_norm = " ".join(full_name.strip().split())
        if name_norm:
            session.provided_pii["full_name"] = name_norm
            accepted_fields.append("full_name")
        else:
            errors.append("The full name is invalid.")

    if dob is not None:
        try:
            session.provided_pii["dob"] = date.fromisoformat(dob).isoformat()
            accepted_fields.append("dob")
        except (TypeError, ValueError):
            errors.append("The date of birth must use YYYY-MM-DD format.")

    if phone is not None:
        phone_digits = re.sub(r"\D", "", phone)
        if len(phone_digits) == 10:
            phone_digits = "1" + phone_digits
        if len(phone_digits) == 11 and phone_digits.startswith("1"):
            session.provided_pii["phone"] = "+" + phone_digits
            accepted_fields.append("phone")
        else:
            errors.append("The phone number is invalid.")

    if email is not None:
        email_norm = email.strip().casefold()
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email_norm):
            session.provided_pii["email"] = email_norm
            accepted_fields.append("email")
        else:
            errors.append("The email address is invalid.")

    submitted_last4 = id_last4 if id_last4 is not None else ssn_last4
    if submitted_last4 is not None:
        last4_norm = submitted_last4.strip()
        if len(last4_norm) == 4 and last4_norm.isdigit():
            session.provided_pii["id_last4"] = last4_norm
            accepted_fields.append("id_last4")
        else:
            errors.append("The ID last four must contain exactly four digits.")

    verified = verification.verify_identity(session)
    return IdentitySubmissionResult(
        verified=verified,
        errors=errors,
        accepted_fields=accepted_fields,
    )
