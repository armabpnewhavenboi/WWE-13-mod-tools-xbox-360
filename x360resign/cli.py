"""Command line interface.  Run with no arguments to open the GUI."""

import argparse
import os
import sys

from . import __version__
from .config import default_config_path, load_config, save_config
from .ids import IdError, fmt, parse_console_id, parse_device_id, parse_profile_id
from .keyvault import KeyVaultError, load_keyvault_file, parse_cpu_key
from .resigner import Options, collect_inputs, ids_from_package, run_batch
from .stfs import StfsError, StfsPackage, decode_ms_time


class UsageError(Exception):
    pass


def _ids_argument_group(parser):
    group = parser.add_argument_group("who the saves should belong to")
    group.add_argument("--profile-id", metavar="HEX16",
                       help="your profile ID, 16 hex characters (e.g. E00001234ABCD567)")
    group.add_argument("--console-id", metavar="HEX10",
                       help="console ID, 10 hex characters (default: taken from the KV)")
    group.add_argument("--device-id", metavar="HEX40",
                       help="device ID of your USB stick / hard drive, 40 hex characters (optional)")
    group.add_argument("--ids-from", metavar="SAVE",
                       help="read profile/console/device IDs from one of YOUR OWN saves")
    keys = parser.add_argument_group("signing")
    keys.add_argument("--kv", metavar="KV.bin", help="your console's KeyVault (needed to sign)")
    keys.add_argument("--cpu-key", metavar="HEX32",
                      help="CPU key, only needed if the KV is still encrypted (raw NAND KV)")
    keys.add_argument("--no-sign", action="store_true",
                      help="no KV: fix IDs and hashes only (retail consoles will still reject it)")
    parser.add_argument("--config", metavar="INI",
                        help="settings file to read (default: %s)" % os.path.basename(default_config_path()))
    parser.add_argument("--save-config", action="store_true",
                        help="remember the IDs / KV path given on this command line")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="x360resign",
        description="Batch rehash + resign Xbox 360 saves (CON packages) to your own profile "
                    "and console. Run with no arguments to open the window version.")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    resign = sub.add_parser("resign", help="rehash + resign every save found in folders / zips")
    resign.add_argument("inputs", nargs="+", help="files, folders or .zip files with the mod saves")
    resign.add_argument("-o", "--output", required=True,
                        help="output folder (originals are never modified)")
    _ids_argument_group(resign)
    resign.add_argument("--patch-embedded-ids", action="store_true",
                        help="also replace the old IDs if they are stored inside the save data")
    resign.add_argument("--include-profiles", action="store_true",
                        help="also process gamer profile packages (skipped by default)")
    resign.add_argument("--layout", choices=("usb", "flat"), default="usb",
                        help="usb = Content\\<profile>\\<title>\\<type>\\file ready to copy to a "
                             "USB stick (default); flat = all files in one folder")

    info = sub.add_parser("info", help="show header details of packages")
    info.add_argument("files", nargs="+")
    info.add_argument("--files-list", action="store_true", help="also list the files inside")

    verify = sub.add_parser("verify", help="check hashes and signatures of packages")
    verify.add_argument("inputs", nargs="+")

    extract = sub.add_parser("extract", help="extract the files stored inside a package")
    extract.add_argument("file")
    extract.add_argument("-o", "--output", required=True)

    kvinfo = sub.add_parser("kvinfo", help="check a KeyVault file and show its console ID")
    kvinfo.add_argument("kv")
    kvinfo.add_argument("--cpu-key", metavar="HEX32")

    ids = sub.add_parser("ids", help="print the profile / console / device IDs stored in a save")
    ids.add_argument("files", nargs="+")

    sub.add_parser("gui", help="open the window version")
    return parser


# ------------------------------------------------------------------- helpers

def resolve_settings(args):
    """Merge config file, --ids-from and explicit flags.  Returns (Options, settings dict)."""
    config = load_config(args.config) if args.config or os.path.isfile(default_config_path()) else {}
    settings = dict(config)

    def given(name):
        return getattr(args, name, None)

    for name in ("profile_id", "console_id", "device_id", "kv", "cpu_key"):
        if given(name):
            settings[name] = given(name)

    explicit_console = bool(given("console_id"))
    if args.ids_from:
        p, c, d = ids_from_package(args.ids_from)
        if not given("profile_id"):
            settings["profile_id"] = p.hex()
        if not explicit_console:
            settings["console_id_from_save"] = c.hex()
        if not given("device_id"):
            settings["device_id"] = d.hex()

    kv = None
    if settings.get("kv") and not args.no_sign:
        cpu = parse_cpu_key(settings["cpu_key"]) if settings.get("cpu_key") else None
        kv = load_keyvault_file(settings["kv"], cpu_key=cpu)
    elif not args.no_sign:
        raise UsageError("No KeyVault given. Use --kv KV.bin to sign the saves, or --no-sign to "
                         "only change the IDs and rehash (they will NOT load on a retail console "
                         "until they are signed).")

    profile = parse_profile_id(settings["profile_id"]) if settings.get("profile_id") else None
    if profile is None:
        raise UsageError("No profile ID given. Use --profile-id or --ids-from.")
    console = None
    if settings.get("console_id"):
        console = parse_console_id(settings["console_id"])
    elif kv is None and settings.get("console_id_from_save"):
        console = parse_console_id(settings["console_id_from_save"])
    device = parse_device_id(settings["device_id"]) if settings.get("device_id") else None

    options = Options(profile_id=profile, console_id=console, device_id=device, keyvault=kv,
                      patch_embedded=getattr(args, "patch_embedded_ids", False),
                      include_profiles=getattr(args, "include_profiles", False),
                      layout=getattr(args, "layout", "usb"))
    return options, settings


