"""用户端 API:/api/chat(SSE)。"""
import logging

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from ..core.config import DEMO_USERS
from ..engine.orchestrator import Orchestrator
from ..engine.session import new_session_id
from ..schemas import ChatRequest

logger = logging.getLogger("biz.api.chat")
router = APIRouter()

_orch: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orch
    if _orch is None:
        _orch = Orchestrator()
    return _orch


def resolve_demo_user(x_demo_user: str | None) -> str:
    if x_demo_user and x_demo_user in DEMO_USERS:
        return x_demo_user
    return "ceo"


@router.post("/chat")
async def chat(req: ChatRequest, x_demo_user: str | None = Header(default=None)):
    demo_user = resolve_demo_user(x_demo_user)
    session_id = (req.session_id or "").strip() or new_session_id()
    question = req.question.strip()
    if not question:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="问题不能为空")

    orch = get_orchestrator()
    gen = orch.stream_sse(question, demo_user, session_id, req.assume)
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
