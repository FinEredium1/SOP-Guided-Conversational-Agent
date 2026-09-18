from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.agent import SOPAgent
from apps.memory import ConversationSession
from apps.schemas import ChatRequest, ChatResponse, SessionResponse


STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="SOP-Guided Insurance Claims Agent")
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

agent = SOPAgent()
sessions: dict[str, ConversationSession] = {}
session_locks: dict[str, Lock] = {}
sessions_lock = Lock()


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/sessions", response_model=SessionResponse)
def create_session() -> SessionResponse:
    session_id = str(uuid4())
    session = ConversationSession()
    greeting = (
        "Hi, I’m the insurance claims assistant. Before I can access claim details, "
        "I’ll need to verify at least three identity details. You can share your full "
        "name, date of birth, phone, email, or the last four digits of your ID."
    )
    session.add_message("assistant", greeting)
    with sessions_lock:
        sessions[session_id] = session
        session_locks[session_id] = Lock()
    return SessionResponse(
        session_id=session_id,
        message=greeting,
        state=session.public_state(),
    )


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    if len(message) > 4000:
        raise HTTPException(status_code=422, detail="Message is too long.")

    with sessions_lock:
        session = sessions.get(request.session_id)
        session_lock = session_locks.get(request.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")

    assert session_lock is not None
    with session_lock:
        result = agent.handle(
            session,
            message,
            api_key=request.api_key,
            provider=request.provider,
            base_url=request.base_url,
            model=request.model,
        )
        state = session.public_state()

    return ChatResponse(
        message=result.message,
        state=state,
        model_used=result.model_used,
        needs_api_key=result.needs_api_key,
    )


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict[str, bool]:
    with sessions_lock:
        removed = sessions.pop(session_id, None) is not None
        session_locks.pop(session_id, None)
    return {"deleted": removed}
