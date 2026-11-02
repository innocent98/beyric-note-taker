"""Application factory. Run with `uvicorn app.main:create_app --factory`."""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import auth, web
from app.config import Settings, get_settings

STATIC = Path(__file__).parent / "static"
CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    auth.check_start(settings)
    app = FastAPI(
        title="Beyric Note Taker",
        dependencies=[Depends(auth.guard)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.limiter = auth.LoginLimiter()

    @app.middleware("http")
    async def session_and_headers(request: Request, call_next):
        loaded = auth.read_session(settings.session_secret, request.cookies.get(auth.COOKIE_NAME))
        request.state.session = loaded or auth.new_session()
        request.state.session_changed = loaded is None
        response = await call_next(request)
        if request.url.path != "/health" and request.state.session_changed:
            response.set_cookie(
                auth.COOKIE_NAME,
                auth.sign_session(settings.session_secret, request.state.session),
                max_age=auth.SESSION_SECONDS,
                httponly=True,
                samesite="lax",
                secure=settings.cookie_secure,
                path="/",
            )
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(auth.NotLoggedIn)
    async def _not_logged_in(request: Request, exc: auth.NotLoggedIn) -> Response:
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(auth.CsrfRejected)
    async def _csrf(request: Request, exc: auth.CsrfRejected) -> Response:
        page = "<!doctype html><title>Refused</title><p>This form has expired. Reload it.</p>"
        return HTMLResponse(page, status_code=403)

    @app.get("/health", include_in_schema=False)
    def health() -> Response:
        return PlainTextResponse("ok")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.include_router(web.router)
    return app
