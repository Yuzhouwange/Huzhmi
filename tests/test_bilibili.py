"""B 站链接识别与去重键的单元测试（不联网）。"""

from __future__ import annotations

import unittest

from douyinkit import bilibili


class BiliUrlTest(unittest.TestCase):
    def test_is_bilibili(self) -> None:
        self.assertTrue(bilibili.is_bilibili("https://www.bilibili.com/video/BV1fZJ26mEiY"))
        self.assertTrue(bilibili.is_bilibili("https://m.bilibili.com/video/BV1fZJ26mEiY"))
        self.assertTrue(bilibili.is_bilibili("https://b23.tv/abcdefg"))
        self.assertFalse(bilibili.is_bilibili("https://www.douyin.com/video/7304228124479262006"))
        self.assertFalse(bilibili.is_bilibili("https://example.com/BV1fZJ26mEiY"))

    def test_normalize_bv(self) -> None:
        clean, key = bilibili.normalize("https://www.bilibili.com/video/BV1fZJ26mEiY")
        self.assertEqual(key, "bili:BV1fZJ26mEiY")
        self.assertIn("BV1fZJ26mEiY", clean)

    def test_normalize_bv_with_page(self) -> None:
        _, key = bilibili.normalize(
            "https://www.bilibili.com/video/BV1fZJ26mEiY?p=3&t=12"
        )
        self.assertEqual(key, "bili:BV1fZJ26mEiY:p3")

    def test_normalize_av(self) -> None:
        _, key = bilibili.normalize("https://www.bilibili.com/video/av170001")
        self.assertEqual(key, "bili:av170001")

    def test_page_from_url(self) -> None:
        self.assertEqual(bilibili._page_index("https://x/video/BV1fZJ26mEiY?p=2"), 2)
        self.assertEqual(bilibili._page_index("https://x/video/BV1fZJ26mEiY"), 1)
        self.assertEqual(bilibili._page_index("https://x/video/BV1fZJ26mEiY?p=abc"), 1)

    def test_unknown_id_message(self) -> None:
        with self.assertRaises(bilibili.BiliError):
            bilibili.extract("https://www.bilibili.com/")

    def test_space_url(self) -> None:
        url = "https://space.bilibili.com/1157901692?spm_id_from=333.1007"
        self.assertTrue(bilibili.is_space_url(url))
        self.assertEqual(bilibili.parse_mid(url), "1157901692")
        self.assertFalse(bilibili.is_space_url("https://www.bilibili.com/video/BV1fZJ26mEiY"))
        self.assertIsNone(bilibili.parse_mid("https://www.bilibili.com/"))

    def test_space_url_forms(self) -> None:
        """手机分享出来的不一定是 space.bilibili.com。"""
        for url in (
            "https://space.bilibili.com/1157901692",
            "https://space.bilibili.com/1157901692/",
            "https://space.bilibili.com/1157901692/video?spm_id_from=333.788",
            "https://space.bilibili.com/1157901692/dynamic",
            "https://m.bilibili.com/space/1157901692",
            "https://www.bilibili.com/space/1157901692/video",
            "https://bilibili.com/space/1157901692",
            "【言吾言的个人空间-哔哩哔哩】 https://space.bilibili.com/1157901692?share_source=copy_web",
        ):
            self.assertTrue(bilibili.is_space_url(url), url)
            self.assertEqual(bilibili.parse_mid(url), "1157901692", url)

    def test_video_and_other_urls_are_not_profiles(self) -> None:
        for url in (
            "https://www.bilibili.com/video/BV1fZJ26mEiY",
            "https://www.bilibili.com/video/av17000",
            "https://b23.tv/abcdefg",
            "https://www.bilibili.com/",
            "https://example.com/space/123456",
        ):
            self.assertFalse(bilibili.is_space_url(url), url)

    def test_platform_adapter_exposes_profile(self) -> None:
        from douyinkit.platforms import by_name
        platform = by_name("bilibili")
        self.assertTrue(platform.is_profile_url("https://m.bilibili.com/space/123456"))
        self.assertEqual(platform.describe()["profile_url"],
                         "https://space.bilibili.com/{id}")

    def test_length_to_seconds(self) -> None:
        self.assertEqual(bilibili._length_to_seconds("02:18"), 138)
        self.assertEqual(bilibili._length_to_seconds("1:02:03"), 3723)
        self.assertEqual(bilibili._length_to_seconds(""), 0)

    def test_cover_normalized(self) -> None:
        self.assertEqual(
            bilibili._cover("//i0.hdslb.com/bfs/archive/x.jpg"),
            "https://i0.hdslb.com/bfs/archive/x.jpg",
        )
        self.assertIsNone(bilibili._cover(None))


