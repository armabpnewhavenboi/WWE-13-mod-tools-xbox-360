"""mindex.xmi - the Xbox 360 hard-drive music library index.

Ripped CDs live in Hdd:\\mindex: the index file mindex.xmi plus one FMIM song
file per track in media\\0000\\, named after the track's record number in
four upper-case hex digits.

mindex.xmi is an array of 600-byte big-endian records.  The first u32 of a
record is its type:

  1 free (deleted)   2 track   3 album   4 artist   5 genre   6 playlist
  7 file header      8 list head         9 playlist entry

Record 0 is the file header (" IMX" at 0x04) and records 1-6 are the list
heads for types 1-6.  Everything else is linked together in circular,
doubly-linked lists.  A list is owned by one record that holds
(last, first, count); every member holds (prev, next, owner), and the first
member's prev and the last member's next point back to the owner.  The
lists are:

  owner       (last,first,count)  members  (prev,next,owner)  order
  header      0x0C 0x10 0x14      list heads 0x04 0x08 0x0C   file order
  list head   0x10 0x14 0x18      records  0x04 0x08 0x0C     by name
  album       0x60 0x64 0x68      tracks   0x60 0x64 0x68     by track number
  artist      0x60 0x64 0x68      tracks   0x6C 0x70 0x74     by name
  artist      0x6C 0x70 0x74      albums   0x6C 0x70 0x74     by name
  genre       0x60 0x64 0x68      tracks   0x78 0x7C 0x80     by name
  genre       0x6C 0x70 0x74      albums   0x78 0x7C 0x80     by name

Names are UTF-16BE at 0x10, at most 39 characters plus a terminating zero.
A track also holds its playlist entries at 0x84/0x88/0x8C (0xFFFFFFFF,
0xFFFFFFFF, 0 when it is in no playlist) and at 0x90 a u32 made of the length
in half-milliseconds (24 bits) and a byte 1 + 4 * track number (at most 63).

Record layout from the Xbox-360-Mindex project by Lyall-A, whose generated
libraries play on real consoles.  The plugin in homebrew/HddMusic has a C++
copy of this code (MusicLibrary.cpp) that must produce the same bytes; the
tests compare the two.
"""

import struct

from .fmim import from_units, to_units, truncate_units

RECORD_SIZE = 600
FREE, TRACK, ALBUM, ARTIST, GENRE, PLAYLIST, HEADER, LIST_HEAD, PLAYLIST_ENTRY = range(1, 10)
TYPE_NAMES = {FREE: "free", TRACK: "track", ALBUM: "album", ARTIST: "artist", GENRE: "genre",
              PLAYLIST: "playlist", HEADER: "header", LIST_HEAD: "list head",
              PLAYLIST_ENTRY: "playlist entry"}
NONE = 0xFFFFFFFF
NAME = 0x10
NAME_UNITS = 39
TRACK_INFO = 0x90
MAX_RECORDS = 0x10000  # song files are named with four hex digits
MAX_TRACK_NUMBER = 63  # six bits in the track record

UNKNOWN_SONG = "Unknown Song"
UNKNOWN_ALBUM = "Unknown Album"
UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_GENRE = "Unknown Genre"


class MindexError(Exception):
    pass


class ListKind(object):
    def __init__(self, name, last, first, count, prev, next, owner):
        self.name = name
        self.last, self.first, self.count = last, first, count
        self.prev, self.next, self.owner = prev, next, owner


HEADS = ListKind("list heads", 0x0C, 0x10, 0x14, 0x04, 0x08, 0x0C)
GLOBAL = ListKind("all", 0x10, 0x14, 0x18, 0x04, 0x08, 0x0C)
ALBUM_TRACKS = ListKind("album tracks", 0x60, 0x64, 0x68, 0x60, 0x64, 0x68)
ARTIST_TRACKS = ListKind("artist tracks", 0x60, 0x64, 0x68, 0x6C, 0x70, 0x74)
ARTIST_ALBUMS = ListKind("artist albums", 0x6C, 0x70, 0x74, 0x6C, 0x70, 0x74)
GENRE_TRACKS = ListKind("genre tracks", 0x60, 0x64, 0x68, 0x78, 0x7C, 0x80)
GENRE_ALBUMS = ListKind("genre albums", 0x6C, 0x70, 0x74, 0x78, 0x7C, 0x80)


def fold(units):
    """Case-insensitive key: A-Z folded to a-z, everything else as is."""
    return tuple(u + 32 if 0x41 <= u <= 0x5A else u for u in units)


