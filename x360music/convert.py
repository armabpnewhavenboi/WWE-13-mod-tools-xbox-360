"""Turning music files into Xbox 360 song files with ffmpeg.

Each song is converted the way the console's CD ripper stores music (WMA,
192 kbps, 44.1 kHz, stereo, constant bitrate) and wrapped in an FMIM header
holding its title, artist, album, genre, length and track number.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zlib

from . import fmim

AUDIO_EXTENSIONS = (".mp3", ".wma", ".m4a", ".aac", ".mp4", ".flac", ".ogg", ".oga", ".opus",
                    ".wav", ".aif", ".aiff", ".ape", ".wv", ".mp2", ".ac3")
BITRATES = (128, 160, 192)
SONG_EXTENSION = ".fmim"


class ConvertError(Exception):
    pass


def find_tool(name):
    """Path of ffmpeg / ffprobe: next to this program first, then on the PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    places = [os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                                              else sys.argv[0] or ".")),
              os.getcwd()]
    for place in places:
        candidate = os.path.join(place, exe)
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which(name)
    if found:
        return found
    raise ConvertError("%s was not found. Download ffmpeg from https://ffmpeg.org/download.html "
                       "and put %s next to this program (or on your PATH)." % (name, exe))


def _run(args):
    kwargs = {}
    if os.name == "nt":  # no console window flashing up for every song
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ConvertError(message[-1] if message else "%s failed" % os.path.basename(args[0]))
    return proc.stdout


def probe(path, ffprobe):
    """Tags (lower-case keys) and length in milliseconds of a music file."""
    out = _run([ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                "-show_streams", "-select_streams", "a:0", path])
    info = json.loads(out.decode("utf-8", "replace") or "{}")
    if not info.get("streams"):
        raise ConvertError("no audio in this file")
    tags = {}
    for source in (info["streams"][0].get("tags", {}), info.get("format", {}).get("tags", {})):
        for key, value in source.items():
            tags.setdefault(key.lower(), value)
    duration = info.get("format", {}).get("duration") or info["streams"][0].get("duration")
    try:
        length_ms = int(round(float(duration) * 1000))
    except (TypeError, ValueError):
        length_ms = 0
    return tags, length_ms


def _clean(value):
    return re.sub(r"\s+", " ", (value or "").replace("\x00", ";")).strip()


def _first(value):
    return _clean(value).split(";")[0].strip()


def _number(value):
    match = re.match(r"\s*(\d+)", value or "")
    return int(match.group(1)) if match else 0


class Song(object):
    """What the library will show for one music file."""

    def __init__(self, path, title, artist, album_artist, album, genre, track_number, length_ms):
        self.path = path
        self.title, self.artist, self.album_artist = title, artist, album_artist
        self.album, self.genre = album, genre
        self.track_number, self.length_ms = track_number, length_ms

    def key(self):
        parts = (self.album_artist, self.album, str(self.track_number), self.title, self.artist)
        return "\x00".join(p.lower() for p in parts)

    def file_name(self):
        """Stable name, so converting the same song again finds the old file."""
        return "%08X%s" % (zlib.crc32(self.key().encode("utf-8")) & 0xFFFFFFFF, SONG_EXTENSION)

    def describe(self):
        return "%s - %s - %02d %s" % (self.album_artist, self.album, self.track_number, self.title)


def song_from_tags(path, tags, length_ms, root=None):
    """Song details from the file's tags, falling back to file and folder names."""
    stem = os.path.splitext(os.path.basename(path))[0]
    title = _clean(tags.get("title"))
    number = _number(tags.get("track") or tags.get("tracknumber"))
    if not title:
        match = re.match(r"^\s*(\d{1,3})\s*[-._ ]\s*(.+)$", stem)
        if match:
            number = number or int(match.group(1))
            stem = match.group(2)
        title = _clean(stem)
    artist = ", ".join(a.strip() for a in _clean(tags.get("artist")).split(";") if a.strip())
    album_artist = _first(tags.get("album_artist") or tags.get("albumartist")
                          or tags.get("album artist"))
    album = _clean(tags.get("album"))
    if not album:
        folder = os.path.dirname(os.path.abspath(path))
        if root is None or os.path.normcase(folder) != os.path.normcase(os.path.abspath(root)):
            album = os.path.basename(folder)
    artist = artist or album_artist or "Unknown Artist"
    return Song(path, title or "Unknown Song", artist, album_artist or artist,
                album or "Unknown Album", _first(tags.get("genre")) or "Unknown Genre",
                number, length_ms)


def find_music(inputs):
    """(file, folder it was found under) for every music file in the inputs."""
    found = []
    for item in inputs:
        if os.path.isfile(item):
            found.append((item, None))
        elif os.path.isdir(item):
            for folder, dirs, files in os.walk(item):
                dirs.sort()
                for name in sorted(files):
                    if name.lower().endswith(AUDIO_EXTENSIONS):
                        found.append((os.path.join(folder, name), item))
        else:
            raise ConvertError("not found: %s" % item)
    return found


def to_wma(path, out_path, ffmpeg, bitrate=192):
    """Convert to WMA like the console's ripper (tags are kept in the FMIM header)."""
    _run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", path,
          "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1",
          "-fflags", "+bitexact", "-flags:a", "+bitexact",
          "-c:a", "wmav2", "-b:a", "%dk" % bitrate, "-ar", "44100", "-ac", "2",
          "-f", "asf", out_path])


def make_song_file(song, out_path, ffmpeg, ffprobe, bitrate=192):
    """Convert one song and write it as an FMIM song file."""
    tmpdir = tempfile.mkdtemp(prefix="x360music-")
    try:
        wma_path = os.path.join(tmpdir, "song.wma")
        to_wma(song.path, wma_path, ffmpeg, bitrate)
        _, length_ms = probe(wma_path, ffprobe)
        with open(wma_path, "rb") as f:
            wma = f.read()
        header = fmim.build_header(song.title, song.album, song.artist, song.album_artist,
                                   song.genre, length_ms or song.length_ms, song.track_number)
        data = fmim.build_song(header, wma)
        partial = out_path + ".part"
        with open(partial, "wb") as f:
            f.write(data)
        os.replace(partial, out_path)
        return len(data)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
