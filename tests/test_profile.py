"""主页链接识别 / sec_uid 解析的单元测试（不启动浏览器）。"""

from __future__ import annotations

import unittest

from douyinkit import bridge
from douyinkit.engine import extract_urls, safe_dirname

SEC = "MS4wLjABAAAAx5HzYzC9dIm19hUfeG3SOpXpV6Odko_W7hIZTsfRjUe5p1g0d4rqe8MwjXT_qVcx"


class ProfileUrlTest(unittest.TestCase):
    def test_user_url(self) -> None:
        url = f"https://www.douyin.com/user/{SEC}"
        self.assertTrue(bridge.is_profile_url(url))
        self.assertEqual(bridge.parse_sec_uid(url), SEC)

    def test_share_user_url(self) -> None:
        url = f"https://www.douyin.com/share/user/{SEC}?from=web"
        self.assertTrue(bridge.is_profile_url(url))
        self.assertEqual(bridge.parse_sec_uid(url), SEC)

    def test_video_url_is_not_profile(self) -> None:
        # 作品页不做主页识别，交给单条下载
        url = "https://www.douyin.com/video/7304228124479262006"
        self.assertFalse(bridge.is_profile_url(url))
        self.assertIsNone(bridge.parse_sec_uid(url))

    def test_short_link_has_no_sec_uid(self) -> None:
        url = "https://v.douyin.com/iAbCdEf/"
        self.assertFalse(bridge.is_profile_url(url))
        self.assertIsNone(bridge.parse_sec_uid(url))

    def test_extract_urls_from_profile_share_text(self) -> None:
        text = f"7.89 复制打开抖音，看看【猴哥】的主页 https://www.douyin.com/user/{SEC} 复制此链接"
        urls = extract_urls(text)
        self.assertEqual(urls, [f"https://www.douyin.com/user/{SEC}"])

    def test_safe_dirname_keeps_chinese(self) -> None:
        self.assertEqual(
            safe_dirname("瓦努阿图猴哥 - 测试#标签"), "瓦努阿图猴哥 - 测试#标签"
        )


if __name__ == "__main__":
    unittest.main()
