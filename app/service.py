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
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageChops, ImageDraw


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
    "generation_schedule_time": "10:00",
    "acquisition_interval_minutes": 165,
    "generation_interval_minutes": 90,
    "timezone": "Asia/Shanghai",
    "lookback_hours": 24,
    "regions": ["United States", "United Kingdom", "Europe", "Global English"],
    "platforms": ["X", "TikTok", "Instagram", "YouTube", "Reddit"],
    "candidate_count": 10,
    "images_per_trend": 5,
    "gemini_discovery_model": "gemini-pro-thinking",
    "gemini_verification_model": "gemini-flash",
    "flow_models": ["gemini-3.1-flash-image-landscape"],
    "generation_concurrency": 2,
    "auto_generate": False,
    "notify_enabled": False,
    "source_sync_enabled": False,
    "source_sync_interval_minutes": 10,
    "source_retention_days": 30,
    "source_include_hotlists": False,
}

SELLABILITY_METRICS = (
    ("shopping_intent", "购买意图", 25),
    ("social_commercial_heat", "社媒商业热度", 20),
    ("search_growth", "搜索增长潜力", 15),
    ("product_fit", "商品适配度", 15),
    ("audience_clarity", "受众清晰度", 10),
    ("lifespan", "销售窗口寿命", 10),
    ("competition_opportunity", "竞争机会", 5),
)
PRODUCT_TYPES = ("vehicle spare-tire cover", "phone case")
GEMINI_SAFE_BATCH_SIZE = 5
EMPTY_RUN_STALE_MINUTES = 10


@lru_cache(maxsize=2)
def rembg_session(model: str):
    from rembg import new_session

    return new_session(model)

SOURCE_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "in",
    "is", "it", "its", "of", "on", "or", "that", "the", "this", "to", "was", "were",
    "will", "with", "after", "amid", "new", "says", "over", "into", "latest", "live",
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
    if isinstance(exc, BaseExceptionGroup):
        details = "; ".join(safe_error(child) for child in exc.exceptions)
        message = f"{type(exc).__name__}: {details}"
    else:
        message = f"{type(exc).__name__}: {exc}"
    return re.sub(r"(?i)(authorization|api[_-]?key|bearer)\s*[:=]?\s*\S+", r"\1=***", message)[:1000]


def canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    query = [
        (key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid", "ref"}
    ]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", urlencode(query), ""))


def parse_source_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Gemini响应中没有JSON对象")
    fragment = cleaned[start : end + 1]
    try:
        parsed = json.loads(fragment, strict=False)
    except json.JSONDecodeError as strict_error:
        try:
            # Gemini occasionally emits valid JSON5 (trailing commas, bare keys,
            # single quotes) despite a JSON-only instruction. Accept it only as
            # a parser fallback; all persisted output is normalised back to JSON.
            import json5

            parsed = json5.loads(fragment)
        except Exception:
            try:
                from json_repair import repair_json

                parsed = json.loads(repair_json(fragment), strict=False)
            except Exception:
                raise strict_error
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
        trendradar_url = os.getenv("TRENDRADAR_MCP_URL", "").strip()
        self.trendradar_mcp_url = (
            trendradar_url if trendradar_url.endswith("/mcp") else f"{trendradar_url.rstrip('/')}/mcp"
        ) if trendradar_url else ""
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        self.feishu_webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        self.feishu_secret = os.getenv("FEISHU_SIGNING_SECRET", "").strip()
        self.http = httpx.AsyncClient(trust_env=False, follow_redirects=True)
        self.operation_lock = asyncio.Lock()
        self.active_task: asyncio.Task | None = None
        self.active_run_id: str | None = None
        self.sellability_backfill = {
            "status": "idle", "total_runs": 0, "completed_runs": 0,
            "current_run_id": "", "error": "", "updated_at": utc_now(),
        }
        self.source_sync_task: asyncio.Task | None = None
        self.source_sync_lock = asyncio.Lock()
        self.scheduler_task: asyncio.Task | None = None
        self._stopping = False
        self._acquisition_interval = 0
        self._generation_interval = 0
        self._acquisition_enabled = False
        self._generation_enabled = False
        self._source_sync_interval = 0
        self._source_sync_enabled = False
        self._next_acquisition_at = 0.0
        self._next_generation_at = 0.0
        self._next_source_sync_at = 0.0
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
                    source_candidate_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trends_run ON trends(run_id, rank);
                CREATE INDEX IF NOT EXISTS idx_trends_created ON trends(created_at DESC);
                CREATE TABLE IF NOT EXISTS sellability_pool (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    trend_id TEXT NOT NULL UNIQUE REFERENCES trends(id) ON DELETE CASCADE,
                    total_score INTEGER NOT NULL,
                    grade TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    target_audience TEXT NOT NULL,
                    recommended_products TEXT NOT NULL,
                    valid_window TEXT NOT NULL,
                    sales_reason TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    risk_reasons TEXT NOT NULL,
                    pattern_quota INTEGER NOT NULL,
                    products_per_pattern INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sellability_run_score
                    ON sellability_pool(run_id, total_score DESC);
                CREATE INDEX IF NOT EXISTS idx_sellability_score_created
                    ON sellability_pool(total_score DESC, created_at DESC);
                CREATE TABLE IF NOT EXISTS raw_sellability_pool (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    candidate_id TEXT NOT NULL,
                    topic_en TEXT NOT NULL,
                    topic_zh TEXT NOT NULL,
                    summary_zh TEXT NOT NULL,
                    category TEXT NOT NULL,
                    region TEXT NOT NULL,
                    total_score INTEGER NOT NULL,
                    grade TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    target_audience TEXT NOT NULL,
                    recommended_products TEXT NOT NULL,
                    valid_window TEXT NOT NULL,
                    sales_reason TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    risk_reasons TEXT NOT NULL,
                    pattern_quota INTEGER NOT NULL,
                    products_per_pattern INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, candidate_id)
                );
                CREATE INDEX IF NOT EXISTS idx_raw_sellability_run
                    ON raw_sellability_pool(run_id, candidate_id);
                CREATE INDEX IF NOT EXISTS idx_raw_sellability_score
                    ON raw_sellability_pool(total_score DESC, created_at DESC);
                CREATE TABLE IF NOT EXISTS prompt_pool (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    trend_id TEXT NOT NULL REFERENCES trends(id) ON DELETE CASCADE,
                    pattern_prompt TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready',
                    used_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_prompt_pool_run ON prompt_pool(run_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_prompt_pool_created ON prompt_pool(created_at DESC);
                CREATE TABLE IF NOT EXISTS pattern_assets (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    trend_id TEXT NOT NULL REFERENCES trends(id) ON DELETE CASCADE,
                    prompt_id TEXT REFERENCES prompt_pool(id) ON DELETE SET NULL,
                    sequence INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    image_path TEXT,
                    mime_type TEXT,
                    duration_ms INTEGER,
                    error TEXT NOT NULL DEFAULT '',
                    raw_response TEXT NOT NULL DEFAULT '',
                    has_transparency INTEGER NOT NULL DEFAULT 0,
                    background_removed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_pattern_assets_run ON pattern_assets(run_id, trend_id);
                CREATE INDEX IF NOT EXISTS idx_pattern_assets_created ON pattern_assets(created_at DESC);
                CREATE TABLE IF NOT EXISTS generations (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    trend_id TEXT NOT NULL REFERENCES trends(id) ON DELETE CASCADE,
                    pattern_asset_id TEXT REFERENCES pattern_assets(id) ON DELETE SET NULL,
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
                CREATE INDEX IF NOT EXISTS idx_generations_created ON generations(created_at DESC);
                CREATE TABLE IF NOT EXISTS source_entries (
                    id TEXT PRIMARY KEY,
                    external_id TEXT NOT NULL UNIQUE,
                    source_kind TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    title_zh TEXT NOT NULL DEFAULT '',
                    summary_zh TEXT NOT NULL DEFAULT '',
                    published_at TEXT,
                    fetched_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    raw_payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_entries_date
                    ON source_entries(published_at DESC, fetched_at DESC);
                CREATE INDEX IF NOT EXISTS idx_source_entries_source
                    ON source_entries(source_id, published_at DESC);
                CREATE INDEX IF NOT EXISTS idx_source_entries_fetched
                    ON source_entries(fetched_at DESC);
                CREATE INDEX IF NOT EXISTS idx_source_entries_display_date
                    ON source_entries(COALESCE(published_at,fetched_at) DESC);
                CREATE INDEX IF NOT EXISTS idx_source_entries_source_display_date
                    ON source_entries(source_id,COALESCE(published_at,fetched_at) DESC);
                CREATE TABLE IF NOT EXISTS source_sync_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    status TEXT NOT NULL,
                    last_attempt_at TEXT,
                    last_success_at TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    fetched_count INTEGER NOT NULL DEFAULT 0,
                    inserted_count INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            generation_columns = {row["name"] for row in db.execute("PRAGMA table_info(generations)")}
            if "prompt_id" not in generation_columns:
                db.execute("ALTER TABLE generations ADD COLUMN prompt_id TEXT")
            if "pattern_asset_id" not in generation_columns:
                db.execute("ALTER TABLE generations ADD COLUMN pattern_asset_id TEXT")
            prompt_columns = {row["name"] for row in db.execute("PRAGMA table_info(prompt_pool)")}
            if "pattern_prompt" not in prompt_columns:
                db.execute("ALTER TABLE prompt_pool ADD COLUMN pattern_prompt TEXT NOT NULL DEFAULT ''")
            trend_columns = {row["name"] for row in db.execute("PRAGMA table_info(trends)")}
            if "source_candidate_id" not in trend_columns:
                db.execute("ALTER TABLE trends ADD COLUMN source_candidate_id TEXT NOT NULL DEFAULT ''")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_trends_source_candidate ON trends(run_id, source_candidate_id)"
            )
            pattern_columns = {row["name"] for row in db.execute("PRAGMA table_info(pattern_assets)")}
            if "has_transparency" not in pattern_columns:
                db.execute("ALTER TABLE pattern_assets ADD COLUMN has_transparency INTEGER NOT NULL DEFAULT 0")
            if "background_removed" not in pattern_columns:
                db.execute("ALTER TABLE pattern_assets ADD COLUMN background_removed INTEGER NOT NULL DEFAULT 0")
            source_columns = {row["name"] for row in db.execute("PRAGMA table_info(source_entries)")}
            for name in ("title_zh", "summary_zh"):
                if name not in source_columns:
                    db.execute(f"ALTER TABLE source_entries ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            if not db.execute("SELECT 1 FROM settings WHERE id = 1").fetchone():
                db.execute(
                    "INSERT INTO settings(id, value, updated_at) VALUES(1, ?, ?)",
                    (json_text(DEFAULT_CONFIG), utc_now()),
                )
            db.execute(
                "INSERT OR IGNORE INTO source_sync_state(id,status) VALUES(1,'idle')"
            )
            for row in db.execute(
                "SELECT id,raw_discovery FROM runs WHERE raw_discovery!='' AND candidate_count=0"
            ).fetchall():
                try:
                    count = len(self._normalise_candidates(
                        extract_json_object(row["raw_discovery"]), None
                    ))
                except (TypeError, ValueError, json.JSONDecodeError):
                    count = 0
                if count:
                    db.execute(
                        "UPDATE runs SET candidate_count=? WHERE id=?", (count, row["id"])
                    )
        self._cleanup_empty_runs_sync(
            datetime.now(timezone.utc) - timedelta(minutes=EMPTY_RUN_STALE_MINUTES)
        )

    async def start(self) -> None:
        if not self.scheduler_task or self.scheduler_task.done():
            self.scheduler_task = asyncio.create_task(self._scheduler_loop(), name="trend-daily-scheduler")

    async def stop(self) -> None:
        self._stopping = True
        for task in (self.scheduler_task, self.active_task, self.source_sync_task):
            if task and not task.done():
                task.cancel()
        await self.http.aclose()

    def get_config(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE id = 1").fetchone()
        stored = json.loads(row["value"])
        config = {**copy.deepcopy(DEFAULT_CONFIG), **stored}
        if "generation_schedule_time" not in stored:
            config["images_per_trend"] = max(5, int(config["images_per_trend"]))
        config.pop("final_count", None)
        return config

    def save_config(self, values: dict[str, Any]) -> dict[str, Any]:
        config = {**self.get_config(), **values}
        ZoneInfo(str(config["timezone"]))
        config["lookback_hours"] = min(168, max(1, int(config["lookback_hours"])))
        config["candidate_count"] = min(30, max(1, int(config["candidate_count"])))
        config["images_per_trend"] = min(30, max(1, int(config["images_per_trend"])))
        config["acquisition_interval_minutes"] = min(
            1440, max(15, int(config["acquisition_interval_minutes"]))
        )
        config["generation_interval_minutes"] = min(
            1440, max(15, int(config["generation_interval_minutes"]))
        )
        config["generation_concurrency"] = min(5, max(1, int(config["generation_concurrency"])))
        config["source_sync_interval_minutes"] = min(
            1440, max(5, int(config["source_sync_interval_minutes"]))
        )
        config["source_retention_days"] = min(
            365, max(1, int(config["source_retention_days"]))
        )
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
            "trendradar_mcp_url": self.trendradar_mcp_url,
            "trendradar_configured": bool(self.trendradar_mcp_url),
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

        async def check_trendradar() -> dict[str, Any]:
            if not self.trendradar_mcp_url:
                return {"ok": False, "configured": False, "status": 0}
            started = time.perf_counter()
            try:
                result = await self._call_mcp_tool("get_rss_feeds_status", {})
                return {
                    "ok": bool(result.get("success", True)),
                    "configured": True,
                    "status": 200,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                }
            except Exception as exc:
                return {"ok": False, "configured": True, "status": 0, "error": safe_error(exc)}

        gemini, flow, trendradar = await asyncio.gather(
            check(self.gemini_base_url, self.gemini_api_key),
            check(self.flow_base_url, self.flow_api_key),
            check_trendradar(),
        )
        return {"gemini": gemini, "flow": flow, "trendradar": trendradar}

    async def _call_mcp_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.trendradar_mcp_url:
            raise ValueError("TRENDRADAR_MCP_URL未配置")
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        timeout_seconds = max(30, int(os.getenv("MCP_TIMEOUT_SECONDS", "300")))
        try:
            async with asyncio.timeout(timeout_seconds):
                async with streamable_http_client(self.trendradar_mcp_url) as streams:
                    read_stream, write_stream = streams[:2]
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        response = await session.call_tool(name, arguments=arguments)
        except Exception as exc:
            raise RuntimeError(
                f"TrendRadar MCP {name} 调用失败: {safe_error(exc)}"
            ) from exc
        if response.isError:
            raise RuntimeError(f"TrendRadar MCP工具调用失败: {name}")
        text = "\n".join(
            str(block.text) for block in response.content if getattr(block, "text", None)
        ).strip()
        if not text:
            return {}
        payload = json.loads(text)
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError(f"TrendRadar MCP工具{name}返回格式错误")
        return payload

    def launch_source_sync(self) -> bool:
        if not self.trendradar_mcp_url:
            raise ValueError("TRENDRADAR_MCP_URL未配置")
        if self.source_sync_task and not self.source_sync_task.done():
            return False
        self.source_sync_task = asyncio.create_task(
            self._source_sync_worker(), name="trendradar-source-sync"
        )
        return True

    async def _source_sync_worker(self) -> None:
        try:
            await self.sync_source_entries()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("TrendRadar source sync failed")

    def _update_source_sync_state(self, **values: Any) -> None:
        if not values:
            return
        columns = ", ".join(f"{key}=?" for key in values)
        with self._connect() as db:
            db.execute(
                f"UPDATE source_sync_state SET {columns} WHERE id=1",
                tuple(values.values()),
            )

    @staticmethod
    def _source_items(kind: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        items = payload.get("data") if isinstance(payload.get("data"), list) else []
        normalised = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            if not title:
                continue
            if kind == "rss":
                source_id = str(raw.get("feed_id") or "unknown")[:200]
                source_name = str(raw.get("feed_name") or source_id)[:300]
                source_time = parse_source_time(raw.get("published_at") or raw.get("date"))
                summary = str(raw.get("summary") or "")[:10000]
                platform = "RSS"
            else:
                source_id = str(raw.get("platform") or "unknown")[:200]
                source_name = str(raw.get("platform_name") or source_id)[:300]
                source_time = parse_source_time(raw.get("timestamp"))
                summary = ""
                platform = source_name
            published_at = source_time.isoformat() if source_time else None
            url = canonical_url(str(raw.get("url") or raw.get("mobileUrl") or ""))
            identity = url or f"{source_id}|{title.casefold()}|{published_at}"
            external_id = hashlib.sha256(f"{kind}|{identity}".encode()).hexdigest()
            content_hash = hashlib.sha256(f"{title.casefold()}|{summary}".encode()).hexdigest()
            normalised.append({
                "id": secrets.token_hex(12),
                "external_id": external_id,
                "source_kind": kind,
                "source_id": source_id,
                "source_name": source_name,
                "platform": platform,
                "title": title[:1000],
                "url": url[:2000],
                "author": str(raw.get("author") or "")[:500],
                "summary": summary,
                "published_at": published_at,
                "fetched_at": utc_now(),
                "content_hash": content_hash,
                "raw_payload": json_text(raw),
            })
        return normalised

    async def sync_source_entries(self) -> dict[str, int]:
        if not self.trendradar_mcp_url:
            raise ValueError("TRENDRADAR_MCP_URL未配置")
        async with self.source_sync_lock:
            config = self.get_config()
            attempted_at = utc_now()
            self._update_source_sync_state(
                status="running", last_attempt_at=attempted_at, error=""
            )
            try:
                days = min(30, max(1, (int(config["lookback_hours"]) + 23) // 24))
                rss = await self._call_mcp_tool(
                    "get_latest_rss",
                    {"days": days, "limit": 500, "include_summary": True},
                )
                if rss.get("success") is False:
                    raise RuntimeError(str(rss.get("error") or "TrendRadar RSS查询失败"))
                entries = self._source_items("rss", rss)
                if config.get("source_include_hotlists"):
                    hotlists = await self._call_mcp_tool(
                        "get_latest_news", {"limit": 1000, "include_url": True}
                    )
                    if hotlists.get("success") is not False:
                        entries.extend(self._source_items("hotlist", hotlists))
                inserted = 0
                cutoff = (datetime.now(timezone.utc) - timedelta(
                    days=int(config["source_retention_days"])
                )).isoformat()
                with self._connect() as db:
                    for entry in entries:
                        inserted += max(0, db.execute(
                            """INSERT OR IGNORE INTO source_entries
                               (id,external_id,source_kind,source_id,source_name,platform,title,url,
                                author,summary,published_at,fetched_at,content_hash,raw_payload)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            tuple(entry[key] for key in (
                                "id", "external_id", "source_kind", "source_id", "source_name",
                                "platform", "title", "url", "author", "summary", "published_at",
                                "fetched_at", "content_hash", "raw_payload",
                            )),
                        ).rowcount)
                    db.execute("DELETE FROM source_entries WHERE fetched_at < ?", (cutoff,))
                self._update_source_sync_state(
                    status="succeeded",
                    last_success_at=utc_now(),
                    error="",
                    fetched_count=len(entries),
                    inserted_count=inserted,
                )
                return {"fetched": len(entries), "inserted": inserted}
            except Exception as exc:
                self._update_source_sync_state(status="failed", error=safe_error(exc))
                raise

    def source_state(self) -> dict[str, Any]:
        with self._connect() as db:
            sync = db.execute("SELECT * FROM source_sync_state WHERE id=1").fetchone()
            rows = db.execute(
                """SELECT source_id,source_name,source_kind,COUNT(*) AS item_count,
                          MAX(COALESCE(published_at,fetched_at)) AS last_item_at,
                          MAX(fetched_at) AS last_fetched_at
                   FROM source_entries GROUP BY source_id,source_name,source_kind
                   ORDER BY last_item_at DESC"""
            ).fetchall()
            total = db.execute("SELECT COUNT(*) FROM source_entries").fetchone()[0]
            recent = db.execute(
                "SELECT COUNT(*) FROM source_entries WHERE fetched_at>=?",
                ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),),
            ).fetchone()[0]
        return {
            "configured": bool(self.trendradar_mcp_url),
            "syncing": bool(self.source_sync_task and not self.source_sync_task.done()),
            "sync": dict(sync) if sync else {"status": "idle"},
            "sources": [dict(row) for row in rows],
            "total_entries": total,
            "recent_entries": recent,
        }

    def list_source_entries(
        self,
        limit: int = 200,
        *,
        source_id: str | None = None,
        lookback_hours: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = []
        values: list[Any] = []
        if source_id:
            clauses.append("source_id=?")
            values.append(source_id)
        if lookback_hours:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
            clauses.append("fetched_at>=?")
            values.append(cutoff)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.extend((min(5000, max(1, limit)), max(0, offset)))
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT id,external_id,source_kind,source_id,source_name,platform,title,url,
                           author,summary,title_zh,summary_zh,published_at,fetched_at,content_hash
                    FROM source_entries {where}
                    ORDER BY COALESCE(published_at,fetched_at) DESC LIMIT ? OFFSET ?""",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def count_source_entries(self, *, source_id: str | None = None) -> int:
        with self._connect() as db:
            if source_id:
                return int(db.execute(
                    "SELECT COUNT(*) FROM source_entries WHERE source_id=?", (source_id,)
                ).fetchone()[0])
            return int(db.execute("SELECT COUNT(*) FROM source_entries").fetchone()[0])

    def get_source_entry(self, entry_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT id,external_id,source_kind,source_id,source_name,platform,title,url,
                          author,summary,title_zh,summary_zh,published_at,fetched_at,content_hash
                   FROM source_entries WHERE id=?""",
                (entry_id,),
            ).fetchone()
        return dict(row) if row else None

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
            raise ValueError("可用图案池已存在；重新提取会破坏已有提示词和图片")
        return self._launch(run_id, self._run_stage(run_id, "classification"), f"classify-{run_id}")

    def launch_prompt_pool(self, run_id: str, count: int | None = None) -> bool:
        with self._connect() as db:
            exists = db.execute(
                """SELECT 1 FROM trends t
                   WHERE t.run_id=? AND t.status!='rejected'
                     AND NOT EXISTS (SELECT 1 FROM prompt_pool p WHERE p.trend_id=t.id)
                   LIMIT 1""",
                (run_id,),
            ).fetchone()
        if not exists:
            raise ValueError("没有待生成提示词的可用图案")
        return self._launch(run_id, self._run_stage(run_id, "prompt_pool", count), f"prompts-{run_id}")

    def launch_sellability_scoring(self, run_id: str) -> bool:
        with self._connect() as db:
            exists = db.execute(
                """SELECT 1 FROM runs r WHERE r.id=? AND r.raw_discovery!=''
                   AND r.candidate_count>
                       (SELECT COUNT(*) FROM raw_sellability_pool s WHERE s.run_id=r.id)""",
                (run_id,),
            ).fetchone()
        if not exists:
            raise ValueError("该任务的热点已经全部完成可卖分计算")
        return self._launch(
            run_id, self._run_stage(run_id, "sellability"), f"sellability-{run_id}"
        )

    def launch_sellability_backfill(self) -> bool:
        if self.active_task and not self.active_task.done():
            return False
        with self._connect() as db:
            run_ids = [row["id"] for row in db.execute(
                """SELECT r.id FROM runs r
                   WHERE r.raw_discovery!='' AND r.candidate_count>
                         (SELECT COUNT(*) FROM raw_sellability_pool s WHERE s.run_id=r.id)
                   ORDER BY r.started_at DESC"""
            ).fetchall()]
        if not run_ids:
            raise ValueError("历史热点已经全部完成可卖分计算")
        self.sellability_backfill = {
            "status": "pending", "total_runs": len(run_ids), "completed_runs": 0,
            "current_run_id": run_ids[0], "error": "", "updated_at": utc_now(),
        }
        return self._launch(
            run_ids[0], self._backfill_sellability(run_ids), "sellability-backfill"
        )

    async def _backfill_sellability(self, run_ids: list[str]) -> None:
        async with self.operation_lock:
            try:
                self.sellability_backfill["status"] = "running"
                self.sellability_backfill["updated_at"] = utc_now()
                for index, run_id in enumerate(run_ids, 1):
                    self.sellability_backfill["current_run_id"] = run_id
                    self.sellability_backfill["updated_at"] = utc_now()
                    with self._connect() as db:
                        previous = db.execute(
                            "SELECT status,stage,finished_at FROM runs WHERE id=?", (run_id,)
                        ).fetchone()
                    await self._score_sellability_pool(run_id, self.get_config())
                    if previous and previous["status"] in {"completed", "partial", "failed"}:
                        self._update_run(
                            run_id,
                            status=previous["status"],
                            stage=previous["stage"],
                            finished_at=previous["finished_at"],
                        )
                    self.sellability_backfill["completed_runs"] = index
                    self.sellability_backfill["updated_at"] = utc_now()
                self.sellability_backfill["status"] = "succeeded"
                self.sellability_backfill["current_run_id"] = ""
                self.sellability_backfill["updated_at"] = utc_now()
            except Exception as exc:
                self.sellability_backfill["status"] = "failed"
                self.sellability_backfill["error"] = safe_error(exc)
                self.sellability_backfill["updated_at"] = utc_now()
                logger.exception("Sellability backfill failed")
            finally:
                self.active_run_id = None

    def sellability_state(self) -> dict[str, Any]:
        with self._connect() as db:
            totals = db.execute(
                """SELECT COALESCE(SUM(candidate_count),0),
                          (SELECT COUNT(*) FROM raw_sellability_pool)
                   FROM runs WHERE raw_discovery!=''"""
            ).fetchone()
        total = int(totals[0] or 0)
        scored = int(totals[1] or 0)
        return {
            **self.sellability_backfill,
            "total_directions": total,
            "scored_directions": scored,
            "pending_directions": max(0, total - scored),
            "total_hotspots": total,
            "scored_hotspots": scored,
            "pending_hotspots": max(0, total - scored),
        }

    def launch_generation(self, run_id: str, count: int | None = None) -> bool:
        with self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM prompt_pool WHERE run_id=? AND status='ready' LIMIT 1", (run_id,)
            ).fetchone()
        if not exists:
            raise ValueError("提示词池为空，请先生成提示词池")
        return self._launch(run_id, self._run_stage(run_id, "generation", count), f"generate-{run_id}")

    def launch_pattern_generation(self, run_id: str, count: int | None = None) -> bool:
        with self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM prompt_pool WHERE run_id=? AND status='ready' LIMIT 1", (run_id,)
            ).fetchone()
        if not exists:
            raise ValueError("提示词池为空，请先生成提示词池")
        return self._launch(
            run_id,
            self._run_stage(run_id, "pattern_generation", count),
            f"patterns-{run_id}",
        )

    def launch_product_generation(self, run_id: str, count: int | None = None) -> bool:
        with self._connect() as db:
            exists = db.execute(
                """SELECT 1 FROM pattern_assets a
                   WHERE a.run_id=? AND a.status='success'
                     AND NOT EXISTS (SELECT 1 FROM generations g
                                     WHERE g.pattern_asset_id=a.id AND g.status='success')
                   LIMIT 1""",
                (run_id,),
            ).fetchone()
        if not exists:
            raise ValueError("没有待生成产品图的图案，请先生成图案")
        return self._launch(
            run_id,
            self._run_stage(run_id, "product_generation", count),
            f"products-{run_id}",
        )

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
                if stage == "sellability":
                    await self._score_sellability_pool(run_id, config)
                if stage in {"prompt_pool", "full"}:
                    await self._create_prompt_pool(run_id, config, count)
                if stage == "pattern_generation":
                    await self._generate_pattern_assets(run_id, config, count)
                if stage == "product_generation":
                    await self._generate_products_from_patterns(run_id, config, count)
                if stage == "generation" or (stage == "full" and auto_generate):
                    await self._generate_from_prompt_pool(run_id, config, count)
                self._finish_duration(run_id, started)
                if stage not in {"generation", "product_generation"} and not (stage == "full" and auto_generate):
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

    @staticmethod
    def _source_title_tokens(title: str) -> set[str]:
        aliases = {"quake": "earthquake", "quakes": "earthquake", "u.s": "us"}
        tokens = []
        for token in re.findall(r"[a-z0-9]+", title.casefold()):
            token = aliases.get(token, token)
            if len(token) > 1 and token not in SOURCE_STOP_WORDS:
                tokens.append(token)
        return set(tokens)

    @classmethod
    def _cluster_source_entries(cls, entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        clusters: list[dict[str, Any]] = []
        # ponytail: O(n^2) is bounded by the 500-entry MCP page; use embeddings only beyond that ceiling.
        for entry in entries:
            tokens = cls._source_title_tokens(entry["title"])
            match = None
            for cluster in clusters:
                shared = len(tokens & cluster["tokens"])
                smaller = min(len(tokens), len(cluster["tokens"]))
                if smaller and shared >= 3 and shared / smaller >= 0.6:
                    match = cluster
                    break
            if match:
                match["entries"].append(entry)
            else:
                clusters.append({"tokens": tokens, "entries": [entry]})
        return [
            item["entries"] for item in sorted(
                clusters,
                key=lambda item: (
                    len({entry["source_id"] for entry in item["entries"]}),
                    str(item["entries"][0].get("published_at") or item["entries"][0]["fetched_at"]),
                ),
                reverse=True,
            )
        ]

    @staticmethod
    def _source_cluster_prompt(clusters: list[dict[str, Any]]) -> str:
        return f"""You annotate clusters of overseas-media source entries for an inclusive social-trend pool.

Return exactly one item for every cluster_id. Translate the event/topic title into Chinese, write a concise factual Chinese summary, explain why the cluster matters now, assign a broad category and region, and record risk flags. Do not omit politics, public figures, brands, disasters, controversy, violence, adult discussion, or topics with low visual value. Do not select products or write image prompts. Never invent facts or evidence. Return strict JSON only.

Clusters:
{json.dumps(clusters, ensure_ascii=False)}

Schema:
{{"trends":[{{"cluster_id":"cluster-1","topic_en":"factual topic","topic_zh":"中文标题","summary_zh":"事实摘要","why_trending":"传播原因","region":"Global","category":"news","risk_flags":[]}}]}}"""

    async def _raw_trends_from_source_entries(
        self, entries: list[dict[str, Any]], config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        clusters = self._cluster_source_entries(entries)
        annotated: dict[str, dict[str, Any]] = {}
        batch_size = min(GEMINI_SAFE_BATCH_SIZE, int(config["candidate_count"]))
        for offset in range(0, len(clusters), batch_size):
            compact = []
            for index, cluster in enumerate(clusters[offset:offset + batch_size], offset + 1):
                compact.append({
                    "cluster_id": f"cluster-{index}",
                    "entries": [{
                        "title": entry["title"],
                        "source": entry["source_name"],
                        "published_at": entry["published_at"],
                        "summary": entry["summary"][:1000],
                    } for entry in cluster[:20]],
                })
            try:
                response = await self._call_gemini(
                    self._source_cluster_prompt(compact),
                    config["gemini_discovery_model"],
                    attempts=2,
                )
                payload = extract_json_object(response)
                for item in payload.get("trends", []):
                    if isinstance(item, dict) and item.get("cluster_id"):
                        annotated[str(item["cluster_id"])] = item
            except Exception as exc:
                logger.warning("Source cluster annotation batch failed: %s", safe_error(exc))

        raw_trends = []
        translations = []
        for index, cluster in enumerate(clusters, 1):
            note = annotated.get(f"cluster-{index}", {})
            representative = cluster[0]
            source_names = list(dict.fromkeys(entry["source_name"] for entry in cluster))
            source_count = len(set(entry["source_id"] for entry in cluster))
            published = [parse_source_time(entry["published_at"]) for entry in cluster]
            first_seen = min((item for item in published if item), default=None)
            evidence = [{
                "source_type": entry["source_kind"],
                "platform": entry["source_name"],
                "title": entry["title"],
                "url": entry["url"],
                "published_at": entry["published_at"],
            } for entry in cluster if entry["url"]]
            title_zh = str(note.get("topic_zh") or "").strip()
            summary_zh = str(note.get("summary_zh") or "").strip()
            if title_zh or summary_zh:
                translations.extend((title_zh, summary_zh, entry["id"]) for entry in cluster)
            raw_trends.append({
                "topic_en": str(note.get("topic_en") or representative["title"]),
                "topic_zh": str(note.get("topic_zh") or representative["title"]),
                "summary_zh": str(note.get("summary_zh") or representative["summary"] or representative["title"]),
                "why_trending": str(note.get("why_trending") or f"由{source_count}个独立外媒来源在当前窗口内报道"),
                "platforms": source_names,
                "region": str(note.get("region") or "Global"),
                "category": str(note.get("category") or "news"),
                "first_seen_at": first_seen.isoformat() if first_seen else representative["published_at"],
                "engagement_signal": f"{len(cluster)} entries from {source_count} sources",
                "evidence": evidence,
                "confidence": min(0.98, 0.6 + max(0, source_count - 1) * 0.08),
                "visual_brief_en": "",
                "risk_flags": note.get("risk_flags") if isinstance(note.get("risk_flags"), list) else [],
            })
        if translations:
            with self._connect() as db:
                db.executemany(
                    "UPDATE source_entries SET title_zh=?, summary_zh=? WHERE id=?",
                    translations,
                )
        return self._normalise_candidates({"trends": raw_trends}, None)

    async def _sync_sources_for_acquisition(self) -> None:
        """Reuse a just-finished sync; never issue two expensive RSS pulls back-to-back."""
        if self.source_sync_task and not self.source_sync_task.done():
            timeout_seconds = max(30, int(os.getenv("MCP_TIMEOUT_SECONDS", "300")))
            await asyncio.wait_for(asyncio.shield(self.source_sync_task), timeout_seconds + 10)
            with self._connect() as db:
                state = db.execute(
                    "SELECT status,error FROM source_sync_state WHERE id=1"
                ).fetchone()
            if state and state["status"] == "failed":
                raise RuntimeError(f"TrendRadar来源同步失败: {state['error'] or '未知错误'}")
            return
        with self._connect() as db:
            state = db.execute(
                "SELECT status,last_success_at FROM source_sync_state WHERE id=1"
            ).fetchone()
        recent_success = parse_source_time(state["last_success_at"]) if state and state["last_success_at"] else None
        if recent_success and recent_success.tzinfo and (
            datetime.now(timezone.utc) - recent_success
        ).total_seconds() < 600:
            return
        await self.sync_source_entries()

    async def _acquire_raw_trends(self, run_id: str, config: dict[str, Any]) -> list[dict[str, Any]]:
        self._update_run(run_id, status="running", stage="acquisition", error="")
        if self.trendradar_mcp_url:
            await self._sync_sources_for_acquisition()
            entries = self.list_source_entries(
                5000, lookback_hours=int(config["lookback_hours"])
            )
            if not entries:
                raise ValueError("TrendRadar来源条目池为空，请先配置并运行外媒RSS采集")
            candidates = await self._raw_trends_from_source_entries(entries, config)
            discovery_text = json_text({"trends": candidates})
        else:
            discovery_text = await self._call_gemini(
                self._discovery_prompt(config), config["gemini_discovery_model"], attempts=3
            )
            candidates = self._normalise_candidates(
                extract_json_object(discovery_text), config["candidate_count"]
            )
        if not candidates:
            raise ValueError("没有形成可解析的原始热点")
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
        candidates = self._normalise_candidates(extract_json_object(row["raw_discovery"]), None)
        self._update_run(run_id, status="running", stage="classification", error="")
        raw_responses = []
        trends = []
        batch_size = min(GEMINI_SAFE_BATCH_SIZE, int(config["candidate_count"]))
        for offset in range(0, len(candidates), batch_size):
            batch = candidates[offset:offset + batch_size]
            verification_text = await self._call_gemini(
                self._classification_prompt(config, batch),
                config["gemini_verification_model"],
                attempts=2,
            )
            raw_responses.append(verification_text)
            payload = extract_json_object(verification_text)
            if not isinstance(payload.get("classified_trends"), list):
                raise ValueError("Gemini没有返回可解析的可用图案池")
            trends.extend(self._normalise_candidates(
                {"trends": payload["classified_trends"]}, None
            ))
        for rank, trend in enumerate(trends, 1):
            trend["rank"] = rank
        trends = self._trend_pool_entries(trends)
        self._replace_trends(run_id, trends)
        usable = [item for item in trends if item["status"] == "ready"]
        self._update_run(
            run_id,
            raw_verification="\n".join(raw_responses),
            verified_count=len(usable),
            status="trend_pool_ready" if usable else "failed",
            stage="trend_pool" if usable else "finished",
            error="" if usable else "可用图案池为空",
        )
        return usable

    @staticmethod
    def _sellability_prompt(trends: list[dict[str, Any]]) -> str:
        compact = [{
            "trend_id": item["id"],
            "topic_en": item["topic_en"],
            "topic_zh": item["topic_zh"],
            "summary_zh": item["summary_zh"],
            "why_trending": item["why_trending"],
            "category": item["category"],
            "region": item["region"],
            "engagement_signal": item["engagement_signal"],
            "confidence": item["confidence"],
            "risk_flags": json.loads(item["risk_flags"]) if isinstance(item["risk_flags"], str) else item["risk_flags"],
            "visual_brief_en": item["visual_brief_en"],
        } for item in trends]
        maxima = {key: maximum for key, _label, maximum in SELLABILITY_METRICS}
        return f"""Estimate the print-on-demand sellability of every supplied social trend or artwork direction for the United States market. This is decision support based only on the supplied trend signals, not verified marketplace sales data. Never omit a trend and never reject it because its score is low.

Score every metric as an integer from zero through its stated maximum and give a concrete Chinese judgement explaining that score. Recommend practical printable products, a target audience, expected selling window, concise sales reason, and legal/sensitivity risk. Genericize trademarks, teams, public figures, and protected characters. Return strict JSON only and preserve every trend_id exactly.

Metric maxima:
{json.dumps(maxima, ensure_ascii=False)}

Judgement rubric:
- shopping_intent (25): identity expression, commemoration, gifting, collectability, or an immediate reason to buy; explain the concrete purchase motive.
- social_commercial_heat (20): independent-source coverage, discussion velocity, engagement signal, and short-term shareability; explain which supplied signals support the score.
- search_growth (15): freshness, recognizable search terms, and room for continued attention; do not invent search-volume data.
- product_fit (15): whether a recognizable, printable visual can read clearly on mugs, apparel, phone cases, or similar products without protected IP.
- audience_clarity (10): whether a specific interest group, community, buyer identity, or use occasion can be named.
- lifespan (10): distinguish one-day news, short-lived discussion, recurring/seasonal events, and evergreen interests; explain the expected window.
- competition_opportunity (5): estimate differentiation and saturation only from the supplied topic; explicitly note that live competitor listings are unavailable.

For each metric, use the same percentage bands against that metric's maximum: 0–20% no meaningful signal, 21–40% weak, 41–60% moderate, 61–80% strong, 81–100% very strong. Every judgement must cite concrete facts or limitations from the supplied trend and explain why they justify that score. Never state invented sales, search-volume, listing-count, or conversion figures.

Trends:
{json.dumps(compact, ensure_ascii=False)}

Schema:
{{"scores":[{{"trend_id":"id","metrics":{{"shopping_intent":{{"score":0,"judgement":"中文判断"}},"social_commercial_heat":{{"score":0,"judgement":"中文判断"}},"search_growth":{{"score":0,"judgement":"中文判断"}},"product_fit":{{"score":0,"judgement":"中文判断"}},"audience_clarity":{{"score":0,"judgement":"中文判断"}},"lifespan":{{"score":0,"judgement":"中文判断"}},"competition_opportunity":{{"score":0,"judgement":"中文判断"}}}},"target_audience":"目标人群","recommended_products":["T-shirt"],"valid_window":"建议销售窗口","sales_reason":"为什么可能卖得动","risk_level":"low|medium|high","risk_reasons":["风险与规避方式"]}}]}}"""

    @staticmethod
    def _normalise_sellability_item(raw: dict[str, Any] | None) -> dict[str, Any]:
        source = raw if isinstance(raw, dict) else {}
        raw_metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
        metrics = []
        for key, label, maximum in SELLABILITY_METRICS:
            value = raw_metrics.get(key)
            value = value if isinstance(value, dict) else {}
            try:
                score = int(round(float(value.get("score", 0))))
            except (TypeError, ValueError):
                score = 0
            score = min(maximum, max(0, score))
            metrics.append({
                "key": key,
                "label": label,
                "score": score,
                "max_score": maximum,
                "judgement": str(value.get("judgement") or "模型未提供判断，按保守值处理")[:1000],
            })
        total = sum(item["score"] for item in metrics)
        if total >= 80:
            grade, pattern_quota, products_per_pattern = "A", 3, 2
        elif total >= 65:
            grade, pattern_quota, products_per_pattern = "B", 1, 2
        elif total >= 60:
            grade, pattern_quota, products_per_pattern = "C", 1, 2
        else:
            grade, pattern_quota, products_per_pattern = "D", 1, 1
        products = []
        for product in string_list(source.get("recommended_products"), limit=8, item_limit=100):
            value = product.casefold()
            if "spare" in value or "备胎" in product:
                choice = PRODUCT_TYPES[0]
            elif "phone" in value or "mobile" in value or "手机" in product:
                choice = PRODUCT_TYPES[1]
            else:
                continue
            if choice not in products:
                products.append(choice)
        return {
            "total_score": total,
            "grade": grade,
            "metrics": metrics,
            "target_audience": str(source.get("target_audience") or "需要进一步验证的美国泛兴趣人群")[:1000],
            "recommended_products": products or list(PRODUCT_TYPES),
            "valid_window": str(source.get("valid_window") or "短期测试，依据点击与收藏数据复核")[:500],
            "sales_reason": str(source.get("sales_reason") or "信息不足，建议以小样测试验证")[:1500],
            "risk_level": str(source.get("risk_level") or "medium").lower()[:20],
            "risk_reasons": string_list(source.get("risk_reasons"), limit=20, item_limit=500),
            "pattern_quota": pattern_quota,
            "products_per_pattern": products_per_pattern,
        }

    @classmethod
    def _fallback_sellability(cls) -> dict[str, Any]:
        return cls._normalise_sellability_item({
            "metrics": {
                key: {"score": maximum // 2, "judgement": "AI评分缺失，使用保守中位估算；上架前需人工验证"}
                for key, _label, maximum in SELLABILITY_METRICS
            },
            "risk_level": "medium",
            "risk_reasons": ["缺少完整 AI 商业判断，先以最小生成配额测试"],
        })

    async def _score_raw_sellability_pool(
        self, run_id: str, config: dict[str, Any]
    ) -> list[str]:
        with self._connect() as db:
            row = db.execute("SELECT raw_discovery FROM runs WHERE id=?", (run_id,)).fetchone()
            existing = {
                value["candidate_id"] for value in db.execute(
                    "SELECT candidate_id FROM raw_sellability_pool WHERE run_id=?", (run_id,)
                ).fetchall()
            }
        if not row or not row["raw_discovery"]:
            return []
        candidates = self._normalise_candidates(
            extract_json_object(row["raw_discovery"]), None
        )
        missing = [item for item in candidates if item["candidate_id"] not in existing]
        if not missing:
            return []
        supplied: dict[str, dict[str, Any]] = {}
        batch_size = min(GEMINI_SAFE_BATCH_SIZE, int(config["candidate_count"]))
        for offset in range(0, len(missing), batch_size):
            batch = [{**item, "id": item["candidate_id"]} for item in missing[offset:offset + batch_size]]
            try:
                response = await self._call_gemini(
                    self._sellability_prompt(batch),
                    config["gemini_verification_model"],
                    attempts=2,
                )
                payload = extract_json_object(response)
                scores = payload.get("scores") if isinstance(payload.get("scores"), list) else []
                supplied.update({
                    str(item.get("trend_id")): item
                    for item in scores if isinstance(item, dict) and item.get("trend_id")
                })
            except Exception as exc:
                logger.warning("Raw sellability scoring batch failed: %s", safe_error(exc))
        ids = []
        with self._connect() as db:
            for candidate in missing:
                score = (
                    self._normalise_sellability_item(supplied[candidate["candidate_id"]])
                    if candidate["candidate_id"] in supplied else self._fallback_sellability()
                )
                item_id = secrets.token_hex(12)
                db.execute(
                    """INSERT OR REPLACE INTO raw_sellability_pool
                       (id,run_id,candidate_id,topic_en,topic_zh,summary_zh,category,region,
                        total_score,grade,metrics,target_audience,recommended_products,
                        valid_window,sales_reason,risk_level,risk_reasons,pattern_quota,
                        products_per_pattern,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (item_id, run_id, candidate["candidate_id"], candidate["topic_en"],
                     candidate["topic_zh"], candidate["summary_zh"], candidate["category"],
                     candidate["region"], score["total_score"], score["grade"],
                     json_text(score["metrics"]), score["target_audience"],
                     json_text(score["recommended_products"]), score["valid_window"],
                     score["sales_reason"], score["risk_level"], json_text(score["risk_reasons"]),
                     score["pattern_quota"], score["products_per_pattern"], utc_now()),
                )
                ids.append(item_id)
        return ids

    def _copy_raw_sellability_to_trends(self, run_id: str) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT t.id AS trend_id,r.* FROM trends t
                   JOIN raw_sellability_pool r
                     ON r.run_id=t.run_id AND r.candidate_id=t.source_candidate_id
                   WHERE t.run_id=? AND NOT EXISTS (
                     SELECT 1 FROM sellability_pool s WHERE s.trend_id=t.id
                   )""",
                (run_id,),
            ).fetchall()
            ids = []
            for source in rows:
                item_id = secrets.token_hex(12)
                db.execute(
                    """INSERT INTO sellability_pool
                       (id,run_id,trend_id,total_score,grade,metrics,target_audience,
                        recommended_products,valid_window,sales_reason,risk_level,risk_reasons,
                        pattern_quota,products_per_pattern,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (item_id, run_id, source["trend_id"], source["total_score"], source["grade"],
                     source["metrics"], source["target_audience"], source["recommended_products"],
                     source["valid_window"], source["sales_reason"], source["risk_level"],
                     source["risk_reasons"], source["pattern_quota"],
                     source["products_per_pattern"], utc_now()),
                )
                ids.append(item_id)
        return ids

    async def _score_sellability_pool(
        self, run_id: str, config: dict[str, Any]
    ) -> list[str]:
        self._update_run(run_id, status="running", stage="sellability_scoring", error="")
        raw_ids = await self._score_raw_sellability_pool(run_id, config)
        copied_ids = self._copy_raw_sellability_to_trends(run_id)
        with self._connect() as db:
            trends = [dict(row) for row in db.execute(
                """SELECT t.* FROM trends t WHERE t.run_id=? AND t.status!='rejected'
                   AND NOT EXISTS (SELECT 1 FROM sellability_pool s WHERE s.trend_id=t.id)
                   ORDER BY t.rank""",
                (run_id,),
            ).fetchall()]
        if not trends:
            if raw_ids or copied_ids:
                self._update_run(run_id, status="sellability_pool_ready", stage="sellability_pool")
                return raw_ids + copied_ids
            raise ValueError("该任务的热点已经全部完成可卖分计算")
        supplied: dict[str, dict[str, Any]] = {}
        batch_size = min(GEMINI_SAFE_BATCH_SIZE, int(config["candidate_count"]))
        for offset in range(0, len(trends), batch_size):
            batch = trends[offset:offset + batch_size]
            try:
                response = await self._call_gemini(
                    self._sellability_prompt(batch),
                    config["gemini_verification_model"],
                    attempts=2,
                )
                payload = extract_json_object(response)
                scores = payload.get("scores") if isinstance(payload.get("scores"), list) else []
                supplied.update({
                    str(item.get("trend_id")): item
                    for item in scores if isinstance(item, dict) and item.get("trend_id")
                })
            except Exception as exc:
                logger.warning("Sellability scoring batch failed: %s", safe_error(exc))
        ids = []
        with self._connect() as db:
            for trend in trends:
                score = (
                    self._normalise_sellability_item(supplied[trend["id"]])
                    if trend["id"] in supplied else self._fallback_sellability()
                )
                item_id = secrets.token_hex(12)
                db.execute(
                    """INSERT OR REPLACE INTO sellability_pool
                       (id,run_id,trend_id,total_score,grade,metrics,target_audience,
                        recommended_products,valid_window,sales_reason,risk_level,risk_reasons,
                        pattern_quota,products_per_pattern,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (item_id, run_id, trend["id"], score["total_score"], score["grade"],
                     json_text(score["metrics"]), score["target_audience"],
                     json_text(score["recommended_products"]), score["valid_window"],
                     score["sales_reason"], score["risk_level"], json_text(score["risk_reasons"]),
                     score["pattern_quota"], score["products_per_pattern"], utc_now()),
                )
                ids.append(item_id)
        self._update_run(run_id, status="sellability_pool_ready", stage="sellability_pool")
        return raw_ids + copied_ids + ids

    async def _create_prompt_pool(
        self,
        run_id: str,
        config: dict[str, Any],
        count: int | None,
    ) -> list[str]:
        with self._connect() as db:
            trends = [dict(row) for row in db.execute(
                """SELECT t.* FROM trends t
                   WHERE t.run_id=? AND t.status!='rejected'
                     AND NOT EXISTS (SELECT 1 FROM prompt_pool p WHERE p.trend_id=t.id)
                   ORDER BY t.rank""",
                (run_id,),
            ).fetchall()]
        if not trends:
            raise ValueError("没有待生成提示词的可用图案")
        self._update_run(run_id, status="running", stage="prompt_pool_generation", error="")
        supplied_map: dict[str, dict[str, str]] = {}
        batch_size = min(GEMINI_SAFE_BATCH_SIZE, int(config["candidate_count"]))
        for offset in range(0, len(trends), batch_size):
            response_text = await self._call_gemini(
                self._prompt_pool_prompt(trends[offset:offset + batch_size]),
                config["gemini_verification_model"],
                attempts=2,
            )
            payload = extract_json_object(response_text)
            supplied = payload.get("prompts") if isinstance(payload.get("prompts"), list) else []
            supplied_map.update({
                str(item.get("trend_id")): {
                    "pattern_prompt": str(item.get("pattern_prompt") or "").strip(),
                    "product_prompt": str(item.get("product_prompt") or item.get("prompt") or "").strip(),
                }
                for item in supplied if isinstance(item, dict)
            })
        prompt_ids = []
        with self._connect() as db:
            for trend in trends:
                prompt_id = secrets.token_hex(12)
                supplied = supplied_map.get(trend["id"], {})
                pattern_prompt = supplied.get("pattern_prompt") or self._pattern_flow_prompt(trend)
                prompt = supplied.get("product_prompt") or self._flow_prompt(trend)
                db.execute(
                    """INSERT INTO prompt_pool
                       (id,run_id,trend_id,pattern_prompt,prompt,status,created_at)
                       VALUES(?,?,?,?,?,'ready',?)""",
                    (prompt_id, run_id, trend["id"], pattern_prompt[:10000], prompt[:10000], utc_now()),
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
        pattern_ids = await self._generate_pattern_assets(run_id, config, count)
        if not pattern_ids:
            raise RuntimeError("Flow没有成功生成图案")
        await self._generate_products_from_patterns(
            run_id, config, None, pattern_ids=pattern_ids
        )

    async def _generate_pattern_assets(
        self,
        run_id: str,
        config: dict[str, Any],
        count: int | None,
    ) -> list[str]:
        with self._connect() as db:
            prompts = [dict(row) for row in db.execute(
                """SELECT t.*, p.id AS prompt_id, p.pattern_prompt
                   FROM prompt_pool p JOIN trends t ON t.id=p.trend_id
                   WHERE p.run_id=? AND p.status='ready'""",
                (run_id,),
            ).fetchall()]
        if not prompts:
            raise ValueError("提示词池为空，请先生成提示词池")
        selected = random.sample(prompts, min(len(prompts), max(1, count or config["images_per_trend"])))
        self._update_run(run_id, status="running", stage="pattern_generation", error="")
        semaphore = asyncio.Semaphore(config["generation_concurrency"])

        async def guarded(item: dict[str, Any], sequence: int) -> str | None:
            async with semaphore:
                return await self._generate_pattern_one(
                    run_id,
                    item,
                    sequence,
                    config,
                    prompt_id=item["prompt_id"],
                    prompt_text=self._isolated_pattern_prompt(
                        item["pattern_prompt"] or self._pattern_flow_prompt(item)
                    ),
                )

        results = await asyncio.gather(*(guarded(item, index) for index, item in enumerate(selected, 1)))
        pattern_ids = [item for item in results if item]
        failed = len(results) - len(pattern_ids)
        self._update_run(
            run_id,
            status="pattern_assets_ready" if pattern_ids and not failed else "partial" if pattern_ids else "failed",
            stage="pattern_assets" if pattern_ids else "finished",
            error="" if pattern_ids else "Flow没有成功生成图案",
        )
        return pattern_ids

    async def _generate_products_from_patterns(
        self,
        run_id: str,
        config: dict[str, Any],
        count: int | None,
        *,
        pattern_ids: list[str] | None = None,
    ) -> None:
        with self._connect() as db:
            params: list[Any] = [run_id]
            pattern_filter = ""
            if pattern_ids:
                pattern_filter = f" AND a.id IN ({','.join('?' for _ in pattern_ids)})"
                params.extend(pattern_ids)
            patterns = [dict(row) for row in db.execute(
                f"""SELECT a.*,t.topic_en,t.topic_zh,t.summary_zh,t.why_trending,t.visual_brief_en,
                           t.status AS trend_status,p.prompt AS product_prompt
                    FROM pattern_assets a JOIN trends t ON t.id=a.trend_id
                    JOIN prompt_pool p ON p.id=a.prompt_id
                    WHERE a.run_id=? AND a.status='success'{pattern_filter}
                      AND NOT EXISTS (SELECT 1 FROM generations g
                                      WHERE g.pattern_asset_id=a.id AND g.status='success')
                    """,
                params,
            ).fetchall()]
        if not patterns:
            raise ValueError("没有待生成产品图的图案，请先生成图案")
        limit = len(patterns) if pattern_ids and count is None else max(1, count or config["images_per_trend"])
        selected = random.sample(patterns, min(len(patterns), limit))
        self._update_run(run_id, status="running", stage="product_generation", error="")
        semaphore = asyncio.Semaphore(config["generation_concurrency"])

        async def guarded(item: dict[str, Any], sequence: int) -> bool:
            async with semaphore:
                pattern_path = self.assets_dir / item["image_path"]
                reference = (pattern_path.read_bytes(), item["mime_type"] or "image/png")
                return await self._generate_one(
                    run_id,
                    item,
                    sequence,
                    config,
                    prompt_id=item["prompt_id"],
                    prompt_text=self._product_reference_prompt(
                        item["product_prompt"] or self._flow_prompt(item),
                    ),
                    pattern_asset_id=item["id"],
                    reference_image=reference,
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
            error="" if success else "Flow没有成功生成产品图",
        )
        await self._notify_run(run_id)

    @staticmethod
    def _prepare_pattern_png(image_bytes: bytes) -> tuple[bytes, bool, bool]:
        """Decode any supported image and return a real PNG with edge background removed."""
        with Image.open(BytesIO(image_bytes)) as opened:
            image = opened.convert("RGBA")
        original_alpha = image.getchannel("A")
        already_transparent = original_alpha.getextrema()[0] < 255
        background_removed = False
        if not already_transparent:
            rgb = image.convert("RGB")
            width, height = rgb.size
            border = []
            step_x = max(1, width // 200)
            step_y = max(1, height // 200)
            border.extend(rgb.getpixel((x, 0)) for x in range(0, width, step_x))
            border.extend(rgb.getpixel((x, height - 1)) for x in range(0, width, step_x))
            border.extend(rgb.getpixel((0, y)) for y in range(0, height, step_y))
            border.extend(rgb.getpixel((width - 1, y)) for y in range(0, height, step_y))
            buckets: dict[tuple[int, int, int], int] = {}
            for pixel in border:
                bucket = tuple(min(255, (value // 16) * 16 + 8) for value in pixel)
                buckets[bucket] = buckets.get(bucket, 0) + 1
            dominant, count = max(buckets.items(), key=lambda item: item[1])
            if count / max(1, len(border)) >= 0.28:
                diff_rgb = ImageChops.difference(rgb, Image.new("RGB", rgb.size, dominant))
                channels = diff_rgb.split()
                difference = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
                connected = difference.point(lambda value: 255 if value <= 72 else 0)
                draw = ImageDraw.Draw(connected)
                for x in range(width):
                    if connected.getpixel((x, 0)) == 255:
                        ImageDraw.floodfill(connected, (x, 0), 128, thresh=0)
                    if connected.getpixel((x, height - 1)) == 255:
                        ImageDraw.floodfill(connected, (x, height - 1), 128, thresh=0)
                for y in range(height):
                    if connected.getpixel((0, y)) == 255:
                        ImageDraw.floodfill(connected, (0, y), 128, thresh=0)
                    if connected.getpixel((width - 1, y)) == 255:
                        ImageDraw.floodfill(connected, (width - 1, y), 128, thresh=0)
                del draw
                connected_mask = connected.point(lambda value: 255 if value == 128 else 0)
                soft_alpha = difference.point(
                    lambda value: 0 if value <= 18 else 255 if value >= 72 else round((value - 18) * 255 / 54)
                )
                alpha = Image.composite(soft_alpha, Image.new("L", rgb.size, 255), connected_mask)
                image.putalpha(ImageChops.multiply(original_alpha, alpha))
                background_removed = image.getchannel("A").getextrema()[0] < 255
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        has_transparency = image.getchannel("A").getextrema()[0] < 255
        return output.getvalue(), has_transparency, background_removed

    @staticmethod
    def _transparent_border_ratio(image_bytes: bytes) -> float:
        with Image.open(BytesIO(image_bytes)) as opened:
            alpha = opened.convert("RGBA").getchannel("A")
        width, height = alpha.size
        border = [alpha.getpixel((x, 0)) for x in range(width)]
        border.extend(alpha.getpixel((x, height - 1)) for x in range(width))
        border.extend(alpha.getpixel((0, y)) for y in range(height))
        border.extend(alpha.getpixel((width - 1, y)) for y in range(height))
        return sum(value <= 16 for value in border) / max(1, len(border))

    @staticmethod
    def _remove_background(image_bytes: bytes) -> bytes:
        from rembg import remove

        model = os.getenv("REMBG_MODEL", "u2netp").strip() or "u2netp"
        with Image.open(BytesIO(image_bytes)) as opened:
            image = opened.convert("RGBA")
        result = remove(
            image,
            session=rembg_session(model),
            post_process_mask=True,
        )
        output = BytesIO()
        if isinstance(result, bytes):
            with Image.open(BytesIO(result)) as extracted:
                extracted.convert("RGBA").save(output, format="PNG", optimize=True)
        else:
            result.convert("RGBA").save(output, format="PNG", optimize=True)
        return output.getvalue()

    async def _generate_pattern_one(
        self,
        run_id: str,
        trend: dict[str, Any],
        sequence: int,
        config: dict[str, Any],
        *,
        prompt_id: str,
        prompt_text: str,
    ) -> str | None:
        asset_id = secrets.token_hex(12)
        model = random.choice(config["flow_models"])
        started = time.perf_counter()
        with self._connect() as db:
            db.execute(
                """INSERT INTO pattern_assets
                   (id,run_id,trend_id,prompt_id,sequence,model,prompt,status,created_at)
                   VALUES(?,?,?,?,?,?,?,'running',?)""",
                (asset_id, run_id, trend["id"], prompt_id, sequence, model, prompt_text, utc_now()),
            )
            db.execute("UPDATE prompt_pool SET used_count=used_count+1 WHERE id=?", (prompt_id,))
            db.execute("UPDATE trends SET status='generating' WHERE id=?", (trend["id"],))
        try:
            response_text, image_bytes, mime_type = await self._call_flow(prompt_text, model)
            png_bytes, has_transparency, background_removed = self._prepare_pattern_png(image_bytes)
            if not has_transparency or self._transparent_border_ratio(png_bytes) < 0.8:
                try:
                    extracted = self._remove_background(image_bytes)
                    png_bytes, has_transparency, _ = self._prepare_pattern_png(extracted)
                    background_removed = True
                    response_text = f"{response_text}\n[rembg-foreground-extraction]"
                except Exception as exc:
                    raise RuntimeError(
                        "rembg前景提取失败；请确认u2netp.onnx已放入U2NET_HOME"
                    ) from exc
            if not has_transparency:
                raise RuntimeError("图案未形成真实透明通道，已拒绝保存；请重试生图")
            relative = Path(run_id) / trend["id"] / f"pattern-{asset_id}.png"
            target = self.assets_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(png_bytes)
            duration = round((time.perf_counter() - started) * 1000)
            with self._connect() as db:
                db.execute(
                    """UPDATE pattern_assets SET status='success',image_path=?,mime_type=?,duration_ms=?,
                       raw_response=?,has_transparency=?,background_removed=?,finished_at=? WHERE id=?""",
                    (relative.as_posix(), "image/png", duration, response_text[:20000],
                     int(has_transparency), int(background_removed), utc_now(), asset_id),
                )
                db.execute("UPDATE trends SET status='pattern_generated' WHERE id=?", (trend["id"],))
            return asset_id
        except asyncio.CancelledError:
            with self._connect() as db:
                db.execute(
                    """UPDATE pattern_assets SET status='failed',duration_ms=?,error=?,finished_at=?
                       WHERE id=?""",
                    (round((time.perf_counter() - started) * 1000), "任务已取消", utc_now(), asset_id),
                )
                generated = db.execute(
                    "SELECT 1 FROM pattern_assets WHERE trend_id=? AND status='success'",
                    (trend["id"],),
                ).fetchone()
                db.execute(
                    "UPDATE trends SET status=? WHERE id=?",
                    ("pattern_generated" if generated else trend["status"], trend["id"]),
                )
            raise
        except Exception as exc:
            with self._connect() as db:
                db.execute(
                    """UPDATE pattern_assets SET status='failed',duration_ms=?,error=?,finished_at=?
                       WHERE id=?""",
                    (round((time.perf_counter() - started) * 1000), safe_error(exc), utc_now(), asset_id),
                )
                generated = db.execute(
                    "SELECT 1 FROM pattern_assets WHERE trend_id=? AND status='success'",
                    (trend["id"],),
                ).fetchone()
                db.execute(
                    "UPDATE trends SET status=? WHERE id=?",
                    ("pattern_generated" if generated else "generation_failed", trend["id"]),
                )
            return None

    async def _generate_one(
        self,
        run_id: str,
        trend: dict[str, Any],
        sequence: int,
        config: dict[str, Any],
        *,
        prompt_id: str | None = None,
        prompt_text: str | None = None,
        pattern_asset_id: str | None = None,
        reference_image: tuple[bytes, str] | None = None,
    ) -> bool:
        generation_id = secrets.token_hex(12)
        trend_id = str(trend.get("trend_id") or trend["id"])
        model = random.choice(config["flow_models"])
        prompt = prompt_text or self._flow_prompt(trend)
        started = time.perf_counter()
        with self._connect() as db:
            db.execute(
                """INSERT INTO generations
                   (id,run_id,trend_id,prompt_id,pattern_asset_id,sequence,model,prompt,status,created_at)
                   VALUES(?,?,?,?,?,?,?,?, 'running', ?)""",
                (generation_id, run_id, trend_id, prompt_id, pattern_asset_id, sequence, model, prompt, utc_now()),
            )
            if prompt_id and not pattern_asset_id:
                db.execute(
                    "UPDATE prompt_pool SET used_count=used_count+1 WHERE id=?", (prompt_id,)
                )
            db.execute("UPDATE trends SET status = 'generating' WHERE id = ?", (trend_id,))
        try:
            if reference_image:
                response_text, image_bytes, mime_type = await self._call_flow(
                    prompt, model, reference_image
                )
            else:
                response_text, image_bytes, mime_type = await self._call_flow(prompt, model)
            suffix = mimetypes.guess_extension(mime_type) or ".png"
            relative = Path(run_id) / trend_id / f"product-{generation_id}{suffix}"
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
                db.execute("UPDATE trends SET status = 'generated' WHERE id = ?", (trend_id,))
            return True
        except asyncio.CancelledError:
            with self._connect() as db:
                db.execute(
                    """UPDATE generations SET status='failed', duration_ms=?, error=?, finished_at=? WHERE id=?""",
                    (round((time.perf_counter() - started) * 1000), "任务已取消", utc_now(), generation_id),
                )
                generated = db.execute(
                    "SELECT 1 FROM generations WHERE trend_id=? AND status='success'", (trend_id,)
                ).fetchone()
                db.execute(
                    "UPDATE trends SET status = ? WHERE id = ?",
                    ("generated" if generated else trend.get("trend_status", trend["status"]), trend_id),
                )
            raise
        except Exception as exc:
            with self._connect() as db:
                db.execute(
                    """UPDATE generations SET status='failed', duration_ms=?, error=?, finished_at=? WHERE id=?""",
                    (round((time.perf_counter() - started) * 1000), safe_error(exc), utc_now(), generation_id),
                )
                remaining = db.execute(
                    "SELECT 1 FROM generations WHERE trend_id=? AND status='success'", (trend_id,)
                ).fetchone()
                if not remaining:
                    db.execute("UPDATE trends SET status = 'generation_failed' WHERE id = ?", (trend_id,))
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
                extract_json_object(str(content))
                return str(content)
            except Exception as exc:
                error = exc
                if attempt < attempts:
                    await asyncio.sleep(5 * attempt if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500 else 2 * attempt)
        raise RuntimeError(f"Gemini请求失败（{attempts}次）: {safe_error(error or RuntimeError('unknown'))}")

    async def _call_flow(
        self,
        prompt: str,
        model: str,
        reference_image: tuple[bytes, str] | None = None,
    ) -> tuple[str, bytes, str]:
        if not self.flow_api_key:
            raise RuntimeError("FLOW_API_KEY未配置")
        content: str | list[dict[str, Any]] = prompt
        if reference_image:
            image_bytes, mime_type = reference_image
            data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        response = await self.http.post(
            f"{self.flow_base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.flow_api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": content}], "stream": False},
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
        return f"""You are a real-time worldwide social-media trend collector.

Current time: {now}
Lookback window: the previous {config['lookback_hours']} hours
Target regions: {', '.join(config['regions'])}
Target platforms: {', '.join(config['platforms'])}
Return at most {config['candidate_count']} candidate trends.

You must use any internet-search capability available in the current Gemini session. Search worldwide; treat the target regions as priority coverage rather than exclusive boundaries. Within the configured result count, collect the strongest current trends across every category: news, politics, public figures, entertainment, brands, sports, business, technology, science, emergencies, controversies, memes, phrases, moods, aesthetics, communities, seasonal moments, and niche subcultures. This stage is an inclusive raw-trend inventory, not a product or design filter.

Rules:
1. Each result is a factual raw social signal. Keep separate movements separate and do not turn any signal into a product concept, printable pattern, or image prompt yet.
2. Do not filter by product suitability or visual potential. Do not reject or omit a trend merely because it involves a brand, copyrighted subject, public figure, politics, controversy, sensitive material, misinformation risk, adult discussion, gambling, hate, or violence. Record sensitive topics at a high factual level without graphic detail and identify concerns in risk_flags for the next stage.
3. Evidence URLs and publication times are optional. Include real sources when available, never invent them, and never omit a trend only because evidence is unavailable.
4. Use null when a value cannot be verified. Distinguish verified facts from unverified claims in the summary and risk flags.
5. Seek broad category coverage and strong current attention rather than only visually attractive or commercially usable trends.
6. Return strict JSON only. Do not use Markdown fences or prose outside JSON. Put every returned trend in the trends array; do not return a rejected list.

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
      "visual_brief_en": "Optional observable symbols or mood; empty is allowed and this is not an image prompt",
      "risk_flags": ["optional: ip|public_figure|sensitive|unsafe|misinformation_risk|other"]
    }}
  ]
}}"""

    def _classification_prompt(self, config: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
        now = datetime.now(ZoneInfo(config["timezone"])).isoformat()
        return f"""You are the creative-pattern extractor that builds a reusable, production-safe pattern pool from an inclusive raw social-trend inventory.

Current time: {now}
Valid lookback: {config['lookback_hours']} hours

Translate the raw trends into original, reusable, production-safe visual directions. Do not require the source event to already be an illustration, aesthetic, meme, merchandise idea, or printable subject. Preserve the event's recognizable narrative anchor: the main type of people or objects, the defining action or conflict, and the setting. A concrete original scene is the default whenever it can be shown safely; abstraction, geometry, symbols, color, and texture should support that scene rather than replace it. News events, technology, sports, culture, public discussion, and other non-visual topics can become original comic scenes, editorial illustrations, stylized narrative graphics, or—only when a concrete depiction would be unsafe—abstract visual metaphors. A raw trend may produce one or multiple pattern entries; return zero only when no safe, respectful, non-infringing visual translation is possible. Split distinct motifs, merge duplicate directions, and assign a concise category such as culture, humor, lifestyle, seasonal, sports, technology, nature, travel, food, pets, or social mood.

The acquisition input intentionally includes every trend type. Do not copy or retain logos, trademarks, copyrighted characters, distinctive existing artwork, public-figure likenesses, hate symbols, explicit/adult imagery, graphic violence, misinformation claims, or unsafe instructions. Genericize protected identity without erasing the event: replace named teams with unbranded athletes in clearly different uniforms, named companies with generic workers or devices, and public figures with non-identifiable roles while retaining the reported action and setting. For example, a story about repeated fights during joint professional-football practices should become two groups of generic American-football players in contrasting unbranded practice uniforms shoving and grappling while teammates separate them on a training field—not a generic football, stripes, or unrelated sports geometry. When a sensitive trend cannot support a respectful concrete scene, use a restrained non-infringing metaphor without implying endorsement or association; otherwise output no pattern. Missing evidence or publication time is not itself a rejection reason.

Every classified_trends item is an accepted pattern-pool entry. Its topic_en and topic_zh must still identify the source event or action, not merely name an art style or mood. It must preserve enough factual context to explain the inspiration, contain a detailed visual brief with subjects, action, setting, composition, style, palette, and mood, have no unresolved risk flags, and remain independent of any physical product. The artwork may interpret the event rather than reproduce protected identities; sensitive events must use restrained, non-graphic visual metaphor and must not trivialize victims or suffering. Do not write the final image prompt or choose a product in this stage. Return strict JSON only.

Candidates:
{json.dumps(candidates, ensure_ascii=False)}

Schema:
{{
  "classified_trends": [
    {{
      "source_candidate_id":"preserve the input candidate_id exactly",
      "topic_en":"recognizable event-linked artwork direction",
      "topic_zh":"与原热点事件直接相关的图案方向",
      "summary_zh":"事实摘要",
      "why_trending":"传播原因",
      "platforms":["X"],
      "region":"Global",
      "category":"culture",
      "first_seen_at":null,
      "engagement_signal":"signal or null",
      "evidence":[],
      "confidence":0.8,
      "visual_brief_en":"Detailed event-linked subjects, defining action, setting, composition, original illustration style, palette, texture, and mood; no protected identity, no product",
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
                "topic_zh": item["topic_zh"],
                "summary_zh": item["summary_zh"],
                "why_trending": item["why_trending"],
                "category": item["category"],
                "visual_brief_en": item["visual_brief_en"],
            }
            for item in trends
        ]
        return f"""You create two production-ready image prompts for every supplied pattern-pool entry extracted from worldwide social trends.

For every input trend_id, write a pattern_prompt and a product_prompt. The pattern_prompt must be a detailed English prompt for one standalone, print-ready artwork exported as a transparent-background PNG. It must contain only the printable design pixels: no full-canvas background, scenery extending to the image edges, sky, ground, wall, room, floor, horizon, photographic environment, poster rectangle, colored backdrop, border, frame, product, mockup, hands, cast shadow, or merchandising scene. Keep transparent negative space around and between design elements. If transparency is technically impossible, use only uniform pure white (#FFFFFF) outside the artwork, never a textured, colored, gradient, or illustrated background. Choose the best event-linked format: a recognizable original comic or editorial illustration, icon set, emblem, badge, symbolic graphic, isolated repeating-motif cluster, geometric motif, or decorative pattern. A comic may include only small internal story cues contained within the design silhouette or vignette; it must not become a rectangular scene. Icons and abstraction are welcome when they still communicate the event; concrete subjects and defining action remain the default for narrative news.

The product_prompt must be roughly 140–240 English words and instruct the image model to use the attached generated pattern image as the exact artwork reference for one realistic print-on-demand product rendering. Preserve the reference artwork's subjects, action, composition, palette, and style rather than redesigning it. Select one suitable physical item and fully specify placement, scale, print treatment, product color, material, camera angle, lighting, and neutral surroundings. The final image must show that supplied artwork printed directly on the product, never as separate flat artwork.

Rules:
1. One prompt must show one main product only, and it must be either a vehicle spare-tire cover or a phone case. Do not choose mugs, tumblers, apparel, bags, posters, stickers, or any other product in this project version.
2. The artwork must conform naturally to curvature, seams, folds, and material and look genuinely printed.
3. Do not use logos, trademarks, copyrighted characters, public-figure likenesses, copied posts, watermarks, or existing artwork. Replace protected identities with generic unbranded equivalents, but keep the event's core action and context recognizable.
4. Avoid text unless essential; if used, it must be short, generic, and correctly spelled.
5. Return exactly one prompt pair for every supplied trend_id, preserve every trend_id exactly, and return strict JSON only.

Pattern-pool entries:
{json.dumps(compact, ensure_ascii=False)}

Schema:
{{"prompts":[{{"trend_id":"id","pattern_prompt":"standalone artwork prompt","product_prompt":"reference-image product rendering prompt"}}]}}"""

    @staticmethod
    def _normalise_candidates(
        payload: dict[str, Any], limit: int | None
    ) -> list[dict[str, Any]]:
        trends = payload.get("trends")
        if not isinstance(trends, list):
            return []
        result = []
        selected = trends if limit is None else trends[:limit]
        for index, raw in enumerate(selected, start=1):
            if not isinstance(raw, dict):
                continue
            topic_en = str(raw.get("topic_en") or "").strip()
            topic_zh = str(raw.get("topic_zh") or topic_en).strip()
            if not topic_en:
                continue
            evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
            result.append({
                "candidate_id": str(
                    raw.get("source_candidate_id") or raw.get("candidate_id") or f"candidate-{index}"
                )[:100],
                "rank": index,
                "topic_en": topic_en[:300],
                "topic_zh": topic_zh[:300],
                "summary_zh": str(raw.get("summary_zh") or "")[:2000],
                "why_trending": str(raw.get("why_trending") or "")[:2000],
                "platforms": string_list(raw.get("platforms"), limit=100, item_limit=100),
                "region": str(raw.get("region") or "")[:200],
                "category": str(raw.get("category") or "other")[:100],
                "first_seen_at": raw.get("first_seen_at"),
                "engagement_signal": raw.get("engagement_signal"),
                "evidence": evidence[:100],
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
            item["source_candidate_id"] = str(item.pop("candidate_id", ""))[:100]
            item["id"] = secrets.token_hex(12)
            item["status"] = "ready"
            item["verification_note"] = "AI已提取可用图案并分类"
            output.append(item)
        return output

    def _replace_trends(self, run_id: str, trends: list[dict[str, Any]]) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM trends WHERE run_id = ?", (run_id,))
            db.executemany(
                """INSERT INTO trends
                   (id, run_id, rank, topic_en, topic_zh, summary_zh, why_trending, platforms,
                    region, category, first_seen_at, engagement_signal, evidence, confidence,
                    visual_brief_en, risk_flags, status, verification_note, source_candidate_id, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(
                    item["id"], run_id, item["rank"], item["topic_en"], item["topic_zh"],
                    item["summary_zh"], item["why_trending"], json_text(item["platforms"]),
                    item["region"], item["category"], item["first_seen_at"],
                    str(item["engagement_signal"] or ""), json_text(item["evidence"]),
                    item["confidence"], item["visual_brief_en"], json_text(item["risk_flags"]),
                    item["status"], item["verification_note"], item.get("source_candidate_id", ""), utc_now(),
                ) for item in trends],
            )

    @staticmethod
    def _pattern_flow_prompt(trend: dict[str, Any]) -> str:
        return f"""Create one standalone, original, print-ready artwork inspired by this current social trend.

Trending topic: {trend['topic_en']}
Verified context: {trend['summary_zh']}
Why it is trending: {trend['why_trending']}
Visual direction: {trend['visual_brief_en']}

Preserve the recognizable generic subjects and defining action or interaction. Choose the most suitable format: original comic or editorial illustration, icon set, emblem, badge, symbolic graphic, isolated repeating-motif cluster, geometric motif, or decorative pattern. Icons and abstraction may simplify the event but must remain meaningfully connected to it. Use no logos, trademarks, copyrighted characters, public-figure likenesses, copied posts, watermarks, or existing artwork. Output only the printable design pixels as a transparent-background PNG with generous transparent negative space. Do not show or fill the canvas with a sky, ground, wall, room, floor, horizon, landscape, photographic environment, poster rectangle, colored backdrop, border, frame, product, mockup, hands, clothing, packaging, cast shadow, or merchandising scene. A comic may contain small story cues inside its isolated silhouette or vignette, but never a rectangular background scene. If alpha transparency is technically impossible, leave uniform pure white (#FFFFFF) outside the artwork."""

    @staticmethod
    def _isolated_pattern_prompt(pattern_prompt: str) -> str:
        return f"""MANDATORY OUTPUT FORMAT — this overrides any conflicting wording below:
- Return one flat, print-ready design asset as a transparent-background PNG.
- Render only the artwork pixels. Every area outside the artwork must be transparent.
- No full-canvas scene or background; no sky, ground, wall, room, floor, horizon, landscape, photograph, backdrop, gradient, texture, poster rectangle, border, frame, product, mockup, hands, packaging, or cast shadow.
- Keep generous transparent negative space around the complete design and transparent gaps between separate icons.
- Story details may appear only as compact internal elements contained inside the design silhouette or vignette, never as a rectangular illustrated scene.
- If alpha transparency is technically impossible, use uniform pure white (#FFFFFF) only outside the design. Never simulate transparency with a checkerboard.

ARTWORK BRIEF:
{pattern_prompt}

Final check before output: isolated artwork only, transparent outside pixels, no background and no product."""

    @staticmethod
    def _product_reference_prompt(product_prompt: str, preferred_product: str | None = None) -> str:
        return f"""The attached image is the exact finished artwork reference. Reproduce that same artwork on the product without redesigning, replacing, simplifying, recoloring, or adding unrelated graphics. Preserve its subjects, action, composition, palette, linework, and style. Do not display the reference as a separate card or floating image. Transparent pixels are non-printing. If the reference has uniform pure-white space connected to an image edge, treat that outer white space as transparency and do not print it as a white rectangle; preserve intentional enclosed white details inside the artwork.

Product rendering instructions:
{product_prompt}
{f'Render this variation specifically on one {preferred_product}.' if preferred_product else ''}"""

    @staticmethod
    def _flow_prompt(trend: dict[str, Any]) -> str:
        return f"""Create a realistic print-on-demand product image inspired by a current worldwide social-media trend.

Trending topic: {trend['topic_en']}
Verified context: {trend['summary_zh']}
Why it is trending: {trend['why_trending']}
Visual direction: {trend['visual_brief_en']}

Requirements:
- Use the attached generated pattern image as the exact artwork reference. Preserve its subjects, action, composition, palette, and style instead of redesigning it.
- Render the design printed directly on the single physical product named in the visual direction. If no product is named, choose exactly one of: vehicle spare-tire cover or phone case.
- Show one main product only, fully visible and easy to inspect. Do not create a collage or show several product types.
- Make the artwork conform naturally to the product's printable area, curvature, seams, folds, and material. It must look genuinely printed, not digitally pasted on top.
- Use a clean commercial product-photography composition with a simple neutral setting. Keep the product and printed design sharp and unobstructed.
- Keep the source event recognizable in the printed artwork by retaining its generic subjects, defining action or interaction, and setting. Prefer a concrete original comic or editorial scene; do not collapse a narrative event into unrelated abstract shapes or a generic category icon.
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
        self._cleanup_empty_runs_sync(
            datetime.now(timezone.utc) - timedelta(minutes=EMPTY_RUN_STALE_MINUTES)
        )
        with self._connect() as db:
            rows = db.execute(
                """SELECT r.id,r.trigger_type,r.status,r.stage,r.started_at,r.finished_at,
                          r.duration_ms,r.error,r.candidate_count,r.verified_count,
                          r.generated_count,r.failed_count,
                          (SELECT COUNT(*) FROM prompt_pool p WHERE p.run_id=r.id) AS prompt_count,
                          (SELECT COUNT(*) FROM sellability_pool s WHERE s.run_id=r.id) AS sellability_count,
                          (SELECT COUNT(*) FROM pattern_assets a
                           WHERE a.run_id=r.id AND a.status='success') AS pattern_count
                   FROM runs r ORDER BY r.started_at DESC LIMIT ?""",
                (min(200, max(1, limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _list_pool_cards_legacy(self, pool: str, limit: int = 24, offset: int = 0) -> dict[str, Any]:
        limit = min(100, max(1, limit))
        offset = max(0, offset)
        if pool == "acquire":
            with self._connect() as db:
                total = int(db.execute(
                    "SELECT COALESCE(SUM(candidate_count),0) FROM runs WHERE raw_discovery!=''"
                ).fetchone()[0])
                runs = db.execute(
                    """SELECT id,started_at,raw_discovery,candidate_count FROM runs
                       WHERE raw_discovery!='' ORDER BY started_at DESC"""
                )
                entries = []
                skip = offset
                for row in runs:
                    run = dict(row)
                    try:
                        items = self._normalise_candidates(
                            extract_json_object(run["raw_discovery"]), None
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        items = []
                    if skip >= len(items):
                        skip -= len(items)
                        continue
                    for item in items[skip:]:
                        entries.append({
                            "item": item,
                            "run": {"id": run["id"], "started_at": run["started_at"]},
                            "date": run["started_at"],
                        })
                        if len(entries) >= limit:
                            return {"entries": entries, "total": total}
                    skip = 0
            return {"entries": entries, "total": total}

        with self._connect() as db:
            if pool == "trends":
                total = int(db.execute("SELECT COUNT(*) FROM trends").fetchone()[0])
                rows = db.execute(
                    """SELECT t.*,r.started_at AS run_started_at
                       FROM trends t JOIN runs r ON r.id=t.run_id
                       ORDER BY t.created_at DESC LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
                entries = []
                for row in rows:
                    item = dict(row)
                    run_started_at = item.pop("run_started_at")
                    for key in ("platforms", "evidence", "risk_flags"):
                        item[key] = json.loads(item[key])
                    entries.append({
                        "item": item,
                        "run": {"id": item["run_id"], "started_at": run_started_at},
                        "date": item["created_at"],
                    })
            elif pool == "prompts":
                total = int(db.execute("SELECT COUNT(*) FROM prompt_pool").fetchone()[0])
                rows = db.execute(
                    """SELECT p.*,t.topic_zh,t.category,r.started_at AS run_started_at
                       FROM prompt_pool p JOIN trends t ON t.id=p.trend_id
                       JOIN runs r ON r.id=p.run_id
                       ORDER BY p.created_at DESC LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
                entries = []
                for row in rows:
                    item = dict(row)
                    run_started_at = item.pop("run_started_at")
                    entries.append({
                        "item": item,
                        "run": {"id": item["run_id"], "started_at": run_started_at},
                        "date": item["created_at"],
                    })
            elif pool == "patterns":
                total = int(db.execute(
                    "SELECT COUNT(*) FROM pattern_assets WHERE image_path IS NOT NULL"
                ).fetchone()[0])
                rows = db.execute(
                    """SELECT a.*,t.topic_zh,r.started_at AS run_started_at
                       FROM pattern_assets a JOIN trends t ON t.id=a.trend_id
                       JOIN runs r ON r.id=a.run_id WHERE a.image_path IS NOT NULL
                       ORDER BY a.created_at DESC LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
                entries = []
                for row in rows:
                    item = dict(row)
                    topic_zh = item.pop("topic_zh")
                    run_started_at = item.pop("run_started_at")
                    item["image_url"] = f"/assets/{item['image_path']}"
                    entries.append({
                        "item": item,
                        "trend": {"id": item["trend_id"], "topic_zh": topic_zh},
                        "run": {"id": item["run_id"], "started_at": run_started_at},
                        "date": item["created_at"],
                    })
            elif pool == "images":
                total = int(db.execute(
                    "SELECT COUNT(*) FROM generations WHERE image_path IS NOT NULL"
                ).fetchone()[0])
                rows = db.execute(
                    """SELECT g.id,g.run_id,g.trend_id,g.prompt_id,g.pattern_asset_id,g.sequence,g.model,g.prompt,
                              g.status,g.image_path,g.mime_type,g.duration_ms,g.error,g.created_at,
                              g.finished_at,t.topic_zh,r.started_at AS run_started_at
                       FROM generations g JOIN trends t ON t.id=g.trend_id
                       JOIN runs r ON r.id=g.run_id WHERE g.image_path IS NOT NULL
                       ORDER BY g.created_at DESC LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
                entries = []
                for row in rows:
                    item = dict(row)
                    topic_zh = item.pop("topic_zh")
                    run_started_at = item.pop("run_started_at")
                    item["image_url"] = f"/assets/{item['image_path']}"
                    entries.append({
                        "item": item,
                        "trend": {"id": item["trend_id"], "topic_zh": topic_zh},
                        "run": {"id": item["run_id"], "started_at": run_started_at},
                        "date": item["created_at"],
                    })
            else:
                raise ValueError("未知卡片池")
        return {"entries": entries, "total": total}

    @staticmethod
    def _sellability_record(row: dict[str, Any], prefix: str = "") -> dict[str, Any] | None:
        if row.get(f"{prefix}total_score") is None:
            return None
        result = {
            "id": row.get(f"{prefix}id"),
            "total_score": int(row[f"{prefix}total_score"]),
            "grade": row[f"{prefix}grade"],
            "pattern_quota": int(row[f"{prefix}pattern_quota"]),
            "products_per_pattern": int(row[f"{prefix}products_per_pattern"]),
        }
        for key in ("metrics", "recommended_products", "risk_reasons"):
            value = row.get(f"{prefix}{key}")
            if value is not None:
                try:
                    result[key] = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    result[key] = []
        for key in ("target_audience", "valid_window", "sales_reason", "risk_level", "created_at"):
            value = row.get(f"{prefix}{key}")
            if value is not None:
                result[key] = value
        return result

    def list_pool_cards(
        self,
        pool: str,
        limit: int = 24,
        offset: int = 0,
        *,
        q: str = "",
        grade: str = "",
        category: str = "",
        sort: str = "newest",
        transparent: str = "",
    ) -> dict[str, Any]:
        limit = min(100, max(1, limit))
        offset = max(0, offset)
        q = q.strip()[:200]
        grade = grade.strip().upper()[:1]
        category = category.strip()[:100]
        sort = sort if sort in {"newest", "score_desc", "score_asc"} else "newest"
        if pool == "prompts" and not any((q, grade, category, transparent)) and sort == "newest":
            return self._list_pool_cards_legacy(pool, limit, offset)

        score_select = """s.id AS sell_id,s.total_score AS sell_total_score,
            s.grade AS sell_grade,s.pattern_quota AS sell_pattern_quota,
            s.products_per_pattern AS sell_products_per_pattern"""
        if pool == "acquire":
            all_entries = []
            with self._connect() as db:
                scores: dict[tuple[str, str], dict[str, Any]] = {}
                for score_row in db.execute(
                    f"""SELECT s.run_id,s.candidate_id AS source_candidate_id,{score_select}
                        FROM raw_sellability_pool s"""
                ):
                    record = self._sellability_record(dict(score_row), "sell_")
                    key = (score_row["run_id"], score_row["source_candidate_id"])
                    if record and (key not in scores or record["total_score"] > scores[key]["total_score"]):
                        scores[key] = record
                runs = db.execute(
                    """SELECT id,started_at,raw_discovery FROM runs
                       WHERE raw_discovery!='' ORDER BY started_at DESC"""
                ).fetchall()
            for row in runs:
                try:
                    items = self._normalise_candidates(extract_json_object(row["raw_discovery"]), None)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                for item in items:
                    item["sellability"] = scores.get((row["id"], item["candidate_id"]))
                    haystack = f"{item['topic_zh']} {item['topic_en']} {item['summary_zh']}".casefold()
                    if q and q.casefold() not in haystack:
                        continue
                    if category and item["category"] != category:
                        continue
                    if grade and (not item["sellability"] or item["sellability"]["grade"] != grade):
                        continue
                    all_entries.append({
                        "item": item,
                        "run": {"id": row["id"], "started_at": row["started_at"]},
                        "date": row["started_at"],
                    })
            if sort.startswith("score"):
                all_entries.sort(
                    key=lambda entry: (entry["item"].get("sellability") or {}).get("total_score", -1),
                    reverse=sort == "score_desc",
                )
            return {"entries": all_entries[offset:offset + limit], "total": len(all_entries)}

        table_map = {
            "trends": ("trends t", "t.id", "t.created_at", "t.topic_zh", "t.category"),
            "patterns": ("pattern_assets a JOIN trends t ON t.id=a.trend_id", "a.id", "a.created_at", "t.topic_zh", "t.category"),
            "images": ("generations g JOIN trends t ON t.id=g.trend_id", "g.id", "g.created_at", "t.topic_zh", "t.category"),
            "sellability": ("raw_sellability_pool s", "s.id", "s.created_at", "s.topic_zh", "s.category"),
        }
        if pool not in table_map:
            return self._list_pool_cards_legacy(pool, limit, offset)
        table, _id_column, created_column, title_column, category_column = table_map[pool]
        if pool != "sellability":
            table += " LEFT JOIN sellability_pool s ON s.trend_id=t.id"
            table += " JOIN runs r ON r.id=t.run_id"
        else:
            table += " JOIN runs r ON r.id=s.run_id"
        where = []
        params: list[Any] = []
        if pool in {"patterns", "images"}:
            alias = "a" if pool == "patterns" else "g"
            where.append(f"{alias}.image_path IS NOT NULL")
        if q:
            text_alias = "s" if pool == "sellability" else "t"
            where.append(f"({title_column} LIKE ? OR {text_alias}.topic_en LIKE ? OR {text_alias}.summary_zh LIKE ?)")
            params.extend([f"%{q}%"] * 3)
        if grade:
            where.append("s.grade=?")
            params.append(grade)
        if category:
            where.append(f"{category_column}=?")
            params.append(category)
        if pool == "patterns" and transparent in {"yes", "no"}:
            where.append("a.has_transparency=?")
            params.append(1 if transparent == "yes" else 0)
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        order_sql = {
            "newest": f"{created_column} DESC",
            "score_desc": "COALESCE(s.total_score,-1) DESC, " + created_column + " DESC",
            "score_asc": "COALESCE(s.total_score,-1) ASC, " + created_column + " DESC",
        }[sort]
        if pool == "sellability":
            select = "s.*,r.started_at AS run_started_at"
        elif pool == "trends":
            select = f"t.*,r.started_at AS run_started_at,{score_select}"
        else:
            alias = "a" if pool == "patterns" else "g"
            select = f"{alias}.*,t.topic_zh,t.category,r.started_at AS run_started_at,{score_select}"
        with self._connect() as db:
            total = int(db.execute(f"SELECT COUNT(*) FROM {table}{where_sql}", params).fetchone()[0])
            rows = db.execute(
                f"SELECT {select} FROM {table}{where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        entries = []
        for source_row in rows:
            item = dict(source_row)
            run_started_at = item.pop("run_started_at")
            if pool == "sellability":
                for key in ("metrics", "recommended_products", "risk_reasons"):
                    item[key] = json.loads(item[key])
                trend = {key: item.pop(key) for key in ("topic_en", "topic_zh", "summary_zh", "category", "region")}
                trend["source_candidate_id"] = item["candidate_id"]
                entries.append({"item": item, "trend": trend, "run": {"id": item["run_id"], "started_at": run_started_at}, "date": item["created_at"]})
                continue
            sellability = self._sellability_record(item, "sell_")
            for key in list(item):
                if key.startswith("sell_"):
                    item.pop(key)
            item["sellability"] = sellability
            if pool == "trends":
                for key in ("platforms", "evidence", "risk_flags"):
                    item[key] = json.loads(item[key])
                entries.append({"item": item, "run": {"id": item["run_id"], "started_at": run_started_at}, "date": item["created_at"]})
            else:
                topic_zh, item_category = item.pop("topic_zh"), item.pop("category")
                item["image_url"] = f"/assets/{item['image_path']}"
                entries.append({"item": item, "trend": {"id": item["trend_id"], "topic_zh": topic_zh, "category": item_category}, "run": {"id": item["run_id"], "started_at": run_started_at}, "date": item["created_at"]})
        return {"entries": entries, "total": total}

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
            pattern_assets = [dict(row) for row in db.execute(
                "SELECT * FROM pattern_assets WHERE run_id=? ORDER BY created_at", (run_id,)
            ).fetchall()]
            prompt_pool = [dict(row) for row in db.execute(
                """SELECT p.*, t.topic_zh, t.category FROM prompt_pool p
                   JOIN trends t ON t.id=p.trend_id WHERE p.run_id=? ORDER BY p.created_at""",
                (run_id,),
            ).fetchall()]
            sellability_pool = [dict(row) for row in db.execute(
                "SELECT * FROM sellability_pool WHERE run_id=? ORDER BY total_score DESC,created_at DESC",
                (run_id,),
            ).fetchall()]
            raw_sellability_pool = [dict(row) for row in db.execute(
                "SELECT * FROM raw_sellability_pool WHERE run_id=? ORDER BY total_score DESC,created_at DESC",
                (run_id,),
            ).fetchall()]
        score_map = {}
        for score in sellability_pool:
            for key in ("metrics", "recommended_products", "risk_reasons"):
                score[key] = json.loads(score[key])
            score_map[score["trend_id"]] = score
        raw_score_map = {}
        for score in raw_sellability_pool:
            for key in ("metrics", "recommended_products", "risk_reasons"):
                score[key] = json.loads(score[key])
            raw_score_map[score["candidate_id"]] = score
        generation_map: dict[str, list[dict[str, Any]]] = {}
        for generation in generations:
            if generation.get("image_path"):
                generation["image_url"] = f"/assets/{generation['image_path']}"
            generation_map.setdefault(generation["trend_id"], []).append(generation)
        pattern_map: dict[str, list[dict[str, Any]]] = {}
        for pattern in pattern_assets:
            if pattern.get("image_path"):
                pattern["image_url"] = f"/assets/{pattern['image_path']}"
            pattern_map.setdefault(pattern["trend_id"], []).append(pattern)
        for trend in trends:
            for key in ("platforms", "evidence", "risk_flags"):
                trend[key] = json.loads(trend[key])
            trend["generations"] = generation_map.get(trend["id"], [])
            trend["pattern_assets"] = pattern_map.get(trend["id"], [])
            trend["sellability"] = score_map.get(trend["id"])
        result = dict(run)
        result["trends"] = trends
        result["prompt_pool"] = prompt_pool
        result["pattern_assets"] = pattern_assets
        result["sellability_pool"] = sellability_pool
        result["raw_sellability_pool"] = raw_sellability_pool
        result["raw_trends"] = []
        if result.get("raw_discovery"):
            try:
                result["raw_trends"] = self._normalise_candidates(
                    extract_json_object(result["raw_discovery"]), None
                )
                raw_scores: dict[str, dict[str, Any]] = dict(raw_score_map)
                for trend in trends:
                    source_id = trend.get("source_candidate_id")
                    score = trend.get("sellability")
                    if source_id and score and (
                        source_id not in raw_scores
                        or score["total_score"] > raw_scores[source_id]["total_score"]
                    ):
                        raw_scores[source_id] = score
                for raw in result["raw_trends"]:
                    raw["sellability"] = raw_scores.get(raw["candidate_id"])
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

    def _empty_run_ids(
        self, stale_before: datetime | None = None, *, include_active: bool = False
    ) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,raw_discovery,started_at FROM runs WHERE candidate_count=0"
            ).fetchall()
        run_ids = []
        for row in rows:
            if not include_active and row["id"] == self.active_run_id:
                continue
            if stale_before:
                started_at = parse_source_time(row["started_at"])
                if started_at and started_at > stale_before:
                    continue
            raw = str(row["raw_discovery"] or "").strip()
            if not raw:
                run_ids.append(row["id"])
                continue
            try:
                payload = extract_json_object(raw)
                if not isinstance(payload.get("trends"), list) or not payload["trends"]:
                    run_ids.append(row["id"])
            except (TypeError, ValueError, json.JSONDecodeError):
                run_ids.append(row["id"])
        return run_ids

    def _delete_run_ids(self, run_ids: list[str]) -> int:
        if not run_ids:
            return 0
        with self._connect() as db:
            deleted = db.execute(
                f"DELETE FROM runs WHERE id IN ({','.join('?' for _ in run_ids)})",
                run_ids,
            ).rowcount
        for run_id in run_ids:
            target = (self.assets_dir / run_id).resolve()
            if target.parent == self.assets_dir.resolve() and target.exists():
                shutil.rmtree(target)
        return int(deleted)

    def _cleanup_empty_runs_sync(self, stale_before: datetime) -> int:
        return self._delete_run_ids(self._empty_run_ids(stale_before))

    async def cleanup_empty_runs(self) -> dict[str, Any]:
        """Delete runs that never produced a single parseable hotspot."""
        run_ids = self._empty_run_ids(include_active=True)
        if self.active_run_id in run_ids and self.active_task and not self.active_task.done():
            self.active_task.cancel()
            await asyncio.gather(self.active_task, return_exceptions=True)
            self.active_task = None
            self.active_run_id = None
        async with self.operation_lock:
            deleted = self._delete_run_ids(run_ids)
        return {"deleted": int(deleted), "run_ids": run_ids}

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
            platforms = db.execute(
                """SELECT p.value AS name, COUNT(*) AS count
                   FROM trends AS t, json_each(t.platforms) AS p
                   WHERE t.status!='rejected'
                   GROUP BY p.value ORDER BY count DESC,name"""
            ).fetchall()
            source_total = db.execute("SELECT COUNT(*) FROM source_entries").fetchone()[0]
            source_recent = db.execute(
                "SELECT COUNT(*) FROM source_entries WHERE fetched_at>=?",
                ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),),
            ).fetchone()[0]
        return {
            "active_run_id": self.active_run_id,
            "running": bool(self.active_task and not self.active_task.done()),
            "today": {"runs": today_row[0], "verified": today_row[1], "generated": today_row[2]},
            "history": {
                "runs": totals[0], "candidates": totals[1], "verified": totals[2],
                "generated": totals[3], "failed": totals[4], "avg_duration_ms": round(totals[5] or 0),
            },
            "platforms": [dict(row) for row in platforms],
            "sources": {
                "total_entries": source_total,
                "recent_entries": source_recent,
            },
        }

    async def _scheduler_loop(self) -> None:
        while not self._stopping:
            try:
                config = self.get_config()
                acquisition_interval = int(config["acquisition_interval_minutes"])
                generation_interval = int(config["generation_interval_minutes"])
                acquisition_enabled = bool(config.get("enabled"))
                generation_enabled = bool(config.get("auto_generate"))
                source_sync_interval = int(config["source_sync_interval_minutes"])
                source_sync_enabled = bool(
                    config.get("source_sync_enabled") and self.trendradar_mcp_url
                )
                now = time.monotonic()
                if self._acquisition_interval != acquisition_interval or self._acquisition_enabled != acquisition_enabled:
                    self._acquisition_interval = acquisition_interval
                    self._acquisition_enabled = acquisition_enabled
                    self._next_acquisition_at = now + acquisition_interval * 60
                if self._generation_interval != generation_interval or self._generation_enabled != generation_enabled:
                    self._generation_interval = generation_interval
                    self._generation_enabled = generation_enabled
                    self._next_generation_at = now + generation_interval * 60
                if self._source_sync_interval != source_sync_interval or self._source_sync_enabled != source_sync_enabled:
                    self._source_sync_interval = source_sync_interval
                    self._source_sync_enabled = source_sync_enabled
                    self._next_source_sync_at = now + source_sync_interval * 60
                if source_sync_enabled and now >= self._next_source_sync_at:
                    if self.launch_source_sync():
                        self._next_source_sync_at = now + source_sync_interval * 60
                if acquisition_enabled and now >= self._next_acquisition_at:
                    run_id = self.launch_full_pipeline(
                        trigger_type="scheduled",
                        auto_generate=False,
                    )
                    if run_id:
                        self._next_acquisition_at = now + acquisition_interval * 60
                if generation_enabled and now >= self._next_generation_at:
                    with self._connect() as db:
                        latest = db.execute(
                            """SELECT r.id FROM runs r
                               WHERE EXISTS (
                                 SELECT 1 FROM prompt_pool p
                                 WHERE p.run_id=r.id AND p.status='ready'
                               )
                               ORDER BY r.started_at DESC LIMIT 1"""
                        ).fetchone()
                    if latest and self.launch_generation(latest["id"]):
                        self._next_generation_at = now + generation_interval * 60
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