def _video(qn: int, codecid: int, height: int, bandwidth: int = 1000) -> dict:
    return {
        "id": qn,
        "codecid": codecid,
        "codecs": "avc1" if codecid == 7 else "hev1",
        "width": int(height * 16 / 9),
        "height": height,
        "bandwidth": bandwidth,
        "baseUrl": f"https://cdn.example/{qn}-{codecid}.m4s",
        "backupUrl": [f"https://backup.example/{qn}-{codecid}.m4s"],
    }


def _audio(aid: int, bandwidth: int) -> dict:
    return {
        "id": aid,
        "bandwidth": bandwidth,
        "baseUrl": f"https://cdn.example/a{aid}.m4s",
        "backupUrl": [],
    }


class DashSelectionTest(unittest.TestCase):
    """DASH 选流规则（纯函数，不联网）。"""

    def test_prefers_highest_quality(self) -> None:
        picked = bilibili._pick_video(
            [_video(32, 7, 480), _video(80, 7, 1080), _video(64, 7, 720)]
        )
        self.assertEqual(picked["id"], 80)

    def test_prefers_avc1_within_same_quality(self) -> None:
        picked = bilibili._pick_video(
            [_video(80, 12, 1080), _video(80, 13, 1080), _video(80, 7, 1080)]
        )
        self.assertEqual(picked["codecid"], 7, "同档位优先 avc1，播放器兼容性最好")

    def test_respects_quality_cap(self) -> None:
        picked = bilibili._pick_video([_video(80, 7, 1080), _video(64, 7, 720)], 64)
        self.assertEqual(picked["id"], 64)

    def test_cap_lower_than_available_falls_back_to_lowest(self) -> None:
        picked = bilibili._pick_video([_video(80, 7, 1080), _video(64, 7, 720)], 16)
        self.assertEqual(picked["id"], 64, "没有更低档时不能用空结果")

    def test_skips_streams_without_url(self) -> None:
        broken = {**_video(120, 7, 2160), "baseUrl": "", "backupUrl": []}
        picked = bilibili._pick_video([broken, _video(80, 7, 1080)])
        self.assertEqual(picked["id"], 80)

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(bilibili._pick_video([]))
        self.assertIsNone(bilibili._pick_audio([]))

    def test_audio_prefers_aac_over_dolby(self) -> None:
        picked = bilibili._pick_audio(
            [_audio(30250, 448000), _audio(30280, 192000)]
        )
        self.assertEqual(picked["id"], 30280, "杜比音轨很多播放器不认，优先纯 AAC")

    def test_audio_highest_bandwidth(self) -> None:
        picked = bilibili._pick_audio([_audio(30216, 64000), _audio(30232, 132000)])
        self.assertEqual(picked["id"], 30232)

    def test_quality_labels(self) -> None:
        self.assertEqual(bilibili._quality_label(80), "1080P")
        self.assertEqual(bilibili._quality_label(120), "4K")
        self.assertEqual(bilibili._quality_label(0), "qn0")

    def test_progressive_multi_part(self) -> None:
        meta = bilibili._progressive_assets(
            [{"url": "https://cdn/1.mp4"}, {"url": "https://cdn/2.mp4"}], 64
        )
        self.assertFalse(meta["need_merge"])
        self.assertEqual(meta["quality"], "720P")
        self.assertEqual(meta["assets"][0]["parts"], ["https://cdn/1.mp4",
                                                     "https://cdn/2.mp4"])

    def test_dash_assets_builds_two_tracks(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "dash": {
                    "video": [_video(80, 7, 1080), _video(32, 7, 480)],
                    "audio": [_audio(30216, 64000), _audio(30280, 192000)],
                }
            },
        }
        original = bilibili._get_json
        bilibili._get_json = lambda *a, **kw: payload
        try:
            meta = bilibili._dash_assets("BV1xx411c7mD", 123, "", "", 1, 0)
        finally:
            bilibili._get_json = original

        self.assertIsNotNone(meta)
        self.assertTrue(meta["need_merge"])
        self.assertEqual(meta["quality_id"], 80)
        roles = [a["role"] for a in meta["assets"]]
        self.assertEqual(roles, ["video", "audio"])
        # 备用 CDN 也要带进资产里，下载失败能换线
        self.assertTrue(meta["assets"][0]["urls"])

    def test_dash_assets_returns_none_without_video(self) -> None:
        original = bilibili._get_json
        bilibili._get_json = lambda *a, **kw: {"code": 0, "data": {"dash": {}}}
        try:
            self.assertIsNone(bilibili._dash_assets("BV1xx411c7mD", 1, "", "", 1, 0))
        finally:
            bilibili._get_json = original


