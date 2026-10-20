"""规则热更新：默认值合并、非法目标过滤、落盘。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from douyinkit import rules


class RulesTest(unittest.TestCase):
    def setUp(self):
        self._old = rules.RULES_PATH
        self._tmp = tempfile.TemporaryDirectory()
        rules.RULES_PATH = Path(self._tmp.name) / "rules.json"

    def tearDown(self):
        rules.RULES_PATH = self._old
        self._tmp.cleanup()

    def test_defaults_and_merge(self):
        base = rules.load()
        self.assertEqual(len(base["douyin"]["share_targets"]), 3)
        saved = rules.save({"douyin": {"browser_fallback": False}})
        self.assertFalse(saved["douyin"]["browser_fallback"])
        self.assertTrue(saved["updated_at"] > 0)

    def test_bad_targets_are_dropped(self):
        rules.RULES_PATH.write_text(json.dumps({
            "douyin": {"share_targets": [
                {"template": "https://x/{aweme_id}", "ua": "mobile"},
                {"template": "https://x/no-placeholder"},
                "not-a-dict",
            ]}
        }), encoding="utf-8")
        loaded = rules.load()
        self.assertEqual(len(loaded["douyin"]["share_targets"]), 1)
        self.assertEqual(loaded["douyin"]["share_targets"][0]["ua"], "mobile")
