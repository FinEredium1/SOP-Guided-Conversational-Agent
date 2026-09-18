from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any


class Phase(IntEnum):
    VERIFY_ID = 0
    RESOLVE_INTENT = 1
    PROCESS_CASE = 2
    POST_PROCESS = 3


@dataclass
class ConversationMemory:
    intent_hint: str | None = None
    claim_type_hint: str | None = None
    date_hint: str | None = None
    status_hint: str | None = None
    selected_claim_id: str | None = None


@dataclass
class ConversationSession:
    phase: Phase = Phase.VERIFY_ID
    provided_pii: dict[str, str] = field(default_factory=dict)
    matched_pii: set[str] = field(default_factory=set)
    policy_number: str | None = None
    candidate_party_id: str | None = None
    verified_party_id: str | None = None
    memory: ConversationMemory = field(default_factory=ConversationMemory)
    failed_verification_attempts: int = 0
    irrelevant_attempts: int = 0
    verification_refusals: int = 0
    escalated_to_human: bool = False
    email_summary_sent: bool = False
    email_summary_skipped: bool = False
    email_summary: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_verified(self) -> bool:
        return self.verified_party_id is not None

    def add_message(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})

    def public_state(self) -> dict[str, Any]:
        """Return UI-safe state. Raw PII and match results stay private."""
        return {
            "phase": self.phase.name,
            "is_verified": self.is_verified,
            "provided_fields": sorted(self.provided_pii),
            "policy_number_provided": self.policy_number is not None,
            "memory": asdict(self.memory),
            "escalated_to_human": self.escalated_to_human,
            "email_summary_sent": self.email_summary_sent,
            "email_summary_skipped": self.email_summary_skipped,
        }


@dataclass
class ExtractedPII:
    full_name: str | None = None
    dob: str | None = None
    phone: str | None = None
    email: str | None = None
    id_last4: str | None = None
