import contextlib
import io
import os
import tempfile
import unittest
import zipfile

from x360resign import cli
from x360resign.ids import IdError, parse_profile_id, profile_id_warning
from x360resign.resigner import (STATUS_COPIED, STATUS_RESIGNED, STATUS_SKIPPED, Options,
                                 ids_from_package, process_package, run_batch)
from x360resign.stfs import StfsPackage

from tests.builder import build_package

OLD_PROFILE = bytes.fromhex("E0000AAAAAAAAAAA")
NEW_PROFILE = bytes.fromhex("E000012345678901")
NEW_DEVICE = bytes.fromhex("11" * 0x14)
OLD_CONSOLE = bytes.fromhex("FFEEDDCCBB")  # builder default
NEW_CONSOLE = bytes.fromhex("1234567890")
TITLE = "545107FC"


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class Fixture:
    """A fake 'mod pack' folder like the ones people pass around."""

    def __init__(self, root):
        self.root = root
        self.mods = os.path.join(root, "Monday Night War")
        embedded = b"header" + OLD_PROFILE + b"middle" + OLD_PROFILE[::-1] + b"tail"
        packages = {
            "Content/E0000AAAAAAAAAAA/545107FC/00000001/WWE13_CAW": build_package(
                [("CAW.dat", os.urandom(0x1000 * 40 + 5))], seed=1,
                display_name="Created Superstars"),
            "Universe/WWE13_UNIVERSE": build_package(
                [("universe.dat", os.urandom(300000)), ("owner.bin", embedded)],
                seed=2, scatter=True, display_name="Universe"),
            "shared/SHARED_DATA": build_package(
                [("s.dat", b"shared" * 100)], seed=3, profile_id=bytes(8)),
            "dlc/DLC_PACK": build_package(
                [("dlc.bin", b"dlc" * 1000)], magic=b"LIVE", seed=4, profile_id=bytes(8),
                content_type=2),
            "profile/E0000AAAAAAAAAAA": build_package(
                [("Account", b"acct")], seed=5, content_type=0x10000,
                title_id=0xFFFE07D1),
        }
        for rel, data in packages.items():
            write(os.path.join(self.mods, rel), data)
        self.zip_save = build_package([("arena.dat", os.urandom(9000))], seed=6,
                                      display_name="Created Arenas")
        with zipfile.ZipFile(os.path.join(self.mods, "arenas.zip"), "w") as zf:
            zf.writestr("pack/WWE13_ARENAS", self.zip_save)
            zf.writestr("pack/readme.txt", "hello")
        write(os.path.join(self.mods, "readme.txt"), b"Monday Night War readme")
        write(os.path.join(self.mods, "extra.rar"), b"Rar!\x1a\x07\x00")
        self.packages = packages

    def out(self, *parts):
        return os.path.join(self.root, "out", *parts)


class ResignerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_batch(self, **kw):
        options = Options(profile_id=NEW_PROFILE, **kw)
        return run_batch([self.fx.mods], self.fx.out(), options, log=lambda *_: None)

    def test_full_batch(self):
        summary = self.run_batch(device_id=NEW_DEVICE)
        by_name = {r.filename: r for r in summary.results}
        self.assertEqual(by_name["WWE13_CAW"].status, STATUS_RESIGNED)
        self.assertEqual(by_name["WWE13_UNIVERSE"].status, STATUS_RESIGNED)
        self.assertEqual(by_name["WWE13_ARENAS"].status, STATUS_RESIGNED)
        self.assertEqual(by_name["SHARED_DATA"].status, STATUS_RESIGNED)
        self.assertEqual(by_name["DLC_PACK"].status, STATUS_COPIED)
        self.assertEqual(by_name["E0000AAAAAAAAAAA"].status, STATUS_SKIPPED)
        self.assertEqual(summary.failed, 0)
        self.assertTrue(any("extract it first" in s for s in summary.skipped_inputs))

        for name in ("WWE13_CAW", "WWE13_UNIVERSE", "WWE13_ARENAS"):
            path = self.fx.out("Content", NEW_PROFILE.hex().upper(), TITLE, "00000001", name)
            self.assertTrue(os.path.isfile(path), path)
            pkg = StfsPackage.from_file(path)
            self.assertTrue(pkg.verify().ok)
            self.assertEqual(pkg.profile_id, NEW_PROFILE)
            self.assertEqual(pkg.console_id, OLD_CONSOLE)  # unchanged unless asked
            self.assertEqual(pkg.device_id, NEW_DEVICE)

        # the signature block is copied through untouched
        caw = self.fx.out("Content", NEW_PROFILE.hex().upper(), TITLE, "00000001", "WWE13_CAW")
        original_caw = self.fx.packages["Content/E0000AAAAAAAAAAA/545107FC/00000001/WWE13_CAW"]
        self.assertEqual(StfsPackage.from_file(caw).data[:0x22C], original_caw[:0x22C])

        shared = self.fx.out("Content", "0000000000000000", TITLE, "00000001", "SHARED_DATA")
        self.assertEqual(StfsPackage.from_file(shared).profile_id, bytes(8))
        dlc = self.fx.out("Content", "0000000000000000", TITLE, "00000002", "DLC_PACK")
        with open(dlc, "rb") as f:
            self.assertEqual(f.read(), self.fx.packages["dlc/DLC_PACK"])

        # original files untouched
        original = os.path.join(self.fx.mods, "Universe", "WWE13_UNIVERSE")
        with open(original, "rb") as f:
            self.assertEqual(f.read(), self.fx.packages["Universe/WWE13_UNIVERSE"])

        self.assertEqual(summary.report_path, self.fx.out("resign_report.txt"))
        with open(summary.report_path, encoding="utf-8") as f:
            report = f.read()
        self.assertIn("RESIGNED", report)
        self.assertIn("old profile ID found inside owner.bin", report)
        self.assertIn("DLC packages in this pack (1)", report)
        self.assertIn(os.path.join("Content", "0000000000000000", TITLE, "00000002", "DLC_PACK"),
                      report)
        self.assertIn("missing or damaged downloadable content", report)
        self.assertEqual([r.filename for r in summary.dlc], ["DLC_PACK"])

    def test_embedded_ids_reported_not_patched_by_default(self):
        summary = self.run_batch()
        uni = [r for r in summary.results if r.filename == "WWE13_UNIVERSE"][0]
        orders = sorted(order for label, path, pos, order in uni.embedded_hits
                        if label == "profile ID")
        self.assertEqual(orders, ["big-endian", "little-endian"])
        pkg = StfsPackage.from_file(uni.output)
        owner = [e for e in pkg.files() if e.name == "owner.bin"][0]
        self.assertIn(OLD_PROFILE, pkg.read_file(owner))

    def test_embedded_ids_patched_on_request(self):
        summary = self.run_batch(patch_embedded=True)
        uni = [r for r in summary.results if r.filename == "WWE13_UNIVERSE"][0]
        pkg = StfsPackage.from_file(uni.output)
        self.assertTrue(pkg.verify().ok)
        owner = [e for e in pkg.files() if e.name == "owner.bin"][0]
        content = pkg.read_file(owner)
        self.assertEqual(content, b"header" + NEW_PROFILE + b"middle" + NEW_PROFILE[::-1] + b"tail")

    def test_console_id_override(self):
        data = self.fx.packages["Universe/WWE13_UNIVERSE"]
        out, result = process_package(data, "x", Options(profile_id=NEW_PROFILE,
                                                         console_id=NEW_CONSOLE))
        self.assertEqual(result.status, STATUS_RESIGNED)
        pkg = StfsPackage(out)
        self.assertTrue(pkg.verify().ok)
        self.assertEqual(pkg.profile_id, NEW_PROFILE)
        self.assertEqual(pkg.console_id, NEW_CONSOLE)

    def test_content_id_named_file_is_renamed(self):
        data = self.fx.packages["Universe/WWE13_UNIVERSE"]
        content_id = StfsPackage(data).compute_header_hash().hex().upper()
        out, result = process_package(data, content_id, Options(profile_id=NEW_PROFILE))
        self.assertEqual(result.status, STATUS_RESIGNED)
        self.assertNotEqual(result.output_name, content_id)
        self.assertEqual(result.output_name, StfsPackage(out).compute_header_hash().hex().upper())
        _, plain = process_package(data, "WWE13_UNIVERSE", Options(profile_id=NEW_PROFILE))
        self.assertEqual(plain.output_name, "WWE13_UNIVERSE")

    def test_output_inside_input_is_not_reprocessed(self):
        out_dir = os.path.join(self.fx.mods, "resigned")
        options = Options(profile_id=NEW_PROFILE)
        first = run_batch([self.fx.mods], out_dir, options, log=lambda *_: None)
        second = run_batch([self.fx.mods], out_dir, options, log=lambda *_: None)
        self.assertEqual(len(first.results), len(second.results))

    def test_layout_flat(self):
        summary = self.run_batch(layout="flat")
        self.assertTrue(os.path.isfile(self.fx.out("WWE13_CAW")))
        with open(summary.report_path, encoding="utf-8") as f:
            report = f.read()
        self.assertIn("they are in the output folder", report)
        self.assertNotIn("Content\\0000000000000000 folder", report)

    def test_dlc_note_tells_people_to_copy_the_dlc(self):
        summary = self.run_batch()
        with open(summary.report_path, encoding="utf-8") as f:
            report = f.read()
        self.assertIn("Copy these to the USB stick too (they are in the Content\\0000000000000000 "
                      "folder)", report)
        self.assertNotIn("bought on your account", report)

    def test_ids_from_own_save_treats_zeros_as_unknown(self):
        own = os.path.join(self.tmp.name, "own_zero_console")
        data = bytearray(build_package([("m", b"m")], profile_id=NEW_PROFILE,
                                       console_id=bytes(5), device_id=bytes(0x14), seed=60))
        data[0x006:0x00B] = NEW_CONSOLE  # console that wrote the package
        write(own, bytes(data))
        self.assertEqual(ids_from_package(own), (NEW_PROFILE, NEW_CONSOLE, None))
        shared = os.path.join(self.tmp.name, "shared_save")
        write(shared, build_package([("m", b"m")], profile_id=bytes(8), seed=61))
        self.assertIsNone(ids_from_package(shared)[0])


class IdTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_profile_id("e000-0123 4567 8901"), NEW_PROFILE)
        self.assertEqual(parse_profile_id("0xE000012345678901"), NEW_PROFILE)
        for bad in ("", "E000", "E00001234567890G", "E0000123456789012"):
            with self.assertRaises(IdError):
                parse_profile_id(bad)

    def test_warnings(self):
        self.assertEqual(profile_id_warning(NEW_PROFILE), "")
        self.assertEqual(profile_id_warning(bytes.fromhex("0009000001234567")), "")
        self.assertTrue(profile_id_warning(bytes(8)))
        self.assertTrue(profile_id_warning(bytes.fromhex("1234567812345678")))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)  # keep any x360resign.ini inside the temp dir

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def call(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(list(argv))
        return code, stdout.getvalue() + stderr.getvalue()

    def test_resign_verify_info_extract(self):
        code, text = self.call("resign", self.fx.mods, "-o", self.fx.out(),
                               "--profile-id", NEW_PROFILE.hex(), "--save-config")
        self.assertEqual(code, 0, text)
        self.assertIn("4 resigned", text)
        code, text = self.call("verify", self.fx.out())
        self.assertEqual(code, 0, text)
        target = self.fx.out("Content", NEW_PROFILE.hex().upper(), TITLE, "00000001", "WWE13_CAW")
        code, text = self.call("info", target, "--files-list")
        self.assertEqual(code, 0, text)
        self.assertIn("CAW.dat", text)
        code, text = self.call("extract", target, "-o", self.fx.out("x"))
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.isfile(self.fx.out("x", "CAW.dat")))
        self.assertTrue(os.path.isfile(self.fx.out("x", "_thumbnail.png")))
        # settings were remembered: a second run needs no IDs on the command line
        code, text = self.call("resign", self.fx.mods, "-o", self.fx.out("again"))
        self.assertEqual(code, 0, text)

    def test_verify_flags_original_mod_files_as_ok(self):
        code, text = self.call("verify", self.fx.mods)
        self.assertEqual(code, 0, text)

    def test_ids(self):
        save = os.path.join(self.fx.mods, "Universe", "WWE13_UNIVERSE")
        code, text = self.call("ids", save)
        self.assertEqual(code, 0, text)
        self.assertIn(OLD_PROFILE.hex().upper(), text)

    def test_ids_from_own_save(self):
        mine = build_package([("m", b"m")], profile_id=NEW_PROFILE, console_id=NEW_CONSOLE,
                             device_id=NEW_DEVICE, seed=50)
        own = os.path.join(self.tmp.name, "my_own_save")
        write(own, mine)
        code, text = self.call("resign", self.fx.mods, "-o", self.fx.out(), "--ids-from", own)
        self.assertEqual(code, 0, text)
        out = self.fx.out("Content", NEW_PROFILE.hex().upper(), TITLE, "00000001", "WWE13_CAW")
        pkg = StfsPackage.from_file(out)
        self.assertEqual(pkg.console_id, NEW_CONSOLE)
        self.assertEqual(pkg.device_id, NEW_DEVICE)

    def test_missing_profile_id_is_an_error(self):
        code, text = self.call("resign", self.fx.mods, "-o", self.fx.out())
        self.assertEqual(code, 2)
        self.assertIn("No profile ID", text)

    def test_nothing_found_is_not_success(self):
        code, text = self.call("resign", os.path.join(self.tmp.name, "typo"), "-o", self.fx.out(),
                               "--profile-id", NEW_PROFILE.hex())
        self.assertEqual(code, 1, text)
        self.assertIn("No Xbox 360 packages", text)

    def test_old_no_sign_flag_still_accepted(self):
        code, text = self.call("resign", self.fx.mods, "-o", self.fx.out(),
                               "--profile-id", NEW_PROFILE.hex(), "--no-sign")
        self.assertEqual(code, 0, text)

    def test_bad_profile_id(self):
        code, text = self.call("resign", self.fx.mods, "-o", self.fx.out(),
                               "--profile-id", "XYZ")
        self.assertEqual(code, 2)
        self.assertIn("Profile ID", text)


if __name__ == "__main__":
    unittest.main()
