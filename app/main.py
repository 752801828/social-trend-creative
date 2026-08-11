from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.service import FLOW_MODELS, GEMINI_MODELS, TrendService


ROOT = Path(__file__).resolve().parents[1]
ADMIN_KEY = os.getenv("ADMIN_KEY", "")
service = TrendService()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await service.start()
    yield
    await service.stop()


app = FastAPI(title="Social Trend Creative", version="0.1.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=service.assets_dir), name="assets")


def require_admin(authorization: str = Header(default="")) -> None:
    if not ADMIN_KEY:
        raise HTTPException(status_code=503, detail="ADMIN_KEY未配置")
    supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not supplied or not hmac.compare_digest(supplied, ADMIN_KEY):
        raise HTTPException(status_code=401, detail="管理密钥无效")


class ConfigUpdate(BaseModel):
    enabled: bool | None = None
    schedule_time: str | None = None
    timezone: str | None = None
    lookback_hours: int | None = Field(default=None, ge=1, le=168)
    regions: list[str] | None = None
    platforms: list[str] | None = None
    candidate_count: int | None = Field(default=None, ge=1, le=30)
    images_per_trend: int | None = Field(default=None, ge=1, le=5)
    gemini_discovery_model: str | None = None
    gemini_verification_model: str | None = None
    flow_models: list[str] | None = None
    generation_concurrency: int | None = Field(default=None, ge=1, le=5)
    auto_generate: bool | None = None
    notify_enabled: bool | None = None


class GenerateRequest(BaseModel):
    trend_ids: list[str] = Field(min_length=1, max_length=30)


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "social-trend-creative"}


@app.get("/api/state", dependencies=[Depends(require_admin)])
async def state(limit: int = Query(default=40, ge=1, le=200)):
    return {
        "config": service.get_config(),
        "connections": service.connection_info(),
        "models": {"gemini": GEMINI_MODELS, "flow": FLOW_MODELS},
        "dashboard": service.dashboard(),
        "runs": service.list_runs(limit),
    }


@app.put("/api/config", dependencies=[Depends(require_admin)])
async def update_config(update: ConfigUpdate):
    try:
        return {"config": service.save_config(update.model_dump(exclude_none=True))}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/connections/test", dependencies=[Depends(require_admin)])
async def test_connections():
    return await service.test_connections()


@app.post("/api/runs/discover", dependencies=[Depends(require_admin)], status_code=202)
async def discover():
    run_id = service.launch_discovery(trigger_type="manual", auto_generate=False)
    if not run_id:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted"}


@app.post("/api/runs/full", dependencies=[Depends(require_admin)], status_code=202)
async def full_run():
    run_id = service.launch_discovery(trigger_type="manual", auto_generate=True)
    if not run_id:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "auto_generate": True}


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_admin)])
async def get_run(run_id: str):
    run = service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="轮次不存在")
    return run


@app.post("/api/runs/{run_id}/generate", dependencies=[Depends(require_admin)], status_code=202)
async def generate(run_id: str, request: GenerateRequest):
    try:
        launched = service.launch_generation(run_id, request.trend_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "trend_ids": request.trend_ids}


@app.post("/api/runs/{run_id}/cancel", dependencies=[Depends(require_admin)])
async def cancel(run_id: str):
    if service.active_run_id != run_id or not service.active_task or service.active_task.done():
        return {"cancelled": False}
    service.active_task.cancel()
    return {"cancelled": True}


@app.delete("/api/runs/{run_id}", dependencies=[Depends(require_admin)])
async def delete_run(run_id: str):
    try:
        deleted = service.delete_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="轮次不存在")
    return {"deleted": True}
