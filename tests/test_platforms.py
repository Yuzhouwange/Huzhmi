"""平台分离的回归测试。

两个要求：
  1. 平台适配器都满足同一套接口，上层不需要知道是谁；
  2. 平台模块之间**不能互相 import**（以前 bilibili 为了一个 decode_text
     去 import douyin，两个平台就此绑死）。
"""

from __future__ import annotations

import inspect
import pathlib
import unittest

from huzhmi import bilibili, douyin, engine, platforms
from huzhmi.net import PlatformError
from huzhmi.platforms import base

PKG_DIR = pathlib.Path(platforms.__file__).resolve().parent


class RegistryTest(unittest.TestCase):
    def test_known_platforms(self) -> None:
        names = [p.name for p in platforms.all_platforms()]
        self.assertEqual(names, ["bilibili", "tiktok", "douyin"])

    def test_resolve_by_url(self) -> None:
        cases = {
            "https://www.douyin.com/video/7304228124479262006": "douyin",
            "https://v.douyin.com/abcdefg/": "douyin",
            "https://www.douyin.com/user/MS4wLjABAAAAxyz": "douyin",
            "https://www.bilibili.com/video/BV1fZJ26mEiY": "bilibili",
            "https://b23.tv/abc123": "bilibili",
            "https://space.bilibili.com/1157901692": "bilibili",
            "https://www.tiktok.com/@tiktok/video/7106594312292453675": "tiktok",
            "https://vt.tiktok.com/ZSabc123/": "tiktok",
        }
        for url, expected in cases.items():
            self.assertEqual(platforms.resolve(url).name, expected, url)

    def test_unknown_url_falls_back_to_douyin(self) -> None:
        self.assertEqual(platforms.resolve("https://example.com/x").name, "douyin")
        self.assertEqual(platforms.resolve("").name, "douyin")

    def test_by_name_and_find(self) -> None:
        self.assertEqual(platforms.by_name("bilibili").name, "bilibili")
        self.assertIsNone(platforms.find("nope"))
        with self.assertRaises(KeyError):
            platforms.by_name("nope")

    def test_describe_shape_for_frontend(self) -> None:
        for item in platforms.describe():
            for key in ("name", "label", "page_size", "pattern", "profile_url",
                        "supports_login"):
                self.assertIn(key, item)
            # 前端用 new RegExp(pattern, "i") 识别链接，必须是合法正则
            import re
            re.compile(item["pattern"], re.I)
            self.assertIn("{id}", item["profile_url"])


class ContractTest(unittest.TestCase):
    def test_every_platform_implements_interface(self) -> None:
        abstract = {
            name for name, value in inspect.getmembers(base.Platform)
            if getattr(value, "__isabstractmethod__", False)
        }
        self.assertTrue(abstract, "接口本身要有抽象方法")
        for platform in platforms.all_platforms():
            for name in abstract:
                method = getattr(platform, name, None)
                self.assertTrue(callable(method), f"{platform.name} 缺少 {name}")
                self.assertFalse(
                    getattr(method, "__isabstractmethod__", False),
                    f"{platform.name} 没有实现 {name}",
                )

    def test_labels_used_by_folder_template(self) -> None:
        self.assertEqual(engine.platform_label("douyin"), "抖音")
        self.assertEqual(engine.platform_label("bilibili"), "B站")
        # 未知平台不要把目录弄成空字符串
        self.assertEqual(engine.platform_label("weibo"), "weibo")

    def test_error_types_are_unified(self) -> None:
        self.assertTrue(issubclass(douyin.ExtractError, PlatformError))
        self.assertTrue(issubclass(bilibili.BiliError, PlatformError))

    def test_media_headers_are_platform_owned(self) -> None:
        cfg = {"cookie": "dy=1", "bilibili_cookie": "bili=2"}
        douyin_headers = platforms.by_name("douyin").media_headers(cfg)
        bili_headers = platforms.by_name("bilibili").media_headers(cfg)
        self.assertIn("douyin.com", douyin_headers["Referer"])
        self.assertEqual(douyin_headers["Cookie"], "dy=1")
        self.assertIn("bilibili.com", bili_headers["Referer"])
        self.assertEqual(bili_headers["Cookie"], "bili=2")

    def test_bilibili_prefers_its_own_cookie(self) -> None:
        cfg = {"cookie": "generic=1", "bilibili_cookie": ""}
        headers = platforms.by_name("bilibili").media_headers(cfg)
        self.assertEqual(headers["Cookie"], "generic=1", "没填专用 Cookie 时退回通用")

    def test_item_urls_and_keys(self) -> None:
        douyin_platform = platforms.by_name("douyin")
        url = douyin_platform.item_source_url("7304228124479262006")
        self.assertEqual(douyin_platform.normalize(url)[1],
                         "aweme:7304228124479262006")

        bili_platform = platforms.by_name("bilibili")
        url = bili_platform.item_source_url("BV1fZJ26mEiY")
        self.assertEqual(bili_platform.normalize(url)[1], "bili:BV1fZJ26mEiY")


class SeparationTest(unittest.TestCase):
    """平台之间不许互相 import —— 这是这次重构的核心。"""

    def test_platform_packages_do_not_import_each_other(self) -> None:
        douyin_src = pathlib.Path(douyin.__file__).read_text("utf-8")
        bilibili_src = pathlib.Path(bilibili.__file__).read_text("utf-8")
        self.assertNotIn("from .bilibili", douyin_src)
        self.assertNotIn("import bilibili", douyin_src)
        self.assertNotIn("from .douyin", bilibili_src)
        self.assertNotIn("import douyin", bilibili_src)

    def test_adapters_only_depend_on_own_platform(self) -> None:
        douyin_adapter = (PKG_DIR / "douyin_platform.py").read_text("utf-8")
        bili_adapter = (PKG_DIR / "bilibili_platform.py").read_text("utf-8")
        self.assertNotIn("bilibili", douyin_adapter)
        self.assertNotIn("douyin", bili_adapter)

    def test_engine_has_no_platform_branches(self) -> None:
        src = pathlib.Path(engine.__file__).read_text("utf-8")
        for needle in ("from . import bilibili", "from . import douyin",
                       'platform == "bilibili"', "is_bilibili("):
            self.assertNotIn(needle, src)

    def test_shared_helpers_live_in_net(self) -> None:
        from huzhmi import net
        self.assertIs(douyin.decode_text, net.decode_text)
        self.assertIs(bilibili.decode_text, net.decode_text)
        self.assertEqual(douyin.UA, bilibili.UA)


if __name__ == "__main__":
    unittest.main()
