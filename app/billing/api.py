"""The webhook route: `POST /api/billing/webhooks/{provider}` (billing spec section 5.4).

The one provider-shaped surface. It answers 404 while `BILLING_ENABLED` is false (and for a provider
nobody registered), before reading the body, so a probe learns nothing and nothing is written. It
needs no login and no CSRF token: the provider's signature is the credential, checked on the raw
bytes (`app/auth.py` lets this path past its login and CSRF check for that reason).

| Answer | When |
|---|---|
| 404 | billing is off, or no such provider |
| 413 | the body is over `MAX_BODY_BYTES` |
| 401 | the signature or secret is wrong or missing; nothing is stored |
| 400 | genuine request, unreadable event; nothing is stored |
| 503 | stored, not decided yet (provider down, still pending, a step failed): provider resends |
| 200 | stored and decided (applied or refused), or already seen |
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.billing import inbox, settle
from app.billing.provider import PaymentProvider, provider_named
from app.billing.types import BadEvent, RetryLater, WebhookRejected
from app.db import get_session

logger = logging.getLogger(__name__)

# A webhook is a small JSON object; anything bigger is not one. Read in chunks and cut off.
MAX_BODY_BYTES = 256 * 1024

router = APIRouter()


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(413, "The request body is too large.")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(413, "The request body is too large.")
        chunks.append(chunk)
    return b"".join(chunks)


def _handle(db: Session, provider: PaymentProvider, headers, raw_body: bytes) -> None:
    now = datetime.now(UTC)
    row, event = inbox.receive(db, provider, headers, raw_body, now)
    if row.processed_at is None:
        settle.process(db, provider, row, event, now)


@router.post("/api/billing/webhooks/{provider}")
async def webhook(
    provider: str, request: Request, db: Session = Depends(get_session)
) -> JSONResponse:
    settings = request.app.state.settings
    adapter = provider_named(provider, settings) if settings.billing_enabled else None
    if adapter is None:
        raise HTTPException(404, "Not found.")
    raw_body = await _read_body(request)
    try:
        await run_in_threadpool(_handle, db, adapter, request.headers, raw_body)
    except WebhookRejected:
        logger.warning("billing webhook for %s refused: bad signature", provider)
        raise HTTPException(401, "The signature is not valid.") from None
    except BadEvent:
        raise HTTPException(400, "The event could not be read.") from None
    except RetryLater:
        raise HTTPException(503, "Try again later.") from None
    return JSONResponse({"status": "ok"})
