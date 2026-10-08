"""Batch pipeline: find every package in a pile of folders / zips, swap in your IDs,
rehash, and write a USB-ready ``Content`` tree plus a report."""

import datetime
import os
import zipfile

from . import __version__
from .ids import fmt, profile_id_warning
from .stfs import (CONTENT_TYPE_PROFILE, MAGIC_CON, StfsError, StfsPackage, is_stfs_magic)

UNSUPPORTED_ARCHIVES = (".rar", ".7z", ".tar", ".gz", ".bz2", ".xz")

STATUS_RESIGNED = "RESIGNED"
STATUS_COPIED = "COPIED UNCHANGED"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"

REPORT_NAME = "resign_report.txt"


class Options:
    def __init__(self, profile_id=None, console_id=None, device_id=None,
                 patch_embedded=False, include_profiles=False, layout="usb"):
        self.profile_id = profile_id
        self.console_id = console_id
        self.device_id = device_id
        self.patch_embedded = patch_embedded
        self.include_profiles = include_profiles
        if layout not in ("usb", "flat"):
            raise ValueError("layout must be 'usb' or 'flat'")
        self.layout = layout


class InputItem:
    def __init__(self, source, filename, loader):
        self.source = source
        self.filename = filename
        self.loader = loader


class Result:
    def __init__(self, source, filename):
        self.source = source
        self.filename = filename
        self.output_name = filename
        self.kind = ""            # "CON", "LIVE" or "PIRS"
        self.status = STATUS_FAILED
        self.notes = []
        self.output = None
        self.title_id = None
        self.content_type = None
        self.display_name = ""
        self.old_ids = None
        self.new_ids = None
        self.embedded_hits = []   # (label, file path in package, offset, byte order)
        self.hashes_fixed = 0
        self.before = None
        self.after = None

    def line(self, shown_source=None):
        name = self.display_name or self.filename
        return "[%s] %s  (%s)" % (self.status, name, shown_source or self.source)


def _short_source(source, roots):
    """Path of an input relative to the folder the user picked (zip members keep '!member')."""
    path, sep, member = source.partition("!")
    for root in roots:
        if os.path.isdir(root):
            try:
                rel = os.path.relpath(path, root)
            except ValueError:  # different drive letters on Windows
                continue
            if not rel.startswith(".."):
                return rel + sep + member
    return source


# ---------------------------------------------------------------- inputs

def _read_head(path, size=4):
    try:
        with open(path, "rb") as f:
            return f.read(size)
    except OSError:
        return b""


def _file_loader(path):
    def load():
        with open(path, "rb") as f:
            return f.read()
    return load


def _zip_loader(zip_path, member):
    def load():
        with zipfile.ZipFile(zip_path) as zf:
            return zf.read(member)
    return load


def collect_inputs(paths, exclude=None, log=print):
    """Return (items, skipped_messages).  ``exclude`` is a directory never descended into."""
    items = []
    skipped = []
    exclude_real = os.path.realpath(exclude) if exclude else None

    def handle_file(path):
        lower = path.lower()
        if lower.endswith(".zip"):
            try:
                with zipfile.ZipFile(path) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        with zf.open(info) as member:
                            head = member.read(4)
                        if is_stfs_magic(head):
                            items.append(InputItem("%s!%s" % (path, info.filename),
                                                   os.path.basename(info.filename),
                                                   _zip_loader(path, info.filename)))
            except (zipfile.BadZipFile, OSError) as exc:
                skipped.append("%s: could not open zip (%s)" % (path, exc))
            return
        if lower.endswith(UNSUPPORTED_ARCHIVES):
            skipped.append("%s: archive - extract it first (e.g. with 7-Zip) and run again" % path)
            return
        if is_stfs_magic(_read_head(path)):
            items.append(InputItem(path, os.path.basename(path), _file_loader(path)))

    for path in paths:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                if exclude_real:
                    dirs[:] = [d for d in dirs
                               if os.path.realpath(os.path.join(root, d)) != exclude_real]
                dirs.sort()
                for name in sorted(files):
                    handle_file(os.path.join(root, name))
        elif os.path.isfile(path):
            handle_file(path)
        else:
            skipped.append("%s: not found" % path)
    return items, skipped


def ids_from_package(path):
    """Read (profile_id, console_id, device_id) from one of the user's own saves.

    An ID that is all zeros in that save means "not known" and is returned as None.
    """
    pkg = StfsPackage.from_file(path)
    console = pkg.console_id
    if not any(console) and pkg.is_con:
        console = pkg.creator_console_id
    return tuple(value if any(value) else None
                 for value in (pkg.profile_id, console, pkg.device_id))


# ------------------------------------------------------------- processing

def _scan_embedded(pkg, swaps):
    """Find old IDs stored inside the save data itself."""
    hits = []
    needles = []
    for label, old, new in swaps:
        if old == new or not any(old):
            continue
        needles.append((label, old, new, "big-endian"))
        if old[::-1] != old:
            needles.append((label, old[::-1], new[::-1], "little-endian"))
    if not needles:
        return hits
    for entry in pkg.files():
        if entry.is_directory or entry.size == 0:
            continue
        content = pkg.read_file(entry)
        for label, old, new, order in needles:
            start = 0
            while True:
                pos = content.find(old, start)
                if pos < 0:
                    break
                hits.append((label, entry, pos, order, new))
                start = pos + 1
    return hits


