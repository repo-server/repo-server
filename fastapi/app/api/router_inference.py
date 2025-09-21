# app/api/router_inference.py
from __future__ import annotations

import inspect
from typing import Any, Annotated, Optional, Callable, Coroutine

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

# استخدم اللودر الحقيقي في مشروعك
from app.plugins.loader import get_plugin_instance

router = APIRouter(prefix="/inference", tags=["inference"])


# ==========================
# Schemas
# ==========================
class InferenceRequest(BaseModel):
    plugin: str
    task: str
    payload: Optional[dict[str, Any]] = None


class InferenceResponse(BaseModel):
    ok: bool
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


# ==========================
# Helpers
# ==========================
def _build_kwargs_from_signature(fn: Callable[..., Any], payload: dict[str, Any]) -> dict[str, Any]:
    """
    يبني kwargs اعتمادًا على توقيع الدالة: يأخذ فقط المفاتيح الموجودة في payload
    والتي تطابق أسماء معاملات الدالة (يتجاهل self, *args, **kwargs).
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {}

    allowed: list[str] = []
    for p in sig.parameters.values():
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if p.name == "self":
            continue
        allowed.append(p.name)

    kwargs = {k: v for k, v in payload.items() if k in allowed}
    return kwargs


def _call_sync_with_strategies(fn: Callable[..., Any], payload: dict[str, Any]) -> Any:
    last_err: Exception | None = None
    # 1) kwargs
    try:
        return fn(**payload)
    except TypeError as e:
        last_err = e
    # 2) single arg (pass the whole payload dict)
    try:
        return fn(payload)
    except TypeError as e:
        last_err = e
    # 3) no args
    try:
        return fn()
    except TypeError as e:
        last_err = e
    # 4) signature-aware kwargs (only matching names)
    try:
        kwargs = _build_kwargs_from_signature(fn, payload)
        return fn(**kwargs)
    except TypeError as e:
        last_err = e

    assert last_err is not None
    raise last_err


async def _call_async_with_strategies(fn: Callable[..., Coroutine[Any, Any, Any]], payload: dict[str, Any]) -> Any:
    last_err: Exception | None = None
    # 1) kwargs
    try:
        return await fn(**payload)
    except TypeError as e:
        last_err = e
    # 2) single arg
    try:
        return await fn(payload)
    except TypeError as e:
        last_err = e
    # 3) no args
    try:
        return await fn()
    except TypeError as e:
        last_err = e
    # 4) signature-aware kwargs
    try:
        kwargs = _build_kwargs_from_signature(fn, payload)
        return await fn(**kwargs)
    except TypeError as e:
        last_err = e

    assert last_err is not None
    raise last_err


# ==========================
# Endpoints
# ==========================
@router.post("/run", response_model=InferenceResponse)
async def run_inference(req: Annotated[InferenceRequest, Body(...)]):
    """
    Run inference by dynamically dispatching to a plugin + task.
    """
    if not req.plugin or not req.task:
        raise HTTPException(status_code=400, detail="Plugin and task are required")

    plugin = get_plugin_instance(req.plugin)
    fn = getattr(plugin, req.task, None)
    if not callable(fn):
        raise HTTPException(status_code=404, detail=f"Task '{req.task}' not found in plugin '{req.plugin}'")

    payload: dict[str, Any] = req.payload or {}

    try:
        if inspect.iscoroutinefunction(fn):
            result = await _call_async_with_strategies(fn, payload)  # type: ignore[arg-type]
        else:
            result = _call_sync_with_strategies(fn, payload)         # type: ignore[arg-type]
    except Exception as e:
        return InferenceResponse(ok=False, error=f"Task failed: {e!s}")

    # Normalize to dict
    if not isinstance(result, dict):
        result = {"result": result}

    return InferenceResponse(ok=True, result=result)


# Alias for backwards-compat
@router.post("", response_model=InferenceResponse)
async def run_inference_alias(req: Annotated[InferenceRequest, Body(...)]):
    return await run_inference(req)
