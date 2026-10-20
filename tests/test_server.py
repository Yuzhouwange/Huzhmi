"""封面代理取 URL 的回归测试：不能对已解码的签名 URL 再解一次码。"""

from __future__ import annotations

import unittest
import urllib.parse

from huzhmi.server import thumb_target


class ThumbTargetTest(unittest.TestCase):
    def test_keeps_signed_url_intact(self) -> None:
        signed = ("https://p3-pc-sign.douyinpic.com/a~tplv-dy-cropcenter:323:430.jpeg"
                  "?lk3s=138a59ce&x-expires=2107756800&x-signature=abc%2Fdef%3D")
        # 浏览器用 encodeURIComponent 发过来，parse_qs 解一次就够了
        encoded = urllib.parse.quote(signed, safe="")
        query = urllib.parse.parse_qs(f"url={encoded}")
        self.assertEqual(thumb_target(query), signed, "不能再解一次，否则签名失效")

    def test_missing_returns_empty(self) -> None:
        self.assertEqual(thumb_target({}), "")
        self.assertEqual(thumb_target({"url": [""]}), "")


if __name__ == "__main__":
    unittest.main()
