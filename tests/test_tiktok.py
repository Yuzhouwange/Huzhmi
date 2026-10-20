"""TikTok 平台适配：链接识别、去重键、下载请求头。"""

from __future__ import annotations

import unittest

from douyinkit import platforms


class TikTokTest(unittest.TestCase):
    def setUp(self):
        self.platform = platforms.by_name("tiktok")

    def test_matches_and_normalize(self):
        url = "https://www.tiktok.com/@tiktok/video/7106594312292453675?lang=en"
        self.assertTrue(self.platform.matches(url))
        self.assertFalse(self.platform.matches("https://www.douyin.com/video/1"))
        clean, key = self.platform.normalize(url)
        self.assertEqual(clean, url)
        self.assertEqual(key, "tiktok:7106594312292453675")

    def test_item_url_and_headers(self):
        self.assertIn("7106594312292453675",
                      self.platform.item_source_url("7106594312292453675"))
        headers = self.platform.media_headers({})
        self.assertEqual(headers["Referer"], "https://www.tiktok.com/")
