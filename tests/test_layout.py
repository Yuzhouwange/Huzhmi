"""目录模板（分类 / 分离）的单元测试。"""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from douyinkit.engine import DEFAULT_TEMPLATE, build_out_dir

BASE = Path("D:/downloads")
META = {
    "aweme_id": "7304228124479262006",
    "title": "测试标题",
    "author": "测试作者",
    "kind": "video",
    "created": 0,
}


class LayoutTest(unittest.TestCase):
    def test_default_template(self) -> None:
        # 默认不再给每条作品单开一层：平台/作者 两层，文件名承载标题
        self.assertEqual(DEFAULT_TEMPLATE, "{platform}/{author}")
        out = build_out_dir(BASE, META, "douyin", DEFAULT_TEMPLATE)
        self.assertEqual(out, BASE / "抖音" / "测试作者")

    def test_bilibili_label(self) -> None:
        out = build_out_dir(BASE, META, "bilibili", "{platform}/{author}")
        self.assertEqual(out, BASE / "B站" / "测试作者")

    def test_kind_separation(self) -> None:
        images = dict(META, kind="images")
        out = build_out_dir(BASE, images, "douyin", "{kind}/{author}")
        self.assertEqual(out, BASE / "图集" / "测试作者")
        out = build_out_dir(BASE, META, "douyin", "{kind}/{author}")
        self.assertEqual(out, BASE / "视频" / "测试作者")

    def test_date_placeholder(self) -> None:
        meta = dict(META, created=1700000000)
        expected = time.strftime("%Y-%m-%d", time.localtime(1700000000))
        out = build_out_dir(BASE, meta, "douyin", "{date}/{title}")
        self.assertEqual(out, BASE / expected / "测试标题")

    def test_illegal_chars_inside_segment(self) -> None:
        meta = dict(META, title="a/b:c*d?")
        out = build_out_dir(BASE, meta, "douyin", "{author}/{title}")
        # 标题里的 / 不能变成层级；非法字符统一变 _
        self.assertEqual(out, BASE / "测试作者" / "a_b_c_d_")

    def test_dotdot_dropped(self) -> None:
        out = build_out_dir(BASE, META, "douyin", "..//{author}/../{title}")
        self.assertEqual(out, BASE / "测试作者" / "测试标题")

    def test_empty_template_falls_back(self) -> None:
        out = build_out_dir(BASE, META, "douyin", "   ")
        self.assertEqual(out, BASE / "抖音" / "测试作者" / "7304228124479262006")

    def test_unknown_placeholder_removed(self) -> None:
        out = build_out_dir(BASE, META, "douyin", "{author}{unknown}")
        self.assertEqual(out, BASE / "测试作者")

    def test_backslash_separator(self) -> None:
        out = build_out_dir(BASE, META, "douyin", r"{platform}\{author}")
        self.assertEqual(out, BASE / "抖音" / "测试作者")


if __name__ == "__main__":
    unittest.main()