class DeletedVideoTest(unittest.TestCase):
    """B 站把「稿件没了」也回成 -403，不能让人白登录一遍。"""

    def _patch(self, view_payload, stat_raises=None, stat_payload=None):
        calls = {"view": 0, "stat": 0}

        def fake_get_json(url, cookie="", proxy="", retries=3):
            if "archive/stat" in url:
                calls["stat"] += 1
                if stat_raises:
                    raise stat_raises
                return stat_payload or {"code": 0}
            calls["view"] += 1
            return view_payload

        original = bilibili._get_json
        bilibili._get_json = fake_get_json
        self.addCleanup(setattr, bilibili, "_get_json", original)
        return calls

    def test_403_with_missing_archive_says_deleted(self) -> None:
        from douyinkit.net import UA  # noqa: F401 - 只是确认模块可用
        calls = self._patch(
            {"code": -403, "message": "访问权限不足"},
            stat_raises=bilibili.BiliError("请求 B 站接口失败：HTTP Error 404: Not Found"),
        )
        with self.assertRaises(bilibili.BiliError) as ctx:
            bilibili.extract("https://www.bilibili.com/video/BV1Ks411f7aQ")
        self.assertIn("视频不存在了", str(ctx.exception))
        self.assertNotIn("需要登录", str(ctx.exception))
        self.assertEqual(calls["stat"], 1, "应该去 archive/stat 核实一次")

    def test_403_with_live_archive_keeps_permission_hint(self) -> None:
        self._patch({"code": -403, "message": "访问权限不足"},
                    stat_payload={"code": 0, "data": {"stat": {}}})
        with self.assertRaises(bilibili.BiliError) as ctx:
            bilibili.extract("https://www.bilibili.com/video/BV1Ks411f7aQ")
        self.assertIn("没有访问权限", str(ctx.exception))

    def test_404_is_not_probed(self) -> None:
        calls = self._patch({"code": -404, "message": "啥都木有"})
        with self.assertRaises(bilibili.BiliError) as ctx:
            bilibili.extract("https://www.bilibili.com/video/BV1Ks411f7aQ")
        self.assertIn("不存在", str(ctx.exception))
        self.assertEqual(calls["stat"], 0, "-404 已经很明确，不用再探")

    def test_other_codes_keep_their_message(self) -> None:
        self._patch({"code": 87008, "message": "需要大会员"})
        with self.assertRaises(bilibili.BiliError) as ctx:
            bilibili.extract("https://www.bilibili.com/video/BV1Ks411f7aQ")
        self.assertIn("大会员", str(ctx.exception))

    def test_archive_missing_without_ids_is_false(self) -> None:
        self.assertFalse(bilibili._archive_missing())


