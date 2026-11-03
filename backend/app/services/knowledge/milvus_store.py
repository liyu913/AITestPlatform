from __future__ import annotations

from typing import Any

from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)

from app.config import settings
from app.errors import AppError

MILVUS_TEXT_PREVIEW_MAX_BYTES = 1024


def truncate_text_preview(text: str, max_bytes: int = MILVUS_TEXT_PREVIEW_MAX_BYTES) -> str:
    """Milvus VARCHAR max_length counts UTF-8 bytes, not Unicode code points."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def _alias() -> str:
    return "default"


def connect() -> None:
    try:
        connections.connect(
            alias=_alias(),
            host=settings.milvus_host,
            port=str(settings.milvus_port),
            timeout=8,
        )
    except AppError:
        raise
    except Exception as ex:
        raise AppError(
            "bad_gateway",
            (
                f"无法连接 Milvus（{settings.milvus_host}:{settings.milvus_port}）。"
                "请在项目根目录执行 docker compose up -d milvus 后重建索引"
            ),
            status_code=502,
        ) from ex


def collection_embedding_dim(coll: Collection) -> int | None:
    for field in coll.schema.fields:
        if getattr(field, "name", None) != "embedding":
            continue
        dim = getattr(field, "dim", None)
        if dim:
            return int(dim)
        params = getattr(field, "params", None) or {}
        if params.get("dim") is not None:
            return int(params["dim"])
    return None


def ensure_collection(dim: int) -> Collection:
    connect()
    name = settings.milvus_collection
    if utility.has_collection(name, using=_alias()):
        coll = Collection(name, using=_alias())
        existing = collection_embedding_dim(coll)
        if existing is not None and int(existing) != int(dim):
            raise AppError(
                "bad_request",
                (
                    f"Milvus collection「{name}」向量维度为 {existing}，"
                    f"与当前 Embedding 配置 {dim} 不一致。"
                    "请将系统 Embedding 维度改回一致，或删除该 collection 后重建索引"
                ),
                status_code=400,
            )
        coll.load()
        return coll

    fields = [
        FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
        FieldSchema(name="project_id", dtype=DataType.INT64),
        FieldSchema(name="source_type", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="source_id", dtype=DataType.INT64),
        FieldSchema(name="text_preview", dtype=DataType.VARCHAR, max_length=1024),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
    ]
    schema = CollectionSchema(fields, description="raina RAG chunks")
    coll = Collection(name, schema, using=_alias())
    coll.create_index(
        field_name="embedding",
        index_params={"index_type": "AUTOINDEX", "metric_type": "L2"},
    )
    coll.load()
    return coll


def open_collection() -> Collection | None:
    connect()
    name = settings.milvus_collection
    if not utility.has_collection(name, using=_alias()):
        return None
    coll = Collection(name, using=_alias())
    coll.load()
    return coll


def delete_by_chunk_ids(coll: Collection, chunk_ids: list[str]) -> None:
    if not chunk_ids:
        return
    coll.load()
    quoted = ",".join(f'"{cid}"' for cid in chunk_ids)
    coll.delete(expr=f"chunk_id in [{quoted}]")


def delete_by_project_id(coll: Collection, project_id: int) -> None:
    coll.load()
    coll.delete(expr=f"project_id == {int(project_id)}")


def upsert_vectors(
    coll: Collection,
    *,
    chunk_ids: list[str],
    project_ids: list[int],
    source_types: list[str],
    source_ids: list[int],
    text_previews: list[str],
    embeddings: list[list[float]],
) -> None:
    coll.load()
    previews = [truncate_text_preview(p) for p in text_previews]
    data: list[Any] = [chunk_ids, project_ids, source_types, source_ids, previews, embeddings]
    coll.insert(data)
    coll.flush()


def search_vectors(
    coll: Collection,
    *,
    query_vector: list[float],
    project_id: int,
    top_k: int,
    source_type: str | None = None,
    source_id: int | None = None,
    upload_source_ids: list[int] | None = None,
) -> list[tuple[str, float]]:
    coll.load()
    expr_parts = [f"project_id == {project_id}"]
    if upload_source_ids is not None:
        if not upload_source_ids:
            return []
        expr_parts.append('source_type == "upload"')
        inner = ", ".join(str(int(x)) for x in upload_source_ids)
        expr_parts.append(f"source_id in [{inner}]")
    else:
        if source_type is not None:
            expr_parts.append(f'source_type == "{source_type}"')
        if source_id is not None:
            expr_parts.append(f"source_id == {source_id}")
    expr = " && ".join(expr_parts)
    res = coll.search(
        data=[query_vector],
        anns_field="embedding",
        param={"metric_type": "L2"},
        limit=top_k,
        expr=expr,
        output_fields=["chunk_id"],
    )
    hits: list[tuple[str, float]] = []
    for hit in res[0]:
        cid = hit.entity.get("chunk_id")
        hits.append((str(cid), float(hit.distance)))
    return hits
