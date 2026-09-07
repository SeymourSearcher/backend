from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import get_settings
from app.dependencies import global_rate_limit
from app.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
)
from app.schemas.common import HealthResponse
from app.routers import auth, users, seymours, stats, hex, marketplace, visualization
from app.routers import auctions as auctions_router

settings = get_settings()

# Auction sync and username resolution both run in a separate process; see
# app/worker.py. Nothing here makes outbound HTTP calls.
#
# The rate limit is an app-level dependency rather than middleware so it resolves
# get_redis through the dependency system, which is where the test suite swaps in
# fakeredis. It exempts /health and /robots.txt itself.
app = FastAPI(
    title="Seymour Searcher API",
    dependencies=[Depends(global_rate_limit)],
)

DOMAIN_ERROR_STATUS = {
    NotFoundError: 404,
    ConflictError: 400,
    InvalidInputError: 400,
    PermissionDeniedError: 403,
}


@app.exception_handler(DomainError)
async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    status = next(
        (code for cls, code in DOMAIN_ERROR_STATUS.items() if isinstance(exc, cls)), 400
    )
    return JSONResponse(status_code=status, content={"detail": str(exc)})


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

# The one HTML body the API returns -- the colour cube -- is fetched as text and
# saved as a file by the client, never rendered from this origin, so nothing here
# needs to execute. Swagger is the exception: it is a real page off a CDN.
CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    if request.url.path not in CSP_EXEMPT_PATHS:
        response.headers["Content-Security-Policy"] = CSP
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(seymours.router, prefix="/seymours", tags=["seymours"])
app.include_router(stats.router, prefix="/stats", tags=["stats"])
app.include_router(hex.router, prefix="/hex", tags=["hex"])
app.include_router(marketplace.router, prefix="/marketplace", tags=["marketplace"])
app.include_router(auctions_router.router, prefix="/auctions", tags=["auctions"])
app.include_router(visualization.router, prefix="/visualization", tags=["visualization"])


ROBOTS_TXT = """User-agent: *
Disallow: /
"""


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return ROBOTS_TXT


@app.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}

