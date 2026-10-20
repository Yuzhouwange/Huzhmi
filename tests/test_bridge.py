"""页面桥的纯逻辑测试：主页文案解析、Cookie 解析、被第一页卡住时的判定。

不连浏览器：`user_posts` 的浏览器交互用假对象替换，只验证判定与返回值。
"""

from __future__ import annotations

import unittest

from douyinkit import bridge
from douyinkit.cdp import parse_cookie_string


class ProfileTextTest(unittest.TestCase):
    def test_reads_total_and_login_wall(self) -> None:
        text = "瓦努阿图猴哥 关注 粉丝 获赞 作品 529 推荐 喜欢 作品 合集 登录后查看更多作品"
        meta = bridge.parse_profile_text(text)
        self.assertEqual(meta["total_works"], 529)
        self.assertTrue(meta["needs_login"])

    def test_logged_in_has_no_wall(self) -> None:
        meta = bridge.parse_profile_text("某作者 作品 12 关注 100 粉丝 200")
        self.assertEqual(meta["total_works"], 12)
        self.assertFalse(meta["needs_login"])

    def test_thousands_separator(self) -> None:
        self.assertEqual(bridge.parse_profile_text("作品 1,204 个")["total_works"], 1204)

    def test_missing_count(self) -> None:
        meta = bridge.parse_profile_text("登录后查看更多作品")
        self.assertEqual(meta["total_works"], 0)
        self.assertTrue(meta["needs_login"])

    def test_empty_text(self) -> None:
        self.assertEqual(bridge.parse_profile_text(""),
                         {"total_works": 0, "needs_login": False})

    def test_first_numeric_works_label_wins(self) -> None:
        # 页面上还有「搜索 Ta 的作品」这类不含数字的文案
        meta = bridge.parse_profile_text("搜索 Ta 的作品 作品 88 作品 99")
        self.assertEqual(meta["total_works"], 88)


class CookieStringTest(unittest.TestCase):
    def test_parses_pairs(self) -> None:
        pairs = parse_cookie_string("sessionid=abc; ttwid=xyz;; broken=; =novalue")
        self.assertEqual(pairs, [("sessionid", "abc"), ("ttwid", "xyz")])

    def test_empty(self) -> None:
        self.assertEqual(parse_cookie_string(""), [])
        self.assertEqual(parse_cookie_string(None), [])  # type: ignore[arg-type]


class _FakeBrowser:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls = 0

    def eval(self, expression: str, timeout: float = 60.0) -> str:
        import json

        data = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return json.dumps(data)


class UserPostsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = bridge.Bridge()
        self.bridge._warm = lambda browser, sec_uid: "作品 529 登录后查看更多作品"  # type: ignore[assignment]

    def test_first_page_keeps_cursor(self) -> None:
        browser = _FakeBrowser([
            {"status": 200, "status_code": 0, "has_more": 1,
             "max_cursor": 1791073680000,
             "items": [{"aweme_id": "1"}, {"aweme_id": "2"}]}
        ])
        self.bridge._browser = browser  # type: ignore[assignment]
        page = self.bridge.user_posts("uid", 0, 50)
        self.assertEqual(len(page["items"]), 2)
        self.assertTrue(page["has_more"])
        self.assertFalse(page["needs_login"])
        self.assertEqual(page["max_cursor"], 1791073680000)

    def test_empty_second_page_means_login_required(self) -> None:
        # 抖音未登录时就是「has_more=1，但下一页给空列表」
        browser = _FakeBrowser([
            {"status": 200, "status_code": 0, "has_more": 1,
             "max_cursor": 1791073680000, "items": []}
        ])
        self.bridge._browser = browser  # type: ignore[assignment]
        page = self.bridge.user_posts("uid", 1791073680000, 50)
        self.assertEqual(page["items"], [])
        self.assertTrue(page["needs_login"])
        self.assertFalse(page["has_more"], "不能再显示「加载更多」，点了也没用")

    def test_403_triggers_one_rewarm_then_succeeds(self) -> None:
        browser = _FakeBrowser([
            {"status": 403, "error": "not json",
             "head": "Blocked by ArgusSecurityPlugin Uifid Not Found"},
            {"status": 200, "status_code": 0, "has_more": 0, "max_cursor": 0,
             "items": [{"aweme_id": "9"}]},
        ])
        self.bridge._browser = browser  # type: ignore[assignment]
        warmed: list[str] = []
        self.bridge._warm = lambda b, uid: warmed.append(uid) or ""  # type: ignore[assignment]
        page = self.bridge.user_posts("uid", 0, 50)
        self.assertEqual([it["aweme_id"] for it in page["items"]], ["9"])
        self.assertTrue(warmed, "403 后应重新预热页面")

    def test_persistent_403_raises(self) -> None:
        browser = _FakeBrowser([{"status": 403, "error": "not json"}])
        self.bridge._browser = browser  # type: ignore[assignment]
        with self.assertRaises(bridge.BridgeError):
            self.bridge.user_posts("uid", 0, 50)


if __name__ == "__main__":
    unittest.main()
