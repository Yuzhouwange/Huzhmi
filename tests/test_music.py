"""音乐模块：AES / NCM 解密 / 平台解析 / 队列路径 / Excel 导出。"""

from __future__ import annotations

import base64
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from douyinkit.music import aes, ncm, providers, service, xlsx
from douyinkit.store import Store


def _pad(data: bytes, block: int = 16) -> bytes:
    pad = block - len(data) % block
    return data + bytes([pad]) * pad


def _make_ncm(path: Path, audio: bytes, key: bytes, meta: dict,
              cover: bytes = b"\xff\xd8\xff\xe0fakejpg") -> None:
    """按 NCM 格式写一个测试文件（密钥前缀在 AES 里）。"""
    key_plain = _pad(ncm.KEY_PREFIX + key)
    key_block = aes.encrypt_ecb(key_plain, ncm.CORE_KEY)
    key_block = bytes(byte ^ 0x64 for byte in key_block)

    meta_plain = _pad(b"music:" + json.dumps(meta, ensure_ascii=False).encode())
    meta_enc = aes.encrypt_ecb(meta_plain, ncm.META_KEY)
    meta_block = ncm.META_PREFIX + base64.b64encode(meta_enc)
    meta_block = bytes(byte ^ 0x63 for byte in meta_block)

    box = ncm._build_keybox(key)
    encrypted = ncm._decrypt_chunk(audio, box)   # XOR 对称

    blob = bytearray()
    blob += ncm.MAGIC + b"\x00\x00"
    blob += struct.pack("<I", len(key_block)) + key_block
    blob += struct.pack("<I", len(meta_block)) + meta_block
    blob += b"\x00\x00\x00\x00" + b"\x00"
    blob += struct.pack("<I", len(cover)) + struct.pack("<I", len(cover)) + cover
    blob += encrypted
    path.write_bytes(bytes(blob))


