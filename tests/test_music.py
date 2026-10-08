"""Tests for x360music and the HddMusic plugin's library code (MusicLibrary.cpp)."""

import hashlib
import os
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

from x360music import asf, convert, fmim, mindex, tasks

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "homebrew", "HddMusic")


def song(title, album, artist, genre, number, length_ms, album_artist=None):
    return fmim.SongInfo(fmim.build_header(title, album, artist, album_artist, genre,
                                           length_ms, number))


# Songs whose library was also made with the Xbox-360-Mindex tool (whose
# libraries play on real consoles), in its creation order.  Its output's
# SHA-256 is pinned below, so the format can't drift by accident.
REFERENCE_SONGS = [
    ("intro", "alpha", "bob", "rock", 1, 200000), ("middle", "alpha", "bob", "rock", 2, 181000),
    ("zulu end", "alpha", "bob", "rock", 3, 59000), ("calm", "beta", "ann", "pop", 1, 240000),
    ("drive", "beta", "ann", "pop", 2, 300000), ("ember", "gamma", "bob", "metal", 1, 123000),
    ("anthem", "delta", "cy", "rock", 1, 61000), ("bridge", "delta", "cy", "rock", 2, 62000),
]
REFERENCE_SHA256 = "b18c92367f860b869eda6483582803823508ff550f56c4ef565635d325d6c119"


class FmimTests(unittest.TestCase):
    def test_header_layout(self):
        data = fmim.build_header("Song", "Album", "Artist", "Band", "Rock", 61499, 3)
        self.assertEqual(len(data), 0xD08)
        self.assertEqual(data[:12], b"FMIM\x00\x00\x00\x01\x00\x01\x00\x01")
        self.assertEqual(data[0x0C:0x14], "Song".encode("utf-16-be"))
        self.assertEqual(data[0x60C:0x614], "Band".encode("utf-16-be"))
        self.assertEqual(data[0xA0C:0xA14], "Rock".encode("utf-16-be"))
        self.assertEqual(struct.unpack(">II", data[0xC0C:0xC14]), (61000, 3))
        self.assertFalse(any(data[0xC14:]))

    def test_round_trip_and_limits(self):
        long_title = "x" * 300
        info = fmim.SongInfo(fmim.build_header(long_title, "", "Ann", None, "Pop", 1500, 0))
        self.assertEqual(info.title, "x" * 255)
        self.assertEqual(info.album_artist, "Ann")  # defaults to the artist
        self.assertEqual(info.length_ms, 2000)
        with self.assertRaises(fmim.FmimError):
            fmim.SongInfo(b"RIFF" + bytes(0xD08))
        with self.assertRaises(fmim.FmimError):
            fmim.build_song(bytes(0xD08), b"ID3")

    def test_truncation_keeps_surrogate_pairs_whole(self):
        units = fmim.to_units("a" * 38 + "\U0001F3B5")
        self.assertEqual(len(units), 40)
        self.assertEqual(fmim.truncate_units(units, 39), units[:38])


