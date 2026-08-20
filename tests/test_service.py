import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="trend-creative-test-"))

from app.service import FLOW_MODELS, TrendService, extract_json_object, utc_now  # noqa: E402


class TrendServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.temp_dir.name
        os.environ["GEMINI_API_KEY"] = "test-key"
        os.environ["FLOW_API_KEY"] = "test-key"
        self.service = TrendService()

    def tearDown(self):
        asyncio.run(self.service.http.aclose())
        self.temp_dir.cleanup()

    def test_extracts_json_from_fenced_response(self):
        self.assertEqual(extract_json_object('```json\n{"trends": []}\n```'), {"trends": []})

    def test_extracts_json_with_unescaped_control_character(self):
        self.assertEqual(
            extract_json_object('{"summary":"first\nsecond"}'),
            {"summary": "first\nsecond"},
        )

    def test_gemini_retries_malformed_json(self):
        async def exercise():
            responses = iter(("{invalid}", '{"trends": []}'))

            async def handler(_request):
                content = next(responses)
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": content}}]},
                )

            await self.service.http.aclose()
            self.service.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return await self.service._call_gemini("prompt", "model", attempts=2)

        with mock.patch("app.service.asyncio.sleep", new=mock.AsyncMock()):
            self.assertEqual(asyncio.run(exercise()), '{"trends": []}')

    def test_flow_catalog_excludes_2k_and_4k(self):
        self.assertTrue(FLOW_MODELS)
        self.assertFalse(any("2k" in model.lower() or "4k" in model.lower() for model in FLOW_MODELS))

    def test_visual_trends_do_not_require_evidence_or_a_final_limit(self):
        candidates = [
            self._candidate(f"candidate-{index}", f"Product {index}", "javascript:missing", None)
            for index in range(1, 7)
        ]
        config = {**self.service.get_config(), "final_count": 1}
        verification = {
            "verified_trends": [],
            "removed_trends": [{"candidate_id": "candidate-1", "reason": "Exceeds the maximum accepted limit"}],
        }
        result = self.service._verify_candidates(config, candidates, verification)
        self.assertEqual([item["status"] for item in result], ["ready"] * 6)
        self.assertTrue(all(not item["evidence"] for item in result))

        verification["removed_trends"] = [
            {"candidate_id": "candidate-1", "reason_code": "unsafe", "reason": "Unsafe product"}
        ]
        result = self.service._verify_candidates(config, candidates, verification)
        self.assertEqual(result[0]["status"], "rejected")

    def test_each_pipeline_stage_has_a_distinct_prompt(self):
        prompt = self.service._discovery_prompt(self.service.get_config())
        self.assertIn("worldwide social-media", prompt)
        self.assertIn("priority coverage rather than exclusive boundaries", prompt)
        self.assertIn("across every category", prompt)
        self.assertIn("Do not filter by product suitability", prompt)
        self.assertIn("Do not reject or omit a trend merely because", prompt)
        self.assertIn("risk_flags", prompt)
        self.assertNotIn('"rejected":', prompt)
        self.assertIn("Evidence URLs and publication times are optional", prompt)
        self.assertIn("strict JSON", prompt)
        self.assertIn("TikTok", prompt)
        self.assertIn("Reddit", prompt)
        classify_prompt = self.service._classification_prompt(
            self.service.get_config(), [self._candidate("candidate-1", "Trend", "", None)]
        )
        self.assertIn("creative-pattern extractor", classify_prompt)
        self.assertIn("zero, one, or multiple pattern entries", classify_prompt)
        self.assertIn("classified_trends", classify_prompt)
        self.assertIn("no unresolved risk flags", classify_prompt)
        trend = self._candidate("candidate-1", "Trend", "", None)
        trend.update({"id": "trend-1"})
        pool_prompt = self.service._prompt_pool_prompt([trend])
        self.assertIn("every supplied pattern-pool entry", pool_prompt)
        self.assertIn("exactly one prompt for every supplied trend_id", pool_prompt)
        self.assertIn("printed directly on the product", pool_prompt)
        self.assertIn("vehicle spare-tire cover", pool_prompt)
        flow_prompt = self.service._flow_prompt(self._candidate("candidate-1", "Trend", "", None))
        self.assertIn("printed directly on the single physical product", flow_prompt)
        self.assertIn("vehicle spare-tire cover", flow_prompt)
        self.assertIn("not digitally pasted", flow_prompt)

    def test_invalid_model_confidence_does_not_break_discovery(self):
        payload = {"trends": [{"topic_en": "Topic", "confidence": "unknown", "platforms": "X"}]}
        result = self.service._normalise_candidates(payload, 10)
        self.assertEqual(result[0]["confidence"], 0)
        self.assertEqual(result[0]["platforms"], [])

    def test_config_keeps_daily_scheduler_disabled_by_default(self):
        config = self.service.get_config()
        self.assertFalse(config["enabled"])
        self.assertFalse(config["auto_generate"])
        self.assertEqual(config["generation_schedule_time"], "10:00")
        self.assertEqual(config["acquisition_interval_minutes"], 165)
        self.assertEqual(config["generation_interval_minutes"], 90)
        self.assertEqual(config["images_per_trend"], 5)
        self.assertFalse(config["source_sync_enabled"])
        self.assertEqual(config["source_sync_interval_minutes"], 10)
        self.assertFalse(config["source_include_hotlists"])
        self.assertNotIn("final_count", config)

    def test_config_accepts_independent_recurring_intervals(self):
        config = self.service.save_config(
            {
                "acquisition_interval_minutes": 165,
                "generation_interval_minutes": 90,
            }
        )
        self.assertEqual(config["acquisition_interval_minutes"], 165)
        self.assertEqual(config["generation_interval_minutes"], 90)

    def test_pipeline_builds_separate_trend_and_prompt_pools_before_generation(self):
        discovery = {
            "trends": [
                {"topic_en": "Raw one", "topic_zh": "原始一", "evidence": []},
                {"topic_en": "Raw two", "topic_zh": "原始二", "evidence": []},
            ]
        }
        classified = {
            "classified_trends": [
                {"topic_en": "Angle one", "topic_zh": "角度一", "category": "culture", "evidence": []},
                {"topic_en": "Angle two", "topic_zh": "角度二", "category": "humor", "evidence": []},
            ]
        }

        async def exercise():
            responses = iter((json.dumps(discovery), json.dumps(classified)))

            async def fake_gemini(_prompt, _model, *, attempts):
                return next(responses)

            self.service._call_gemini = fake_gemini
            run_id = self.service.create_run("manual")
            config = self.service.get_config()
            await self.service._acquire_raw_trends(run_id, config)
            await self.service._classify_trend_pool(run_id, config)
            run = self.service.get_run(run_id)
            self.assertEqual(len(run["raw_trends"]), 2)
            self.assertEqual([item["category"] for item in run["trends"]], ["culture", "humor"])

            prompt_response = {
                "prompts": [
                    {"trend_id": item["id"], "prompt": f"Prompt for {item['topic_en']}"}
                    for item in run["trends"]
                ]
            }

            async def fake_prompt_gemini(_prompt, _model, *, attempts):
                return json.dumps(prompt_response)

            self.service._call_gemini = fake_prompt_gemini
            await self.service._create_prompt_pool(run_id, config, 1)
            with self.assertRaisesRegex(ValueError, "没有待生成提示词"):
                await self.service._create_prompt_pool(run_id, config, 1)
            return self.service.get_run(run_id), self.service.list_runs()

        run, runs = asyncio.run(exercise())
        self.assertEqual(len(run["prompt_pool"]), 2)
        self.assertTrue(all(item["used_count"] == 0 for item in run["prompt_pool"]))
        self.assertEqual(runs[0]["prompt_count"], 2)

    def test_invalid_classification_schema_never_promotes_raw_trends(self):
        run_id = self.service.create_run("manual")
        discovery = {
            "trends": [
                {
                    "topic_en": "Protected raw topic",
                    "topic_zh": "受保护的原始热点",
                    "summary_zh": "仅用于验证流程边界",
                    "risk_flags": ["ip", "public_figure"],
                }
            ]
        }
        self.service._update_run(run_id, raw_discovery=json.dumps(discovery), candidate_count=1)

        async def exercise():
            async def fake_gemini(_prompt, _model, *, attempts):
                return json.dumps({"verified_trends": discovery["trends"]})

            self.service._call_gemini = fake_gemini
            with self.assertRaisesRegex(ValueError, "可用图案池"):
                await self.service._classify_trend_pool(run_id, self.service.get_config())

        asyncio.run(exercise())
        self.assertEqual(self.service.get_run(run_id)["trends"], [])

    def test_generation_randomly_consumes_a_prompt_pool_entry(self):
        run_id = self.service.create_run("manual")
        trend = self._candidate("candidate-1", "Fresh", "", None)
        trend.pop("candidate_id")
        trend.update({"id": "trend-1", "status": "ready", "verification_note": "AI已拆分分类"})
        self.service._replace_trends(run_id, [trend])
        with self.service._connect() as db:
            db.execute(
                "INSERT INTO prompt_pool(id,run_id,trend_id,prompt,status,created_at) VALUES(?,?,?,?,?,?)",
                ("prompt-1", run_id, "trend-1", "POOL PROMPT ONLY", "ready", utc_now()),
            )

        async def exercise():
            async def fake_flow(prompt, _model):
                self.assertEqual(prompt, "POOL PROMPT ONLY")
                return "ok", b"image", "image/png"

            self.service._call_flow = fake_flow
            await self.service._generate_from_prompt_pool(run_id, self.service.get_config(), 1)

        asyncio.run(exercise())
        run = self.service.get_run(run_id)
        generation = run["trends"][0]["generations"][0]
        self.assertEqual(generation["prompt_id"], "prompt-1")
        self.assertEqual(generation["prompt"], "POOL PROMPT ONLY")
        self.assertEqual(run["prompt_pool"][0]["used_count"], 1)

    def test_generation_schedule_runs_without_acquisition_schedule(self):
        run_id = self.service.create_run("manual")
        trend = self._candidate("candidate-1", "Fresh", "", None)
        trend.pop("candidate_id")
        trend.update({"id": "trend-1", "status": "ready", "verification_note": "AI已提取可用图案"})
        self.service._replace_trends(run_id, [trend])
        with self.service._connect() as db:
            db.execute(
                "INSERT INTO prompt_pool(id,run_id,trend_id,prompt,status,created_at) VALUES(?,?,?,?,?,?)",
                ("prompt-1", run_id, "trend-1", "POOL PROMPT", "ready", utc_now()),
            )
        self.service.save_config({
            "enabled": False,
            "auto_generate": True,
            "generation_interval_minutes": 90,
        })
        self.service._generation_interval = 90
        self.service._generation_enabled = True
        self.service._next_generation_at = 0

        async def exercise():
            launched = []
            self.service.launch_generation = lambda selected_run_id: launched.append(selected_run_id) or True
            task = asyncio.create_task(self.service._scheduler_loop())
            for _ in range(100):
                if launched:
                    break
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return launched

        self.assertEqual(asyncio.run(exercise()), [run_id])

    def test_trendradar_source_sync_is_idempotent(self):
        self.service.trendradar_mcp_url = "http://trendradar-mcp:3333/mcp"

        async def fake_mcp(name, arguments):
            self.assertEqual(name, "get_latest_rss")
            self.assertTrue(arguments["include_summary"])
            return {
                "success": True,
                "data": [{
                    "title": "Major earthquake strikes Indonesia",
                    "feed_id": "bbc-world",
                    "feed_name": "BBC World",
                    "url": "https://example.com/quake?utm_source=rss",
                    "published_at": "Tue, 18 Aug 2026 10:00:00 GMT",
                    "author": "Reporter",
                    "summary": "A major earthquake was reported.",
                }],
            }

        self.service._call_mcp_tool = fake_mcp
        first = asyncio.run(self.service.sync_source_entries())
        second = asyncio.run(self.service.sync_source_entries())
        entries = self.service.list_source_entries()
        self.assertEqual(first, {"fetched": 1, "inserted": 1})
        self.assertEqual(second, {"fetched": 1, "inserted": 0})
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["url"], "https://example.com/quake")
        self.assertEqual(entries[0]["published_at"], "2026-08-18T10:00:00+00:00")

        async def annotate():
            async def fake_gemini(_prompt, _model, *, attempts):
                return json.dumps({"trends": [{
                    "cluster_id": "cluster-1",
                    "topic_en": "Major earthquake strikes Indonesia",
                    "topic_zh": "印度尼西亚发生强烈地震",
                    "summary_zh": "外媒报道印度尼西亚发生强烈地震。",
                }]})

            self.service._call_gemini = fake_gemini
            await self.service._raw_trends_from_source_entries(
                self.service.list_source_entries(), self.service.get_config()
            )

        asyncio.run(annotate())
        translated = self.service.list_source_entries()[0]
        self.assertEqual(translated["title_zh"], "印度尼西亚发生强烈地震")
        self.assertEqual(translated["summary_zh"], "外媒报道印度尼西亚发生强烈地震。")

    def test_source_entries_cluster_repeated_overseas_reports(self):
        entries = [
            {"title": "Powerful earthquake strikes eastern Indonesia", "source_id": "bbc", "published_at": "2", "fetched_at": "2"},
            {"title": "Powerful earthquake strikes Indonesia coast", "source_id": "reuters", "published_at": "1", "fetched_at": "1"},
            {"title": "New phone launches with folding display", "source_id": "tech", "published_at": "3", "fetched_at": "3"},
        ]
        clusters = self.service._cluster_source_entries(entries)
        self.assertEqual(sorted(len(cluster) for cluster in clusters), [1, 2])

    def test_external_flow_image_download_does_not_receive_api_key(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(200, json={"choices": [{"message": {"content": "https://cdn.example/image.png"}}]})
            return httpx.Response(200, content=b"image", headers={"content-type": "image/png"})

        asyncio.run(self.service.http.aclose())
        self.service.flow_base_url = "https://flow.example"
        self.service.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        asyncio.run(self.service._call_flow("prompt", FLOW_MODELS[0]))

        self.assertEqual(requests[0].headers["authorization"], "Bearer test-key")
        self.assertNotIn("authorization", requests[1].headers)

    def test_cancelled_generation_restores_selectable_trend_status(self):
        run_id = self.service.create_run("manual")
        trend = self._candidate("candidate-1", "Fresh", "https://x.com/example/status/1", utc_now())
        trend.pop("candidate_id")
        trend.update({"id": "trend-1", "status": "ready", "verification_note": "核验通过"})
        self.service._replace_trends(run_id, [trend])

        async def exercise():
            async def wait_forever(_prompt, _model):
                await asyncio.Event().wait()

            self.service._call_flow = wait_forever
            stored = self.service.get_run(run_id)["trends"][0]
            task = asyncio.create_task(self.service._generate_one(run_id, stored, 1, self.service.get_config()))
            while not self.service.get_run(run_id)["trends"][0]["generations"]:
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(exercise())
        stored = self.service.get_run(run_id)["trends"][0]
        self.assertEqual(stored["status"], "ready")
        self.assertEqual(stored["generations"][0]["status"], "failed")
        self.assertEqual(stored["generations"][0]["error"], "任务已取消")

    @staticmethod
    def _candidate(candidate_id, title, url, published_at):
        return {
            "candidate_id": candidate_id,
            "rank": int(candidate_id.rsplit("-", 1)[1]),
            "topic_en": title,
            "topic_zh": title,
            "summary_zh": "summary",
            "why_trending": "reason",
            "platforms": ["X"],
            "region": "Global",
            "category": "culture",
            "first_seen_at": published_at,
            "engagement_signal": "signal",
            "evidence": [{"url": url, "published_at": published_at, "platform": "X"}],
            "confidence": 0.8,
            "visual_brief_en": "editorial visual",
            "risk_flags": [],
        }


class StaticPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    def test_separate_pipeline_controls_are_present(self):
        main = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("① 获取热点", self.html)
        self.assertIn("② 提取可用图案", self.html)
        self.assertIn("③ 补齐全部提示词", self.html)
        self.assertIn("④ 随机产品生图", self.html)
        self.assertIn("获取全部热点，并在同一任务中依次建立可用图案池和提示词池", self.html)
        self.assertIn('launch_full_pipeline(trigger_type="manual", auto_generate=False)', main)
        self.assertIn("热点来源平台", self.html)
        self.assertIn("优先地区（全球搜索", self.html)
        self.assertNotIn("生成所选热点", self.html)

    def test_each_pool_has_a_clickable_module_page(self):
        main = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        for path, label in (
            ("/acquire", "全部热点"),
            ("/trends", "可用图案"),
            ("/prompts", "生成提示词"),
            ("/images", "成品图库"),
        ):
            self.assertIn(f'@app.get("{path}")', main)
            self.assertIn(label, self.html)
        for label in ("总览", "信息采集", "AI 创意", "成品图库"):
            self.assertIn(label, self.html)
        self.assertEqual(self.html.count('class="module-nav"'), 1)
        self.assertEqual(self.html.count('data-group="'), 4)
        self.assertIn('id="moduleTabs"', self.html)
        self.assertIn("groupTabs", self.html)
        self.assertIn("renderModuleTabs", self.html)
        self.assertIn('id="moduleContent"', self.html)
        self.assertIn("renderModuleContent", self.html)
        self.assertIn("moduleEntries", self.html)
        self.assertIn("new Date(b.date)-new Date(a.date)", self.html)
        self.assertIn("cardAttrs", self.html)
        self.assertIn("safeHttpUrl", self.html)
        self.assertIn("风险标记：", self.html)

    def test_feed_source_and_entry_pages_are_independent(self):
        main = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        for path, label in (("/sources", "媒体源"), ("/signals", "原始资讯")):
            self.assertIn(f'@app.get("{path}")', main)
            self.assertIn(label, self.html)
        self.assertIn("/api/sources/sync", self.html)
        self.assertIn("/api/signals/${encodeURIComponent(entryId)}", self.html)

    def test_source_entry_cards_show_ai_chinese_translation(self):
        self.assertIn("item.title_zh||item.title", self.html)
        self.assertIn("item.summary_zh||item.summary", self.html)
        self.assertIn("AI 中文翻译", self.html)
        self.assertIn("renderSourceContent", self.html)
        self.assertIn("renderSignalContent", self.html)
        self.assertIn("openSignal", self.html)
        self.assertIn("打开外媒原文", self.html)

    def test_source_settings_and_trendradar_connection_are_exposed(self):
        for element_id in (
            "cfgSourceSync", "cfgSourceInterval", "cfgSourceRetention", "cfgSourceHotlists"
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        for config_key in (
            "source_sync_enabled", "source_sync_interval_minutes",
            "source_retention_days", "source_include_hotlists",
        ):
            self.assertIn(config_key, self.html)
        self.assertIn("result.trendradar.ok", self.html)
        self.assertIn(
            "TRENDRADAR_MCP_URL=http://host.docker.internal:3333/mcp",
            (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8"),
        )

    def test_pool_cards_open_the_selected_content_inside_its_run(self):
        self.assertIn("currentSelection", self.html)
        self.assertIn("cardAttrs(run,'raw',item.candidate_id)", self.html)
        self.assertIn("cardAttrs(run,'trend',item.id)", self.html)
        self.assertIn("cardAttrs(run,'prompt',item.id)", self.html)
        self.assertIn("cardAttrs(run,'image',item.id)", self.html)
        self.assertIn("selectedContent", self.html)
        self.assertIn("renderRawDetail", self.html)
        self.assertIn("点击卡片查看对应内容", self.html)

    def test_stylekit_japanese_fresh_theme_is_applied(self):
        self.assertIn("Japanese Fresh", (PROJECT_ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("#e8eee8", self.html)
        self.assertIn("Yeseva One", self.html)
        self.assertIn('class="botanical"', self.html)
        self.assertIn("prefers-reduced-motion", self.html)
        self.assertIn("box-shadow:var(--shadow)", self.html)

    def test_acquisition_and_generation_have_separate_schedule_controls(self):
        self.assertIn('id="cfgAcquireInterval"', self.html)
        self.assertIn('id="cfgGenerationInterval"', self.html)
        self.assertIn("acquisition_interval_minutes", self.html)
        self.assertIn("generation_interval_minutes", self.html)
        self.assertIn("热点、图案与提示词生成间隔（分钟）", self.html)
        self.assertIn("每轮随机生图数（1–30）", self.html)

    def test_generated_images_open_in_an_accessible_viewer(self):
        self.assertIn('id="imageDialog"', self.html)
        self.assertIn('onclick="openImage(this)"', self.html)
        self.assertIn('aria-label="关闭放大图"', self.html)
        self.assertIn("cursor:zoom-in", self.html)

    def test_page_connects_automatically_without_admin_key(self):
        main = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("已自动连接", self.html)
        self.assertIn("renderPage();loadState();", self.html)
        self.assertNotIn('id="adminKey"', self.html)
        self.assertNotIn("Authorization:`Bearer", self.html)
        self.assertNotIn("require_admin", main)
        self.assertNotIn("ADMIN_KEY", env_example)
        self.assertNotIn("localStorage", self.html)
        self.assertNotIn("sessionStorage", self.html)
        self.assertNotIn("document.cookie", self.html)

    def test_related_service_frontends_are_linked_by_current_host(self):
        self.assertIn("检测上游服务", self.html)
        self.assertIn("打开 TrendRadar", self.html)
        self.assertIn("serviceUrl(8080)", self.html)
        self.assertNotIn("关联服务", self.html)
        self.assertNotIn("data-service-port", self.html)
        self.assertNotIn("RSSHub", self.html)
        self.assertNotIn("NewsNow", self.html)
        self.assertIn('target="_blank"', self.html)

    def test_project_update_button_uses_the_update_api(self):
        main = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('id="updateBtn"', self.html)
        self.assertIn("updateProject()", self.html)
        self.assertIn("watchProjectUpdate", self.html)
        self.assertIn('@app.post("/api/system/update", status_code=202)', main)
        self.assertIn("当前有热点或生图任务运行", main)


if __name__ == "__main__":
    unittest.main()
