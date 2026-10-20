"""解析器单元测试：不联网，用构造的分享页 HTML 验证。"""

from __future__ import annotations

import json
import unittest

from huzhmi import douyin
from huzhmi.engine import extract_urls, safe_dirname


def _page(item: dict) -> bytes:
    payload = {
        "loaderData": {"video_(id)/page": {"videoInfoRes": {"item_list": [item]}}},
        "errors": None,
    }
    return (
        "<html><head><script>window._ROUTER_DATA = "
        + json.dumps(payload, ensure_ascii=False)
        + ";</script></head><body></body></html>"
    ).encode("utf-8")


VIDEO_ITEM = {
    "aweme_id": "7300000000000000001",
    "desc": "测试 作品/标题:带*特殊字符?",
    "author": {"nickname": "测试作者"},
    "video": {
        "play_addr": {
            "url_list": [
                "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v0300&ratio=720p"
            ]
        },
        "download_addr": {"url_list": ["https://aweme.snssdk.com/aweme/v1/play/?watermark=1"]},
    },
}

IMAGE_ITEM = {
    "aweme_id": "7300000000000000002",
    "desc": "图集作品",
    "author": {"nickname": "图集作者"},
    "images": [
        {"url_list": ["https://p3.douyinpic.com/a~tplv-obj.jpeg?x=1"]},
        {"url_list": ["https://p3.douyinpic.com/b~tplv-obj.webp"]},
    ],
}


class ParseTest(unittest.TestCase):
    def test_video_item_is_deprecated_watermark(self) -> None:
        item = douyin._parse_item(_page(VIDEO_ITEM))
        self.assertIsNotNone(item)
        meta = douyin._build_meta(item, "https://www.douyin.com/video/7300000000000000001")
        self.assertEqual(meta["kind"], "video")
        self.assertEqual(len(meta["assets"]), 1)
        url = meta["assets"][0]["url"]
        self.assertNotIn("playwm", url)
        self.assertIn("/play/", url)
        self.assertEqual(meta["author"], "测试作者")

    def test_image_item(self) -> None:
        item = douyin._parse_item(_page(IMAGE_ITEM))
        meta = douyin._build_meta(item, "https://www.douyin.com/note/7300000000000000002")
        self.assertEqual(meta["kind"], "images")
        self.assertEqual([a["idx"] for a in meta["assets"]], [0, 1])
        self.assertTrue(meta["assets"][0]["url"].endswith("x=1"))

    def test_missing_router_data(self) -> None:
        self.assertIsNone(douyin._parse_item(b"<html>no data</html>"))

    def test_broken_json(self) -> None:
        bad = b"<script>window._ROUTER_DATA = {not json};</script>"
        self.assertIsNone(douyin._parse_item(bad))

    def test_normalize_uses_aweme_id(self) -> None:
        url = "https://www.douyin.com/video/7300000000000000001?previous_page=app"
        _, key = douyin.normalize(url)
        self.assertEqual(key, "aweme:7300000000000000001")

    def test_normalize_short_link(self) -> None:
        url = "https://v.douyin.com/iAbCdEf/"
        clean, key = douyin.normalize(url)
        self.assertEqual(clean, url)
        self.assertTrue(key.startswith("url:https://v.douyin.com/iAbCdEf/"))

    def test_guess_ext(self) -> None:
        self.assertEqual(
            douyin.guess_ext("https://x/a~tplv-obj.jpeg?y=1", "image"), ".jpeg"
        )
        self.assertEqual(douyin.guess_ext("https://x/v.mp4", "video"), ".mp4")
        self.assertEqual(douyin.guess_ext("https://x/blob", "image"), ".jpg")


class EngineHelpersTest(unittest.TestCase):
    def test_extract_urls_from_share_text(self) -> None:
        text = (
            "2.43 复制打开抖音，看看【某人】的作品 https://v.douyin.com/AAA/ "
            "再一个 https://www.douyin.com/video/7300000000000000001"
            "，重复的 https://v.douyin.com/AAA/ 结束。"
        )
        urls = extract_urls(text)
        self.assertEqual(
            urls,
            [
                "https://v.douyin.com/AAA/",
                "https://www.douyin.com/video/7300000000000000001",
            ],
        )

    def test_extract_urls_strips_trailing_punct(self) -> None:
        self.assertEqual(
            extract_urls("看看 https://v.douyin.com/BBB/。"), ["https://v.douyin.com/BBB/"]
        )

    def test_extract_urls_empty(self) -> None:
        self.assertEqual(extract_urls("没有链接"), [])

    def test_safe_dirname(self) -> None:
        self.assertEqual(safe_dirname('a/b:c*d?e"f<g>h|i'), "a_b_c_d_e_f_g_h_i")
        self.assertEqual(safe_dirname("   "), "untitled")
        self.assertLessEqual(len(safe_dirname("长" * 200)), 80)


