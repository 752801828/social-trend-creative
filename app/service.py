from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import random
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx


logger = logging.getLogger(__name__)

GEMINI_MODELS = [
    "gemini-pro",
    "gemini-pro-thinking",
    "gemini-flash",
    "gemini-flash-thinking",
    "gemini-flash-lite",
]

FLOW_MODELS = [
    "gemini-3.0-pro-image-landscape",
    "gemini-3.0-pro-image-portrait",
    "gemini-3.0-pro-image-square",
    "gemini-3.0-pro-image-four-three",
    "gemini-3.0-pro-image-three-four",
    "gemini-3.1-flash-image-landscape",
    "gemini-3.1-flash-image-portrait",
    "gemini-3.1-flash-image-square",
    "gemini-3.1-flash-image-four-three",
    "gemini-3.1-flash-image-three-four",
    "imagen-4.0-generate-preview-landscape",
    "imagen-4.0-generate-preview-portrait",
]

DEFAULT_CONFIG = {
    "enabled": False,
    "schedule_time": "09:00",
    "timezone": "Asia/Shanghai",
    "lookback_hours": 24,
    "regions": ["United States", "United Kingdom", "Europe", "Global English"],
    "platforms": ["X", "TikTok", "Instagram", "YouTube", "Reddit"],
    "candidate_count": 10,
    "images_per_trend": 1,
    "gemini_discovery_model": "gemini-pro-thinking",
    "gemini_verification_model": "gemini-flash",
    "flow_models": ["gemini-3.1-flash-image-landscape"],
    "generation_concurrency": 2,
    "auto_generate": False,
    "notify_enabled": False,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def string_list(value: Any, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:item_limit] for item in value if str(item).strip()][:limit]


def safe_error(exc: Exception) -> str:
    return re.sub(r"(?i)(authorization|api[_-]?key|bearer)\s*[:=]?\s*\S+", r"\1=***", str(exc))[:1000]


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Gemini响应中没有JSON对象")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Gemini响应JSON必须是对象")
    return parsed


