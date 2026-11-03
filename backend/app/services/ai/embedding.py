import time
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.errors import AppError
from app.services.ai.ai_http_errors import check_ai_http_response
from app.services.ai.call_log import format_error, record_ai_call, token_fields
from app.services.ai.effective_ai_settings import get_embedding_runtime


def embed_texts(
    texts: list[str],
    db: Session,
    *,
    scene: str | None = None,
) -> list[list[float]]:
    if not texts:
        return []
    cfg = get_embedding_runtime(db)
    request_text = "\n".join(texts)
    started = time.perf_counter()
    status_code: int | None = None
    vectors: list[list[float]] = []
    usage: Any = None
    error: BaseException | None = None
    try:
        if not cfg.api_key.strip():
            raise AppError(
                "bad_request",
                "未配置 Embedding API Key，请在系统 AI 配置或 .env 中设置",
                status_code=400,
            )
        url = f"{cfg.api_base.rstrip('/')}/embeddings"
        payload: dict[str, Any] = {
            "model": cfg.model,
            "input": texts,
        }
        try:
            with httpx.Client(timeout=120.0) as client:
                r = client.post(
                    url,
                    headers={"Authorization": f"Bearer {cfg.api_key}"},
                    json=payload,
                )
                status_code = r.status_code
                check_ai_http_response(r, "Embedding")
                data = r.json()
            usage = data.get("usage")
            rows = sorted(data["data"], key=lambda x: x["index"])
            vectors = [row["embedding"] for row in rows]
        except AppError:
            raise
        except httpx.TimeoutException as ex:
            raise AppError("bad_gateway", "Embedding 请求超时，请检查网络或稍后重试", status_code=502) from ex
        except httpx.RequestError as ex:
            raise AppError("bad_gateway", f"Embedding 无法连接：{ex}", status_code=502) from ex
        except (KeyError, TypeError, ValueError) as ex:
            raise AppError("bad_gateway", "Embedding 返回格式异常，请检查模型与 API Base", status_code=502) from ex
        if vectors and len(vectors[0]) != int(cfg.dim):
            raise AppError(
                "bad_request",
                (
                    f"Embedding 返回维度 {len(vectors[0])} 与配置 {cfg.dim} 不一致，"
                    "请在系统 AI 配置中修正向量维度后重建索引"
                ),
                status_code=400,
            )
        return vectors
    except Exception as ex:
        error = ex
        raise
    finally:
        dim = len(vectors[0]) if vectors and vectors[0] else None
        summary = f"条数={len(vectors)} 维度={dim if dim is not None else cfg.dim}"
        prompt_tokens, completion_tokens, total_tokens = token_fields(usage)
        record_ai_call(
            kind="embedding",
            scene=scene,
            model=cfg.model,
            status="error" if error is not None else "success",
            http_status=status_code,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            error_message=format_error(error) if error is not None else None,
            request_text=request_text,
            response_text=summary if vectors or error is None else None,
        )


def embed_query(text: str, db: Session, *, scene: str | None = None) -> list[float]:
    vecs = embed_texts([text], db, scene=scene)
    return vecs[0]