class EpisodesTest(unittest.TestCase):
    """选集识别：分 P 优先，其次合集。"""

    def _view(self, data: dict, code: int = 0):
        def fake_get_json(url, cookie="", proxy="", retries=3):
            return {"code": code, "data": data, "message": "x"}

        original = bilibili._get_json
        bilibili._get_json = fake_get_json
        self.addCleanup(setattr, bilibili, "_get_json", original)

    @staticmethod
    def _pages(count: int) -> list[dict]:
        return [
            {"page": index, "cid": 1000 + index,
             "part": f"第{index}段", "duration": 60 * index}
            for index in range(1, count + 1)
        ]

    def test_multi_page_lists_every_part(self) -> None:
        self._view({"bvid": "BV1xx411c7mD", "title": "合集视频", "owner": {"name": "作者"},
                    "pages": self._pages(3), "ugc_season": {}})
        result = bilibili.episodes("https://www.bilibili.com/video/BV1xx411c7mD")
        self.assertTrue(result["has_multi"])
        self.assertEqual(result["source"], "pages")
        self.assertEqual(len(result["episodes"]), 3)
        first = result["episodes"][0]
        self.assertEqual(first["label"], "P1 第1段")
        self.assertEqual(first["url"], "https://www.bilibili.com/video/BV1xx411c7mD?p=1")
        self.assertEqual(first["key"], "bili:BV1xx411c7mD:p1")
        self.assertEqual(result["episodes"][2]["duration"], 180)

    def test_single_page_is_not_multi(self) -> None:
        self._view({"bvid": "BV1yy411c7mE", "title": "单集", "owner": {"name": "作者"},
                    "pages": self._pages(1), "ugc_season": {}})
        result = bilibili.episodes("https://www.bilibili.com/video/BV1yy411c7mE")
        self.assertFalse(result["has_multi"])
        self.assertEqual(result["episodes"], [])

    def test_season_is_used_when_single_page(self) -> None:
        season = {
            "title": "合集",
            "sections": [
                {"episodes": [
                    {"bvid": "BV1aa411c7m1", "title": "第一集",
                     "page": {"page": 1, "duration": 300}, "arc": {}},
                    {"bvid": "BV1bb411c7m2", "title": "第二集",
                     "page": {"page": 1, "duration": 400}, "arc": {}},
                    {"bvid": "BV1cc411c7m3", "title": "", "page": 2, "arc": {"duration": 50}},
                ]}
            ],
        }
        self._view({"bvid": "BV1aa411c7m1", "title": "第一集", "owner": {"name": "作者"},
                    "pages": self._pages(1), "ugc_season": season})
        result = bilibili.episodes("https://www.bilibili.com/video/BV1aa411c7m1")
        self.assertTrue(result["has_multi"])
        self.assertEqual(result["source"], "season")
        self.assertEqual([ep["label"] for ep in result["episodes"]],
                         ["第一集", "第二集", "BV1cc411c7m3"])
        # `page` 既可能是对象也可能是整数，两种都要能读出 P 号
        self.assertEqual(result["episodes"][2]["url"],
                         "https://www.bilibili.com/video/BV1cc411c7m3?p=2")
        self.assertEqual(result["episodes"][2]["key"], "bili:BV1cc411c7m3:p2")
        self.assertEqual(result["episodes"][0]["duration"], 300)

    def test_multi_page_wins_over_season(self) -> None:
        season = {"sections": [{"episodes": [
            {"bvid": "BV1zz411c7m4", "page": {"page": 1}, "title": "x"},
            {"bvid": "BV1ww411c7m5", "page": {"page": 1}, "title": "y"},
        ]}]}
        self._view({"bvid": "BV1xx411c7mD", "title": "t", "owner": {},
                    "pages": self._pages(4), "ugc_season": season})
        result = bilibili.episodes("https://www.bilibili.com/video/BV1xx411c7mD")
        self.assertEqual(result["source"], "pages", "分 P 比合集更贴近这条链接")

    def test_current_page_is_reported(self) -> None:
        self._view({"bvid": "BV1xx411c7mD", "title": "t", "owner": {},
                    "pages": self._pages(5), "ugc_season": {}})
        result = bilibili.episodes("https://www.bilibili.com/video/BV1xx411c7mD411c7mD?p=3")
        self.assertEqual(result["current"], 3)

    def test_platform_without_episodes(self) -> None:
        from douyinkit.platforms import by_name
        result = by_name("douyin").episodes("https://www.douyin.com/video/1", {})
        self.assertFalse(result["has_multi"])
        self.assertEqual(result["episodes"], [])


