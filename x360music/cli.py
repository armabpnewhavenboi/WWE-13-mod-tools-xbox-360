"""Command line interface.  Run with no arguments to open the window."""

import argparse
import os
import sys

from . import __version__
from .asf import AsfError
from .convert import BITRATES, ConvertError
from .fmim import FmimError
from .mindex import MindexError
from .tasks import build_library, convert_all, describe_file


def build_parser():
    parser = argparse.ArgumentParser(
        prog="x360music",
        description="Convert music for the Xbox 360 hard-drive music library (used with the "
                    "HddMusic plugin). Run with no arguments to open the window version.")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    conv = sub.add_parser("convert", help="convert music files to song files on a USB stick")
    conv.add_argument("inputs", nargs="+", help="music files or folders (searched recursively)")
    conv.add_argument("-o", "--output", required=True,
                      help="the USB stick (e.g. E:\\); songs go in its HddMusic folder")
    conv.add_argument("--bitrate", type=int, choices=BITRATES, default=192,
                      help="WMA bitrate in kbps (default 192, what the console's CD ripper uses)")
    conv.add_argument("--jobs", type=int, default=None, help="songs to convert at once")
    conv.add_argument("--force", action="store_true", help="convert songs again even if done before")

    build = sub.add_parser("build", help="without the plugin: make a mindex folder on the PC to "
                                         "copy to the Xbox hard drive by FTP")
    build.add_argument("inputs", nargs="+", help="HddMusic folders / .fmim song files")
    build.add_argument("-o", "--output", required=True, help="folder to write 'mindex' into")
    build.add_argument("--library", metavar="MINDEX.XMI",
                       help="the console's own mindex.xmi, to keep the songs already on it")

    inspect = sub.add_parser("inspect", help="show what is in a song file or mindex.xmi")
    inspect.add_argument("files", nargs="+")
    inspect.add_argument("--records", action="store_true", help="list every library record")

    sub.add_parser("gui", help="open the window version")
    return parser


def cmd_convert(args):
    summary = convert_all(args.inputs, args.output, jobs=args.jobs, bitrate=args.bitrate,
                          force=args.force)
    print()
    print("Done: %d converted, %d already there, %d duplicates skipped, %d failed"
          % (summary.converted, summary.existing, summary.duplicates, len(summary.failed)))
    for path, message in summary.failed:
        print("  FAILED %s: %s" % (path, message))
    print("Songs: %s" % os.path.abspath(summary.out_dir))
    print("Next: plug the stick into the Xbox, hold BACK and press X (HddMusic plugin).")
    return 1 if summary.failed else 0


def cmd_build(args):
    added, skipped = build_library(args.inputs, args.output, args.library)
    out_dir = os.path.abspath(os.path.join(args.output, "mindex"))
    print()
    print("Done: %d songs added, %d already in the library" % (added, skipped))
    print("Library: %s" % out_dir)
    print("Next: copy that mindex folder to the root of the Xbox hard drive (Hdd1:\\) by FTP,")
    print("replacing mindex.xmi, then restart the console. Keep a copy of the old mindex.xmi.")
    return 0


def cmd_inspect(args):
    status = 0
    for path in args.files:
        print("== %s" % path)
        try:
            for line in describe_file(path, records=args.records):
                print(line)
        except (MindexError, FmimError, AsfError, OSError) as exc:
            print("ERROR: %s" % exc)
            status = 1
        print()
    return status


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
    handlers = {"convert": cmd_convert, "build": cmd_build, "inspect": cmd_inspect}
    if args.command == "gui":
        return launch_gui()
    if args.command not in handlers:
        parser.print_help()
        return 1
    try:
        return handlers[args.command](args)
    except (ConvertError, MindexError, FmimError, OSError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
