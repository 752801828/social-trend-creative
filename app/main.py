from __future__ import annotations

import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.service import FLOW_MODELS, GEMINI_MODELS, TrendService, utc_now


ROOT = Path(__file__).resolve().parents[1]
ADMIN_KEY = os.getenv("ADMIN_KEY", "")
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


class PoolRequest(BaseModel):
    count: int | None = Field(default=None, ge=1, le=30)


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/acquire")
@app.get("/trends")
@app.get("/prompts")
@app.get("/images")
async def module_page():
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
        "update": read_update_status(),
    }


@app.get("/api/system/update", dependencies=[Depends(require_admin)])
async def system_update_status():
    return read_update_status()


@app.post("/api/system/update", dependencies=[Depends(require_admin)], status_code=202)
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
    run_id = service.launch_full_pipeline(trigger_type="manual", auto_generate=False)
    if not run_id:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "stages": ["acquisition", "classification", "prompt_pool"]}


@app.post("/api/runs/full", dependencies=[Depends(require_admin)], status_code=202)
async def full_run():
    run_id = service.launch_full_pipeline(trigger_type="manual", auto_generate=True)
    if not run_id:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "auto_generate": True}


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_admin)])
async def get_run(run_id: str):
    run = service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="轮次不存在")
    return run


@app.post("/api/runs/{run_id}/classify", dependencies=[Depends(require_admin)], status_code=202)
async def classify(run_id: str):
    try:
        launched = service.launch_classification(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted"}


@app.post("/api/runs/{run_id}/prompts", dependencies=[Depends(require_admin)], status_code=202)
async def prompts(run_id: str, request: PoolRequest):
    try:
        launched = service.launch_prompt_pool(run_id, request.count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "count": request.count}


@app.post("/api/runs/{run_id}/generate", dependencies=[Depends(require_admin)], status_code=202)
async def generate(run_id: str, request: PoolRequest):
    try:
        launched = service.launch_generation(run_id, request.count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not launched:
        raise HTTPException(status_code=409, detail="已有任务正在执行")
    return {"run_id": run_id, "status": "accepted", "count": request.count}


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