class BangumiTest(unittest.TestCase):
    """番剧（pgc）：识别、选集、取流。只验证「拿到什么用什么」，不做付费绕过。"""

    SEASON = {
        "title": "间谍过家家 第二季",
        "up_info": {"uname": "哔哩哔哩番剧"},
        "publish": {"pub_time": "2023-10-07 23:00:00"},
        "episodes": [
            {"id": 781703, "cid": 1323548379, "title": "26",
             "long_title": "任务26 跟踪父亲和母亲", "duration": 1421000},
            {"id": 787878, "cid": 1324000000, "title": "27",
             "long_title": "任务27 邦德的生存战略", "duration": 11000},
        ],
    }

    PLAY = {
        "dash": {
            "video": [{"id": 80, "codecid": 7, "codecs": "avc1", "height": 1080,
                       "width": 1920, "bandwidth": 1000,
                       "baseUrl": "https://cdn/v.m4s", "backupUrl": []}],
            "audio": [{"id": 30280, "bandwidth": 192000,
                       "baseUrl": "https://cdn/a.m4s", "backupUrl": []}],
        },
        "quality": 80,
    }

    def _patch(self, play=None, play_code=0):
        season = self.SEASON

        def fake_get_json(url, cookie="", proxy="", retries=3):
            if "pgc/view/web/season" in url:
                return {"code": 0, "result": season}
            if "pgc/player/web/playurl" in url:
                return {"code": play_code,
                        "result": play if play is not None else self.PLAY,
                        "message": "test"}
            return {"code": 0, "data": {}, "result": {}}

        original = bilibili._get_json
        bilibili._get_json = fake_get_json
        self.addCleanup(setattr, bilibili, "_get_json", original)

    def test_recognise_links(self) -> None:
        self.assertTrue(bilibili.is_bangumi(
            "https://www.bilibili.com/bangumi/play/ss46085"))
        self.assertTrue(bilibili.is_bangumi(
            "https://www.bilibili.com/bangumi/play/ep781703?from=search"))
        self.assertFalse(bilibili.is_bangumi(
            "https://www.bilibili.com/video/BV1fZJ26mEiY"))
        self.assertEqual(
            bilibili.parse_bangumi(
                "https://www.bilibili.com/bangumi/play/ep781703"),
            ("ep", 781703))

    def test_normalize_uses_ep_id(self) -> None:
        _, key = bilibili.normalize(
            "https://www.bilibili.com/bangumi/play/ep781703")
        self.assertEqual(key, "bili:ep781703")

    def test_season_lists_episodes(self) -> None:
        self._patch()
        data = bilibili.episodes("https://www.bilibili.com/bangumi/play/ss46085")
        self.assertTrue(data["has_multi"])
        self.assertEqual(data["source"], "bangumi")
        self.assertEqual(data["title"], "间谍过家家 第二季")
        self.assertEqual(data["author"], "哔哩哔哩番剧")
        self.assertEqual(len(data["episodes"]), 2)
        first = data["episodes"][0]
        self.assertEqual(first["url"],
                         "https://www.bilibili.com/bangumi/play/ep781703")
        self.assertEqual(first["key"], "bili:ep781703")
        self.assertEqual(first["duration"], 1421, "接口给的是毫秒")

    def test_ep_link_marks_current(self) -> None:
        self._patch()
        data = bilibili.episodes("https://www.bilibili.com/bangumi/play/ep787878")
        self.assertEqual(data["current"], 2)

    def test_extract_builds_meta(self) -> None:
        self._patch()
        meta = bilibili.extract("https://www.bilibili.com/bangumi/play/ep781703")
        self.assertIn("间谍过家家 第二季", meta["title"])
        self.assertIn("任务26", meta["title"])
        self.assertEqual(meta["aweme_id"], "ep781703")
        self.assertEqual(meta["author"], "哔哩哔哩番剧")
        self.assertEqual(meta["duration"], 1421)
        self.assertTrue(meta["need_merge"], "番剧也是 DASH，需要合成")
        self.assertEqual(len(meta["assets"]), 2)
        self.assertEqual(meta["quality"], "1080P")

    def test_permission_error_is_explained(self) -> None:
        self._patch(play={}, play_code=87008)
        with self.assertRaises(bilibili.BiliError) as ctx:
            bilibili.extract("https://www.bilibili.com/bangumi/play/ep781703")
        self.assertIn("大会员", str(ctx.exception))

    def test_region_limit_is_explained(self) -> None:
        self._patch(play={}, play_code=-10403)
        with self.assertRaises(bilibili.BiliError) as ctx:
            bilibili.extract("https://www.bilibili.com/bangumi/play/ep781703")
        self.assertIn("地区", str(ctx.exception))

    def test_no_stream_gives_clear_error(self) -> None:
        self._patch(play={})
        with self.assertRaises(bilibili.BiliError) as ctx:
            bilibili.extract("https://www.bilibili.com/bangumi/play/ep781703")
        self.assertIn("没有拿到播放地址", str(ctx.exception))

    def test_media_page_link_is_rejected_with_hint(self) -> None:
        with self.assertRaises(bilibili.BiliError) as ctx:
            bilibili.extract("https://www.bilibili.com/bangumi/media/md12345")
        self.assertIn("媒体页", str(ctx.exception))