def process_package(data, filename, options, source=None):
    """Process one package.  Returns (output_bytes or None, Result)."""
    result = Result(source or filename, filename)
    try:
        pkg = StfsPackage(data, name=filename)
    except StfsError as exc:
        result.status = STATUS_SKIPPED
        result.notes.append("not a usable package: %s" % exc)
        return None, result

    result.title_id = pkg.title_id
    result.content_type = pkg.content_type
    result.display_name = pkg.display_name
    result.kind = pkg.magic.decode("ascii", "replace").strip()
    result.old_ids = (pkg.profile_id, pkg.console_id, pkg.device_id)
    # Some content is stored under its content ID (the header hash), which
    # changes when the header changes - such files must be renamed to match.
    named_by_content_id = filename.upper() == pkg.compute_header_hash().hex().upper()

    try:
        result.before = pkg.verify()
    except StfsError as exc:
        result.notes.append("could not check original: %s" % exc)

    if pkg.magic != MAGIC_CON:
        result.status = STATUS_COPIED
        result.notes.append("%s package (DLC / marketplace content). It is not tied to a "
                            "profile, so it is copied unchanged." % pkg.magic.decode().strip())
        result.new_ids = result.old_ids
        return bytes(data), result

    if not pkg.is_stfs:
        result.status = STATUS_COPIED
        result.notes.append("SVOD package - copied unchanged")
        result.new_ids = result.old_ids
        return bytes(data), result

    if pkg.content_type == CONTENT_TYPE_PROFILE and not options.include_profiles:
        result.status = STATUS_SKIPPED
        result.notes.append("this is a gamer profile, not a save - skipped "
                            "(command line: --include-profiles processes it anyway)")
        return None, result

    old_profile, old_console, old_device = result.old_ids

    if options.profile_id is None or not any(old_profile):
        new_profile = old_profile
        if options.profile_id is not None:
            result.notes.append("shared content (profile ID 0000000000000000) - profile ID left as-is")
    else:
        new_profile = options.profile_id
    new_console = options.console_id if options.console_id is not None else old_console
    new_device = options.device_id if options.device_id is not None else old_device

    try:
        hits = _scan_embedded(pkg, [("profile ID", old_profile, new_profile),
                                    ("console ID", old_console, new_console),
                                    ("device ID", old_device, new_device)])
        for label, entry, pos, order, new in hits:
            result.embedded_hits.append((label, entry.path, pos, order))
            if options.patch_embedded:
                pkg.write_file_bytes(entry, pos, new)
        if hits:
            if options.patch_embedded:
                result.notes.append("patched %d old ID(s) stored inside the save data" % len(hits))
            else:
                result.notes.append(
                    "the save data itself contains the old ID(s) (%d place%s) - if the game "
                    "rejects this save, run again with 'patch IDs inside save data' turned on"
                    % (len(hits), "" if len(hits) == 1 else "s"))

        pkg.profile_id = new_profile
        pkg.console_id = new_console
        pkg.device_id = new_device
        result.new_ids = (new_profile, new_console, new_device)

        result.hashes_fixed = pkg.rehash()  # also refreshes the header hash
        result.status = STATUS_RESIGNED

        result.after = pkg.verify()
        if not result.after.ok:
            result.status = STATUS_FAILED
            result.notes.append("self-check after resigning failed: %s" % result.after.summary())
            return None, result
        if named_by_content_id:
            result.output_name = pkg.compute_header_hash().hex().upper()
            result.notes.append("file is named after its content ID, renamed to %s"
                                % result.output_name)
    except StfsError as exc:
        result.status = STATUS_FAILED
        result.notes.append(str(exc))
        return None, result

    return pkg.to_bytes(), result


# --------------------------------------------------------------- output

def output_relpath(result, layout):
    if layout == "flat":
        return result.output_name
    profile = result.new_ids[0] if result.new_ids else bytes(8)
    return os.path.join("Content", profile.hex().upper(), "%08X" % (result.title_id or 0),
                        "%08X" % (result.content_type or 0), result.output_name)


class BatchSummary:
    def __init__(self):
        self.results = []
        self.skipped_inputs = []
        self.report_path = None

    def count(self, status):
        return sum(1 for r in self.results if r.status == status)

    @property
    def dlc(self):
        """LIVE / PIRS packages (DLC / marketplace content) found in the input."""
        return [r for r in self.results if r.kind in ("LIVE", "PIRS")]

    @property
    def failed(self):
        return self.count(STATUS_FAILED)


