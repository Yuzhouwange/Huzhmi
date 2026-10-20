"""文件名模板、重名处理、老库拍平、配置升级的单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from huzhmi import config as config_mod
from huzhmi import library
from huzhmi.engine import (
    DEFAULT_FILE_TEMPLATE,
    DEFAULT_TEMPLATE,
    build_file_stem,
    build_out_dir,
    unique_path,
)

META = {
    "aweme_id": "7304228124479262006",
    "title": "开局一条狗",
    "author": "可爱的光头强",
    "kind": "video",
    "created": 1700000000,
}


class FileNameTest(unittest.TestCase):
    def test_default_is_title_without_folder(self) -> None:
        self.assertEqual(DEFAULT_FILE_TEMPLATE, "{title}")
        self.assertEqual(build_file_stem(META, "douyin", "{title}"), "开局一条狗")

    def test_author_template(self) -> None:
        stem = build_file_stem(META, "douyin", "{author} - {title}")
        self.assertEqual(stem, "可爱的光头强 - 开局一条狗")

    def test_multi_asset_gets_index(self) -> None:
        stem = build_file_stem(META, "douyin", "{title}", index=2, total=3)
        self.assertEqual(stem, "开局一条狗 - 02")

    def test_single_asset_has_no_index(self) -> None:
        self.assertEqual(build_file_stem(META, "douyin", "{title}", index=1, total=1),
                         "开局一条狗")

    def test_explicit_index_not_duplicated(self) -> None:
        stem = build_file_stem(META, "douyin", "{title}_{index}", index=2, total=2)
        self.assertEqual(stem, "开局一条狗_02")

    def test_illegal_chars_cleaned(self) -> None:
        meta = dict(META, title='a/b:c*d?"<>|')
        stem = build_file_stem(meta, "douyin", "{title}")
        self.assertNotIn("/", stem)
        self.assertNotIn(":", stem)

    def test_empty_template_falls_back_to_id(self) -> None:
        meta = dict(META, title="...")
        stem = build_file_stem(meta, "douyin", "{title}")
        self.assertEqual(stem, "7304228124479262006")

    def test_unknown_placeholder_dropped(self) -> None:
        self.assertEqual(build_file_stem(META, "douyin", "{title}{nope}"), "开局一条狗")


class UniquePathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_free_name_when_missing(self) -> None:
        dest = self.root / "a.mp4"
        self.assertEqual(unique_path(dest), dest)

    def test_collision_appends_counter(self) -> None:
        (self.root / "a.mp4").write_text("x", encoding="utf-8")
        (self.root / "a (2).mp4").write_text("x", encoding="utf-8")
        self.assertEqual(unique_path(self.root / "a.mp4").name, "a (3).mp4")

    def test_retry_reuses_own_file(self) -> None:
        dest = self.root / "a.mp4"
        dest.write_text("x", encoding="utf-8")
        # 本任务上一轮已经写出的文件，重试时应该原地覆盖，而不是变成 a (2)
        self.assertEqual(unique_path(dest, keep={dest}), dest)


class FolderTemplateTest(unittest.TestCase):
    def test_default_folder_is_platform_author(self) -> None:
        self.assertEqual(DEFAULT_TEMPLATE, "{platform}/{author}")
        out = build_out_dir(Path("E:/视频"), META, "douyin", DEFAULT_TEMPLATE)
        self.assertEqual(out, Path("E:/视频/抖音/可爱的光头强"))

    def test_template_with_title_still_works(self) -> None:
        out = build_out_dir(Path("E:/视频"), META, "douyin",
                            "{platform}/{author}/{title}")
        self.assertEqual(out, Path("E:/视频/抖音/可爱的光头强/开局一条狗"))


class ConfigMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._saved = (config_mod.CONFIG_PATH, config_mod.DATA_DIR)
        config_mod.CONFIG_PATH = self.dir / "config.json"
        config_mod.DATA_DIR = self.dir

    def tearDown(self) -> None:
        config_mod.CONFIG_PATH, config_mod.DATA_DIR = self._saved
        self._tmp.cleanup()

    def _write(self, data: dict) -> None:
        config_mod.CONFIG_PATH.write_text(json.dumps(data), encoding="utf-8")

    def test_legacy_folder_template_is_upgraded(self) -> None:
        self._write({"folder_template": "{platform}/{author}/{title}"})
        cfg = config_mod.load()
        self.assertEqual(cfg["folder_template"], "{platform}/{author}")

    def test_custom_template_is_kept(self) -> None:
        self._write({"folder_template": "{author}/{date}"})
        self.assertEqual(config_mod.load()["folder_template"], "{author}/{date}")

    def test_file_template_default(self) -> None:
        cfg = config_mod.load()
        self.assertEqual(cfg["file_template"], "{title}")

    def test_empty_file_template_reset(self) -> None:
        self._write({"file_template": "   "})
        self.assertEqual(config_mod.load()["file_template"], "{title}")


class FlattenLibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make(self, rel: str, size: int = 8) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        return path

    def test_plan_moves_nested_media(self) -> None:
        self._make("标题A/01.mp4")
        self._make("标题B/01.mp4")
        moves = library.plan_flatten(self.root)
        self.assertEqual(len(moves), 2)
        targets = sorted(target.name for _, target in moves)
        self.assertEqual(targets, ["标题A.mp4", "标题B.mp4"])

    def test_multi_file_folder_gets_numbers(self) -> None:
        self._make("图集/01.jpg")
        self._make("图集/02.jpg")
        moves = library.plan_flatten(self.root)
        self.assertEqual(sorted(t.name for _, t in moves),
                         ["图集 - 01.jpg", "图集 - 02.jpg"])

    def test_already_flat_is_noop(self) -> None:
        self._make("标题A.mp4")
        self.assertEqual(library.plan_flatten(self.root), [])

    def test_partial_files_skipped(self) -> None:
        self._make("标题A/01.mp4.part")
        self.assertEqual(library.plan_flatten(self.root), [])

    def test_name_collision_adds_counter(self) -> None:
        self._make("标题A/01.mp4")
        self._make("其它/标题A.mp4")
        targets = [t.name for _, t in library.plan_flatten(self.root)]
        self.assertEqual(len(set(targets)), len(targets))

    def test_apply_then_prune(self) -> None:
        self._make("标题A/01.mp4", 16)
        self._make("标题B/01.mp4", 32)
        moves = library.plan_flatten(self.root)
        library.apply_moves(moves)
        removed = library.prune_empty_dirs(self.root)
        self.assertEqual(removed, 2)
        names = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(names, ["标题A.mp4", "标题B.mp4"])
        self.assertEqual((self.root / "标题A.mp4").stat().st_size, 16)

    def test_prune_keeps_non_empty_dirs(self) -> None:
        self._make("标题A/01.mp4")
        self._make("别的/sub/notes.txt")
        library.apply_moves(library.plan_flatten(self.root))
        library.prune_empty_dirs(self.root)
        self.assertTrue((self.root / "别的" / "sub" / "notes.txt").is_file())


if __name__ == "__main__":
    unittest.main()
