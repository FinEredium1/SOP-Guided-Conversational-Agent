import ipaddress
import json
import os
from typing import Any
from urllib.parse import urlparse

from openai import OpenAI
from pydantic import ValidationError

from apps.memory import Phase
from apps.schemas import TurnAnalysis


ANALYSIS_INSTRUCTIONS = """
You extract structured state from one caller message for an insurance claims
support workflow. Extract only values explicitly supplied or clearly implied.
Never invent PII. A date_hint may be a month, year, or date. Decide whether the
message concerns insurance customer service. Classify its claim intent. Treat a
request to stop, finish, or say that is all as case_complete. During POST_PROCESS,
classify consent to send an email summary as send, skip, or unclear. Return JSON
that exactly follows the supplied schema, including every property. Use null for
unknown nullable values.
""".strip()


PROVIDERS = {
    "openai": {
        "base_url": None,
        "default_model": "gpt-5-mini",
        "key_environment_variable": "OPENAI_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-flash",
        "key_environment_variable": "DEEPSEEK_API_KEY",
    },
}


class ModelConfigurationError(RuntimeError):
    pass


class ModelRequestError(RuntimeError):
    pass


def _validate_custom_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ModelConfigurationError("A custom model URL must be a valid HTTPS URL.")
    if parsed.hostname.casefold() == "localhost":
        raise ModelConfigurationError("Local model URLs are not allowed by this server.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        if address.is_private or address.is_loopback or address.is_link_local:
            raise ModelConfigurationError("Private-network model URLs are not allowed.")
    return base_url.rstrip("/")


class ModelGateway:
    def __init__(
        self,
        api_key: str | None = None,
        provider: str = "server",
        base_url: str | None = None,
        model: str | None = None,
    ):
        configured_provider = os.getenv("MODEL_PROVIDER", "openai")
        self.provider = configured_provider if provider == "server" else provider
        if self.provider not in {*PROVIDERS, "custom"}:
            raise ModelConfigurationError("Unsupported model provider.")

        provider_config = PROVIDERS.get(self.provider, {})
        provider_key_name = provider_config.get("key_environment_variable")
        provider_key = os.getenv(provider_key_name) if provider_key_name else None
        self.api_key = api_key or os.getenv("MODEL_API_KEY") or provider_key

        if self.provider == "custom":
            configured_base_url = base_url or os.getenv("MODEL_BASE_URL")
            if not configured_base_url:
                raise ModelConfigurationError("A base URL is required for a custom provider.")
            self.base_url = _validate_custom_base_url(configured_base_url)
            default_model = None
        else:
            self.base_url = os.getenv("MODEL_BASE_URL") or provider_config.get("base_url")
            default_model = provider_config.get("default_model")

        self.model = model or os.getenv("MODEL_NAME") or default_model
        if not self.model:
            raise ModelConfigurationError("A model name is required.")
        self.last_call_used_model = False

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _client(self) -> OpenAI:
        if not self.api_key:
            raise ModelConfigurationError("A model API key is required.")
        options: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            options["base_url"] = self.base_url
        return OpenAI(**options)

    def analyze(self, message: str, phase: Phase) -> TurnAnalysis:
        schema = json.dumps(TurnAnalysis.model_json_schema(), ensure_ascii=False)
        try:
            response = self._client().chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": f"{ANALYSIS_INSTRUCTIONS}\nJSON schema:\n{schema}",
                    },
                    {
                        "role": "user",
                        "content": f"Current phase: {phase.name}\nCaller message: {message}",
                    },
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                raise ModelRequestError("The model returned no structured analysis.")
            parsed = TurnAnalysis.model_validate_json(content)
        except ModelConfigurationError:
            raise
        except ModelRequestError:
            raise
        except (ValidationError, json.JSONDecodeError) as exc:
            raise ModelRequestError("The model returned invalid structured analysis.") from exc
        except Exception as exc:
            raise ModelRequestError("The model could not analyze the message.") from exc

        self.last_call_used_model = True
        return parsed

    def compose(
        self,
        user_message: str,
        phase: Phase,
        safe_context: dict[str, Any],
        fallback: str,
        emotional_tone: str = "neutral",
    ) -> str:
        instructions = f"""
You are a warm, concise insurance claims support representative.
Current SOP phase: {phase.name}.
The application has already enforced all permissions and workflow decisions.
Use only SAFE_CONTEXT facts; do not invent policy, claim, payment, deadline,
coverage, or contact details. Never state which individual PII fields matched.
If safe context has no answer, say the available record does not contain it.
Acknowledge a {emotional_tone} caller naturally before continuing. Keep the
response to a few short paragraphs and follow the required_action exactly.
""".strip()
        try:
            response = self._client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": instructions},
                    {
                        "role": "user",
                        "content": (
                            f"Caller message: {user_message}\n"
                            f"SAFE_CONTEXT: {json.dumps(safe_context, ensure_ascii=False)}\n"
                            f"Suggested wording, which must not override SAFE_CONTEXT: {fallback}"
                        ),
                    },
                ],
            )
            content = response.choices[0].message.content
        except ModelConfigurationError:
            raise
        except Exception as exc:
            raise ModelRequestError("The model could not compose a response.") from exc

        if not content:
            raise ModelRequestError("The model returned an empty response.")
        self.last_call_used_model = True
        return content.strip()