class LibraryTests(unittest.TestCase):
    def test_fresh_library(self):
        lib = mindex.Library()
        data = lib.to_bytes()
        self.assertEqual(len(data), 7 * 600)
        self.assertEqual(data[:24], struct.pack(">I4sIIII", 7, b" IMX", 2, 6, 1, 6))
        self.assertEqual(lib.check(), [])
        self.assertEqual(lib.u32(6, 0x10), mindex.NONE)  # empty playlist list

    def test_matches_reference_tool(self):
        lib = mindex.Library()
        for title, album, artist, genre, number, length in REFERENCE_SONGS:
            lib.add_song(song(title, album, artist, genre, number, length))
        self.assertEqual(hashlib.sha256(lib.to_bytes()).hexdigest(), REFERENCE_SHA256)
        self.assertEqual(lib.check(), [])

    def test_lists_are_sorted_and_linked(self):
        lib = mindex.Library()
        for info in (song("Zebra", "Alpha", "Bob", "Rock", 2, 1000),
                     song("apple", "Alpha", "Bob", "Rock", 1, 1000),
                     song("Mid", "beta", "Ann", "Pop", 0, 1000),
                     song("Guest", "Hits", "Cy", "Pop", 4, 1000, album_artist="Various")):
            self.assertIsNotNone(lib.add_song(info))
        self.assertEqual(lib.check(), [])
        names = lambda owner, kind: [lib.name(i) for i in lib.members(owner, kind)]  # noqa: E731
        self.assertEqual(names(lib.heads[mindex.TRACK], mindex.GLOBAL),
                         ["apple", "Guest", "Mid", "Zebra"])
        self.assertEqual(names(lib.heads[mindex.ARTIST], mindex.GLOBAL),
                         ["Ann", "Bob", "Cy", "Various"])
        album = lib.find(mindex.ALBUM, fmim.to_units("alpha"))
        self.assertEqual(names(album, mindex.ALBUM_TRACKS), ["apple", "Zebra"])
        various = lib.find(mindex.ARTIST, fmim.to_units("Various"))
        self.assertEqual(names(various, mindex.ARTIST_ALBUMS), ["Hits"])
        self.assertEqual(names(various, mindex.ARTIST_TRACKS), [])
        mid = lib.find(mindex.TRACK, fmim.to_units("Mid"))
        self.assertEqual(lib.track_number(mid), 1)  # no track number: position in the album

    def test_duplicates_and_reload(self):
        lib = mindex.Library()
        first = song("One", "Album", "Ann", "Pop", 1, 5000)
        index = lib.add_song(first)
        self.assertIsNone(lib.add_song(song("ONE", "album", "ann", "Rock", 1, 9000)))
        self.assertIsNone(lib.plan_song(first))
        reloaded = mindex.Library(lib.to_bytes())
        self.assertEqual(reloaded.to_bytes(), lib.to_bytes())
        self.assertEqual(reloaded.find_song(first), index)
        added = reloaded.add_song(song("Two", "Album", "Ann", "Pop", 2, 5000))
        self.assertEqual(added, len(reloaded.records) - 1)
        self.assertEqual(reloaded.check(), [])

    def test_same_title_different_track_numbers(self):
        lib = mindex.Library()
        skits = [song("Skit", "Album", "Ann", "Pop", n, 5000) for n in (3, 7, 11)]
        self.assertEqual([lib.add_song(s) is not None for s in skits], [True, True, True])
        self.assertIsNone(lib.add_song(song("Skit", "Album", "Ann", "Pop", 7, 5000)))
        long_a = song("x" * 39 + " part one", "Album", "Ann", "Pop", 1, 5000)
        long_b = song("x" * 39 + " part two", "Album", "Ann", "Pop", 2, 5000)
        self.assertIsNotNone(lib.add_song(long_a))
        self.assertIsNotNone(lib.add_song(long_b))
        high = lib.add_song(song("Bonus", "Album", "Ann", "Pop", 70, 5000))
        self.assertEqual(lib.track_number(high), 63)  # six bits: capped, not wrapped
        self.assertEqual(lib.check(), [])

    def test_plan_matches_add(self):
        lib = mindex.Library()
        for info in random_songs(random.Random(7), 120):
            planned = lib.plan_song(info)
            self.assertEqual(lib.add_song(info), planned)
        self.assertEqual(lib.check(), [])

    def test_rejects_broken_libraries(self):
        lib = mindex.Library()
        lib.add_song(song("One", "Album", "Ann", "Pop", 1, 5000))
        data = bytearray(lib.to_bytes())
        with self.assertRaises(mindex.MindexError):
            mindex.Library(b"x" * 600)
        broken = bytearray(data)
        struct.pack_into(">I", broken, 2 * 600 + 0x14, 999)  # track list points nowhere
        with self.assertRaises(mindex.MindexError):
            mindex.Library(bytes(broken))
        broken_lib = mindex.Library(bytes(broken), validate=False)
        self.assertTrue(broken_lib.check())
        with self.assertRaises(mindex.MindexError):
            broken_lib.add_song(song("Two", "Album", "Ann", "Pop", 2, 5000))
        truncated = data[:len(data) - 600]  # the last record (the track) cut off
        with self.assertRaises(mindex.MindexError):
            mindex.Library(truncated)

    def test_record_limit(self):
        lib = mindex.Library()
        lib.records.extend(bytearray(600) for _ in range(mindex.MAX_RECORDS - len(lib.records)))
        with self.assertRaises(mindex.MindexError):
            lib.add_song(song("One", "Album", "Ann", "Pop", 1, 5000))


