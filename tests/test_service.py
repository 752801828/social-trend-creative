import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="trend-creative-test-"))

from app.service import FLOW_MODELS, TrendService, extract_json_object  # noqa: E402


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
