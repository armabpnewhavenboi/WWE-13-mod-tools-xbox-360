import os
import random
import unittest

from x360resign.keyvault import load_keyvault
from x360resign.stfs import BLOCK_SIZE, StfsError, StfsPackage

from tests.builder import build_package, make_keyvault, simulate_layout

RUN_SLOW = os.environ.get("X360RESIGN_SLOW", "1") != "0"

_KV = None


def test_kv():
    global _KV
    if _KV is None:
        _KV = load_keyvault(make_keyvault(seed=11))
    return _KV


def sample_files(rng, sizes):
    return [("file%d.dat" % i, bytes(rng.getrandbits(8) for _ in range(size)))
            for i, size in enumerate(sizes)]


class LayoutTests(unittest.TestCase):
    def _package_with_total(self, total, read_only):
        # Minimal package object whose addressing math we can query.
        data = build_package([("a", b"x")], read_only=read_only)
        pkg = StfsPackage(data)
        pkg.total_blocks = total
        pkg.top_level = 0 if total <= 0xAA else (1 if total <= 0x70E4 else 2)
        return pkg

    def test_closed_form_matches_sequential_walk(self):
        for read_only in (False, True):
            for total in (1, 0xAA, 0xAB, 0x200, 0x70E4, 0x70E5, 0x70E4 + 0x300, 3 * 0x70E4 + 5):
                with self.subTest(read_only=read_only, total=total):
                    data_phys, tables, _ = simulate_layout(total, read_only)
                    pkg = self._package_with_total(total, read_only)
                    for b in sorted({0, 1, 0xA9, 0xAA, 0xAB, total // 2, total - 1}):
                        if b < total:
                            self.assertEqual(pkg.backing_data_block(b), data_phys[b], "block %d" % b)
                    for (level, index), phys in tables.items():
                        self.assertEqual(
                            pkg.backing_hash_block(index * (0xAA, 0x70E4, 0x4AF768)[level], level),
                            phys, "table L%d#%d" % (level, index))


class PackageTests(unittest.TestCase):
    def check_roundtrip(self, files, **kw):
        data = build_package(files, kv=test_kv(), **kw)
        pkg = StfsPackage(data)
        report = pkg.verify()
        self.assertTrue(report.ok, report.summary())
        listing = {e.path: e for e in pkg.files()}
        for path, content in files:
            self.assertIn(path, listing)
            self.assertEqual(pkg.read_file(listing[path]), content)
        self.assertEqual(pkg.rehash(), 0, "rehash of a valid package must change nothing")
        self.assertEqual(pkg.to_bytes(), data)
        return data, pkg

    def test_level0_both_formats(self):
        rng = random.Random(1)
        files = sample_files(rng, [10, 5000, 0x1000, 0x3001])
        for read_only in (False, True):
            with self.subTest(read_only=read_only):
                self.check_roundtrip(files, read_only=read_only, seed=3)

    def test_level1_scattered_chains(self):
        rng = random.Random(2)
        files = sample_files(rng, [0x1000 * 150, 0x1000 * 90 + 17, 123])
        files.append(("folder/sub/inner.bin", b"inner data" * 500))
        for read_only in (False, True):
            for seed in (1, 2, 3):
                with self.subTest(read_only=read_only, seed=seed):
                    self.check_roundtrip(files, read_only=read_only, scatter=True, seed=seed)

    @unittest.skipUnless(RUN_SLOW, "slow test disabled")
    def test_level2(self):
        rng = random.Random(3)
        files = sample_files(rng, [0x2000, 777])
        data = build_package(files, kv=test_kv(), extra_blocks=0x70E4 + 0x150, seed=9)
        pkg = StfsPackage(data)
        self.assertEqual(pkg.top_level, 2)
        self.assertTrue(pkg.verify().ok)
        pkg.data[pkg.data_block_offset(0x70E4 + 0x100) + 5] ^= 0xFF
        report = pkg.verify()
        self.assertEqual(report.bad_blocks, [0x70E4 + 0x100])
        self.assertGreater(pkg.rehash(), 0)
        self.assertTrue(pkg.verify().hashes_ok)
        pkg.sign(test_kv())
        self.assertTrue(pkg.verify().ok)

    def test_detects_and_repairs_damage(self):
        rng = random.Random(4)
        files = sample_files(rng, [0x1000 * 200, 4000])
        data = build_package(files, kv=test_kv(), seed=5)
        pkg = StfsPackage(data)
        target = pkg.files()[0]
        pkg.write_file_bytes(target, 0x1000 * 180 + 3, b"MODDED")
        report = pkg.verify()
        self.assertFalse(report.ok)
        self.assertEqual(len(report.bad_blocks), 1)
        changed = pkg.rehash()
        self.assertGreaterEqual(changed, 3)  # block hash, L0 table hash, root
        report = pkg.verify()
        self.assertTrue(report.hashes_ok)
        self.assertIs(report.signature_ok, False)  # header changed, old signature no longer fits
        pkg.sign(test_kv())
        self.assertTrue(pkg.verify().ok)
        self.assertIn(b"MODDED", pkg.read_file(pkg.files()[0]))

    def test_inactive_hash_table_copies_are_left_alone(self):
        rng = random.Random(5)
        files = sample_files(rng, [0x1000 * 300])
        data = build_package(files, kv=test_kv(), seed=6)
        pkg = StfsPackage(data)
        stale_before = [i for i in range(0, len(data), BLOCK_SIZE) if data[i:i + 16] == b"\xEE" * 16]
        pkg.write_file_bytes(pkg.files()[0], 0, b"\x01\x02")
        pkg.rehash()
        out = pkg.to_bytes()
        stale_after = [i for i in range(0, len(out), BLOCK_SIZE) if out[i:i + 16] == b"\xEE" * 16]
        self.assertEqual(stale_before, stale_after)
        self.assertTrue(stale_before)

    def test_header_fields_and_ids(self):
        data = build_package([("x", b"1")], seed=7, display_name="Universe Mode")
        pkg = StfsPackage(data)
        self.assertEqual(pkg.title_id, 0x545107FC)
        self.assertEqual(pkg.display_name, "Universe Mode")
        self.assertEqual(pkg.title_name, "WWE '13")
        self.assertEqual(pkg.content_type_name, "Saved Game")
        self.assertTrue(pkg.thumbnail().startswith(b"\x89PNG"))
        pkg.profile_id = bytes.fromhex("E000123456789ABC")
        self.assertEqual(pkg.profile_id.hex().upper(), "E000123456789ABC")
        with self.assertRaises(ValueError):
            pkg.console_id = b"\x01\x02"
        self.assertIn("E000123456789ABC", pkg.describe())

    def test_rejects_garbage(self):
        with self.assertRaises(StfsError):
            StfsPackage(b"\0" * 0x2000)
        with self.assertRaises(StfsError):
            StfsPackage(b"CON " + b"\0" * 10)

    def test_live_package_cannot_be_signed(self):
        data = build_package([("dlc.bin", b"dlc")], magic=b"LIVE", seed=8)
        pkg = StfsPackage(data)
        self.assertIsNone(pkg.verify_signature())
        with self.assertRaises(StfsError):
            pkg.sign(test_kv())

    def test_extract(self):
        import tempfile
        files = [("a.bin", b"A" * 5000), ("dir/b.bin", b"B" * 10)]
        pkg = StfsPackage(build_package(files, seed=9))
        with tempfile.TemporaryDirectory() as tmp:
            written = pkg.extract(tmp)
            self.assertEqual(len(written), 2)
            with open(os.path.join(tmp, "dir", "b.bin"), "rb") as f:
                self.assertEqual(f.read(), b"B" * 10)

    def test_truncated_package_is_padded_on_rehash(self):
        data = build_package([("a", b"z" * 0x3000)], seed=10, kv=test_kv())
        pkg = StfsPackage(data[:-0x800])
        self.assertTrue(pkg.verify().problems)
        pkg.rehash()
        pkg.sign(test_kv())
        self.assertEqual(len(pkg.data), len(data))
        self.assertTrue(pkg.verify().ok)


if __name__ == "__main__":
    unittest.main()
