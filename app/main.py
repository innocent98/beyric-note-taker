"""Application factory. Run with `uvicorn app.main:create_app --factory`."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import api, auth, edge, limits, mail, ratelimit, skribby, web
from app.config import Settings, get_settings
from app.db import get_rate_session, get_session

STATIC = Path(__file__).parent / "static"
CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; font-src 'self'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)


@contextmanager
def _own_session(app: FastAPI) -> Iterator[Session]:
    """A session of its own for the rate limiter (it commits at once, apart from the request's
    transaction). In the app it comes from the limiter's own small pool (`get_rate_session`); when
    `get_session` is overridden (the tests') the override is used, so tests and the app share one
    database."""
    generator = (app.dependency_overrides.get(get_session) or get_rate_session)()
    try:
        yield next(generator)
    finally:
        generator.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    auth.check_start(settings)
    skribby.check_region_unchanged(settings)
    mail.check_settings(settings)
    mail.warn_if_unlimited(settings)
    edge.warn_if_unsafe(settings)
    app = FastAPI(
        title="Beyric Note Taker",
        dependencies=[Depends(auth.guard)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    key = app.state.session_key = auth.signing_key(settings)
    # The login and sign-up counters live in the database (`rate_hits`), so a restart does not
    # reset a lockout and a second process shares it. The session is looked up when it is used, so
    # a dependency override (the tests') is honoured exactly as it is for a request.
    rate_store = ratelimit.DbStore(lambda: _own_session(app), ratelimit.store_secret(settings))
    app.state.rate_store = rate_store
    app.state.limiter = auth.LoginLimiter(store=rate_store)
    app.state.public_limits = limits.PublicLimits(rate_store)
    app.state.device_key = auth.device_key(settings)

    @app.middleware("http")
    async def session_and_headers(request: Request, call_next):
        loaded = auth.read_session(key, request.cookies.get(auth.COOKIE_NAME))
        request.state.session = loaded or auth.new_session()
        request.state.session_changed = loaded is None
        request.state.user = None  # `auth.guard` sets the real one
        if auth.request_path(request) != "/health":
            edge.note_missing_secret(request, settings)
        response = await call_next(request)
        if auth.request_path(request) != "/health" and request.state.session_changed:
            response.set_cookie(
                auth.COOKIE_NAME,
                auth.sign_session(key, request.state.session),
                max_age=auth.SESSION_SECONDS,
                httponly=True,
                samesite="lax",
                secure=settings.cookie_secure,
                path="/",
            )
        device_cookie = getattr(request.state, "device_cookie", None)
        if device_cookie is not None:
            # Marks this browser as the owner's, so a lockout caused by someone else's failed
            # guesses cannot keep it out. It grants no access by itself: the password is still
            # checked, and the address limit still applies.
            account, known_id = device_cookie
            response.set_cookie(
                auth.device_cookie_name(settings),
                auth.sign_device(app.state.device_key, account, device_id=known_id),
                max_age=auth.DEVICE_SECONDS,
                httponly=True,
                samesite="lax",
                secure=settings.cookie_secure,
                path="/",
            )
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not auth.request_path(request).startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(auth.NotLoggedIn)
    async def _not_logged_in(request: Request, exc: auth.NotLoggedIn) -> Response:
        if api.is_api_path(request):  # a JSON client must never be sent to the login page
            return api.error_response(401, "not_authenticated", "Log in first.")
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(auth.CsrfRejected)
    async def _csrf(request: Request, exc: auth.CsrfRejected) -> Response:
        if api.is_api_path(request):
            message = f"Missing or wrong {auth.CSRF_HEADER} header. Read it from GET /api/session."
            return api.error_response(403, "csrf_rejected", message)
        page = "<!doctype html><title>Refused</title><p>This form has expired. Reload it.</p>"
        return HTMLResponse(page, status_code=403)

    @app.get("/health", include_in_schema=False)
    def health(request: Request, db: Session = Depends(get_session)) -> Response:
        """Unauthenticated liveness check that also needs the database. No detail in the body.

        `X-Edge-Ok` says whether THIS request carried the right edge secret, so
        `curl -sI https://<site>/health` shows that nginx, Next and the app agree on it. It
        never holds the secret, and is false whenever no secret is configured."""
        edge_ok = "true" if edge.has_edge_secret(request, settings) else "false"
        try:
            db.execute(text("select 1"))
        except SQLAlchemyError:
            return PlainTextResponse("unavailable", status_code=503, headers={"X-Edge-Ok": edge_ok})
        return PlainTextResponse("ok", headers={"X-Edge-Ok": edge_ok})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    api.install_errors(app)
    app.include_router(api.router)
    app.include_router(web.router)
    return app
