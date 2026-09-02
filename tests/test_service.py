import asyncio
import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

import httpx
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="trend-creative-test-"))

from app.service import (  # noqa: E402
    FLOW_MODELS, SELLABILITY_METRICS, TrendService, extract_json_object, utc_now,
)


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

    def test_extracts_json5_from_gemini_response(self):
        self.assertEqual(
            extract_json_object("```json\n{trends: [{topic_en: 'A',}],}\n```")['trends'][0]['topic_en'],
            "A",
        )

    def test_safe_error_expands_exception_group(self):
        from app.service import safe_error

        error = ExceptionGroup("TaskGroup failed", [TimeoutError("MCP read timed out")])
        self.assertIn("TimeoutError: MCP read timed out", safe_error(error))

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

    def test_gemini_safe_batch_size_caps_long_json_stages(self):
        from app.service import GEMINI_SAFE_BATCH_SIZE

        self.assertEqual(GEMINI_SAFE_BATCH_SIZE, 5)
        self.assertIn("json5>=0.9,<1", (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8"))
        self.assertIn("json-repair>=0.30,<1", (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8"))

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
        self.assertIn("one or multiple pattern entries", classify_prompt)
        self.assertIn("classified_trends", classify_prompt)
        self.assertIn("no unresolved risk flags", classify_prompt)
        self.assertIn("Do not require the source event to already be", classify_prompt)
        self.assertIn("Preserve the event's recognizable narrative anchor", classify_prompt)
        self.assertIn("American-football players in contrasting unbranded practice uniforms", classify_prompt)
        self.assertIn("not merely name an art style or mood", classify_prompt)
        self.assertIn("return zero only when no safe", classify_prompt)
        trend = self._candidate("candidate-1", "Trend", "", None)
        trend.update({"id": "trend-1"})
        pool_prompt = self.service._prompt_pool_prompt([trend])
        self.assertIn('"topic_zh": "Trend"', pool_prompt)
        self.assertIn("every supplied pattern-pool entry", pool_prompt)
        self.assertIn("exactly one prompt pair for every supplied trend_id", pool_prompt)
        self.assertIn("pattern_prompt and a product_prompt", pool_prompt)
        self.assertIn("transparent-background PNG", pool_prompt)
        self.assertIn("no full-canvas background", pool_prompt)
        self.assertIn("uniform pure white (#FFFFFF)", pool_prompt)
        self.assertIn("printed directly on the product", pool_prompt)
        self.assertIn("vehicle spare-tire cover", pool_prompt)
        self.assertIn("must be either a vehicle spare-tire cover or a phone case", pool_prompt)
        self.assertNotIn("mug, tumbler, phone case, T-shirt", pool_prompt)
        self.assertIn("roughly 140–240 English words", pool_prompt)
        self.assertIn("attached generated pattern image as the exact artwork reference", pool_prompt)
        self.assertIn("icon set, emblem, badge", pool_prompt)
        self.assertIn("generic unbranded equivalents", pool_prompt)
        sellability_prompt = self.service._sellability_prompt([trend])
        self.assertIn("identity expression, commemoration, gifting", sellability_prompt)
        self.assertIn("independent-source coverage, discussion velocity", sellability_prompt)
        self.assertIn("recognizable search terms", sellability_prompt)
        self.assertIn("live competitor listings are unavailable", sellability_prompt)
        self.assertIn("Every judgement must cite concrete facts", sellability_prompt)
        self.assertIn("Never state invented sales, search-volume", sellability_prompt)
        pattern_flow_prompt = self.service._pattern_flow_prompt(trend)
        self.assertIn("standalone, original, print-ready artwork", pattern_flow_prompt)
        self.assertIn("transparent-background PNG", pattern_flow_prompt)
        self.assertIn("never a rectangular background scene", pattern_flow_prompt)
        isolated_prompt = self.service._isolated_pattern_prompt("DRAW THE ART")
        self.assertIn("MANDATORY OUTPUT FORMAT", isolated_prompt)
        self.assertIn("Every area outside the artwork must be transparent", isolated_prompt)
        self.assertIn("Never simulate transparency with a checkerboard", isolated_prompt)
        self.assertIn("DRAW THE ART", isolated_prompt)
        product_reference_prompt = self.service._product_reference_prompt("PRODUCT")
        self.assertIn("outer white space as transparency", product_reference_prompt)
        self.assertIn("do not print it as a white rectangle", product_reference_prompt)
        flow_prompt = self.service._flow_prompt(self._candidate("candidate-1", "Trend", "", None))
        self.assertIn("printed directly on the single physical product", flow_prompt)
        self.assertIn("vehicle spare-tire cover", flow_prompt)
        self.assertIn("choose exactly one of: vehicle spare-tire cover or phone case", flow_prompt)
        self.assertNotIn("mug, tumbler", flow_prompt)
        self.assertIn("not digitally pasted", flow_prompt)
        self.assertIn("Keep the source event recognizable", flow_prompt)
        sellability_prompt = self.service._sellability_prompt([trend])
        self.assertIn("identity expression, commemoration, gifting", sellability_prompt)
        self.assertIn("independent-source coverage", sellability_prompt)
        self.assertIn("do not invent search-volume data", sellability_prompt)
        self.assertIn("live competitor listings are unavailable", sellability_prompt)
        self.assertIn("Every judgement must cite concrete facts or limitations", sellability_prompt)

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
                    {
                        "trend_id": item["id"],
                        "pattern_prompt": f"Pattern for {item['topic_en']}",
                        "product_prompt": f"Product for {item['topic_en']}",
                    }
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
        self.assertTrue(all(item["pattern_prompt"].startswith("Pattern for") for item in run["prompt_pool"]))
        self.assertEqual(runs[0]["prompt_count"], 2)
        self.assertEqual(self.service.list_pool_cards("acquire", 1, 0)["total"], 2)
        self.assertEqual(len(self.service.list_pool_cards("acquire", 1, 0)["entries"]), 1)
        self.assertEqual(self.service.list_pool_cards("trends", 1, 1)["total"], 2)
        self.assertEqual(len(self.service.list_pool_cards("trends", 1, 1)["entries"]), 1)
        self.assertEqual(self.service.list_pool_cards("prompts", 1, 0)["total"], 2)

    def test_run_list_omits_large_ai_responses(self):
        run_id = self.service.create_run("manual")
        self.service._update_run(
            run_id,
            raw_discovery='{"trends":[]}',
            raw_verification='{"classified_trends":[]}',
        )
        summary = self.service.list_runs()[0]
        self.assertNotIn("raw_discovery", summary)
        self.assertNotIn("raw_verification", summary)
        self.assertEqual(summary["id"], run_id)

    def test_dashboard_aggregates_platform_counts_and_card_indexes_exist(self):
        run_id = self.service.create_run("manual")
        trends = []
        for index, platforms in enumerate((["X", "Reddit"], ["X"], ["YouTube"]), 1):
            trend = self._candidate(f"candidate-{index}", f"Trend {index}", "", utc_now())
            trend.pop("candidate_id")
            trend.update({
                "id": f"trend-{index}",
                "platforms": platforms,
                "status": "rejected" if index == 3 else "ready",
                "verification_note": "test",
            })
            trends.append(trend)
        self.service._replace_trends(run_id, trends)

        self.assertEqual(
            self.service.dashboard()["platforms"],
            [{"name": "X", "count": 2}, {"name": "Reddit", "count": 1}],
        )
        with self.service._connect() as db:
            indexes = {
                row["name"] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            query_plans = " ".join(
                row["detail"] for sql in (
                    "SELECT * FROM trends ORDER BY created_at DESC LIMIT 24",
                    "SELECT * FROM prompt_pool ORDER BY created_at DESC LIMIT 24",
                    "SELECT * FROM pattern_assets ORDER BY created_at DESC LIMIT 24",
                    "SELECT * FROM generations ORDER BY created_at DESC LIMIT 24",
                    "SELECT COUNT(*) FROM source_entries WHERE fetched_at>='2026-01-01'",
                    "SELECT * FROM source_entries ORDER BY COALESCE(published_at,fetched_at) DESC LIMIT 24",
                    "SELECT * FROM source_entries WHERE source_id='source' ORDER BY COALESCE(published_at,fetched_at) DESC LIMIT 24",
                ) for row in db.execute(f"EXPLAIN QUERY PLAN {sql}")
            )
        self.assertTrue({
            "idx_trends_created", "idx_prompt_pool_created",
            "idx_pattern_assets_created", "idx_generations_created",
            "idx_source_entries_fetched", "idx_source_entries_display_date",
            "idx_source_entries_source_display_date",
        }.issubset(indexes))
        for index in (
            "idx_trends_created", "idx_prompt_pool_created",
            "idx_pattern_assets_created", "idx_generations_created",
            "idx_source_entries_fetched", "idx_source_entries_display_date",
            "idx_source_entries_source_display_date",
        ):
            self.assertIn(index, query_plans)

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

    def test_generation_creates_pattern_then_product_from_the_same_prompt(self):
        run_id = self.service.create_run("manual")
        trend = self._candidate("candidate-1", "Fresh", "", None)
        trend.pop("candidate_id")
        trend.update({"id": "trend-1", "status": "ready", "verification_note": "AI已拆分分类"})
        self.service._replace_trends(run_id, [trend])
        with self.service._connect() as db:
            db.execute(
                """INSERT INTO prompt_pool
                   (id,run_id,trend_id,pattern_prompt,prompt,status,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                ("prompt-1", run_id, "trend-1", "PATTERN PROMPT", "PRODUCT PROMPT", "ready", utc_now()),
            )

        async def exercise():
            calls = []
            source = BytesIO()
            Image.new("RGB", (64, 64), "white").save(source, format="JPEG")
            product = BytesIO()
            Image.new("RGB", (64, 64), "blue").save(product, format="PNG")

            async def fake_flow(prompt, _model, reference_image=None):
                calls.append((prompt, reference_image))
                return "ok", source.getvalue() if reference_image is None else product.getvalue(), "image/jpeg" if reference_image is None else "image/png"

            self.service._call_flow = fake_flow
            await self.service._generate_from_prompt_pool(run_id, self.service.get_config(), 1)
            return calls

        calls = asyncio.run(exercise())
        run = self.service.get_run(run_id)
        pattern = run["pattern_assets"][0]
        generation = run["trends"][0]["generations"][0]
        self.assertIn("MANDATORY OUTPUT FORMAT", calls[0][0])
        self.assertIn("PATTERN PROMPT", calls[0][0])
        self.assertIsNone(calls[0][1])
        self.assertIn("exact finished artwork reference", calls[1][0])
        self.assertIn("PRODUCT PROMPT", calls[1][0])
        self.assertEqual(calls[1][1][1], "image/png")
        self.assertTrue(calls[1][1][0].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(pattern["prompt_id"], "prompt-1")
        self.assertIn("MANDATORY OUTPUT FORMAT", pattern["prompt"])
        self.assertIn("PATTERN PROMPT", pattern["prompt"])
        self.assertEqual(generation["prompt_id"], "prompt-1")
        self.assertEqual(generation["pattern_asset_id"], pattern["id"])
        self.assertIn("exact finished artwork reference", generation["prompt"])
        self.assertIn("PRODUCT PROMPT", generation["prompt"])
        self.assertEqual(run["prompt_pool"][0]["used_count"], 1)
        self.assertEqual(self.service.list_runs()[0]["pattern_count"], 1)
        self.assertEqual(self.service.list_pool_cards("patterns", 1, 0)["total"], 1)
        self.assertEqual(self.service.list_pool_cards("images", 1, 0)["total"], 1)

    def test_pattern_cleanup_outputs_real_transparent_png(self):
        source = Image.new("RGB", (80, 80), "white")
        for x in range(24, 56):
            for y in range(24, 56):
                source.putpixel((x, y), (220, 30, 50))
        buffer = BytesIO()
        source.save(buffer, format="JPEG", quality=95)

        payload, transparent, removed = self.service._prepare_pattern_png(buffer.getvalue())

        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(transparent)
        self.assertTrue(removed)
        with Image.open(BytesIO(payload)) as output:
            self.assertEqual(output.format, "PNG")
            self.assertEqual(output.mode, "RGBA")
            self.assertEqual(output.getpixel((0, 0))[3], 0)
            self.assertGreater(output.getpixel((40, 40))[3], 240)

    def test_fake_checkerboard_transparency_is_sent_to_rembg(self):
        source = Image.new("RGB", (96, 96))
        for x in range(96):
            for y in range(96):
                shade = 220 if (x // 12 + y // 12) % 2 else 245
                source.putpixel((x, y), (shade, shade, shade))
        buffer = BytesIO()
        source.save(buffer, format="PNG")

        payload, transparent, _removed = self.service._prepare_pattern_png(buffer.getvalue())

        self.assertTrue(transparent)
        self.assertLess(self.service._transparent_border_ratio(payload), 0.8)

    def test_sellability_score_is_server_computed_and_controls_quota(self):
        raw = {
            "metrics": {
                "shopping_intent": {"score": 99, "judgement": "high"},
                "social_commercial_heat": {"score": 20, "judgement": "high"},
                "search_growth": {"score": 15, "judgement": "high"},
                "product_fit": {"score": 15, "judgement": "high"},
                "audience_clarity": {"score": 10, "judgement": "high"},
                "lifespan": {"score": 10, "judgement": "high"},
                "competition_opportunity": {"score": 5, "judgement": "high"},
            }
        }
        score = self.service._normalise_sellability_item(raw)
        self.assertEqual(score["total_score"], 100)
        self.assertEqual((score["grade"], score["pattern_quota"], score["products_per_pattern"]), ("A", 3, 2))

        low = self.service._normalise_sellability_item({"metrics": {}})
        self.assertLess(low["total_score"], 60)
        self.assertEqual((low["grade"], low["pattern_quota"], low["products_per_pattern"]), ("D", 1, 1))

    def test_sellability_pool_supports_score_sorting_and_grade_filter(self):
        run_id = self.service.create_run("manual")
        score = self.service._normalise_sellability_item({
            "metrics": {
                key: {"score": maximum, "judgement": "strong"}
                for key, _label, maximum in SELLABILITY_METRICS
            },
            "recommended_products": ["mug", "phone case"],
        })
        with self.service._connect() as db:
            db.execute(
                """INSERT INTO raw_sellability_pool
                   (id,run_id,candidate_id,topic_en,topic_zh,summary_zh,category,region,
                    total_score,grade,metrics,target_audience,recommended_products,valid_window,
                    sales_reason,risk_level,risk_reasons,pattern_quota,products_per_pattern,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("score-1", run_id, "candidate-1", "Sellable trend", "可卖热点", "summary",
                 "sports", "US", score["total_score"], score["grade"],
                 json.dumps(score["metrics"]), "fans", json.dumps(score["recommended_products"]),
                 "now", "reason", "low", "[]", score["pattern_quota"],
                 score["products_per_pattern"], utc_now()),
            )
        result = self.service.list_pool_cards("sellability", grade="A", sort="score_desc")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["entries"][0]["item"]["total_score"], 100)
        self.assertEqual(result["entries"][0]["item"]["recommended_products"], ["phone case"])

    def test_sellability_backfill_scores_old_runs_and_preserves_terminal_status(self):
        run_id = self.service.create_run("manual")
        raw = {"trends": [{"topic_en": "Old trend", "topic_zh": "旧热点", "evidence": []}]}
        self.service._update_run(
            run_id, raw_discovery=json.dumps(raw), candidate_count=1,
            status="completed", stage="finished", finished_at=utc_now(),
        )

        async def exercise():
            async def fake_gemini(prompt, _model, *, attempts):
                self.assertIn("candidate-1", prompt)
                return json.dumps({"scores": [{
                    "trend_id": "candidate-1",
                    "metrics": {
                        key: {"score": maximum, "judgement": "strong"}
                        for key, _label, maximum in SELLABILITY_METRICS
                    },
                }]})

            self.service._call_gemini = fake_gemini
            await self.service._backfill_sellability([run_id])

        asyncio.run(exercise())
        run = self.service.get_run(run_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["raw_sellability_pool"][0]["total_score"], 100)
        progress = self.service.sellability_state()
        self.assertEqual(progress["status"], "succeeded")
        self.assertEqual(progress["completed_runs"], 1)
        self.assertEqual(progress["scored_directions"], 1)
        self.assertEqual(progress["pending_directions"], 0)
        acquire = self.service.list_pool_cards("acquire")
        self.assertEqual(acquire["entries"][0]["item"]["sellability"]["total_score"], 100)
        candidates = self.service.list_pool_cards("sellability")
        self.assertEqual(candidates["entries"][0]["trend"]["topic_zh"], "旧热点")

    def test_startup_repairs_zero_candidate_count_for_old_raw_runs(self):
        run_id = self.service.create_run("manual")
        self.service._update_run(
            run_id,
            raw_discovery=json.dumps({"trends": [
                {"topic_en": "One"}, {"topic_en": "Two"},
            ]}),
            candidate_count=0,
        )
        repaired = TrendService()
        try:
            run = next(item for item in repaired.list_runs() if item["id"] == run_id)
            self.assertEqual(run["candidate_count"], 2)
            self.assertEqual(repaired.sellability_state()["total_directions"], 2)
        finally:
            asyncio.run(repaired.http.aclose())

    def test_raw_sellability_is_copied_to_classified_direction_quota(self):
        run_id = self.service.create_run("manual")
        score = self.service._normalise_sellability_item({
            "metrics": {
                key: {"score": maximum, "judgement": "strong"}
                for key, _label, maximum in SELLABILITY_METRICS
            }
        })
        with self.service._connect() as db:
            db.execute(
                """INSERT INTO raw_sellability_pool
                   (id,run_id,candidate_id,topic_en,topic_zh,summary_zh,category,region,
                    total_score,grade,metrics,target_audience,recommended_products,valid_window,
                    sales_reason,risk_level,risk_reasons,pattern_quota,products_per_pattern,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("raw-score", run_id, "candidate-1", "Topic", "热点", "summary", "sports", "US",
                 100, "A", json.dumps(score["metrics"]), "fans", '["T-shirt"]', "now",
                 "reason", "low", "[]", 3, 2, utc_now()),
            )
        trend = self._candidate("candidate-1", "Direction", "", None)
        trend.pop("candidate_id")
        trend.update({
            "id": "trend-linked", "source_candidate_id": "candidate-1",
            "status": "ready", "verification_note": "linked",
        })
        self.service._replace_trends(run_id, [trend])
        self.service._copy_raw_sellability_to_trends(run_id)
        linked = self.service.get_run(run_id)["trends"][0]["sellability"]
        self.assertEqual((linked["grade"], linked["pattern_quota"], linked["products_per_pattern"]), ("A", 3, 2))

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

    def test_full_pipeline_does_not_wait_for_sellability_scoring(self):
        run_id = self.service.create_run("manual")
        self.service._acquire_raw_trends = mock.AsyncMock()
        self.service._classify_trend_pool = mock.AsyncMock()
        self.service._create_prompt_pool = mock.AsyncMock()
        self.service._score_sellability_pool = mock.AsyncMock(side_effect=AssertionError("scoring must be independent"))

        asyncio.run(self.service._run_stage(run_id, "full", auto_generate=False))

        self.service._acquire_raw_trends.assert_awaited_once()
        self.service._classify_trend_pool.assert_awaited_once()
        self.service._create_prompt_pool.assert_awaited_once()
        self.service._score_sellability_pool.assert_not_awaited()

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
        self.assertEqual(self.service.count_source_entries(), 1)
        self.assertEqual(self.service.list_source_entries(offset=1), [])
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

    def test_acquisition_reuses_recent_source_sync(self):
        self.service._update_source_sync_state(
            status="succeeded", last_success_at=utc_now(), error=""
        )
        self.service.sync_source_entries = mock.AsyncMock()
        asyncio.run(self.service._sync_sources_for_acquisition())
        self.service.sync_source_entries.assert_not_awaited()

    def test_mcp_timeout_configuration_is_documented(self):
        self.assertIn(
            "MCP_TIMEOUT_SECONDS=300",
            (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "asyncio.timeout(timeout_seconds)",
            (PROJECT_ROOT / "app" / "service.py").read_text(encoding="utf-8"),
        )
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
        asyncio.run(self.service._call_flow(
            "prompt", FLOW_MODELS[0], (b"pattern", "image/png")
        ))

        self.assertEqual(requests[0].headers["authorization"], "Bearer test-key")
        content = json.loads(requests[0].content)["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "prompt"})
        self.assertEqual(
            content[1]["image_url"]["url"],
            "data:image/png;base64,cGF0dGVybg==",
        )
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
        self.assertIn("④ 随机生成图案", self.html)
        self.assertIn("⑤ 生成产品图", self.html)
        self.assertIn("建立可用图案、销售候选和提示词池", self.html)
        self.assertIn('"sellability"', main)
        self.assertIn('"stages": ["acquisition", "classification", "prompt_pool"]', main)
        self.assertIn('launch_full_pipeline(trigger_type="manual", auto_generate=False)', main)
        self.assertIn("热点来源平台", self.html)
        self.assertIn("优先地区（全球搜索", self.html)
        self.assertNotIn("生成所选热点", self.html)

    def test_each_pool_has_a_clickable_module_page(self):
        main = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        for path, label in (
            ("/acquire", "全部热点"),
            ("/trends", "可用图案"),
            ("/sellability", "销售候选"),
            ("/prompts", "生成提示词"),
            ("/patterns", "图案图库"),
            ("/images", "产品图库"),
        ):
            self.assertIn(f'@app.get("{path}")', main)
            self.assertIn(label, self.html)
        for label in ("总览", "信息采集", "AI 创意", "生图工坊"):
            self.assertIn(label, self.html)
        self.assertEqual(self.html.count('class="module-nav"'), 1)
        self.assertEqual(self.html.count('data-group="'), 4)
        self.assertIn('id="moduleTabs"', self.html)
        self.assertIn("groupTabs", self.html)
        self.assertIn("renderModuleTabs", self.html)
        self.assertIn('id="moduleContent"', self.html)
        self.assertIn("renderModuleContent", self.html)
        self.assertIn("/api/cards/${currentPage}", self.html)
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
        self.assertIn("cardAttrs(run,'pattern',item.id)", self.html)
        self.assertIn("cardAttrs(run,'image',item.id)", self.html)
        self.assertIn("cardAttrs(run,'sellability',item.id)", self.html)
        self.assertIn("selectedContent", self.html)
        self.assertIn("renderRawDetail", self.html)
        self.assertIn("点击卡片查看对应内容", self.html)

    def test_apple_style_is_applied(self):
        self.assertIn("Apple 风格", (PROJECT_ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("#f5f5f7", self.html)
        self.assertIn("#0071e3", self.html)
        self.assertIn("-apple-system", self.html)
        self.assertIn("body:before,.botanical{display:none!important}", self.html)
        self.assertNotIn("fonts.loli.net", self.html)
        self.assertIn("prefers-reduced-motion", self.html)
        self.assertIn("box-shadow:var(--shadow)", self.html)
        self.assertIn(".module-layout{display:block;margin-top:30px}", self.html)

    def test_all_pool_cards_render_in_lazy_batches(self):
        self.assertIn("CARD_BATCH_SIZE=24", self.html)
        self.assertIn("IntersectionObserver", self.html)
        self.assertIn("renderLazyCards(result.sources,sourceCard", self.html)
        self.assertIn("renderLazyCards(result.entries||[],signalCard", self.html)
        self.assertIn("renderLazyCards(result.entries||[],renderer", self.html)
        self.assertIn("offset=>api(signalPageUrl(offset))", self.html)
        self.assertIn("offset=>api(poolPageUrl(offset))", self.html)
        self.assertIn('loading="lazy" decoding="async"', self.html)
        self.assertIn("content-visibility:auto", self.html)
        self.assertNotIn("Promise.all(runs.map", self.html)
        self.assertIn("if(signature===moduleSignature)return;moduleSignature=signature;token=++moduleLoadToken", self.html)
        self.assertIn("const meta=moduleMeta[currentPage];let token=moduleLoadToken", self.html)

    def test_sellability_filters_and_transparent_png_download_are_exposed(self):
        main = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("grade: str = Query", main)
        self.assertIn("transparent: str = Query", main)
        self.assertIn("poolPageUrl", self.html)
        self.assertIn("可卖分高到低", self.html)
        self.assertIn("全部等级", self.html)
        self.assertIn("透明 PNG", self.html)
        self.assertIn('id="imageDownload"', self.html)
        self.assertIn("download=", self.html)
        self.assertIn("Pillow", requirements)
        self.assertIn("rembg[cpu]", requirements)
        self.assertIn("/api/sellability/backfill", self.html)
        self.assertIn("补算历史评分", self.html)
        self.assertIn("评分进度：", self.html)
        self.assertIn("scored_directions", self.html)
        self.assertIn('"sellability": service.sellability_state()', main)
        self.assertIn("查看评分规则与生图配额", self.html)
        for rule in (
            "购买意图 <b>25分", "社媒商业热度 <b>20分",
            "搜索增长潜力 <b>15分", "商品适配度 <b>15分",
            "受众清晰度 <b>10分", "销售窗口寿命 <b>10分",
            "竞争机会 <b>5分", "A · 80–100：3图案 × 2产品",
            "D · 0–59：1图案 × 1产品", "不采用模型自报总分",
        ):
            self.assertIn(rule, self.html)
        self.assertIn('class="score-rules"', self.html)
        for reason in (
            "身份表达、纪念、赠礼或即时购买动机",
            "多来源覆盖、讨论速度和互动信号",
            "关键词可识别性和后续发酵空间",
            "适配杯子、服装、手机壳等载体",
            "选品、广告定向和商品文案",
            "一日新闻、阶段性话题、周期事件还是常青兴趣",
            "未接入真实平台竞品数据",
            "每项必须给出该热点的具体评分理由",
        ):
            self.assertIn(reason, self.html)
        for judgement in (
            "身份表达、纪念、赠礼或即时购买动机",
            "多来源覆盖、讨论速度和互动信号",
            "事件新鲜度、关键词可识别性和后续发酵空间",
            "是否醒目、易印刷并适配杯子、服装、手机壳",
            "兴趣人群、社群和使用场景是否具体",
            "一日新闻、阶段性话题、周期事件还是常青兴趣",
            "是否过度饱和以及能否形成差异",
            "每项必须给出该热点的具体评分理由",
        ):
            self.assertIn(judgement, self.html)
        self.assertIn('id="sellabilityRules"', self.html)
        self.assertIn("REMBG_MODEL=u2netp", (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8"))

    def test_acquisition_and_generation_have_separate_schedule_controls(self):
        self.assertIn('id="cfgAcquireInterval"', self.html)
        self.assertIn('id="cfgGenerationInterval"', self.html)
        self.assertIn("acquisition_interval_minutes", self.html)
        self.assertIn("generation_interval_minutes", self.html)
        self.assertIn("热点、图案与提示词生成间隔（分钟）", self.html)
        self.assertIn("每轮随机图案/产品数（1–30）", self.html)
        self.assertIn("备胎罩或手机壳", self.html)

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
