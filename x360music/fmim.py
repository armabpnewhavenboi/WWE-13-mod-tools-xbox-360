"""FMIM song files - how the Xbox 360 stores a ripped CD track.

The dashboard's CD ripper keeps each song in Hdd:\\mindex\\media\\0000\\XXXX as
a 0xD08-byte "FMIM" header followed by an ordinary WMA (ASF) file.  All
numbers are big-endian and all strings are UTF-16BE, zero-padded.

  0x000  4      "FMIM"
  0x004  8      00 00 00 01 00 01 00 01
  0x00C  0x200  title
  0x20C  0x200  album
  0x40C  0x200  artist
  0x60C  0x200  second artist field (we store the album artist here)
  0x80C  0x200  genre
  0xA0C  0x200  genre again
  0xC0C  4      length in milliseconds, rounded to the nearest second
  0xC10  4      track number, starting at 1
  0xC14  0xF4   unknown, left as zeros
  0xD08         WMA file

Layout from the Free60 wiki (System-Software/Formats/FMIM) and the
Xbox-360-Mindex project by Lyall-A.
"""

import struct

MAGIC = b"FMIM"
SIGNATURE = b"\x00\x00\x00\x01\x00\x01\x00\x01"
HEADER_SIZE = 0xD08

TITLE = 0x00C
ALBUM = 0x20C
ARTIST = 0x40C
ALBUM_ARTIST = 0x60C
GENRE = 0x80C
GENRE2 = 0xA0C
LENGTH_MS = 0xC0C
TRACK_NUMBER = 0xC10
STRING_UNITS = 0x100  # 0x200 bytes of UTF-16, including the terminating zero

ASF_HEADER_GUID = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")


class FmimError(Exception):
    pass


def to_units(text):
    """UTF-16 code units of a string."""
    data = (text or "").encode("utf-16-be", "surrogatepass")
    return list(struct.unpack(">%dH" % (len(data) // 2), data))


def from_units(units):
    return struct.pack(">%dH" % len(units), *units).decode("utf-16-be", "replace")


def truncate_units(units, limit):
    """At most `limit` code units, without leaving half a surrogate pair."""
    units = list(units[:limit])
    if units and 0xD800 <= units[-1] <= 0xDBFF:
        units.pop()
    return units


def _put_string(buf, offset, text):
    units = truncate_units(to_units(text), STRING_UNITS - 1)
    buf[offset:offset + 2 * len(units)] = struct.pack(">%dH" % len(units), *units)


def _get_units(data, offset, limit=STRING_UNITS):
    units = []
    for i in range(limit):
        (unit,) = struct.unpack_from(">H", data, offset + 2 * i)
        if unit == 0:
            break
        units.append(unit)
    return units


def build_header(title, album, artist, album_artist, genre, length_ms, track_number):
    """The 0xD08-byte FMIM header for one song."""
    buf = bytearray(HEADER_SIZE)
    buf[0:4] = MAGIC
    buf[4:12] = SIGNATURE
    _put_string(buf, TITLE, title)
    _put_string(buf, ALBUM, album)
    _put_string(buf, ARTIST, artist)
    _put_string(buf, ALBUM_ARTIST, album_artist or artist)
    _put_string(buf, GENRE, genre)
    _put_string(buf, GENRE2, genre)
    rounded = int((max(0, length_ms) + 500) // 1000) * 1000
    struct.pack_into(">II", buf, LENGTH_MS, min(rounded, 0xFFFFFFFF),
                     max(0, min(int(track_number or 0), 0xFFFFFFFF)))
    return bytes(buf)


def build_song(header, wma):
    if not wma.startswith(ASF_HEADER_GUID):
        raise FmimError("the converted audio is not a WMA (ASF) file")
    return header + wma


class SongInfo(object):
    """The fields of an FMIM header."""

    def __init__(self, data):
        if len(data) < HEADER_SIZE or data[:4] != MAGIC:
            raise FmimError("not an Xbox 360 song file (no FMIM header)")
        self.signature = bytes(data[4:12])
        self.title_units = _get_units(data, TITLE)
        self.album_units = _get_units(data, ALBUM)
        self.artist_units = _get_units(data, ARTIST)
        self.album_artist_units = _get_units(data, ALBUM_ARTIST)
        self.genre_units = _get_units(data, GENRE)
        self.genre2_units = _get_units(data, GENRE2)
        self.length_ms, self.track_number = struct.unpack_from(">II", data, LENGTH_MS)
        self.unknown = bytes(data[TRACK_NUMBER + 4:HEADER_SIZE])

    title = property(lambda self: from_units(self.title_units))
    album = property(lambda self: from_units(self.album_units))
    artist = property(lambda self: from_units(self.artist_units))
    album_artist = property(lambda self: from_units(self.album_artist_units))
    genre = property(lambda self: from_units(self.genre_units))
    genre2 = property(lambda self: from_units(self.genre2_units))


def read_header(path):
    with open(path, "rb") as f:
        return SongInfo(f.read(HEADER_SIZE))
