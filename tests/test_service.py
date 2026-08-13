import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

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
        self.assertIn("Do not choose products or write image prompts", prompt)
        self.assertIn("Evidence URLs and publication times are optional", prompt)
        self.assertIn("strict JSON", prompt)
        self.assertIn("TikTok", prompt)
        self.assertIn("Reddit", prompt)
        classify_prompt = self.service._classification_prompt(
            self.service.get_config(), [self._candidate("candidate-1", "Trend", "", None)]
        )
        self.assertIn("Split broad raw trends", classify_prompt)
        self.assertIn("classified_trends", classify_prompt)
        trend = self._candidate("candidate-1", "Trend", "", None)
        trend.update({"id": "trend-1"})
        pool_prompt = self.service._prompt_pool_prompt([trend])
        self.assertIn("randomly selected worldwide trend-pool entries", pool_prompt)
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
        self.assertNotIn("final_count", config)

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
            await self.service._create_prompt_pool(run_id, config, 2)
            return self.service.get_run(run_id), self.service.list_runs()

        run, runs = asyncio.run(exercise())
        self.assertEqual(len(run["prompt_pool"]), 2)
        self.assertTrue(all(item["used_count"] == 0 for item in run["prompt_pool"]))
        self.assertEqual(runs[0]["prompt_count"], 2)

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
        self.assertIn("① 获取热点", self.html)
        self.assertIn("② AI拆分分类", self.html)
        self.assertIn("③ 随机生成提示词池", self.html)
        self.assertIn("④ 随机提示词生图", self.html)
        self.assertIn("热点来源平台", self.html)
        self.assertIn("优先地区（全球搜索", self.html)
        self.assertNotIn("生成所选热点", self.html)

    def test_each_pool_has_a_clickable_module_page(self):
        main = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        for path, label in (
            ("/acquire", "原始热点"),
            ("/trends", "热点池"),
            ("/prompts", "提示词池"),
            ("/images", "生图池"),
        ):
            self.assertIn(f'@app.get("{path}")', main)
            self.assertIn(f'href="{path}"', self.html)
            self.assertIn(label, self.html)
        self.assertIn('id="moduleRuns"', self.html)
        self.assertIn('id="moduleContent"', self.html)
        self.assertIn("renderModuleContent", self.html)
        self.assertIn("safeHttpUrl", self.html)

    def test_generated_images_open_in_an_accessible_viewer(self):
        self.assertIn('id="imageDialog"', self.html)
        self.assertIn('onclick="openImage(this)"', self.html)
        self.assertIn('aria-label="关闭放大图"', self.html)
        self.assertIn("cursor:zoom-in", self.html)

    def test_credentials_are_not_persisted_in_browser_storage(self):
        self.assertNotIn("localStorage", self.html)
        self.assertNotIn("sessionStorage", self.html)
        self.assertNotIn("document.cookie", self.html)


if __name__ == "__main__":
    unittest.main()