class ImagePickTest(unittest.TestCase):
    """抖音一张图有多个地址：优先 jpeg，别给用户留一堆打不开的 webp。"""

    def test_prefers_jpeg_over_webp(self) -> None:
        urls = [
            "https://p11.douyinpic.com/a~tplv-dy-aweme-images:q75.webp?x=1",
            "https://p11.douyinpic.com/a~tplv-dy-aweme-images:q75.jpeg?x=1",
        ]
        self.assertIn(".jpeg", douyin._pick_image(urls))

    def test_png_beats_webp(self) -> None:
        urls = ["https://x/a.webp", "https://x/a.png"]
        self.assertIn(".png", douyin._pick_image(urls))

    def test_falls_back_to_first(self) -> None:
        urls = ["https://x/a", "https://x/b"]
        self.assertEqual(douyin._pick_image(urls), "https://x/a")

    def test_empty_list(self) -> None:
        self.assertEqual(douyin._pick_image([]), "")

    def test_built_meta_uses_jpeg_variant(self) -> None:
        item = {
            "aweme_id": "7300000000000000003",
            "desc": "多地址图集",
            "author": {"nickname": "作者"},
            "images": [{
                "url_list": [
                    "https://p11.douyinpic.com/x~tplv-dy-aweme-images:q75.webp?x=1",
                    "https://p3.douyinpic.com/x~tplv-dy-aweme-images:q75.webp?x=2",
                    "https://p11.douyinpic.com/x~tplv-dy-aweme-images:q75.jpeg?x=3",
                ]
            }],
        }
        meta = douyin._build_meta(item, "https://www.douyin.com/video/1")
        self.assertIn(".jpeg", meta["assets"][0]["url"])


class ImageNamingTest(unittest.TestCase):
    """多张图要带序号，不能靠「(2)(3)」这种重名后缀。"""

    def test_multi_image_stems_are_numbered(self) -> None:
        from huzhmi.engine import build_file_stem

        meta = {"title": "图集作品", "author": "作者", "kind": "images",
                "aweme_id": "1", "created": 0}
        stems = [build_file_stem(meta, "douyin", "{title}", index=i, total=3)
                 for i in (1, 2, 3)]
        self.assertEqual(stems, ["图集作品 - 01", "图集作品 - 02", "图集作品 - 03"])


LIVE_ITEM = {
    "aweme_id": "7300000000000000009",
    "desc": "实况作品",
    "is_live_photo": 1,
    "author": {"nickname": "实况作者"},
    "images": [
        {
            "url_list": ["https://p3.douyinpic.com/a~tplv-obj.jpeg?x=1"],
            "video": {
                "play_addr": {"url_list": ["https://v11.douyinvod.com/a.mp4"]},
                "play_addr_h264": {"url_list": ["https://v11.douyinvod.com/a_h264.mp4"]},
                "duration": 4800,
            },
        }
    ],
}


class LivePhotoTest(unittest.TestCase):
    """实况图 = 一张静态图 + 一段短视频；默认只存那段 mp4。"""

    def test_default_is_video_only(self) -> None:
        meta = douyin._build_meta(LIVE_ITEM, "https://www.douyin.com/video/9")
        self.assertEqual(meta["kind"], "live")
        self.assertEqual(len(meta["assets"]), 1)
        asset = meta["assets"][0]
        self.assertEqual(asset["kind"], "video")
        self.assertTrue(asset["url"].endswith(".mp4"))

    def test_both_keeps_image_and_pairs_index(self) -> None:
        meta = douyin._build_meta(LIVE_ITEM, "https://www.douyin.com/video/9", "both")
        kinds = [a["kind"] for a in meta["assets"]]
        self.assertEqual(sorted(kinds), ["image", "video"])
        self.assertEqual({a["index"] for a in meta["assets"]}, {1},
                         "图和视频用同一个序号，方便成对命名")

    def test_image_mode_is_old_behaviour(self) -> None:
        meta = douyin._build_meta(LIVE_ITEM, "https://www.douyin.com/video/9", "image")
        self.assertEqual(meta["kind"], "images")
        self.assertEqual([a["kind"] for a in meta["assets"]], ["image"])

    def test_plain_album_has_no_live(self) -> None:
        meta = douyin._build_meta({"aweme_id": "1", "desc": "x", "author": {},
                                   "images": [{"url_list": ["https://a/b.jpeg"]}]}, "u")
        self.assertEqual(meta["kind"], "images")

    def test_adapter_swaps_in_live_from_detail(self) -> None:
        """分享页没有实况信息时，适配器用详情接口补上。"""
        from huzhmi import platforms

        adapter = platforms.by_name("douyin")
        original_extract = douyin.extract
        original_build = douyin._build_meta

        def fake_extract(url, cookie="", proxy="", retries=3, live_mode="video"):
            meta = original_build(
                {"aweme_id": "7300000000000000009", "desc": "实况作品",
                 "author": {"nickname": "a"},
                 "images": [{"url_list": ["https://a/b.jpeg"]}]},
                url, live_mode)
            return meta

        class FakeBridge:
            def aweme_detail(self, aweme_id):
                return LIVE_ITEM

        class FakeBridgeModule:
            class Bridge:
                @staticmethod
                def get():
                    return FakeBridge()

        douyin.extract = fake_extract
        import huzhmi.platforms.douyin_platform as dp
        original_get = dp.bridge_mod
        dp.bridge_mod = FakeBridgeModule
        try:
            meta = adapter.extract("https://www.douyin.com/video/9",
                                   {"live_photo": "video", "retries": 1})
        finally:
            douyin.extract = original_extract
            dp.bridge_mod = original_get
        self.assertEqual(meta["kind"], "live")
        self.assertEqual([a["kind"] for a in meta["assets"]], ["video"])


if __name__ == "__main__":
    unittest.main()
