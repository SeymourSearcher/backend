from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import get_settings
from app.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
)
from app.routers import auth, users, seymours, stats, hex, marketplace, visualization
from app.routers import auctions as auctions_router
from app.services.player import close_client

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_client()


# Auction sync runs in a separate process; see app/worker.py.
app = FastAPI(title="Seymour Searcher API", lifespan=lifespan)

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


@app.get("/health")
async def health():
    return {"status": "ok"}

