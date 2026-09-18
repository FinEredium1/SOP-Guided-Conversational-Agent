from typing import Literal

from pydantic import BaseModel, ConfigDict


Intent = Literal[
    "claim_status",
    "denial_reason",
    "required_documents",
    "document_submission",
    "payment_amount",
    "appeal",
    "next_steps",
    "general_claim_help",
    "unknown",
]


class TurnAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_number: str | None
    full_name: str | None
    dob: str | None
    phone: str | None
    email: str | None
    id_last4: str | None
    case_id: str | None
    intent: Intent
    claim_type_hint: str | None
    date_hint: str | None
    status_hint: str | None
    emotion: Literal["neutral", "frustrated", "anxious", "angry", "confused"]
    refuses_verification: bool
    requests_human: bool
    in_scope: bool
    email_choice: Literal["send", "skip", "unclear"]
    case_complete: bool


class ChatRequest(BaseModel):
    session_id: str
    message: str
    api_key: str | None = None
    provider: Literal["server", "openai", "deepseek", "custom"] = "server"
    base_url: str | None = None
    model: str | None = None


class ChatResponse(BaseModel):
    message: str
    state: dict
    model_used: bool
    needs_api_key: bool = False


class SessionResponse(BaseModel):
    session_id: str
    message: str
    state: dict
