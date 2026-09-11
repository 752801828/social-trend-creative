from __future__ import annotations

import json
import os
import secrets
import sqlite3
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

from app.service import FLOW_MODELS, GEMINI_MODELS, TrendService, utc_now


ROOT = Path(__file__).resolve().parents[1]
service = TrendService()
UPDATE_REQUEST_PATH = service.data_dir / "update-request.json"
UPDATE_STATUS_PATH = service.data_dir / "update-status.json"


def read_update_status() -> dict:
    try:
        return json.loads(UPDATE_STATUS_PATH.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"status": "idle", "message": "尚未请求更新"}


def write_update_file(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await service.start()
    yield
    await service.stop()


app = FastAPI(title="Social Trend Creative", version="0.1.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)
_state_cache: dict[int, tuple[float, dict]] = {}
_state_cache_lock = asyncio.Lock()


@app.middleware("http")
async def cache_static_assets(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/assets/") and response.status_code == 200:
        response.headers.setdefault("Cache-Control", "public, max-age=86400, immutable")
    return response


app.mount("/assets", StaticFiles(directory=service.assets_dir), name="assets")


class ConfigUpdate(BaseModel):
    enabled: bool | None = None
    schedule_time: str | None = None
    generation_schedule_time: str | None = None
    acquisition_interval_minutes: int | None = Field(default=None, ge=15, le=1440)
    generation_interval_minutes: int | None = Field(default=None, ge=15, le=1440)
    timezone: str | None = None
    lookback_hours: int | None = Field(default=None, ge=1, le=168)
    regions: list[str] | None = None
    platforms: list[str] | None = None
    candidate_count: int | None = Field(default=None, ge=1, le=30)
    images_per_trend: int | None = Field(default=None, ge=1, le=30)
    gemini_discovery_model: str | None = None
    gemini_verification_model: str | None = None
    flow_models: list[str] | None = None
    generation_concurrency: int | None = Field(default=None, ge=1, le=5)
    auto_generate: bool | None = None
    notify_enabled: bool | None = None
    source_sync_enabled: bool | None = None
    source_sync_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    source_retention_days: int | None = Field(default=None, ge=1, le=365)
    source_include_hotlists: bool | None = None


class PoolRequest(BaseModel):
    count: int | None = Field(default=None, ge=1, le=30)


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/acquire")
@app.get("/sources")
@app.get("/signals")
@app.get("/trends")
@app.get("/prompts")
@app.get("/patterns")
@app.get("/images")
async def module_page():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "social-trend-creative"}


@app.get("/api/state")
async def state(limit: int = Query(default=40, ge=1, le=200)):
    cached = _state_cache.get(limit)
    if cached and time.monotonic() - cached[0] < 5:
        return cached[1]
    async with _state_cache_lock:
        cached = _state_cache.get(limit)
        if cached and time.monotonic() - cached[0] < 5:
            return cached[1]

    async def safe(fn, fallback):
        try:
            return await asyncio.to_thread(fn)
        except sqlite3.OperationalError:
            return fallback

    dashboard_fallback = {
        "active_run_id": service.active_run_id,
        "running": bool(service.active_task and not service.active_task.done()),
        "today": {"runs": 0, "verified": 0, "generated": 0},
        "history": {"runs": 0, "candidates": 0, "verified": 0, "generated": 0, "failed": 0, "avg_duration_ms": 0},
        "platforms": [], "sources": {"total_entries": 0, "recent_entries": 0},
    }
    tagging_fallback = {
        "status": service.tagging_backfill.get("status", "idle"),
        "total_runs": service.tagging_backfill.get("total_runs", 0),
        "completed_runs": service.tagging_backfill.get("completed_runs", 0),
        "current_run_id": service.tagging_backfill.get("current_run_id", ""),
        "error": service.tagging_backfill.get("error", ""),
        "updated_at": service.tagging_backfill.get("updated_at", ""),
        "total_prompts": 0,
        "tagged_prompts": 0,
        "pending_prompts": 0,
    }
    pattern_analysis_fallback = {
        "status": service.pattern_analysis_backfill.get("status", "idle"),
        "total_assets": service.pattern_analysis_backfill.get("total_assets", 0),
        "completed_assets": service.pattern_analysis_backfill.get("completed_assets", 0),
        "current_asset_id": service.pattern_analysis_backfill.get("current_asset_id", ""),
        "error": service.pattern_analysis_backfill.get("error", ""),
        "updated_at": service.pattern_analysis_backfill.get("updated_at", ""),
        "tagged_assets": 0,
        "screened_assets": 0,
        "pending_assets": 0,
    }
    config, connections, dashboard, runs, tagging, pattern_analysis, source_state = await asyncio.gather(
        safe(service.get_config, {}),
        safe(service.connection_info, {}),
        safe(service.dashboard, dashboard_fallback),
        safe(lambda: service.list_runs(limit), []),
        safe(service.tagging_state, tagging_fallback),
        safe(service.pattern_analysis_state, pattern_analysis_fallback),
        safe(
            service.source_state,
            {"syncing": False, "sync": {}, "sources": [], "total_entries": 0, "recent_entries": 0},
        ),
    )
    payload = {
        "config": config,
        "connections": connections,
        "models": {"gemini": GEMINI_MODELS, "flow": FLOW_MODELS},
        "dashboard": dashboard,
        "runs": runs,
        "tagging": tagging,
        "pattern_analysis": pattern_analysis,
        "source_state": source_state,
        "update": read_update_status(),
    }
    _state_cache[limit] = (time.monotonic(), payload)
    return payload


@app.get("/api/sources")
def sources():
    return service.source_state()


@app.post("/api/sources/sync", status_code=202)
async def sync_sources():
    try:
        launched = service.launch_source_sync()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="外媒来源同步已在运行")
    return {"status": "accepted"}


@app.get("/api/signals")
def signals(
    limit: int = Query(default=200, ge=1, le=1000),
    source_id: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
):
    return {
        "entries": service.list_source_entries(limit, source_id=source_id, offset=offset),
        "total": service.count_source_entries(source_id=source_id),
    }


@app.get("/api/cards/{pool}")
def pool_cards(
    pool: str,
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: str = Query(default="", max_length=200),
    category: str = Query(default="", max_length=100),
    transparent: str = Query(default="", max_length=3),
):
    try:
        return service.list_pool_cards(
            pool, limit, offset, q=q, category=category, transparent=transparent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/signals/{entry_id}")
def signal(entry_id: str):
    entry = service.get_source_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="来源条目不存在")
    return entry


@app.get("/api/system/update")
async def system_update_status():
    return read_update_status()


@app.post("/api/system/update", status_code=202)
async def request_system_update():
    if service.active_task and not service.active_task.done():
        raise HTTPException(status_code=409, detail="当前有热点或生图任务运行，请等待任务完成后再更新")
    current = read_update_status()
    if current.get("status") in {"pending", "running"}:
        raise HTTPException(status_code=409, detail="项目更新已在进行中")
    request = {
        "request_id": secrets.token_hex(8),
        "status": "pending",
        "message": "等待服务机更新器处理",
        "requested_at": utc_now(),
    }
    write_update_file(UPDATE_STATUS_PATH, request)
    write_update_file(UPDATE_REQUEST_PATH, request)
    return request


@app.put("/api/config")
async def update_config(update: ConfigUpdate):
    try:
        return {"config": service.save_config(update.model_dump(exclude_none=True))}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/connections/test")
async def test_connections():
    return await service.test_connections()


@app.post("/api/runs/discover", status_code=202)
async def discover():
    run_id = service.launch_full_pipeline(trigger_type="manual", auto_generate=False)
    if not run_id:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "stages": ["acquisition", "classification", "prompt_pool"]}


