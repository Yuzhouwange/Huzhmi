"""文件名清洗与文本解码的单元测试。"""

from __future__ import annotations

import unittest

from huzhmi.douyin import decode_text
from huzhmi.engine import safe_dirname


class SafeDirnameTest(unittest.TestCase):
    def test_chinese_and_emoji_kept(self) -> None:
        name = "瓦努阿图猴哥 - 测试 😂🎬"
        self.assertEqual(safe_dirname(name), name)

    def test_windows_illegal_chars(self) -> None:
        self.assertEqual(safe_dirname('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_control_chars_removed(self) -> None:
        self.assertEqual(safe_dirname("标题\x00\x01\x1f测试"), "标题_测试")

    def test_zero_width_removed(self) -> None:
        self.assertEqual(safe_dirname("标\u200b题\ufeff测试"), "标题测试")

    def test_lone_surrogate_dropped(self) -> None:
        # 孤立代理项无法编码，必须丢掉而不是写成乱码
        cleaned = safe_dirname("abc\ud800def")
        self.assertEqual(cleaned, "abcdef")
        cleaned.encode("utf-8")  # 不应抛异常

    def test_trailing_dots_and_spaces(self) -> None:
        self.assertEqual(safe_dirname("  标题...  "), "标题")

    def test_reserved_device_name(self) -> None:
        self.assertEqual(safe_dirname("CON"), "_CON")
        self.assertEqual(safe_dirname("com1"), "_com1")

    def test_empty_falls_back_to_id(self) -> None:
        self.assertEqual(safe_dirname("", fallback="7304228124479262006"),
                         "7304228124479262006")
        self.assertEqual(safe_dirname("///", fallback="BV1xx"), "BV1xx")

    def test_length_limit(self) -> None:
        self.assertLessEqual(len(safe_dirname("长" * 200)), 80)
        self.assertLessEqual(len(safe_dirname("长" * 200, limit=20)), 20)


class DecodeTextTest(unittest.TestCase):
    def test_utf8(self) -> None:
        self.assertEqual(decode_text("中文标题".encode("utf-8")), "中文标题")

    def test_gb18030_fallback(self) -> None:
        self.assertEqual(decode_text("中文标题".encode("gb18030")), "中文标题")

    def test_broken_bytes_do_not_raise(self) -> None:
        out = decode_text(b"\xff\xfeabc")
        self.assertIn("abc", out)


if __name__ == "__main__":
    unittest.main()
