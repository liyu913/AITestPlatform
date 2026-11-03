from pydantic import BaseModel, Field


class RagSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=50)
    source_type: str | None = None
    source_id: int | None = None
    knowledge_library_id: int | None = None


class RagHitOut(BaseModel):
    chunk_id: int
    text: str
    source_type: str
    source_id: int
    title_resolved: str
    score: float
