"""The work behind each command, shared by the command line and the window."""

import concurrent.futures
import glob
import os
import shutil
import struct

from . import asf, convert, fmim, mindex

SONG_FOLDER = "HddMusic"


class Summary(object):
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.converted = 0
        self.existing = 0
        self.duplicates = 0
        self.failed = []  # (file, message)


def song_folder(path):
    """<path>\\HddMusic, unless the path already is that folder."""
    if os.path.basename(os.path.normpath(path)).lower() == SONG_FOLDER.lower():
        return path
    return os.path.join(path, SONG_FOLDER)


def is_song_file(path):
    try:
        return os.path.getsize(path) > fmim.HEADER_SIZE and fmim.read_header(path) is not None
    except (OSError, fmim.FmimError):
        return False


def song_files(inputs):
    """Every .fmim song file in the given files / folders."""
    found = []
    for item in inputs:
        if os.path.isdir(item):
            found.extend(sorted(glob.glob(os.path.join(glob.escape(item),
                                                       "*" + convert.SONG_EXTENSION))))
        elif os.path.isfile(item):
            found.append(item)
        else:
            raise convert.ConvertError("not found: %s" % item)
    return found


def _length(ms):
    seconds = int(round(ms / 1000.0))
    return "%d:%02d" % (seconds // 60, seconds % 60)


def write_song_list(folder):
    """songs.txt: what each song file in the folder holds."""
    entries = []
    for path in song_files([folder]):
        try:
            info = fmim.read_header(path)
        except (OSError, fmim.FmimError):
            continue
        entries.append((mindex.import_order(info, os.path.basename(path)), path, info))
    lines = ["Songs for the HddMusic plugin. Plug this stick into the Xbox, hold BACK and",
             "press X to add them to the hard-drive music library.", ""]
    for _, path, info in sorted(entries, key=lambda e: e[0]):
        lines.append("%s  %s - %s - %02d %s (%s)" % (
            os.path.basename(path), info.album_artist, info.album, info.track_number,
            info.title, _length(info.length_ms)))
    with open(os.path.join(folder, "songs.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def convert_all(inputs, usb, jobs=None, bitrate=192, force=False, log=print, cancel=None):
    """Convert every music file in `inputs` to a song file in <usb>\\HddMusic.
    Setting the `cancel` event (threading.Event) stops before the next song."""
    ffmpeg = convert.find_tool("ffmpeg")
    ffprobe = convert.find_tool("ffprobe")
    files = convert.find_music(inputs)
    summary = Summary(song_folder(usb))
    os.makedirs(summary.out_dir, exist_ok=True)
    for stale in glob.glob(os.path.join(glob.escape(summary.out_dir), "*.part")):
        os.remove(stale)
    if not files:
        log("No music files found.")
        return summary
    jobs = jobs or max(1, min(8, os.cpu_count() or 2))

    log("Reading the tags of %d file(s)..." % len(files))
    songs = []
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        def read(item):
            path, root = item
            tags, length_ms = convert.probe(path, ffprobe)
            return convert.song_from_tags(path, tags, length_ms, root)
        for item, future in [(item, pool.submit(read, item)) for item in files]:
            try:
                songs.append(future.result())
            except (convert.ConvertError, OSError, ValueError) as exc:
                summary.failed.append((item[0], str(exc)))
                log("SKIPPED %s: %s" % (item[0], exc))

    todo, seen = [], {}
    for song in songs:
        name = song.file_name()
        if name in seen:
            summary.duplicates += 1
            log("Same song as %s, skipped: %s" % (seen[name], song.path))
            continue
        seen[name] = song.path
        target = os.path.join(summary.out_dir, name)
        if not force and is_song_file(target):
            summary.existing += 1
            continue
        todo.append((song, target))
    if summary.existing:
        log("%d song(s) were converted before, keeping those." % summary.existing)

    if todo:
        log("Converting %d song(s) to WMA %d kbps..." % (len(todo), bitrate))
    def make(song, target):
        if cancel is not None and cancel.is_set():
            raise convert.ConvertError("cancelled")
        return convert.make_song_file(song, target, ffmpeg, ffprobe, bitrate)

    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        futures = {pool.submit(make, song, target): song for song, target in todo}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            song = futures[future]
            done += 1
            try:
                future.result()
                summary.converted += 1
                log("[%d/%d] %s" % (done, len(todo), song.describe()))
            except (convert.ConvertError, fmim.FmimError, OSError) as exc:
                summary.failed.append((song.path, str(exc)))
                log("[%d/%d] FAILED %s: %s" % (done, len(todo), song.path, exc))
    write_song_list(summary.out_dir)
    return summary


def build_library(inputs, out, library_path=None, log=print):
    """Do on the PC what the plugin does: add song files to a mindex folder.

    Returns (added, skipped).  The result goes in <out>\\mindex, ready to copy
    to the root of the Xbox hard drive."""
    library = mindex.Library()
    if library_path:
        with open(library_path, "rb") as f:
            library = mindex.Library(f.read())
        for problem in library.check():
            log("warning: %s" % problem)
    entries = []
    for path in song_files(inputs):
        info = fmim.read_header(path)
        entries.append((mindex.import_order(info, os.path.basename(path)), path, info))
    entries.sort(key=lambda e: e[0])

    out_dir = os.path.join(out, "mindex")
    media = os.path.join(out_dir, "media", "0000")
    os.makedirs(media, exist_ok=True)
    added = skipped = 0
    for _, path, info in entries:
        index = library.add_song(info)
        if index is None:
            skipped += 1
            continue
        shutil.copyfile(path, os.path.join(media, mindex.media_name(index)))
        added += 1
        log("%s  %s - %s - %s" % (mindex.media_name(index), info.artist, info.album, info.title))
    with open(os.path.join(out_dir, "mindex.xmi"), "wb") as f:
        f.write(library.to_bytes())
    return added, skipped


# ------------------------------------------------------------------ inspect

def _hex_lines(data, base):
    lines = []
    for pos in range(0, len(data), 16):
        chunk = data[pos:pos + 16]
        if any(chunk):
            lines.append("    %04X  %s" % (base + pos, chunk.hex(" ")))
    return lines


def describe_song(data):
    info = fmim.SongInfo(data)
    lines = ["FMIM song file",
             "  title        : %s" % info.title,
             "  album        : %s" % info.album,
             "  artist       : %s" % info.artist,
             "  artist (2nd) : %s" % info.album_artist,
             "  genre        : %s / %s" % (info.genre, info.genre2),
             "  length       : %d ms (%s)" % (info.length_ms, _length(info.length_ms)),
             "  track number : %d" % info.track_number,
             "  bytes 4-11   : %s" % info.signature.hex(" ")]
    if any(info.unknown):
        lines.append("  unknown area 0xC14-0xD07:")
        lines.extend(_hex_lines(info.unknown, 0xC14))
    lines.extend(describe_wma(data[fmim.HEADER_SIZE:]))
    return lines


def describe_wma(data):
    try:
        info = asf.describe(data)
    except (asf.AsfError, ValueError, struct.error) as exc:
        return ["  audio: %s" % exc]
    keys = ("format_tag", "channels", "sample_rate", "bitrate", "block_align", "codec_data",
            "duration_ms", "preroll_ms", "packet_size", "packets", "flags", "file_size")
    lines = ["  audio (WMA):"]
    lines.extend("    %-12s %s" % (key, info[key]) for key in keys if key in info)
    lines.append("    codecs       %s" % "; ".join(info["codecs"]))
    lines.append("    header       %s" % ", ".join(info["header_objects"]))
    lines.append("    objects      %s" % ", ".join(info["objects"]))
    return lines


def _record_line(lib, index):
    kind = lib.rtype(index)
    u = lambda off: lib.u32(index, off)  # noqa: E731
    name = lib.name(index) if kind in (mindex.TRACK, mindex.ALBUM, mindex.ARTIST, mindex.GENRE,
                                       mindex.PLAYLIST) else ""
    text = "%5d %-14s %-30s" % (index, mindex.TYPE_NAMES.get(kind, "type %d" % kind), name[:30])
    if kind == mindex.HEADER:
        text += " %s" % " ".join("%d" % u(off) for off in (0x08, 0x0C, 0x10, 0x14))
    elif kind == mindex.LIST_HEAD:
        text += " links %d,%d,%d  list %d,%d count %d  of type %d" % (
            u(4), u(8), u(0xC), u(0x10), u(0x14), u(0x18), u(0x1C))
    elif kind == mindex.TRACK:
        text += " album %d artist %d genre %d  #%d %s  playlists %d" % (
            u(0x68), u(0x74), u(0x80), lib.track_number(index), _length(lib.length_ms(index)),
            u(0x8C))
    elif kind == mindex.ALBUM:
        extra = sum(1 for b in lib.records[index][0x84:] if b)
        text += " artist %d genre %d  %d tracks%s" % (
            u(0x74), u(0x80), u(0x68), "  (%d unknown bytes set)" % extra if extra else "")
    elif kind in (mindex.ARTIST, mindex.GENRE):
        text += " %d tracks, %d albums" % (u(0x68), u(0x74))
    elif kind == mindex.PLAYLIST:
        text += " %d entries" % u(0x68)
    return text


def describe_library(data, records=False):
    lib = mindex.Library(data, validate=False)
    counts = ", ".join("%d %ss" % (lib.count(kind), mindex.TYPE_NAMES[kind])
                       for kind in (mindex.TRACK, mindex.ALBUM, mindex.ARTIST, mindex.GENRE,
                                    mindex.PLAYLIST, mindex.FREE))
    lines = ["mindex.xmi music library: %d records (%s)" % (len(lib.records), counts)]
    if len(data) % mindex.RECORD_SIZE:
        lines.append("  note: %d extra bytes at the end" % (len(data) % mindex.RECORD_SIZE))
    problems = lib.check()
    lines.append("  check: %s" % ("all links OK" if not problems else "%d problem(s)" % len(problems)))
    lines.extend("    - " + p for p in problems[:50])
    if records:
        lines.extend(_record_line(lib, i) for i in range(len(lib.records)))
    else:
        try:
            for index, title, album, artist, number, length in lib.songs():
                lines.append("  %s  %s - %s - %02d %s (%s)" % (
                    mindex.media_name(index), artist, album, number, title, _length(length)))
        except (mindex.MindexError, IndexError) as exc:
            lines.append("  can't list the songs: %s" % exc)
    return lines


def describe_file(path, records=False):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] == fmim.MAGIC:
        return describe_song(data)
    if data[:16] == asf.HEADER:
        return ["WMA file"] + describe_wma(data)
    return describe_library(data, records)