def random_songs(rng, count):
    """Awkward but valid song headers: case clashes, long and non-ASCII names, gaps."""
    words = ["love", "Love", "LOVE", "night", "Zed", "alpha", "Ärger", "café", "日本",
             "\U0001F3B5beat", "a" * 45, "", "the end", "Track"]
    artists = ["Ann", "ann", "Bob Band", "Cy & Dee", "Ünïcödé", "", "x" * 50]
    albums = ["First", "first", "Hits", "", "y" * 60, "Live\U0001F3A4"]
    genres = ["Rock", "rock", "Pop", "", "Hip-Hop"]
    songs = []
    for _ in range(count):
        title = " ".join(rng.choice(words) for _ in range(rng.randint(0, 3)))
        artist = rng.choice(artists)
        album_artist = rng.choice([None, None, None, "Various", artist.upper()])
        songs.append(song(title, rng.choice(albums), artist, rng.choice(genres),
                          rng.choice([0, 1, 2, 3, 7, 12, 63, 64, 99]),
                          rng.choice([0, 999, 61500, 4000000, 9000000]), album_artist))
    return songs


def find_compiler():
    for name in ("g++", "clang++", "c++"):
        path = shutil.which(name)
        if path:
            return path
    return None


@unittest.skipUnless(find_compiler(), "no C++ compiler")
class PluginParityTests(unittest.TestCase):
    """MusicLibrary.cpp (used on the console) must write exactly what mindex.py writes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="x360music-test-")
        cls.exe = os.path.join(cls.tmp, "MusicLibraryTest" + (".exe" if os.name == "nt" else ""))
        subprocess.run([find_compiler(), "-std=c++98", "-Wall", "-Wextra", "-O1", "-o", cls.exe,
                        os.path.join(PLUGIN, "tests", "MusicLibraryTest.cpp"),
                        os.path.join(PLUGIN, "MusicLibrary.cpp")], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def write_songs(self, folder, songs):
        os.makedirs(folder, exist_ok=True)
        paths = []
        for i, info in enumerate(songs):
            path = os.path.join(folder, "%08X.fmim" % (i * 2654435761 % 2 ** 32))
            with open(path, "wb") as f:
                f.write(fmim.build_header(info.title, info.album, info.artist, info.album_artist,
                                          info.genre, info.length_ms, info.track_number)
                        + fmim.ASF_HEADER_GUID)
            paths.append(path)
        return paths

    def python_run(self, lib, paths):
        lines = []
        entries = sorted((mindex.import_order(fmim.read_header(p), os.path.basename(p)), p)
                         for p in paths)
        for _, path in entries:
            info = fmim.read_header(path)
            planned = lib.plan_song(info)
            index = lib.add_song(info)
            self.assertEqual(index, planned)
            lines.append("%s %s" % (os.path.basename(path),
                                    "duplicate" if index is None else index))
        return lines

    def cpp_run(self, out, library, paths):
        proc = subprocess.run([self.exe, out, library] + paths, stdout=subprocess.PIPE,
                              universal_newlines=True)
        return proc.returncode, proc.stdout.splitlines()

    def test_same_bytes(self):
        rng = random.Random(1234)
        for round_no in range(4):
            folder = os.path.join(self.tmp, "round%d" % round_no)
            first = self.write_songs(os.path.join(folder, "a"), random_songs(rng, 80))
            second = self.write_songs(os.path.join(folder, "b"), random_songs(rng, 60))

            lib = mindex.Library()
            expected = self.python_run(lib, first)
            out = os.path.join(folder, "first.xmi")
            code, lines = self.cpp_run(out, "new", first)
            self.assertEqual(code, 0, lines)
            self.assertEqual(lines, expected)
            with open(out, "rb") as f:
                self.assertEqual(f.read(), lib.to_bytes())
            self.assertEqual(lib.check(), [])

            # Adding to an existing library, as on a console that has ripped CDs.
            expected = self.python_run(lib, second)
            out2 = os.path.join(folder, "second.xmi")
            code, lines = self.cpp_run(out2, out, second)
            self.assertEqual(code, 0, lines)
            self.assertEqual(lines, expected)
            with open(out2, "rb") as f:
                self.assertEqual(f.read(), lib.to_bytes())
            self.assertEqual(lib.check(), [])

    def test_reference_order(self):
        folder = os.path.join(self.tmp, "reference")
        paths = self.write_songs(folder, [song(*s) for s in REFERENCE_SONGS])
        lib = mindex.Library()
        self.python_run(lib, paths)
        out = os.path.join(folder, "out.xmi")
        self.assertEqual(self.cpp_run(out, "new", paths)[0], 0)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), lib.to_bytes())

    def test_broken_library_is_refused(self):
        folder = os.path.join(self.tmp, "broken")
        paths = self.write_songs(folder, random_songs(random.Random(5), 5))
        lib = mindex.Library()
        lib.add_song(song("One", "Album", "Ann", "Pop", 1, 5000))
        data = bytearray(lib.to_bytes())
        struct.pack_into(">I", data, 2 * 600 + 0x14, 999)
        path = os.path.join(folder, "broken.xmi")
        with open(path, "wb") as f:
            f.write(bytes(data))
        code, lines = self.cpp_run(os.path.join(folder, "out.xmi"), path, paths)
        self.assertEqual(code, 1)
        self.assertIn("isn't in the expected format", lines[-1])


class TagTests(unittest.TestCase):
    def test_fallbacks(self):
        s = convert.song_from_tags(os.path.join("music", "Band", "Record", "07 - Song Name.mp3"),
                                   {}, 1000, root="music")
        self.assertEqual((s.title, s.track_number, s.album, s.artist, s.genre),
                         ("Song Name", 7, "Record", "Unknown Artist", "Unknown Genre"))
        s = convert.song_from_tags(os.path.join("music", "x.mp3"), {}, 1000, root="music")
        self.assertEqual(s.album, "Unknown Album")

    def test_tags(self):
        tags = {"title": " A  Song ", "artist": "Ann;Bob", "album_artist": "Various",
                "album": "Hits", "track": "3/12", "genre": "Rock;Pop"}
        s = convert.song_from_tags("x.mp3", tags, 1000)
        self.assertEqual((s.title, s.artist, s.album_artist, s.track_number, s.genre),
                         ("A Song", "Ann, Bob", "Various", 3, "Rock"))
        self.assertEqual(s.file_name(), convert.song_from_tags("y.mp3", tags, 5).file_name())


def _have_ffmpeg():
    try:
        convert.find_tool("ffmpeg")
        convert.find_tool("ffprobe")
        return True
    except convert.ConvertError:
        return False


@unittest.skipUnless(_have_ffmpeg(), "ffmpeg not installed")
class ConvertTests(unittest.TestCase):
    def test_convert_and_build(self):
        tmp = tempfile.mkdtemp(prefix="x360music-test-")
        try:
            music = os.path.join(tmp, "music", "Band", "Record")
            os.makedirs(music)
            ffmpeg = convert.find_tool("ffmpeg")
            for number, freq in ((1, 440), (2, 550)):
                subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                                "sine=frequency=%d:duration=2" % freq, "-ac", "2",
                                "-metadata", "title=Song %d" % number, "-metadata",
                                "artist=Band", "-metadata", "album=Record", "-metadata",
                                "track=%d" % number, "-c:a", "libmp3lame",
                                os.path.join(music, "%02d.mp3" % number)], check=True)
            usb = os.path.join(tmp, "usb")
            os.makedirs(usb)
            summary = tasks.convert_all([os.path.join(tmp, "music")], usb, log=lambda m: None)
            self.assertEqual((summary.converted, summary.failed), (2, []))
            songs = tasks.song_files([summary.out_dir])
            self.assertEqual(len(songs), 2)
            with open(songs[0], "rb") as f:
                data = f.read()
            info = fmim.SongInfo(data)
            self.assertEqual((info.artist, info.album), ("Band", "Record"))
            self.assertEqual(info.length_ms, 2000)
            audio = asf.describe(data[fmim.HEADER_SIZE:])
            self.assertEqual((audio["format_tag"], audio["channels"], audio["sample_rate"],
                              audio["bitrate"]), ("0x0161", 2, 44100, 192000))
            self.assertNotIn("Content Encryption", audio["header_objects"])

            again = tasks.convert_all([os.path.join(tmp, "music")], usb, log=lambda m: None)
            self.assertEqual((again.converted, again.existing), (0, 2))

            added, skipped = tasks.build_library([summary.out_dir], os.path.join(tmp, "pc"),
                                                 log=lambda m: None)
            self.assertEqual((added, skipped), (2, 0))
            media = os.path.join(tmp, "pc", "mindex", "media", "0000")
            self.assertEqual(sorted(os.listdir(media)), ["000A", "000B"])
            with open(os.path.join(tmp, "pc", "mindex", "mindex.xmi"), "rb") as f:
                self.assertEqual(mindex.Library(f.read()).check(), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