class AesTest(unittest.TestCase):
    def test_fips_197_vector(self):
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plain = bytes.fromhex("00112233445566778899aabbccddeeff")
        cipher = aes.encrypt_block(plain, key)
        self.assertEqual(cipher.hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")
        self.assertEqual(aes.decrypt_block(cipher, key), plain)

    def test_ecb_roundtrip(self):
        key = b"0123456789abcdef"
        data = b"x" * 64
        self.assertEqual(aes.decrypt_ecb(aes.encrypt_ecb(data, key), key), data)


class NcmTest(unittest.TestCase):
    def test_decode_roundtrip(self):
        meta = {
            "musicName": "测试歌曲",
            "artist": [["歌手甲", 1], ["歌手乙", 2]],
            "album": "测试专辑",
            "format": "mp3",
            "bitrate": 320000,
            "duration": 1000,
        }
        audio = b"ID3\x03\x00\x00\x00\x00\x00\x00" + bytes(range(256)) * 40
        key = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "song.ncm"
            _make_ncm(source, audio, key, meta)
            self.assertTrue(ncm.is_ncm(source))
            info = ncm.read_meta(source)
            self.assertEqual(info.title, "测试歌曲")
            self.assertEqual(info.artist, "歌手甲 / 歌手乙")
            self.assertEqual(info.album, "测试专辑")
            self.assertEqual(info.cover[:2], b"\xff\xd8")

            result = ncm.decode_file(source, root / "out")
            decoded = Path(result["path"])
            self.assertEqual(decoded.suffix, ".mp3")
            self.assertEqual(decoded.read_bytes(), audio)
            self.assertTrue((root / "out" / "cover.jpg").is_file())

    def test_decode_rejects_non_ncm(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.ncm"
            path.write_bytes(b"not an ncm file")
            with self.assertRaises(ncm.NcmError):
                ncm.decode_file(path, tmp)


class ProviderTest(unittest.TestCase):
    def test_cookie_value(self):
        cookie = "MUSIC_U=abc; uin=o123456; qqmusic_key=xyz"
        self.assertEqual(
            providers.Provider._cookie_value(cookie, "uin", "qqmusic_uin"), "o123456")
        self.assertEqual(
            providers.Provider._cookie_value(cookie, "qqmusic_key"), "xyz")
        self.assertEqual(providers.Provider._cookie_value("", "uin"), "")

    def test_netease_login_empty_account(self):
        from douyinkit.music import login

        info = login.NeteaseLogin().account("")
        self.assertFalse(info["logged_in"])
        self.assertFalse(info["vip"])

    def test_song_normalization(self):
        row = providers.song("kuwo", "1", "晴天&nbsp;(Live)", "周杰伦 &amp; 某",
                             album="叶惠美", duration="269.5", ext="MP3")
        self.assertEqual(row["title"], "晴天 (Live)")
        self.assertEqual(row["artist"], "周杰伦 & 某")
        self.assertEqual(row["duration"], 269.5)
        self.assertEqual(row["ext"], "mp3")

    def test_parse_collection_id(self):
        cases = [
            ("netease", "https://music.163.com/album?id=36412633", "36412633"),
            ("netease", "https://music.163.com/playlist/24381616?userid=1", "24381616"),
            ("qq", "https://y.qq.com/n/ryqq/albumDetail/003RMaRI1iFoYd", "003RMaRI1iFoYd"),
            ("qq", "https://y.qq.com/n/ryqq/playlist/7011264340", "7011264340"),
            ("kugou", "https://www.kugou.com/album/966846.html", "966846"),
            ("kuwo", "https://www.kuwo.cn/album_detail/1077", "1077"),
            ("netease", "36412633", "36412633"),
            ("netease", "", ""),
        ]
        for provider, text, expect in cases:
            self.assertEqual(providers.parse_collection_id(provider, text), expect)

    def test_describe(self):
        rows = {row["name"]: row for row in providers.describe()}
        self.assertTrue(rows["netease"]["album"])
        self.assertTrue(rows["netease"]["playlist"])
        self.assertFalse(rows["kuwo"]["album"])
        self.assertTrue(rows["qq"]["ranks"])


class ServiceTest(unittest.TestCase):
    def test_plan_target(self):
        root = Path("D:/Music")
        target = service.plan_target(
            root, {"artist": "A/B", "album": "C:D", "title": "E?F"}, "mp3")
        self.assertEqual(target, root / "A_B" / "C_D" / "E_F.mp3")
        target = service.plan_target(root, {"artist": "", "album": "", "title": ""}, "wav")
        self.assertEqual(target.name, "未命名.wav")

    def test_unique_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = folder / "a.mp3"
            first.write_bytes(b"x")
            self.assertEqual(service.unique_path(first).name, "a (2).mp3")

    def test_collect_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.mp3").write_bytes(b"x")
            (folder / "b.flac").write_bytes(b"x")
            (folder / "c.txt").write_bytes(b"x")
            names = [item.name for item in service.collect_audio(folder)]
            self.assertEqual(names, ["a.mp3", "b.flac"])
            self.assertEqual(service.collect_audio(folder / "a.mp3")[0].name, "a.mp3")
            self.assertEqual(service.collect_audio(folder / "c.txt"), [])
            with self.assertRaises(service.MusicError):
                service.collect_audio(folder / "missing")

    def test_submit_convert_and_local(self):
        class NoWorkers(service.MusicService):
            def _spawn(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(str(root / "db.sqlite"))
            audio = root / "song.mp3"
            audio.write_bytes(b"ID3" + b"\x00" * 2048)
            jid = store.create_music_job(
                payload="{}", dedupe_key="k", provider="netease", song_id="1",
                title="song", artist="a", album="b", target=str(audio))
            store.update_music_job(jid, status="done")
            music = NoWorkers(store, {"music_dir": str(root)})
            with self.assertRaises(service.MusicError):
                music.submit_convert(jid, "mp3")
            with self.assertRaises(service.MusicError):
                music.submit_convert(jid, "ape")
            result = music.submit_convert(jid, "wav")
            self.assertEqual(result["count"], 1)
            self.assertEqual(Path(result["target"]).suffix, ".wav")

            source = root / "local.flac"
            source.write_bytes(b"fLaC" + b"\x00" * 2048)
            out = root / "out"
            local = music.submit_convert_local(str(source), "mp3", str(out))
            self.assertEqual(local["count"], 1)
            self.assertTrue((out / "local.mp3").as_posix().endswith("local.mp3"))
            store.close()

    def test_store_music_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(str(Path(tmp) / "db.sqlite"))
            jid = store.create_music_job(
                payload="{}", dedupe_key="k", provider="netease", song_id="1",
                title="t", artist="a", album="b", target=str(Path(tmp) / "t.mp3"))
            self.assertEqual(store.get_music_job(jid)["status"], "queued")
            store.update_music_job(jid, status="running", progress=0.5)
            self.assertEqual(store.get_music_job(jid)["progress"], 0.5)
            self.assertEqual(store.music_counts()["running"], 1)
            self.assertEqual(store.recover_music(), [jid])
            self.assertEqual(store.get_music_job(jid)["status"], "queued")
            store.update_music_job(jid, status="done")
            self.assertEqual(store.clear_music_finished(), 1)
            self.assertEqual(store.list_music_jobs(), [])
            store.close()

    def test_pause_resume_cancel(self):
        class NoWorkers(service.MusicService):
            def _spawn(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(str(root / "db.sqlite"))
            target = root / "song.mp3"
            payload = json.dumps({"kind": "song", "fmt": "mp3",
                                  "target": str(target), "song": {}})
            jid = store.create_music_job(
                payload=payload, dedupe_key="k", provider="netease",
                song_id="1", title="t", artist="a", album="b",
                target=str(target))
            music = NoWorkers(store, {"music_dir": str(root)})
            self.assertTrue(music.pause(jid))
            self.assertEqual(store.get_music_job(jid)["status"], "paused")
            self.assertTrue(music.resume(jid))
            self.assertEqual(store.get_music_job(jid)["status"], "queued")
            self.assertTrue(music.pause(jid))
            partial = service.MusicService._partial_path(store.get_music_job(jid))
            partial.write_bytes(b"half")
            self.assertTrue(music.cancel(jid))
            self.assertEqual(store.get_music_job(jid)["status"], "cancelled")
            self.assertFalse(partial.exists())
            store.close()

    def test_codec_quality(self):
        high = service.codec_args("mp3", "high")
        low = service.codec_args("mp3", "low")
        self.assertIn("320k", high)
        self.assertIn("128k", low)
        self.assertIn("libmp3lame", high)
        self.assertIn("-q:a", service.codec_args("ogg", "standard"))

    def test_clear_finished_video_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(str(Path(tmp) / "db.sqlite"))
            jid = store.create_job("https://example/a", "k:a")
            store.add_assets(jid, [{"idx": 0, "kind": "video", "url": "x"}])
            store.add_step(jid, 0, "audio_mp3", {})
            store.update_job(jid, status="done")
            self.assertEqual(store.clear_finished(), 1)
            self.assertIsNone(store.get_job(jid))
            store.close()

    def test_find_active_by_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(str(Path(tmp) / "db.sqlite"))
            jid = store.create_job("https://example/a", "k:a")
            self.assertEqual(store.find_active_by_key("k:a")["id"], jid)
            store.update_job(jid, status="done")
            self.assertIsNone(store.find_active_by_key("k:a"))
            self.assertEqual(store.find_done_by_key("k:a")["id"], jid)
            store.close()


class LibraryTest(unittest.TestCase):
    def _fake_mp3(self, path: Path) -> None:
        frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
        path.write_bytes(frame * 40)

    def test_scan_lyrics_m3u_delete(self):
        from douyinkit.music import library

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            song = root / "歌手" / "测试歌.mp3"
            song.parent.mkdir()
            self._fake_mp3(song)
            song.with_suffix(".lrc").write_text(
                "\ufeff[00:00.00]第一行\n[00:01.00]第二行", encoding="utf-8")
            data = library.scan(root)
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["items"][0]["title"], "测试歌")
            self.assertTrue(data["items"][0]["has_lrc"])
            self.assertIn("第一行", library.lyrics_text(song))
            self.assertNotIn("\ufeff", library.lyrics_text(song))
            m3u = library.export_m3u(root, [str(song)])
            self.assertTrue(Path(m3u).is_file())
            self.assertIn("测试歌.mp3", Path(m3u).read_text("utf-8"))
            self.assertTrue(library.delete(root, song))
            self.assertFalse(song.exists())
            self.assertFalse(song.with_suffix(".lrc").exists())
            with self.assertRaises(PermissionError):
                library.delete(root, root.parent / "outside.mp3")

    def test_scan_missing_root(self):
        from douyinkit.music import library

        with self.assertRaises(FileNotFoundError):
            library.scan("D:/definitely-not-exists")


class XlsxTest(unittest.TestCase):
    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.xlsx"
            xlsx.write_xlsx(path, [["歌名", "歌手"], ["晴天", "周杰伦"]],
                            widths=[20, 20])
            self.assertTrue(path.is_file())
            with zipfile.ZipFile(path) as jar:
                names = set(jar.namelist())
                self.assertIn("xl/worksheets/sheet1.xml", names)
                self.assertIn("[Content_Types].xml", names)
                sheet = jar.read("xl/worksheets/sheet1.xml")
            root = ElementTree.fromstring(sheet)
            namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            cells = root.findall(f".//{namespace}c")
            self.assertEqual(len(cells), 4)
            texts = [cell.findtext(f".//{namespace}t") for cell in cells]
            self.assertEqual(texts, ["歌名", "歌手", "晴天", "周杰伦"])


if __name__ == "__main__":
    unittest.main()
