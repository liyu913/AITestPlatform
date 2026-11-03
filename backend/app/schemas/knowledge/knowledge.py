from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ChunkMode = Literal["window", "heading"]


def _check_chunk_pair(window: int | None, overlap: int | None) -> None:
    if window is None or overlap is None:
        return
    if overlap >= window:
        raise ValueError("分块重叠必须小于分块大小")


class KnowledgeDocOut(BaseModel):
    id: int
    library_id: int
    filename: str
    storage_path: str
    mime: str | None
    size_bytes: int
    category: str | None = None
    parse_status: str
    error_message: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeDocPatch(BaseModel):
    category: str | None = Field(default=None, max_length=128)


class KnowledgeLibraryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    chunk_mode: ChunkMode = "window"
    chunk_window: int = Field(default=400, ge=100, le=8000)
    chunk_overlap: int = Field(default=80, ge=0, le=4000)

    @model_validator(mode="after")
    def _chunk_pair(self) -> "KnowledgeLibraryCreate":
        _check_chunk_pair(self.chunk_window, self.chunk_overlap)
        return self


class KnowledgeLibraryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    chunk_mode: ChunkMode | None = None
    chunk_window: int | None = Field(default=None, ge=100, le=8000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=4000)

    @model_validator(mode="after")
    def _chunk_pair(self) -> "KnowledgeLibraryPatch":
        _check_chunk_pair(self.chunk_window, self.chunk_overlap)
        return self


class KnowledgeLibraryOut(BaseModel):
    id: int
    project_id: int
    name: str
    description: str | None
    document_count: int = 0
    ready_count: int = 0
    failed_count: int = 0
    parsing_count: int = 0
    chunk_mode: str = "window"
    chunk_window: int = 400
    chunk_overlap: int = 80
    reindex_required: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeIndexStatusOut(BaseModel):
    embedding_ready: bool


class IndexChunkOut(BaseModel):
    id: int
    document_id: int
    document_filename: str
    library_id: int | None
    library_name: str | None
    chunk_index: int
    text: str
    index_status: str
    embedding_model: str
    created_at: datetime

