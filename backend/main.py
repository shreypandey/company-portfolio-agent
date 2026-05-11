"""FastAPI server.

Routes:
  GET  /            → static frontend (index.html)
  GET  /research?company=...  → SSE stream of agent events
  GET  /outputs/{run_id}/{filename}  → download generated portfolio.md or .xlsx
  GET  /health      → liveness probe
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from .agent import Agent
from .config import HEARTBEAT_SECONDS, OUTPUTS_DIR
from .sources import ticker_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("agent.server")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Portfolio Research Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/symbols/search")
async def symbols_search(
    q: str = Query(..., min_length=1, max_length=80),
    limit: int = Query(10, ge=1, le=25),
) -> dict:
    """Typeahead-style search over the SEC EDGAR public company index.

    Returns up to `limit` ranked matches: exact ticker → ticker-prefix → name-prefix → substring.
    Powers the search box in the UI. ~10K records, fully in-memory after first load.
    """
    results = await ticker_index.search(q, limit=limit)
    return {"query": q, "results": results, "count": len(results)}


@app.get("/research")
async def research(company: str = Query(..., min_length=1, max_length=200)):
    """Start a research run and stream events as SSE."""
    run_id = uuid.uuid4().hex[:12]
    queue: asyncio.Queue = asyncio.Queue()

    async def sink(event_type: str, data: dict) -> None:
        await queue.put({"event": event_type, "data": json.dumps(data, default=str)})

    async def runner() -> None:
        try:
            agent = Agent(company=company, run_id=run_id, sink=sink)
            await agent.run()
        except Exception as e:  # last-resort guard
            log.exception("agent crashed at top level")
            try:
                await sink("error", {"fatal": True, "message": f"{type(e).__name__}: {e}"})
            except Exception:
                pass
        finally:
            await queue.put(None)  # sentinel

    async def event_gen():
        task = asyncio.create_task(runner())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # sse-starlette injects :ping comments every HEARTBEAT_SECONDS to keep proxies alive
    return EventSourceResponse(event_gen(), ping=HEARTBEAT_SECONDS)


@app.get("/outputs/{run_id}/{filename}")
async def get_output(run_id: str, filename: str):
    """Serve a generated artifact with directory-traversal protection."""
    base = OUTPUTS_DIR.resolve()
    target = (base / run_id / filename).resolve()
    if not str(target).startswith(str(base) + "/"):
        raise HTTPException(status_code=403, detail="path escape")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target, filename=filename)


# Mount the static frontend LAST so it doesn't shadow the API routes above.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