def song_names(info):
    """(title, album, artist, album artist, genre) as stored in the index."""
    def pick(units, default):
        return truncate_units(units, NAME_UNITS) or default
    artist = pick(info.artist_units, to_units(UNKNOWN_ARTIST))
    return (pick(info.title_units, to_units(UNKNOWN_SONG)),
            pick(info.album_units, to_units(UNKNOWN_ALBUM)),
            artist, pick(info.album_artist_units, artist),
            pick(info.genre_units, to_units(UNKNOWN_GENRE)))


def import_order(info, file_name=""):
    """Sort key for adding songs: album by album, in track order."""
    title, album, _, album_artist, _ = song_names(info)
    return (fold(album_artist), fold(album), info.track_number, fold(title), file_name)


def media_name(index):
    return "%04X" % index


class Library(object):
    def __init__(self, data=None, validate=True):
        if data is None:
            self.records = self._fresh()
        else:
            if len(data) < RECORD_SIZE:
                raise MindexError("mindex.xmi is too small")
            # A partial record at the end (never seen in console files) is dropped.
            count = len(data) // RECORD_SIZE
            if count > MAX_RECORDS:
                raise MindexError("mindex.xmi is too big")
            self.records = [bytearray(data[i * RECORD_SIZE:(i + 1) * RECORD_SIZE])
                            for i in range(count)]
        if self.rtype(0) != HEADER or bytes(self.records[0][4:8]) != b" IMX":
            raise MindexError("mindex.xmi has no IMX header")
        self.heads = {}
        for index in range(len(self.records)):
            if self.rtype(index) == LIST_HEAD:
                kind = self.u32(index, 0x1C)
                if FREE <= kind <= PLAYLIST and kind not in self.heads:
                    self.heads[kind] = index
        for kind in (TRACK, ALBUM, ARTIST, GENRE):
            if kind not in self.heads:
                raise MindexError("mindex.xmi has no list of %ss" % TYPE_NAMES[kind])
        if data is not None and validate:
            self.validate()

    def validate(self):
        """Refuse a damaged library before anything is changed (Validate() in C++)."""
        for kind in (TRACK, ALBUM, ARTIST, GENRE):
            self.members(self.heads[kind], GLOBAL)
        for index in range(len(self.records)):
            kind = self.rtype(index)
            if kind == ALBUM:
                self.members(index, ALBUM_TRACKS)
            elif kind == ARTIST:
                self.members(index, ARTIST_TRACKS)
                self.members(index, ARTIST_ALBUMS)
            elif kind == GENRE:
                self.members(index, GENRE_TRACKS)
                self.members(index, GENRE_ALBUMS)
            if kind in (TRACK, ALBUM):
                links = (0x68, 0x74, 0x80) if kind == TRACK else (0x74, 0x80)
                if any(self.u32(index, off) >= len(self.records) for off in links):
                    raise MindexError("record %d points past the end of the library" % index)

    @staticmethod
    def _fresh():
        header = bytearray(RECORD_SIZE)
        struct.pack_into(">I4sIIII", header, 0, HEADER, b" IMX", 2, 6, 1, 6)
        records = [header]
        for kind in range(FREE, PLAYLIST + 1):
            head = bytearray(RECORD_SIZE)
            prev = kind - 1
            nxt = kind + 1 if kind < PLAYLIST else 0
            empty = NONE if kind == PLAYLIST else kind
            struct.pack_into(">III", head, 0, LIST_HEAD, prev, nxt)
            struct.pack_into(">IIII", head, 0x10, empty, empty, 0, kind)
            records.append(head)
        return records

    def to_bytes(self):
        return b"".join(bytes(r) for r in self.records)

    # ------------------------------------------------------------ fields

    def u32(self, index, offset):
        return struct.unpack_from(">I", self.records[index], offset)[0]

    def set_u32(self, index, offset, value):
        struct.pack_into(">I", self.records[index], offset, value)

    def rtype(self, index):
        return self.u32(index, 0)

    def name_units(self, index):
        units = []
        record = self.records[index]
        for i in range(NAME_UNITS + 1):
            (unit,) = struct.unpack_from(">H", record, NAME + 2 * i)
            if unit == 0:
                break
            units.append(unit)
        return units

    def name(self, index):
        return from_units(self.name_units(index))

    def track_number(self, index):
        return (self.records[index][TRACK_INFO + 3] >> 2) & 0x3F

    def length_ms(self, index):
        return (self.u32(index, TRACK_INFO) >> 8) // 2

    # ------------------------------------------------------------- lists

    def members(self, owner, kind):
        """Members of a list in order, checking every link on the way."""
        count = self.u32(owner, kind.count)
        result = []
        if count == 0:
            return result
        prev, node = owner, self.u32(owner, kind.first)
        while node != owner:
            if node >= len(self.records) or len(result) >= count:
                raise MindexError("broken %s list of record %d" % (kind.name, owner))
            if self.u32(node, kind.prev) != prev or self.u32(node, kind.owner) != owner:
                raise MindexError("broken %s list of record %d at record %d"
                                  % (kind.name, owner, node))
            result.append(node)
            prev, node = node, self.u32(node, kind.next)
        if len(result) != count or self.u32(owner, kind.last) != prev:
            raise MindexError("broken %s list of record %d" % (kind.name, owner))
        return result

    def _insert(self, owner, kind, item, key):
        """Link `item` in before the first member whose key is greater."""
        members = self.members(owner, kind)
        item_key = key(item)
        pos = len(members)
        for i, member in enumerate(members):
            if key(member) > item_key:
                pos = i
                break
        prev = members[pos - 1] if pos > 0 else owner
        nxt = members[pos] if pos < len(members) else owner
        self.set_u32(item, kind.prev, prev)
        self.set_u32(item, kind.next, nxt)
        self.set_u32(item, kind.owner, owner)
        if prev == owner:
            self.set_u32(owner, kind.first, item)
        else:
            self.set_u32(prev, kind.next, item)
        if nxt == owner:
            self.set_u32(owner, kind.last, item)
        else:
            self.set_u32(nxt, kind.prev, item)
        self.set_u32(owner, kind.count, len(members) + 1)

    def _name_key(self, index):
        return fold(self.name_units(index))

    def _album_track_key(self, index):
        return (self.track_number(index), fold(self.name_units(index)))

    # ----------------------------------------------------------- lookups

    def find(self, rtype, units, extra=None):
        key = fold(units)
        for index in range(len(self.records)):
            if (self.rtype(index) == rtype and self._name_key(index) == key
                    and (extra is None or extra(index))):
                return index
        return None

    def _find_album(self, album, album_artist_index):
        if album_artist_index is None:
            return None
        return self.find(ALBUM, album, lambda i: self.u32(i, 0x74) == album_artist_index)

    def find_song(self, info):
        """Record number of a track already in the library, or None.  Same
        title, album, artists and (when the song has one) track number."""
        title, album, artist, album_artist, _ = song_names(info)
        artist_index = self.find(ARTIST, artist)
        album_index = self._find_album(album, self.find(ARTIST, album_artist))
        if artist_index is None or album_index is None:
            return None
        number = min(info.track_number, MAX_TRACK_NUMBER)
        return self.find(TRACK, title, lambda i: self.u32(i, 0x68) == album_index
                         and self.u32(i, 0x74) == artist_index
                         and (number == 0 or self.track_number(i) == number))

    def plan_song(self, info):
        """Record number add_song() would give this song, or None if it is already there."""
        if self.find_song(info) is not None:
            return None
        _, album, artist, album_artist, genre = song_names(info)
        new = 0
        if self.find(ARTIST, artist) is None:
            new += 1
        album_artist_index = self.find(ARTIST, album_artist)
        if album_artist_index is None and fold(album_artist) != fold(artist):
            new += 1
        if self.find(GENRE, genre) is None:
            new += 1
        if self._find_album(album, album_artist_index) is None:
            new += 1
        return len(self.records) + new

    # ---------------------------------------------------------- creation

    def _new_record(self, rtype, units):
        if len(self.records) >= MAX_RECORDS:
            raise MindexError("the music library is full")
        record = bytearray(RECORD_SIZE)
        struct.pack_into(">I", record, 0, rtype)
        struct.pack_into(">%dH" % len(units), record, NAME, *units)
        self.records.append(record)
        return len(self.records) - 1

    def _owner_init(self, index, *kinds):
        for kind in kinds:
            self.set_u32(index, kind.last, index)
            self.set_u32(index, kind.first, index)
            self.set_u32(index, kind.count, 0)

    def _find_or_create(self, rtype, units):
        index = self.find(rtype, units)
        if index is None:
            index = self._new_record(rtype, units)
            # Artists and genres both own a track list (0x60) and an album list (0x6C).
            self._owner_init(index, ARTIST_TRACKS, ARTIST_ALBUMS)
            self._insert(self.heads[rtype], GLOBAL, index, self._name_key)
        return index

    def add_song(self, info):
        """Add one song (an fmim.SongInfo).  Returns its record number, or None
        if the library already has it.  The song file must then be stored as
        media\\0000\\<media_name(record number)>."""
        planned = self.plan_song(info)
        if planned is None:
            return None
        if planned >= MAX_RECORDS:
            raise MindexError("the music library is full")
        title, album, artist, album_artist, genre = song_names(info)
        artist_index = self._find_or_create(ARTIST, artist)
        album_artist_index = self._find_or_create(ARTIST, album_artist)
        genre_index = self._find_or_create(GENRE, genre)
        album_index = self._find_album(album, album_artist_index)
        if album_index is None:
            album_index = self._new_record(ALBUM, album)
            self._owner_init(album_index, ALBUM_TRACKS)
            self.set_u32(album_index, 0x74, album_artist_index)
            self.set_u32(album_index, 0x80, genre_index)
            self._insert(self.heads[ALBUM], GLOBAL, album_index, self._name_key)
            self._insert(album_artist_index, ARTIST_ALBUMS, album_index, self._name_key)
            self._insert(genre_index, GENRE_ALBUMS, album_index, self._name_key)

        number = info.track_number
        if number == 0:
            number = self.u32(album_index, ALBUM_TRACKS.count) + 1
        number = min(number, MAX_TRACK_NUMBER)
        half_ms = min(info.length_ms * 2, 0xFFFFFF)
        index = self._new_record(TRACK, title)
        self.set_u32(index, 0x84, NONE)
        self.set_u32(index, 0x88, NONE)
        self.set_u32(index, 0x8C, 0)
        self.set_u32(index, TRACK_INFO, (half_ms << 8) | (1 + 4 * number))
        self._insert(self.heads[TRACK], GLOBAL, index, self._name_key)
        self._insert(album_index, ALBUM_TRACKS, index, self._album_track_key)
        self._insert(artist_index, ARTIST_TRACKS, index, self._name_key)
        self._insert(genre_index, GENRE_TRACKS, index, self._name_key)
        if index != planned:
            raise MindexError("internal error: record %d instead of %d" % (index, planned))
        return index

    # ------------------------------------------------------------- check

    def check(self):
        """List of problems found by following every link (empty when sound)."""
        problems = []

        def members(owner, kind):
            try:
                return self.members(owner, kind)
            except MindexError as exc:
                problems.append(str(exc))
                return []

        def expect(owner, kind, wanted, what):
            found = members(owner, kind)
            if sorted(found) != sorted(wanted) or len(set(found)) != len(found):
                problems.append("%s of record %d (%s) do not match the records that point to it"
                                % (what, owner, kind.name))

        by_type = {}
        for index in range(len(self.records)):
            by_type.setdefault(self.rtype(index), []).append(index)
        expect(0, HEADS, by_type.get(LIST_HEAD, []), "list heads")
        for kind, head in sorted(self.heads.items()):
            expect(head, GLOBAL, by_type.get(kind, []), TYPE_NAMES[kind] + "s")
        tracks = by_type.get(TRACK, [])
        albums = by_type.get(ALBUM, [])
        for album in albums:
            expect(album, ALBUM_TRACKS, [t for t in tracks if self.u32(t, 0x68) == album], "tracks")
        for artist in by_type.get(ARTIST, []):
            expect(artist, ARTIST_TRACKS, [t for t in tracks if self.u32(t, 0x74) == artist], "tracks")
            expect(artist, ARTIST_ALBUMS, [a for a in albums if self.u32(a, 0x74) == artist], "albums")
        for genre in by_type.get(GENRE, []):
            expect(genre, GENRE_TRACKS, [t for t in tracks if self.u32(t, 0x80) == genre], "tracks")
            expect(genre, GENRE_ALBUMS, [a for a in albums if self.u32(a, 0x80) == genre], "albums")
        links = [(t, 0x68, ALBUM) for t in tracks]
        for index in tracks + albums:
            links += [(index, 0x74, ARTIST), (index, 0x80, GENRE)]
        for index, offset, rtype in links:
            target = self.u32(index, offset)
            if target >= len(self.records) or self.rtype(target) != rtype:
                problems.append("record %d points to record %d as its %s"
                                % (index, target, TYPE_NAMES[rtype]))
        return problems

    # ----------------------------------------------------------- reading

    def count(self, rtype):
        return sum(1 for i in range(len(self.records)) if self.rtype(i) == rtype)

    def songs(self):
        """(record, title, album, artist, track number, length ms) in album order."""
        result = []
        for album in self.members(self.heads[ALBUM], GLOBAL):
            for track in self.members(album, ALBUM_TRACKS):
                artist = self.u32(track, 0x74)
                if artist >= len(self.records):
                    raise MindexError("track %d points to record %d as its artist" % (track, artist))
                result.append((track, self.name(track), self.name(album), self.name(artist),
                               self.track_number(track), self.length_ms(track)))
        return result
