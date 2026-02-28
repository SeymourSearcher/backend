import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.config import get_settings
from app.routers import auth, users, seymours, stats, hex, marketplace, visualization
from app.routers import auctions as auctions_router
from app.services.auction_sync import start_sync_loop

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(start_sync_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Seymour Searcher API", lifespan=lifespan)

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