def cmd_resign(args):
    options, settings = resolve_settings(args)
    if args.save_config:
        path = save_config({k: settings.get(k, "") for k in
                            ("profile_id", "console_id", "device_id", "kv", "cpu_key")}, args.config)
        print("Settings saved to %s" % path)
    print("Profile ID : %s" % fmt(options.profile_id))
    if options.keyvault:
        print("Signing KV : console %s (%s)" % (fmt(options.keyvault.console_id),
                                                options.keyvault.console_type))
        if not options.keyvault.cert_matches_key:
            print("WARNING: this KV's certificate does not match its private key")
    else:
        print("Signing KV : none (--no-sign)")
    print("Console ID : %s" % fmt(options.console_id or
                                  (options.keyvault.console_id if options.keyvault else None)))
    print("Device ID  : %s" % (fmt(options.device_id) if options.device_id else "(unchanged)"))
    print()
    summary = run_batch(args.inputs, args.output, options)
    print()
    print("Done: %d resigned, %d rehashed (unsigned), %d copied, %d skipped, %d failed"
          % (summary.count("RESIGNED"), summary.count("REHASHED, NOT SIGNED"),
             summary.count("COPIED UNCHANGED"), summary.count("SKIPPED"), summary.failed))
    if summary.report_path:
        print("Report     : %s" % summary.report_path)
        print("Output     : %s" % os.path.abspath(args.output))
    return 1 if summary.failed else 0


def cmd_info(args):
    status = 0
    for path in args.files:
        print("== %s" % path)
        try:
            pkg = StfsPackage.from_file(path)
            print(pkg.describe())
            print("Check           : %s" % pkg.verify().summary())
            if args.files_list and pkg.is_stfs:
                for entry in pkg.files():
                    kind = "<DIR>" if entry.is_directory else "%10d" % entry.size
                    print("   %s  %s  %s" % (kind, decode_ms_time(entry.modified) or " " * 19,
                                             entry.path))
        except (StfsError, OSError) as exc:
            print("ERROR: %s" % exc)
            status = 1
        print()
    return status


def cmd_verify(args):
    items, skipped = collect_inputs(args.inputs)
    for message in skipped:
        print("note: " + message)
    bad = 0
    for item in items:
        try:
            pkg = StfsPackage(item.loader(), name=item.filename)
            report = pkg.verify()
            state = "OK " if report.ok else "BAD"
            if not report.ok:
                bad += 1
            print("[%s] %s - %s (%s)" % (state, item.source, report.summary(),
                                         fmt(pkg.profile_id)))
            for problem in report.problems:
                print("        - " + problem)
        except (StfsError, OSError) as exc:
            bad += 1
            print("[ERR] %s - %s" % (item.source, exc))
    print("\n%d package(s) checked, %d with problems" % (len(items), bad))
    return 1 if bad else 0


def cmd_extract(args):
    pkg = StfsPackage.from_file(args.file)
    written = pkg.extract(args.output)
    for name, title in (("_thumbnail.png", False), ("_title_thumbnail.png", True)):
        image = pkg.thumbnail(title=title)
        if image:
            with open(os.path.join(args.output, name), "wb") as f:
                f.write(image)
    print("Extracted %d file(s) to %s" % (len(written), args.output))
    return 0


def cmd_kvinfo(args):
    cpu = parse_cpu_key(args.cpu_key) if args.cpu_key else None
    kv = load_keyvault_file(args.kv, cpu_key=cpu)
    print(kv.describe())
    print("KV is usable for signing.")
    return 0


def cmd_ids(args):
    for path in args.files:
        profile, console, device = ids_from_package(path)
        print("== %s" % path)
        print("Profile ID : %s" % fmt(profile))
        print("Console ID : %s" % fmt(console))
        print("Device ID  : %s" % fmt(device))
    return 0


def launch_gui():
    try:
        from .gui import main as gui_main
    except ImportError as exc:  # tkinter missing (some Linux installs)
        print("The window version needs tkinter (%s). Use the commands instead - see --help." % exc)
        return 1
    return gui_main()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return launch_gui()
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"resign": cmd_resign, "info": cmd_info, "verify": cmd_verify,
                "extract": cmd_extract, "kvinfo": cmd_kvinfo, "ids": cmd_ids}
    if args.command == "gui":
        return launch_gui()
    if args.command not in handlers:
        parser.print_help()
        return 1
    try:
        return handlers[args.command](args)
    except (UsageError, IdError, KeyVaultError, StfsError, OSError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