class BangumiShapesTest(unittest.TestCase):
    """老番的 payload 和当季番不一样，不能假设字段类型。"""

    OLD = {
        "title": "凉宫春日的忧郁 2006",
        "up_info": None,                     # 老番没有 up_info
        "staff": "原作 / 构成协力：谷川流\\n监督：石原立也",   # 一整段纯文本
        "sections": None,
        "publish": {"pub_time": "2006-04-02 00:00:00"},      # 日期文本
        "episodes": [
            {"id": 18901, "cid": 1, "title": "1", "long_title": "朝比奈实玖琉的冒险", "duration": 1461000},
            {"id": 18902, "cid": 2, "title": "2", "long_title": "凉宫春日的忧郁 I", "duration": 0},
            "这条不是 dict，应该被跳过",
            {"id": "18903", "cid": 3, "title": "3", "long_title": "", "duration": None},
        ],
    }

    PLAY = {
        "dash": {
            "video": [{"id": 80, "codecid": 7, "codecs": "avc1", "height": 1080,
                       "width": 1920, "bandwidth": 1000,
                       "baseUrl": "https://cdn/v.m4s", "backupUrl": []}],
            "audio": [{"id": 30280, "bandwidth": 192000,
                       "baseUrl": "https://cdn/a.m4s", "backupUrl": []}],
        },
        "quality": 80,
    }

    def _patch(self, season: dict):
        def fake_get_json(url, cookie="", proxy="", retries=3):
            if "pgc/view/web/season" in url:
                return {"code": 0, "result": season}
            if "pgc/player/web/playurl" in url:
                return {"code": 0, "result": self.PLAY}
            return {"code": 0, "result": {}, "data": {}}

        original = bilibili._get_json
        bilibili._get_json = fake_get_json
        self.addCleanup(setattr, bilibili, "_get_json", original)

    def test_author_falls_back_when_staff_is_text(self) -> None:
        self.assertEqual(bilibili._bangumi_author(self.OLD), "bilibili 番剧")
        self.assertEqual(bilibili._bangumi_author({"up_info": {"uname": "某组"}}), "某组")
        self.assertEqual(
            bilibili._bangumi_author({"staff": [{"name": "A"}, {"name": "B"}]}), "A")

    def test_episodes_survive_messy_payload(self) -> None:
        self._patch(self.OLD)
        data = bilibili.episodes("https://www.bilibili.com/bangumi/play/ep18901")
        self.assertTrue(data["has_multi"])
        self.assertEqual(data["author"], "bilibili 番剧")
        self.assertEqual(len(data["episodes"]), 3, "非 dict 的条目要跳过")
        self.assertEqual(data["current"], 1)
        self.assertEqual(data["episodes"][0]["label"], "1 朝比奈实玖琉的冒险")
        self.assertEqual(data["episodes"][2]["duration"], 0, "duration 为空当 0")

    def test_extract_survives_messy_payload(self) -> None:
        self._patch(self.OLD)
        meta = bilibili.extract("https://www.bilibili.com/bangumi/play/ep18901")
        self.assertIn("凉宫春日的忧郁 2006", meta["title"])
        self.assertEqual(meta["created"], 0, "日期文本转不成时间戳时给 0")


class RetryFailedTest(unittest.TestCase):
    """重启后一堆「上次退出时未完成」，要能一键重排。"""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        from douyinkit.engine import Engine
        from douyinkit.store import Store

        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self._tmp.name) / "t.db"))
        self.engine = Engine(self.store, {"output_dir": self._tmp.name,
                                          "concurrency": 1})
        self.engine.pause_queue(True)   # 只验证排队，不真的去下载

    def tearDown(self) -> None:
        self.engine.pause_queue(False)
        self.engine.shutdown()
        self.store.close()
        self._tmp.cleanup()

    def test_requeues_only_failed(self) -> None:
        a = self.store.create_job("https://x/1", "k1", platform="bilibili")
        b = self.store.create_job("https://x/2", "k2", platform="bilibili")
        c = self.store.create_job("https://x/3", "k3", platform="bilibili")
        self.store.update_job(a, status="failed", error="上次退出时未完成")
        self.store.update_job(b, status="failed", error="上次退出时未完成")
        self.store.update_job(c, status="done")

        count = self.engine.retry_failed()
        self.assertEqual(count, 2)
        self.assertEqual(self.store.get_job(a)["status"], "queued")
        self.assertEqual(self.store.get_job(b)["status"], "queued")
        self.assertEqual(self.store.get_job(c)["status"], "done", "完成的任务不能被重排")
        self.assertIsNone(self.store.get_job(a)["error"])


if __name__ == "__main__":
    unittest.main()