class TrendService:
    def __init__(self) -> None:
        self.data_dir = Path(os.getenv("DATA_DIR", "data")).resolve()
        self.assets_dir = self.data_dir / "assets"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "trend-creative.db"
        self.gemini_base_url = os.getenv("GEMINI_BASE_URL", "http://127.0.0.1:5918").rstrip("/")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.flow_base_url = os.getenv("FLOW_BASE_URL", "http://127.0.0.1:38000").rstrip("/")
        self.flow_api_key = os.getenv("FLOW_API_KEY", "")
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        self.feishu_webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        self.feishu_secret = os.getenv("FEISHU_SIGNING_SECRET", "").strip()
        self.http = httpx.AsyncClient(trust_env=False, follow_redirects=True)
        self.operation_lock = asyncio.Lock()
        self.active_task: asyncio.Task | None = None
        self.active_run_id: str | None = None
        self.scheduler_task: asyncio.Task | None = None
        self._stopping = False
        self._last_scheduled_date = ""
        self._init_db()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init_db(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    trigger_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms INTEGER,
                    error TEXT NOT NULL DEFAULT '',
                    raw_discovery TEXT NOT NULL DEFAULT '',
                    raw_verification TEXT NOT NULL DEFAULT '',
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    verified_count INTEGER NOT NULL DEFAULT 0,
                    generated_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS trends (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    rank INTEGER NOT NULL,
                    topic_en TEXT NOT NULL,
                    topic_zh TEXT NOT NULL,
                    summary_zh TEXT NOT NULL,
                    why_trending TEXT NOT NULL,
                    platforms TEXT NOT NULL,
                    region TEXT NOT NULL,
                    category TEXT NOT NULL,
                    first_seen_at TEXT,
                    engagement_signal TEXT,
                    evidence TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    visual_brief_en TEXT NOT NULL,
                    risk_flags TEXT NOT NULL,
                    status TEXT NOT NULL,
                    verification_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trends_run ON trends(run_id, rank);
                CREATE TABLE IF NOT EXISTS prompt_pool (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    trend_id TEXT NOT NULL REFERENCES trends(id) ON DELETE CASCADE,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready',
                    used_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_prompt_pool_run ON prompt_pool(run_id, created_at);
                CREATE TABLE IF NOT EXISTS generations (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    trend_id TEXT NOT NULL REFERENCES trends(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    image_path TEXT,
                    mime_type TEXT,
                    duration_ms INTEGER,
                    error TEXT NOT NULL DEFAULT '',
                    raw_response TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_generations_run ON generations(run_id, trend_id);
                """
            )
            generation_columns = {row["name"] for row in db.execute("PRAGMA table_info(generations)")}
            if "prompt_id" not in generation_columns:
                db.execute("ALTER TABLE generations ADD COLUMN prompt_id TEXT")
            if not db.execute("SELECT 1 FROM settings WHERE id = 1").fetchone():
                db.execute(
                    "INSERT INTO settings(id, value, updated_at) VALUES(1, ?, ?)",
                    (json_text(DEFAULT_CONFIG), utc_now()),
                )

    async def start(self) -> None:
        if not self.scheduler_task or self.scheduler_task.done():
            self.scheduler_task = asyncio.create_task(self._scheduler_loop(), name="trend-daily-scheduler")

    async def stop(self) -> None:
        self._stopping = True
        for task in (self.scheduler_task, self.active_task):
            if task and not task.done():
                task.cancel()
        await self.http.aclose()

    def get_config(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE id = 1").fetchone()
        config = {**copy.deepcopy(DEFAULT_CONFIG), **json.loads(row["value"])}
        config.pop("final_count", None)
        return config

    def save_config(self, values: dict[str, Any]) -> dict[str, Any]:
        config = {**self.get_config(), **values}
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(config["schedule_time"])):
            raise ValueError("执行时间必须使用HH:MM格式")
        ZoneInfo(str(config["timezone"]))
        config["lookback_hours"] = min(168, max(1, int(config["lookback_hours"])))
        config["candidate_count"] = min(30, max(1, int(config["candidate_count"])))
        config["images_per_trend"] = min(5, max(1, int(config["images_per_trend"])))
        config["generation_concurrency"] = min(5, max(1, int(config["generation_concurrency"])))
        config["regions"] = [str(item).strip() for item in config.get("regions", []) if str(item).strip()][:20]
        config["platforms"] = [str(item).strip() for item in config.get("platforms", []) if str(item).strip()][:20]
        if not config["regions"] or not config["platforms"]:
            raise ValueError("地区和平台至少各选择一项")
        for key in ("gemini_discovery_model", "gemini_verification_model"):
            if config[key] not in GEMINI_MODELS:
                raise ValueError(f"不支持的Gemini模型: {config[key]}")
        flow_models = [item for item in config.get("flow_models", []) if item in FLOW_MODELS]
        if not flow_models:
            raise ValueError("至少选择一个Flow生图模型")
        config["flow_models"] = flow_models
        with self._connect() as db:
            db.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE id = 1",
                (json_text(config), utc_now()),
            )
        return config

    def connection_info(self) -> dict[str, Any]:
        return {
            "gemini_base_url": self.gemini_base_url,
            "gemini_key_configured": bool(self.gemini_api_key),
            "flow_base_url": self.flow_base_url,
            "flow_key_configured": bool(self.flow_api_key),
            "feishu_configured": bool(self.feishu_webhook),
            "public_base_url": self.public_base_url,
        }

    async def test_connections(self) -> dict[str, Any]:
        async def check(base_url: str, key: str) -> dict[str, Any]:
            started = time.perf_counter()
            try:
                response = await self.http.get(
                    f"{base_url}/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=15,
                )
                return {
                    "ok": response.status_code == 200,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                }
            except Exception as exc:
                return {"ok": False, "status": 0, "error": safe_error(exc)}

        gemini, flow = await asyncio.gather(
            check(self.gemini_base_url, self.gemini_api_key),
            check(self.flow_base_url, self.flow_api_key),
        )
        return {"gemini": gemini, "flow": flow}

    def create_run(self, trigger_type: str) -> str:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        with self._connect() as db:
            db.execute(
                "INSERT INTO runs(id, trigger_type, status, stage, started_at) VALUES(?, ?, 'pending', 'queued', ?)",
                (run_id, trigger_type, utc_now()),
            )
        return run_id

    def _launch(self, run_id: str, coroutine: Any, name: str) -> bool:
        if self.active_task and not self.active_task.done():
            coroutine.close()
            return False
        self.active_run_id = run_id
        self.active_task = asyncio.create_task(coroutine, name=name)
        return True

    def launch_acquisition(self, *, trigger_type: str = "manual") -> str | None:
        if self.active_task and not self.active_task.done():
            return None
        run_id = self.create_run(trigger_type)
        self._launch(run_id, self._run_stage(run_id, "acquisition"), f"acquire-{run_id}")
        return run_id

    def launch_classification(self, run_id: str) -> bool:
        with self._connect() as db:
            run = db.execute("SELECT raw_discovery FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise ValueError("轮次不存在")
        if not run["raw_discovery"]:
            raise ValueError("原始热点为空，请先获取热点")
        with self._connect() as db:
            exists = db.execute("SELECT 1 FROM trends WHERE run_id=? LIMIT 1", (run_id,)).fetchone()
        if exists:
            raise ValueError("热点池已存在；重新分类会破坏已有提示词和图片")
        return self._launch(run_id, self._run_stage(run_id, "classification"), f"classify-{run_id}")

    def launch_prompt_pool(self, run_id: str, count: int | None = None) -> bool:
        with self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM trends WHERE run_id=? AND status!='rejected' LIMIT 1", (run_id,)
            ).fetchone()
        if not exists:
            raise ValueError("热点池为空，请先执行AI拆分分类")
        return self._launch(run_id, self._run_stage(run_id, "prompt_pool", count), f"prompts-{run_id}")

    def launch_generation(self, run_id: str, count: int | None = None) -> bool:
        with self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM prompt_pool WHERE run_id=? AND status='ready' LIMIT 1", (run_id,)
            ).fetchone()
        if not exists:
            raise ValueError("提示词池为空，请先生成提示词池")
        return self._launch(run_id, self._run_stage(run_id, "generation", count), f"generate-{run_id}")

    def launch_full_pipeline(self, *, trigger_type: str = "manual", auto_generate: bool = True) -> str | None:
        if self.active_task and not self.active_task.done():
            return None
        run_id = self.create_run(trigger_type)
        self._launch(
            run_id,
            self._run_stage(run_id, "full", auto_generate=auto_generate),
            f"pipeline-{run_id}",
        )
        return run_id

    async def _run_stage(
        self,
        run_id: str,
        stage: str,
        count: int | None = None,
        *,
        auto_generate: bool = True,
    ) -> None:
        async with self.operation_lock:
            started = time.perf_counter()
            config = self.get_config()
            try:
                if stage in {"acquisition", "full"}:
                    await self._acquire_raw_trends(run_id, config)
                if stage in {"classification", "full"}:
                    await self._classify_trend_pool(run_id, config)
                if stage in {"prompt_pool", "full"}:
                    await self._create_prompt_pool(run_id, config, count)
                if stage == "generation" or (stage == "full" and auto_generate):
                    await self._generate_from_prompt_pool(run_id, config, count)
                self._finish_duration(run_id, started)
                if stage != "generation" and not (stage == "full" and auto_generate):
                    await self._notify_run(run_id)
            except asyncio.CancelledError:
                self._update_run(run_id, status="cancelled", stage="finished", error="任务已取消", finished_at=utc_now())
                self._finish_duration(run_id, started)
                raise
            except Exception as exc:
                logger.exception("Pipeline stage %s failed", stage)
                self._update_run(run_id, status="failed", stage="finished", error=safe_error(exc), finished_at=utc_now())
                self._finish_duration(run_id, started)
                await self._notify_run(run_id)
            finally:
                self.active_run_id = None

    async def _acquire_raw_trends(self, run_id: str, config: dict[str, Any]) -> list[dict[str, Any]]:
        self._update_run(run_id, status="running", stage="acquisition", error="")
        discovery_text = await self._call_gemini(
            self._discovery_prompt(config), config["gemini_discovery_model"], attempts=3
        )
        candidates = self._normalise_candidates(
            extract_json_object(discovery_text), config["candidate_count"]
        )
        if not candidates:
            raise ValueError("Gemini没有返回可解析的原始热点")
        self._update_run(
            run_id,
            raw_discovery=discovery_text,
            candidate_count=len(candidates),
            status="awaiting_classification",
            stage="raw_trends",
        )
        return candidates

    async def _classify_trend_pool(self, run_id: str, config: dict[str, Any]) -> list[dict[str, Any]]:
        with self._connect() as db:
            row = db.execute("SELECT raw_discovery FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row or not row["raw_discovery"]:
            raise ValueError("原始热点为空，请先获取热点")
        candidates = self._normalise_candidates(
            extract_json_object(row["raw_discovery"]), config["candidate_count"]
        )
        self._update_run(run_id, status="running", stage="classification", error="")
        verification_text = await self._call_gemini(
            self._classification_prompt(config, candidates),
            config["gemini_verification_model"],
            attempts=2,
        )
        payload = extract_json_object(verification_text)
        if isinstance(payload.get("classified_trends"), list):
            trends = self._normalise_candidates(
                {"trends": payload["classified_trends"]}, config["candidate_count"] * 3
            )
            trends = self._trend_pool_entries(trends)
        else:
            trends = self._verify_candidates(config, candidates, payload)
        self._replace_trends(run_id, trends)
        usable = [item for item in trends if item["status"] == "ready"]
        self._update_run(
            run_id,
            raw_verification=verification_text,
            verified_count=len(usable),
            status="trend_pool_ready" if usable else "failed",
            stage="trend_pool" if usable else "finished",
            error="" if usable else "热点池为空",
        )
        return usable

    async def _create_prompt_pool(
        self,
        run_id: str,
        config: dict[str, Any],
        count: int | None,
    ) -> list[str]:
        with self._connect() as db:
            trends = [dict(row) for row in db.execute(
                "SELECT * FROM trends WHERE run_id=? AND status!='rejected'", (run_id,)
            ).fetchall()]
        if not trends:
            raise ValueError("热点池为空，请先执行AI拆分分类")
        selected = random.sample(
            trends,
            min(len(trends), max(1, count or config["candidate_count"])),
        )
        self._update_run(run_id, status="running", stage="prompt_pool_generation", error="")
        response_text = await self._call_gemini(
            self._prompt_pool_prompt(selected), config["gemini_verification_model"], attempts=2
        )
        payload = extract_json_object(response_text)
        supplied = payload.get("prompts") if isinstance(payload.get("prompts"), list) else []
        supplied_map = {
            str(item.get("trend_id")): str(item.get("prompt") or "").strip()
            for item in supplied if isinstance(item, dict)
        }
        prompt_ids = []
        with self._connect() as db:
            for trend in selected:
                prompt_id = secrets.token_hex(12)
                prompt = supplied_map.get(trend["id"]) or self._flow_prompt(trend)
                db.execute(
                    "INSERT INTO prompt_pool(id,run_id,trend_id,prompt,status,created_at) VALUES(?,?,?,?, 'ready', ?)",
                    (prompt_id, run_id, trend["id"], prompt[:10000], utc_now()),
                )
                prompt_ids.append(prompt_id)
        self._update_run(run_id, status="prompt_pool_ready", stage="prompt_pool")
        return prompt_ids

    async def _generate_from_prompt_pool(
        self,
        run_id: str,
        config: dict[str, Any],
        count: int | None,
    ) -> None:
        with self._connect() as db:
            prompts = [dict(row) for row in db.execute(
                """SELECT t.*, p.id AS prompt_id, p.prompt AS pool_prompt
                   FROM prompt_pool p JOIN trends t ON t.id=p.trend_id
                   WHERE p.run_id=? AND p.status='ready'""",
                (run_id,),
            ).fetchall()]
        if not prompts:
            raise ValueError("提示词池为空，请先生成提示词池")
        selected = random.sample(prompts, min(len(prompts), max(1, count or config["images_per_trend"])))
        self._update_run(run_id, status="running", stage="generation", error="")
        semaphore = asyncio.Semaphore(config["generation_concurrency"])

        async def guarded(item: dict[str, Any], sequence: int) -> bool:
            async with semaphore:
                return await self._generate_one(
                    run_id,
                    item,
                    sequence,
                    config,
                    prompt_id=item["prompt_id"],
                    prompt_text=item["pool_prompt"],
                )

        results = await asyncio.gather(*(guarded(item, index) for index, item in enumerate(selected, 1)))
        success = sum(bool(item) for item in results)
        failed = len(results) - success
        with self._connect() as db:
            totals = db.execute(
                "SELECT SUM(status='success'), SUM(status='failed') FROM generations WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        total_success = int(totals[0] or 0)
        total_failed = int(totals[1] or 0)
        self._update_run(
            run_id,
            status="completed" if success and not failed else "partial" if success else "failed",
            stage="finished",
            generated_count=total_success,
            failed_count=total_failed,
            finished_at=utc_now(),
            error="" if success else "Flow没有成功生成图片",
        )
        await self._notify_run(run_id)

    async def _generate_one(
        self,
        run_id: str,
        trend: dict[str, Any],
        sequence: int,
        config: dict[str, Any],
        *,
        prompt_id: str | None = None,
        prompt_text: str | None = None,
    ) -> bool:
        generation_id = secrets.token_hex(12)
        model = random.choice(config["flow_models"])
        prompt = prompt_text or self._flow_prompt(trend)
        started = time.perf_counter()
        with self._connect() as db:
            db.execute(
                """INSERT INTO generations
                   (id, run_id, trend_id, prompt_id, sequence, model, prompt, status, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
                (generation_id, run_id, trend["id"], prompt_id, sequence, model, prompt, utc_now()),
            )
            if prompt_id:
                db.execute(
                    "UPDATE prompt_pool SET used_count=used_count+1 WHERE id=?", (prompt_id,)
                )
            db.execute("UPDATE trends SET status = 'generating' WHERE id = ?", (trend["id"],))
        try:
            response_text, image_bytes, mime_type = await self._call_flow(prompt, model)
            suffix = mimetypes.guess_extension(mime_type) or ".png"
            relative = Path(run_id) / trend["id"] / f"{generation_id}{suffix}"
            target = self.assets_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(image_bytes)
            duration = round((time.perf_counter() - started) * 1000)
            with self._connect() as db:
                db.execute(
                    """UPDATE generations SET status='success', image_path=?, mime_type=?, duration_ms=?,
                       raw_response=?, finished_at=? WHERE id=?""",
                    (relative.as_posix(), mime_type, duration, response_text[:20000], utc_now(), generation_id),
                )
                db.execute("UPDATE trends SET status = 'generated' WHERE id = ?", (trend["id"],))
            return True
        except asyncio.CancelledError:
            with self._connect() as db:
                db.execute(
                    """UPDATE generations SET status='failed', duration_ms=?, error=?, finished_at=? WHERE id=?""",
                    (round((time.perf_counter() - started) * 1000), "任务已取消", utc_now(), generation_id),
                )
                generated = db.execute(
                    "SELECT 1 FROM generations WHERE trend_id=? AND status='success'", (trend["id"],)
                ).fetchone()
                db.execute(
                    "UPDATE trends SET status = ? WHERE id = ?",
                    ("generated" if generated else trend["status"], trend["id"]),
                )
            raise
        except Exception as exc:
            with self._connect() as db:
                db.execute(
                    """UPDATE generations SET status='failed', duration_ms=?, error=?, finished_at=? WHERE id=?""",
                    (round((time.perf_counter() - started) * 1000), safe_error(exc), utc_now(), generation_id),
                )
                remaining = db.execute(
                    "SELECT 1 FROM generations WHERE trend_id=? AND status='success'", (trend["id"],)
                ).fetchone()
                if not remaining:
                    db.execute("UPDATE trends SET status = 'generation_failed' WHERE id = ?", (trend["id"],))
            return False

    async def _call_gemini(self, prompt: str, model: str, *, attempts: int) -> str:
        if not self.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY未配置")
        error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await self.http.post(
                    f"{self.gemini_base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.gemini_api_key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                    },
                    timeout=300,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"].get("content", "")
                if not str(content).strip():
                    raise RuntimeError("Gemini返回空内容")
                return str(content)
            except Exception as exc:
                error = exc
                if attempt < attempts:
                    await asyncio.sleep(2 * attempt)
        raise RuntimeError(f"Gemini请求失败（{attempts}次）: {safe_error(error or RuntimeError('unknown'))}")

    async def _call_flow(self, prompt: str, model: str) -> tuple[str, bytes, str]:
        if not self.flow_api_key:
            raise RuntimeError("FLOW_API_KEY未配置")
        response = await self.http.post(
            f"{self.flow_base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.flow_api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
            timeout=1800,
        )
        response.raise_for_status()
        raw = response.text
        content = str(response.json()["choices"][0]["message"].get("content", ""))
        match = re.search(r"!\[[^\]]*\]\((.+?)\)", content, flags=re.S)
        image_url = match.group(1).strip() if match else content.strip()
        if image_url.startswith("data:image"):
            header, encoded = image_url.split(",", 1)
            mime_type = header.split(";", 1)[0].split(":", 1)[1]
            return raw, base64.b64decode(encoded), mime_type
        if not image_url.startswith(("http://", "https://")):
            image_url = urljoin(f"{self.flow_base_url}/", image_url.lstrip("/"))
        image_origin = urlparse(image_url)
        flow_origin = urlparse(self.flow_base_url)
        download_headers = (
            {"Authorization": f"Bearer {self.flow_api_key}"}
            if (image_origin.scheme, image_origin.hostname, image_origin.port)
            == (flow_origin.scheme, flow_origin.hostname, flow_origin.port)
            else {}
        )
        image_response = await self.http.get(
            image_url,
            headers=download_headers,
            timeout=120,
        )
        image_response.raise_for_status()
        payload = image_response.content
        if not payload:
            raise RuntimeError("Flow生成图片下载为空")
        mime_type = image_response.headers.get("content-type", "").split(";", 1)[0] or "image/png"
        return raw, payload, mime_type

    def _discovery_prompt(self, config: dict[str, Any]) -> str:
        now = datetime.now(ZoneInfo(config["timezone"])).isoformat()
        return f"""You are a real-time worldwide social-media visual-trend researcher for print-on-demand products.

Current time: {now}
Lookback window: the previous {config['lookback_hours']} hours
Target regions: {', '.join(config['regions'])}
Target platforms: {', '.join(config['platforms'])}
Return at most {config['candidate_count']} candidate trends.

You must use any internet-search capability available in the current Gemini session. Search worldwide; treat the target regions as priority coverage rather than exclusive boundaries. Collect broad raw trends: current events, memes, phrases, moods, aesthetics, communities, seasonal moments, and visual symbols. Do not choose products or write image prompts in this acquisition stage.

Rules:
1. Each result is a raw social signal. Keep separate movements separate, but do not turn one signal into product concepts yet.
2. Evidence URLs and publication times are optional. Include real sources when available, but never invent them and never omit a useful visual opportunity only because evidence is unavailable.
3. Use null when a value cannot be verified.
4. Reject gambling, adult content, graphic violence, obvious misinformation, hate, trademarks, copyrighted characters, and ideas dependent on a real person's likeness.
5. Prefer signals with recognizable shapes, color moods, symbols, textures, communities, or emotional hooks that can be analyzed later without copying an existing post or artwork.
6. Return strict JSON only. Do not use Markdown fences or prose outside JSON.

Schema:
{{
  "searched_at": "ISO-8601",
  "trends": [
    {{
      "rank": 1,
      "topic_en": "English title",
      "topic_zh": "Chinese title",
      "summary_zh": "Chinese factual summary",
      "why_trending": "Why it is trending",
      "platforms": ["X"],
      "region": "Primary region",
      "category": "trend category",
      "first_seen_at": "ISO-8601 or null",
      "engagement_signal": "Verified signal or null",
      "evidence": [
        {{"source_type":"platform","platform":"X","title":"Source title","url":"https://...","published_at":"ISO-8601 or null"}}
      ],
      "confidence": 0.85,
      "visual_brief_en": "Optional raw visual observation, not an image prompt",
      "risk_flags": []
    }}
  ],
  "rejected": [{{"topic":"topic","reason":"reason"}}]
}}"""

    def _classification_prompt(self, config: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
        now = datetime.now(ZoneInfo(config["timezone"])).isoformat()
        return f"""You are the AI classifier that builds a reusable worldwide trend pool.

Current time: {now}
Valid lookback: {config['lookback_hours']} hours

Split broad raw trends into independently usable creative angles, merge duplicates, and assign a concise category such as culture, humor, lifestyle, seasonal, sports, technology, nature, travel, food, pets, or social mood. A raw trend may produce multiple classified entries when it contains distinct visual angles. Each entry must preserve factual context and include a reusable visual direction, but it must not be a finished image prompt or choose a physical product yet. Missing evidence or publication time is not a rejection reason. Exclude only empty, unsafe, trademark-dependent, copyrighted-character-dependent, or real-person-likeness-dependent angles. Return strict JSON only.

Candidates:
{json.dumps(candidates, ensure_ascii=False)}

Schema:
{{
  "classified_trends": [
    {{
      "topic_en":"independent English angle",
      "topic_zh":"独立中文角度",
      "summary_zh":"事实摘要",
      "why_trending":"传播原因",
      "platforms":["X"],
      "region":"Global",
      "category":"culture",
      "first_seen_at":null,
      "engagement_signal":"signal or null",
      "evidence":[],
      "confidence":0.8,
      "visual_brief_en":"Reusable visual motif and mood, not a finished prompt",
      "risk_flags":[]
    }}
  ]
}}"""

    @staticmethod
    def _prompt_pool_prompt(trends: list[dict[str, Any]]) -> str:
        compact = [
            {
                "trend_id": item["id"],
                "topic_en": item["topic_en"],
                "summary_zh": item["summary_zh"],
                "why_trending": item["why_trending"],
                "category": item["category"],
                "visual_brief_en": item["visual_brief_en"],
            }
            for item in trends
        ]
        return f"""You create production-ready image prompts from randomly selected worldwide trend-pool entries.

For every input trend_id, write one complete English prompt for a realistic print-on-demand product rendering. Keep the trend's original idea and category, select one suitable physical item, and fully specify the artwork, placement, scale, print treatment, product color, material, camera angle, lighting, and neutral setting.

Rules:
1. One prompt must show one main product only: mug, tumbler, phone case, T-shirt, hoodie, tote bag, cushion, blanket, vehicle spare-tire cover, sticker, poster, or another clearly named printable item.
2. The artwork must conform naturally to curvature, seams, folds, and material and look genuinely printed.
3. Do not use logos, trademarks, copyrighted characters, public-figure likenesses, copied posts, watermarks, or existing artwork.
4. Avoid text unless essential; if used, it must be short, generic, and correctly spelled.
5. Return strict JSON only and preserve every trend_id exactly.

Trend-pool entries:
{json.dumps(compact, ensure_ascii=False)}

Schema:
{{"prompts":[{{"trend_id":"id","prompt":"complete English image prompt"}}]}}"""

    @staticmethod
    def _normalise_candidates(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        trends = payload.get("trends")
        if not isinstance(trends, list):
            return []
        result = []
        for index, raw in enumerate(trends[:limit], start=1):
            if not isinstance(raw, dict):
                continue
            topic_en = str(raw.get("topic_en") or "").strip()
            topic_zh = str(raw.get("topic_zh") or topic_en).strip()
            if not topic_en:
                continue
            evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
            result.append({
                "candidate_id": f"candidate-{index}",
                "rank": index,
                "topic_en": topic_en[:300],
                "topic_zh": topic_zh[:300],
                "summary_zh": str(raw.get("summary_zh") or "")[:2000],
                "why_trending": str(raw.get("why_trending") or "")[:2000],
                "platforms": string_list(raw.get("platforms"), limit=20, item_limit=50),
                "region": str(raw.get("region") or "")[:200],
                "category": str(raw.get("category") or "other")[:100],
                "first_seen_at": raw.get("first_seen_at"),
                "engagement_signal": raw.get("engagement_signal"),
                "evidence": evidence[:10],
                "confidence": confidence(raw.get("confidence")),
                "visual_brief_en": str(raw.get("visual_brief_en") or "")[:3000],
                "risk_flags": string_list(raw.get("risk_flags"), limit=20, item_limit=100),
            })
        return result

    def _verify_candidates(
        self,
        config: dict[str, Any],
        candidates: list[dict[str, Any]],
        verification: dict[str, Any],
    ) -> list[dict[str, Any]]:
        verified_items = verification.get("verified_trends", [])
        removed_items = verification.get("removed_trends", [])
        verified_items = verified_items if isinstance(verified_items, list) else []
        removed_items = removed_items if isinstance(removed_items, list) else []
        accepted = {
            str(item.get("candidate_id")): item
            for item in verified_items
            if isinstance(item, dict) and str(item.get("decision", "accept")).lower() == "accept"
        }
        removed = {
            str(item.get("candidate_id")): str(item.get("reason") or "Gemini核验拒绝")
            for item in removed_items
            if isinstance(item, dict) and str(item.get("reason_code") or "").lower() in {"duplicate", "empty", "unsafe"}
        }
        output = []
        for candidate in candidates:
            item = copy.deepcopy(candidate)
            candidate_id = item.pop("candidate_id")
            decision = accepted.get(candidate_id)
            evidence = []
            for source in item["evidence"]:
                if not isinstance(source, dict):
                    continue
                url = str(source.get("url") or "").strip()
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    continue
                clean = {
                    "source_type": str(source.get("source_type") or "unknown")[:50],
                    "platform": str(source.get("platform") or "")[:50],
                    "title": str(source.get("title") or "")[:500],
                    "url": url[:2000],
                    "published_at": source.get("published_at"),
                }
                evidence.append(clean)
            item["evidence"] = evidence
            if candidate_id in removed:
                item["status"] = "rejected"
                item["verification_note"] = removed[candidate_id]
            else:
                item["status"] = "ready"
                item["verification_note"] = str((decision or {}).get("reason") or "热点图案可用")[:500]
                item["confidence"] = max(item["confidence"], confidence((decision or {}).get("confidence")))
                if str((decision or {}).get("visual_brief_en") or "").strip():
                    item["visual_brief_en"] = str(decision["visual_brief_en"])[:3000]
            item["id"] = secrets.token_hex(12)
            output.append(item)
        return output

    @staticmethod
    def _trend_pool_entries(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output = []
        for candidate in candidates:
            item = copy.deepcopy(candidate)
            item.pop("candidate_id", None)
            item["id"] = secrets.token_hex(12)
            item["status"] = "ready"
            item["verification_note"] = "AI已拆分分类"
            output.append(item)
        return output

    def _replace_trends(self, run_id: str, trends: list[dict[str, Any]]) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM trends WHERE run_id = ?", (run_id,))
            db.executemany(
                """INSERT INTO trends
                   (id, run_id, rank, topic_en, topic_zh, summary_zh, why_trending, platforms,
                    region, category, first_seen_at, engagement_signal, evidence, confidence,
                    visual_brief_en, risk_flags, status, verification_note, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(
                    item["id"], run_id, item["rank"], item["topic_en"], item["topic_zh"],
                    item["summary_zh"], item["why_trending"], json_text(item["platforms"]),
                    item["region"], item["category"], item["first_seen_at"],
                    str(item["engagement_signal"] or ""), json_text(item["evidence"]),
                    item["confidence"], item["visual_brief_en"], json_text(item["risk_flags"]),
                    item["status"], item["verification_note"], utc_now(),
                ) for item in trends],
            )

    @staticmethod
    def _flow_prompt(trend: dict[str, Any]) -> str:
        return f"""Create a realistic print-on-demand product image inspired by a current worldwide social-media trend.

Trending topic: {trend['topic_en']}
Verified context: {trend['summary_zh']}
Why it is trending: {trend['why_trending']}
Visual direction: {trend['visual_brief_en']}

Requirements:
- Render the design printed directly on the single physical product named in the visual direction. If no product is named, choose the best fit from a mug, tumbler, phone case, T-shirt, hoodie, tote bag, cushion, blanket, vehicle spare-tire cover, sticker, or poster.
- Show one main product only, fully visible and easy to inspect. Do not create a collage or show several product types.
- Make the artwork conform naturally to the product's printable area, curvature, seams, folds, and material. It must look genuinely printed, not digitally pasted on top.
- Use a clean commercial product-photography composition with a simple neutral setting. Keep the product and printed design sharp and unobstructed.
- Do not include logos, trademarks, copyrighted characters, public-figure likenesses, copied posts, watermarks, or existing artwork.
- Avoid text unless the trend cannot work without it; any text must be short, correctly spelled, and generic."""

    def _update_run(self, run_id: str, **values: Any) -> None:
        if not values:
            return
        columns = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as db:
            db.execute(f"UPDATE runs SET {columns} WHERE id = ?", (*values.values(), run_id))

    def _finish_duration(self, run_id: str, started: float) -> None:
        self._update_run(run_id, duration_ms=round((time.perf_counter() - started) * 1000), finished_at=utc_now())

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (min(200, max(1, limit)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            run = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not run:
                return None
            trends = [dict(row) for row in db.execute(
                "SELECT * FROM trends WHERE run_id = ? ORDER BY rank", (run_id,)
            ).fetchall()]
            generations = [dict(row) for row in db.execute(
                "SELECT * FROM generations WHERE run_id = ? ORDER BY created_at", (run_id,)
            ).fetchall()]
            prompt_pool = [dict(row) for row in db.execute(
                """SELECT p.*, t.topic_zh, t.category FROM prompt_pool p
                   JOIN trends t ON t.id=p.trend_id WHERE p.run_id=? ORDER BY p.created_at""",
                (run_id,),
            ).fetchall()]
        generation_map: dict[str, list[dict[str, Any]]] = {}
        for generation in generations:
            if generation.get("image_path"):
                generation["image_url"] = f"/assets/{generation['image_path']}"
            generation_map.setdefault(generation["trend_id"], []).append(generation)
        for trend in trends:
            for key in ("platforms", "evidence", "risk_flags"):
                trend[key] = json.loads(trend[key])
            trend["generations"] = generation_map.get(trend["id"], [])
        result = dict(run)
        result["trends"] = trends
        result["prompt_pool"] = prompt_pool
        result["raw_trends"] = []
        if result.get("raw_discovery"):
            try:
                result["raw_trends"] = self._normalise_candidates(
                    extract_json_object(result["raw_discovery"]), 30
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning("Run %s contains an unreadable raw discovery response", run_id)
        return result

    def delete_run(self, run_id: str) -> bool:
        if self.active_run_id == run_id:
            raise ValueError("运行中的轮次不能删除")
        with self._connect() as db:
            deleted = db.execute("DELETE FROM runs WHERE id = ?", (run_id,)).rowcount
        target = (self.assets_dir / run_id).resolve()
        if deleted and target.parent == self.assets_dir.resolve() and target.exists():
            shutil.rmtree(target)
        return bool(deleted)

    def dashboard(self) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._connect() as db:
            totals = db.execute(
                """SELECT COUNT(*), COALESCE(SUM(candidate_count),0), COALESCE(SUM(verified_count),0),
                   COALESCE(SUM(generated_count),0), COALESCE(SUM(failed_count),0), COALESCE(AVG(duration_ms),0)
                   FROM runs"""
            ).fetchone()
            today_row = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(verified_count),0), COALESCE(SUM(generated_count),0) FROM runs WHERE substr(started_at,1,10)=?",
                (today,),
            ).fetchone()
            platforms = db.execute("SELECT platforms FROM trends WHERE status != 'rejected'").fetchall()
        platform_counts: dict[str, int] = {}
        for row in platforms:
            for platform in json.loads(row["platforms"]):
                platform_counts[platform] = platform_counts.get(platform, 0) + 1
        return {
            "active_run_id": self.active_run_id,
            "running": bool(self.active_task and not self.active_task.done()),
            "today": {"runs": today_row[0], "verified": today_row[1], "generated": today_row[2]},
            "history": {
                "runs": totals[0], "candidates": totals[1], "verified": totals[2],
                "generated": totals[3], "failed": totals[4], "avg_duration_ms": round(totals[5] or 0),
            },
            "platforms": [
                {"name": name, "count": count}
                for name, count in sorted(platform_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
        }

    async def _scheduler_loop(self) -> None:
        while not self._stopping:
            try:
                config = self.get_config()
                if config.get("enabled"):
                    local = datetime.now(ZoneInfo(config["timezone"]))
                    date_key = local.date().isoformat()
                    if local.strftime("%H:%M") >= config["schedule_time"] and self._last_scheduled_date != date_key:
                        run_id = self.launch_full_pipeline(
                            trigger_type="scheduled",
                            auto_generate=bool(config.get("auto_generate")),
                        )
                        if run_id:
                            self._last_scheduled_date = date_key
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler check failed")
                await asyncio.sleep(30)

    async def _notify_run(self, run_id: str) -> None:
        config = self.get_config()
        if not config.get("notify_enabled") or not self.feishu_webhook:
            return
        run = self.get_run(run_id)
        if not run:
            return
        lines = [
            "【海外社媒热点创作任务】",
            f"轮次：{run_id}",
            f"状态：{run['status']}",
            f"候选：{run['candidate_count']} · 核验：{run['verified_count']} · 生图：{run['generated_count']}",
        ]
        for trend in run["trends"][:10]:
            if trend["status"] == "rejected":
                continue
            lines.append(f"- {trend['topic_zh']}（{', '.join(trend['platforms']) or '来源待确认'}）")
            if trend["evidence"]:
                lines.append(f"  {trend['evidence'][0]['url']}")
            if self.public_base_url and trend["generations"]:
                image = next((item for item in trend["generations"] if item.get("image_url")), None)
                if image:
                    lines.append(f"  图片：{self.public_base_url}{image['image_url']}")
        payload: dict[str, Any] = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
        if self.feishu_secret:
            timestamp = str(int(time.time()))
            signature = base64.b64encode(
                hmac.new(f"{timestamp}\n{self.feishu_secret}".encode(), digestmod=hashlib.sha256).digest()
            ).decode()
            payload.update({"timestamp": timestamp, "sign": signature})
        try:
            response = await self.http.post(self.feishu_webhook, json=payload, timeout=15)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("Feishu notification failed: %s", safe_error(exc))
