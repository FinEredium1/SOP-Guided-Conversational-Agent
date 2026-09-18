# SOP-Guided Insurance Claims Agent

A conversational insurance claims demo that combines a strict, application-controlled standard operating procedure with flexible language-model interpretation and response phrasing.

The workflow always follows:

`VERIFY_ID → RESOLVE_INTENT → PROCESS_CASE → POST_PROCESS`

The application decides what the agent is allowed to do. The model interprets natural language and writes responses within the facts and actions supplied by the application.

## Design

| Phase | Deterministic application controls | Model responsibilities |
| --- | --- | --- |
| `VERIFY_ID` | Blocks claim access, validates identity fields, requires three matching PII fields, limits retries, and controls escalation | Extracts identity fields, detects emotion or refusal, and interprets conversational language |
| `RESOLVE_INTENT` | Restricts candidates to claims owned by the verified party and selects only a uniquely supported match | Interprets messy intent, claim type, status, date, and case hints |
| `PROCESS_CASE` | Supplies only grounded claim and guidance data; prevents unverified record access | Answers follow-up questions naturally using the supplied facts |
| `POST_PROCESS` | Requires an explicit send-or-skip choice and records the demo outcome | Interprets the caller's email-summary choice |

The model also classifies whether a request is relevant to insurance customer service. Enforcement remains deterministic: out-of-scope requests receive a fixed refusal, and repeated attempts trigger a recorded human escalation.

## Important behavior

- Claim records cannot be accessed before successful identity verification.
- Verification requires at least three matching fields from full name, date of birth, phone number, email address, and ID/SSN last four.
- Repeating the same field cannot increase the verification count.
- Intent and case hints are remembered even when supplied during verification.
- Claim responses are grounded in the included fixture data and document guidance.
- Frustration, anxiety, anger, confusion, and verification refusal can be acknowledged without bypassing required gates.
- Callers can request a human representative.
- The email step is a demo: it records the generated summary as sent or skipped but does not send a real email.
- Public API state excludes raw PII and match results.

## Sample test case

Use the **Use Margaret test case** button or send:

> I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.

The agent should verify the three supplied identity fields, remember the denied-healthcare-January hint, resolve claim `CL-2048`, and answer only from its grounded record.

## Run locally

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
```

Activate the environment, then install dependencies:

```bash
pip install -r requirements.txt
```

Start the application:

```bash
uvicorn apps.main:app --reload
```

Open <http://127.0.0.1:8000>. In **Model settings**, choose DeepSeek, OpenAI, or another OpenAI-compatible HTTPS API, enter the API key, and apply the settings. Keys entered in the browser are sent only with chat requests and are not stored by the demo.

DeepSeek defaults to `deepseek-flash`; OpenAI defaults to `gpt-5-mini`. A custom provider requires both an HTTPS base URL and a model name. Local and private-network custom endpoints are rejected by the server.

## Test

```bash
python -m unittest discover -s tests -v
```

The tests cover verification boundaries, duplicate identity fields, cross-phase memory, sample-case resolution, out-of-scope escalation, post-processing consent, API behavior, provider configuration, and PII-safe public state.

## Deploy on Render

Create a **Web Service** from this repository and use:

| Setting | Value |
| --- | --- |
| Branch | `main` |
| Runtime | Python 3 |
| Root Directory | Leave blank |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn apps.main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/api/health` |

No environment variable is required when each user supplies a model key in the interface. Avoid configuring a personal model key on a public deployment unless you intend visitors to consume that account's quota.

## Project structure

```text
apps/
  agent.py          SOP orchestration and bounded workflow decisions
  claims.py         Verified claim access, resolution, and grounded context
  llm.py            Provider-neutral model gateway
  memory.py         Conversation phase and cross-phase memory
  tools.py          Identity input validation and submission
  verification.py   Deterministic identity matching
  static/           Browser interface
  insurance_claims/ Demo records and guidance fixtures
tests/
  test_workflow.py  Workflow, safety, API, and provider tests
```

## Demo limitations

- Conversations are stored in process memory and reset when the server restarts.
- The included records are synthetic local fixtures.
- The app does not make real claim changes, transfer calls, or send email.
- This is an assessment/demo harness, not a production claims system.
