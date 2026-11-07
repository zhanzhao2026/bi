"""请求模型(pydantic,仅用于请求体校验;响应统一 dict)。"""
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    session_id: str | None = None
    assume: str | None = None


class FeedbackRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=32)
    rating: str = Field(pattern="^(up|down)$")
    tags: list[str] | None = None
    comment: str | None = None
    session_id: str | None = None


class SettingsUpdate(BaseModel):
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
