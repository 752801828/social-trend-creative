import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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

    def test_verification_gates_fresh_missing_time_and_bad_url(self):
        fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        candidates = [
            self._candidate("candidate-1", "Fresh", "https://x.com/example/status/1", fresh),
            self._candidate("candidate-2", "Review", "https://reddit.com/r/test/1", None),
            self._candidate("candidate-3", "Reject", "javascript:alert(1)", fresh),
        ]
        verification = {
            "verified_trends": [
                {"candidate_id": item["candidate_id"], "decision": "accept", "confidence": 0.9}
                for item in candidates
            ]
        }
        result = self.service._verify_candidates(self.service.get_config(), candidates, verification)
        self.assertEqual([item["status"] for item in result], ["ready", "needs_review", "rejected"])

    def test_prompts_require_evidence_and_strict_json(self):
        prompt = self.service._discovery_prompt(self.service.get_config())
        self.assertIn("evidence URL", prompt)
        self.assertIn("strict JSON", prompt)
        self.assertIn("TikTok", prompt)
        self.assertIn("Reddit", prompt)

    def test_invalid_model_confidence_does_not_break_discovery(self):
        payload = {"trends": [{"topic_en": "Topic", "confidence": "unknown", "platforms": "X"}]}
        result = self.service._normalise_candidates(payload, 10)
        self.assertEqual(result[0]["confidence"], 0)
        self.assertEqual(result[0]["platforms"], [])

    def test_config_keeps_daily_scheduler_disabled_by_default(self):
        config = self.service.get_config()
        self.assertFalse(config["enabled"])
        self.assertFalse(config["auto_generate"])

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

    def test_manual_review_controls_are_present(self):
        self.assertIn("发现今日热点", self.html)
        self.assertIn("生成所选热点", self.html)
        self.assertIn("需人工确认", self.html)

    def test_credentials_are_not_persisted_in_browser_storage(self):
        self.assertNotIn("localStorage", self.html)
        self.assertNotIn("sessionStorage", self.html)
        self.assertNotIn("document.cookie", self.html)


if __name__ == "__main__":
    unittest.main()
