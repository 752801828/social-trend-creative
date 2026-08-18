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
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit
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
    return re.sub(r"(?i)(authorization|api[_-]?key|bearer)\s*[:=]?\s*\S+", r"\1=***", str(exc))[:1000]


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
                    published_at TEXT,
                    fetched_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    raw_payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_entries_date
                    ON source_entries(published_at DESC, fetched_at DESC);
                CREATE INDEX IF NOT EXISTS idx_source_entries_source
                    ON source_entries(source_id, published_at DESC);
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
            if not db.execute("SELECT 1 FROM settings WHERE id = 1").fetchone():
                db.execute(
                    "INSERT INTO settings(id, value, updated_at) VALUES(1, ?, ?)",
                    (json_text(DEFAULT_CONFIG), utc_now()),
                )
            db.execute(
                "INSERT OR IGNORE INTO source_sync_state(id,status) VALUES(1,'idle')"
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

        async with streamable_http_client(self.trendradar_mcp_url) as streams:
            read_stream, write_stream = streams[:2]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.call_tool(name, arguments=arguments)
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
            self.sync_source_entries(), name="trendradar-source-sync"
        )
        return True

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
        values.append(min(5000, max(1, limit)))
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT id,external_id,source_kind,source_id,source_name,platform,title,url,
                           author,summary,published_at,fetched_at,content_hash
                    FROM source_entries {where}
                    ORDER BY COALESCE(published_at,fetched_at) DESC LIMIT ?""",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_source_entry(self, entry_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT id,external_id,source_kind,source_id,source_name,platform,title,url,
                          author,summary,published_at,fetched_at,content_hash
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
        batch_size = int(config["candidate_count"])
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
        return self._normalise_candidates({"trends": raw_trends}, None)

    async def _acquire_raw_trends(self, run_id: str, config: dict[str, Any]) -> list[dict[str, Any]]:
        self._update_run(run_id, status="running", stage="acquisition", error="")
        if self.trendradar_mcp_url:
            await self.sync_source_entries()
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
        batch_size = int(config["candidate_count"])
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
        supplied_map = {}
        batch_size = int(config["candidate_count"])
        for offset in range(0, len(trends), batch_size):
            response_text = await self._call_gemini(
                self._prompt_pool_prompt(trends[offset:offset + batch_size]),
                config["gemini_verification_model"],
                attempts=2,
            )
            payload = extract_json_object(response_text)
            supplied = payload.get("prompts") if isinstance(payload.get("prompts"), list) else []
            supplied_map.update({
                str(item.get("trend_id")): str(item.get("prompt") or "").strip()
                for item in supplied if isinstance(item, dict)
            })
        prompt_ids = []
        with self._connect() as db:
            for trend in trends:
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

Extract only original, reusable, production-safe visual pattern directions from the raw trends. A raw trend may produce zero, one, or multiple pattern entries. Split distinct motifs, merge duplicate directions, and assign a concise category such as culture, humor, lifestyle, seasonal, sports, technology, nature, travel, food, pets, or social mood.

The acquisition input intentionally includes every trend type. Do not copy or retain logos, trademarks, copyrighted characters, distinctive existing artwork, public-figure likenesses, hate symbols, explicit/adult imagery, graphic violence, misinformation claims, or unsafe instructions. When a protected or sensitive trend has a meaningful generic underlying mood, shape language, color story, community feeling, or visual metaphor, extract only that non-infringing generic direction without implying endorsement or association; otherwise output no pattern for it. Missing evidence or publication time is not itself a rejection reason.

Every classified_trends item is an accepted pattern-pool entry. It must preserve enough factual context to explain the inspiration, contain a concrete reusable visual motif and mood, have no unresolved risk flags, and remain independent of any physical product. Do not write the final image prompt or choose a product in this stage. Return strict JSON only.

Candidates:
{json.dumps(candidates, ensure_ascii=False)}

Schema:
{{
  "classified_trends": [
    {{
      "topic_en":"independent safe pattern direction",
      "topic_zh":"独立可用图案方向",
      "summary_zh":"事实摘要",
      "why_trending":"传播原因",
      "platforms":["X"],
      "region":"Global",
      "category":"culture",
      "first_seen_at":null,
      "engagement_signal":"signal or null",
      "evidence":[],
      "confidence":0.8,
      "visual_brief_en":"Concrete original visual motif, composition, palette, texture, and mood; not a finished prompt and no product",
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
        return f"""You create production-ready image prompts for every supplied pattern-pool entry extracted from worldwide social trends.

For every input trend_id, write one complete English prompt for a realistic print-on-demand product rendering. Keep the safe pattern direction and category, select one suitable physical item, and fully specify the artwork, placement, scale, print treatment, product color, material, camera angle, lighting, and neutral setting. The final image must show the pattern already printed directly on the product, never as separate flat artwork.

Rules:
1. One prompt must show one main product only: mug, tumbler, phone case, T-shirt, hoodie, tote bag, cushion, blanket, vehicle spare-tire cover, sticker, poster, or another clearly named printable item.
2. The artwork must conform naturally to curvature, seams, folds, and material and look genuinely printed.
3. Do not use logos, trademarks, copyrighted characters, public-figure likenesses, copied posts, watermarks, or existing artwork.
4. Avoid text unless essential; if used, it must be short, generic, and correctly spelled.
5. Return exactly one prompt for every supplied trend_id, preserve every trend_id exactly, and return strict JSON only.

Pattern-pool entries:
{json.dumps(compact, ensure_ascii=False)}

Schema:
{{"prompts":[{{"trend_id":"id","prompt":"complete English image prompt"}}]}}"""

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
                "candidate_id": f"candidate-{index}",
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
            item.pop("candidate_id", None)
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
                """SELECT r.*,
                          (SELECT COUNT(*) FROM prompt_pool p WHERE p.run_id=r.id) AS prompt_count
                   FROM runs r ORDER BY r.started_at DESC LIMIT ?""",
                (min(200, max(1, limit)),),
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
                    extract_json_object(result["raw_discovery"]), None
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
            source_total = db.execute("SELECT COUNT(*) FROM source_entries").fetchone()[0]
            source_recent = db.execute(
                "SELECT COUNT(*) FROM source_entries WHERE fetched_at>=?",
                ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),),
            ).fetchone()[0]
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
