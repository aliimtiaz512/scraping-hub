import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import run_manager
from app.db import init_db
from app.scrapers.bidnet.router import router as bidnet_router
from app.scrapers.myflorida.router import router as myflorida_router
from app.scrapers.northdakota.router import router as northdakota_router
from app.scrapers.ridemetro.router import router as ridemetro_router
from app.scrapers.wisconsin.router import router as wisconsin_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="MFMP Bid Scraper", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    try:
        init_db()
        logger.info("Database tables ready")
    except Exception:  # noqa: BLE001 — the API still serves /categories without a DB
        logger.exception("Could not initialize database — check DATABASE_URL in .env")
    # Recover runs that were in flight when the process last stopped, so the
    # frontend polling them sees a terminal status instead of a 404.
    run_manager.load_persisted_runs()

app.add_middleware(
    CORSMiddleware,
    # localhost and 127.0.0.1 are distinct origins to the browser, so allow both.
    # Any port is allowed because Next's dev server auto-increments when 3000 is taken.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(myflorida_router)
app.include_router(ridemetro_router)
app.include_router(bidnet_router)
app.include_router(wisconsin_router)
app.include_router(northdakota_router)


@app.get("/")
def health() -> dict:
    return {
        "status": "ok",
        "service": "scraping-hub",
        "scrapers": ["myflorida", "ridemetro", "bidnet", "wisconsin", "northdakota"],
    }