def run_batch(paths, out_dir, options, log=print):
    summary = BatchSummary()
    out_dir = os.path.abspath(out_dir)
    items, skipped = collect_inputs(paths, exclude=out_dir, log=log)
    summary.skipped_inputs = skipped
    for message in skipped:
        log("  note: " + message)
    if not items:
        log("No Xbox 360 packages (CON/LIVE/PIRS files) were found in the input.")
        return summary

    if options.profile_id is not None:
        warning = profile_id_warning(options.profile_id)
        if warning:
            log("WARNING: " + warning)

    log("Found %d package(s). Working..." % len(items))
    used = {}
    for number, item in enumerate(items, 1):
        try:
            data = item.loader()
        except (OSError, zipfile.BadZipFile) as exc:
            result = Result(item.source, item.filename)
            result.notes.append("could not read: %s" % exc)
            summary.results.append(result)
            log("%3d/%d %s" % (number, len(items), result.line(_short_source(item.source, paths))))
            continue

        out_bytes, result = process_package(data, item.filename, options, source=item.source)
        summary.results.append(result)
        if out_bytes is not None:
            rel = output_relpath(result, options.layout)
            key = rel.lower()
            if key in used:
                used[key] += 1
                rel = os.path.join("_duplicates", str(used[key]), rel)
                result.notes.append("another input produced the same output name; this copy was "
                                    "put in %s" % rel)
            else:
                used[key] = 1
            target = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(out_bytes)
            result.output = target
        log("%3d/%d %s" % (number, len(items), result.line(_short_source(item.source, paths))))
        for note in result.notes:
            log("        - " + note)

    for line in dlc_notice(summary, out_dir, options.layout):
        log(line)
    summary.report_path = write_report(summary, out_dir, options)
    return summary


def dlc_notice(summary, out_dir, layout="usb"):
    """Lines explaining the DLC situation - the usual cause of WWE's "missing or damaged
    downloadable content" message after a save has been resigned successfully."""
    lines = [""]
    dlc = summary.dlc
    if dlc:
        lines.append("DLC packages in this pack (%d) - copied unchanged:" % len(dlc))
        for r in dlc:
            where = os.path.relpath(r.output, out_dir) if r.output else "(not written)"
            lines.append("  - %s  ->  %s" % (r.display_name or r.filename, where))
        where = ("they are in the Content\\0000000000000000 folder" if layout == "usb"
                 else "they are in the output folder")
        lines.append("Copy these to the USB stick too (%s). If a save still says DLC is "
                     "missing, it needs DLC that wasn't in this pack - install that DLC "
                     "yourself." % where)
    if summary.count(STATUS_RESIGNED):
        lines.append("If the game says a save can't be used because of \"missing or damaged "
                     "downloadable content\", the save itself loaded fine: it was made with DLC "
                     "(superstars, moves, arenas...) that is not installed on your console. "
                     "Install that DLC and the game's latest title update, then try again.")
    return lines if len(lines) > 1 else []


def write_report(summary, out_dir, options):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, REPORT_NAME)
    lines = [
        "x360resign %s report - %s" % (__version__, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
        "",
        "Target profile ID : %s" % fmt(options.profile_id),
        "Target console ID : %s" % (fmt(options.console_id) if options.console_id else "(unchanged)"),
        "Target device ID  : %s" % (fmt(options.device_id) if options.device_id else "(unchanged)"),
        "Patch IDs in data : %s" % ("yes" if options.patch_embedded else "no (scan only)"),
        "",
    ]
    for status in (STATUS_RESIGNED, STATUS_COPIED, STATUS_SKIPPED, STATUS_FAILED):
        lines.append("%-22s %d" % (status + ":", summary.count(status)))
    lines.extend(dlc_notice(summary, out_dir, options.layout))
    lines.append("")
    for result in summary.results:
        lines.append("=" * 78)
        lines.append(result.line())
        if result.title_id is not None:
            lines.append("  title %08X, content type %08X" % (result.title_id, result.content_type))
        if result.old_ids:
            lines.append("  profile %s -> %s" % (fmt(result.old_ids[0]),
                                                 fmt(result.new_ids[0]) if result.new_ids else "-"))
            lines.append("  console %s -> %s" % (fmt(result.old_ids[1]),
                                                 fmt(result.new_ids[1]) if result.new_ids else "-"))
            lines.append("  device  %s -> %s" % (fmt(result.old_ids[2]),
                                                 fmt(result.new_ids[2]) if result.new_ids else "-"))
        if result.before is not None:
            lines.append("  original : %s" % result.before.summary())
        if result.after is not None:
            tree = (", %d hash%s recomputed" % (result.hashes_fixed,
                                                "" if result.hashes_fixed == 1 else "es")
                    if result.hashes_fixed else ", data hash tree was already intact")
            lines.append("  output   : %s%s" % (result.after.summary(), tree))
        for label, inner_path, pos, order in result.embedded_hits:
            lines.append("  old %s found inside %s at offset 0x%X (%s)%s"
                         % (label, inner_path, pos, order,
                            " - patched" if options.patch_embedded else ""))
        for note in result.notes:
            lines.append("  note: " + note)
        if result.output:
            lines.append("  -> " + os.path.relpath(result.output, out_dir))
    if summary.skipped_inputs:
        lines.append("=" * 78)
        lines.append("Inputs that were not processed:")
        lines.extend("  " + s for s in summary.skipped_inputs)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path
