# app/core/errors.py
from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import HTMLResponse, JSONResponse, Response

logger = logging.getLogger("errors")


# ---------------------------
# Helpers: content negotiation
# ---------------------------
def _wants_html(request: Request) -> bool:
    """
    Decide if the client prefers HTML:
    - query param format=html
    - or Accept header contains text/html
    """
    fmt = (request.query_params.get("format") or "").lower().strip()
    if fmt == "html":
        return True
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept


def _reason_phrase(code: int) -> str:
    try:
        return HTTPStatus(code).phrase
    except Exception:
        # Fallback
        return {404: "Not Found", 405: "Method Not Allowed"}.get(code, "Error")


def _build_html_page(title: str, body_fragment: str) -> str:
    # Full valid HTML page to satisfy tests expecting "<html" in body
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <title>{title}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
  </head>
  <body>
    {body_fragment}
  </body>
</html>"""


def _html_error(code: int, request: Request, message: str, details: Any | None = None) -> HTMLResponse:
    title = f"{code} – {_reason_phrase(code)}"
    path = request.url.path
    method = request.method.upper()
    frag = [
        f"<h1>{title}</h1>",
        f"<p><strong>Path:</strong> {path}</p>",
        f"<p><strong>Method:</strong> {method}</p>",
    ]
    if details is not None:
        # Pretty-print details as repr to keep dependencies minimal
        frag.append(f"<pre>{repr(details)}</pre>")
    html = _build_html_page(title, "\n".join(frag))
    return HTMLResponse(html, status_code=code)


def _json_error(code: int, request: Request, message: str, details: Any | None = None) -> JSONResponse:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "path": request.url.path,
        "method": request.method.upper(),
    }
    if details is not None:
        body["details"] = details
    return JSONResponse(body, status_code=code)


def _error_response(code: int, request: Request, message: str, details: Any | None = None) -> Response:
    if _wants_html(request):
        return _html_error(code, request, message, details)
    return _json_error(code, request, message, details)


# ---------------------------
# Exception handlers
# ---------------------------
async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> Response:
    code = exc.status_code
    # Starlette/FastAPI place text in .detail (can be dict/str)
    detail_text = exc.detail if isinstance(exc.detail, str) else _reason_phrase(code)
    logger.info("%s %s: %s", code, request.url.path, detail_text)
    return _error_response(code, request, str(detail_text))


async def handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
    code = status.HTTP_422_UNPROCESSABLE_ENTITY
    # Pydantic v2 provides .errors() as a list of dicts
    details = exc.errors()
    message = "Validation error"
    logger.info("%s %s: %s", code, request.url.path, message)
    return _error_response(code, request, message, details)


async def handle_unhandled_exception(request: Request, exc: Exception) -> Response:
    code = status.HTTP_500_INTERNAL_SERVER_ERROR
    # لا نكشف تفاصيل الاستثناء للعميل
    message = "Internal Server Error"
    logger.exception("Unhandled error @ %s %s", request.method, request.url.path)
    return _error_response(code, request, message)


# ---------------------------
# Registration hook
# ---------------------------
def register_exception_handlers(app) -> None:
    """
    Call from main.py to install consistent JSON/HTML error responses.
    """
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unhandled_exception)