@app.post("/api/runs/full", status_code=202)
async def full_run():
    run_id = service.launch_full_pipeline(trigger_type="manual", auto_generate=True)
    if not run_id:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "auto_generate": True}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="轮次不存在")
    return run


@app.post("/api/runs/{run_id}/classify", status_code=202)
async def classify(run_id: str):
    try:
        launched = service.launch_classification(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted"}


@app.post("/api/runs/{run_id}/prompts", status_code=202)
async def prompts(run_id: str, request: PoolRequest):
    try:
        launched = service.launch_prompt_pool(run_id, request.count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "count": request.count}


@app.post("/api/runs/{run_id}/tags", status_code=202)
async def tag_prompts(run_id: str):
    try:
        launched = service.launch_tagging(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted"}


@app.post("/api/tags/backfill", status_code=202)
async def backfill_tags():
    try:
        launched = service.launch_tagging_backfill()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"status": "accepted"}


@app.post("/api/patterns/analyze/backfill", status_code=202)
async def backfill_pattern_analysis(force: bool = Query(default=False)):
    try:
        launched = service.launch_pattern_analysis_backfill(force=force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"status": "accepted"}


@app.post("/api/patterns/{asset_id}/analyze", status_code=202)
async def analyze_pattern(asset_id: str):
    try:
        launched = service.launch_pattern_analysis(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"asset_id": asset_id, "status": "accepted"}


@app.post("/api/runs/{run_id}/generate", status_code=202)
async def generate(run_id: str, request: PoolRequest):
    try:
        launched = service.launch_generation(run_id, request.count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "count": request.count}


@app.post("/api/runs/{run_id}/patterns", status_code=202)
async def generate_patterns(run_id: str, request: PoolRequest):
    try:
        launched = service.launch_pattern_generation(run_id, request.count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "count": request.count}


@app.post("/api/runs/{run_id}/products", status_code=202)
async def generate_products(run_id: str, request: PoolRequest):
    try:
        launched = service.launch_product_generation(run_id, request.count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "count": request.count}


@app.post("/api/runs/{run_id}/cancel")
async def cancel(run_id: str):
    if service.active_run_id != run_id or not service.active_task or service.active_task.done():
        return {"cancelled": False}
    service.active_task.cancel()
    return {"cancelled": True}


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: str):
    try:
        deleted = service.delete_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="轮次不存在")
    return {"deleted": True}


@app.post("/api/runs/cleanup-empty")
async def cleanup_empty_runs():
    return await service.cleanup_empty_runs()
