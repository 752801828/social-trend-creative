import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

import app.main as main


class ProjectUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_request_path = main.UPDATE_REQUEST_PATH
        self.original_status_path = main.UPDATE_STATUS_PATH
        main.UPDATE_REQUEST_PATH = Path(self.temp_dir.name) / "update-request.json"
        main.UPDATE_STATUS_PATH = Path(self.temp_dir.name) / "update-status.json"

    def tearDown(self):
        main.UPDATE_REQUEST_PATH = self.original_request_path
        main.UPDATE_STATUS_PATH = self.original_status_path
        self.temp_dir.cleanup()

    def test_update_request_is_atomic_and_rejects_duplicates(self):
        result = asyncio.run(main.request_system_update())
        self.assertEqual(result["status"], "pending")
        self.assertEqual(json.loads(main.UPDATE_REQUEST_PATH.read_text(encoding="utf-8")), result)
        self.assertEqual(main.read_update_status(), result)
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(main.request_system_update())
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
