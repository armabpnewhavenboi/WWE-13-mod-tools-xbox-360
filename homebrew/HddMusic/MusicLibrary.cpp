// MusicLibrary - see MusicLibrary.h.  The file format is described in
// x360music/mindex.py; this is a line-for-line port of its Library class.

#include "MusicLibrary.h"

#include <stdlib.h>
#include <string.h>

namespace MusicLibrary {

// A list is owned by one record that holds (last, first, count); every
// member holds (prev, next, owner), and the ends point back to the owner.
struct ListKind {
    unsigned int last, first, count, prev, next, owner;
};

namespace {

enum {
    TYPE_FREE = 1, TYPE_TRACK, TYPE_ALBUM, TYPE_ARTIST, TYPE_GENRE, TYPE_PLAYLIST,
    TYPE_HEADER, TYPE_LIST_HEAD, TYPE_PLAYLIST_ENTRY
};

const ListKind GLOBAL        = { 0x10, 0x14, 0x18, 0x04, 0x08, 0x0C };
const ListKind ALBUM_TRACKS  = { 0x60, 0x64, 0x68, 0x60, 0x64, 0x68 };
const ListKind ARTIST_TRACKS = { 0x60, 0x64, 0x68, 0x6C, 0x70, 0x74 };
const ListKind ARTIST_ALBUMS = { 0x6C, 0x70, 0x74, 0x6C, 0x70, 0x74 };
const ListKind GENRE_TRACKS  = { 0x60, 0x64, 0x68, 0x78, 0x7C, 0x80 };
const ListKind GENRE_ALBUMS  = { 0x6C, 0x70, 0x74, 0x78, 0x7C, 0x80 };

const unsigned int NONE       = 0xFFFFFFFF;
const unsigned int NAME       = 0x10;
const unsigned int NAME_UNITS = 39;
const unsigned int TRACK_INFO = 0x90;
const unsigned int MAX_TRACK_NUMBER = 63;  // six bits in the track record

// FMIM header fields
const unsigned int FMIM_TITLE        = 0x00C;
const unsigned int FMIM_ALBUM        = 0x20C;
const unsigned int FMIM_ARTIST       = 0x40C;
const unsigned int FMIM_ALBUM_ARTIST = 0x60C;
const unsigned int FMIM_GENRE        = 0x80C;
const unsigned int FMIM_LENGTH_MS    = 0xC0C;
const unsigned int FMIM_TRACK_NUMBER = 0xC10;

unsigned int ReadBE32(const unsigned char* p)
{
    return ((unsigned int)p[0] << 24) | ((unsigned int)p[1] << 16) |
           ((unsigned int)p[2] << 8) | p[3];
}

void WriteBE32(unsigned char* p, unsigned int value)
{
    p[0] = (unsigned char)(value >> 24);
    p[1] = (unsigned char)(value >> 16);
    p[2] = (unsigned char)(value >> 8);
    p[3] = (unsigned char)value;
}

unsigned short ReadBE16(const unsigned char* p)
{
    return (unsigned short)((p[0] << 8) | p[1]);
}

unsigned short Fold(unsigned short unit)
{
    return (unit >= 0x41 && unit <= 0x5A) ? (unsigned short)(unit + 32) : unit;
}

int CompareNames(const Name& a, const Name& b)
{
    for (unsigned int i = 0; i < a.length && i < b.length; i++) {
        unsigned short x = Fold(a.units[i]), y = Fold(b.units[i]);
        if (x != y)
            return x < y ? -1 : 1;
    }
    if (a.length != b.length)
        return a.length < b.length ? -1 : 1;
    return 0;
}

// The first 39 characters of an FMIM string, without half a surrogate pair.
void ReadSongString(const unsigned char* header, unsigned int offset, Name* name)
{
    name->length = 0;
    for (unsigned int i = 0; i < NAME_UNITS; i++) {
        unsigned short unit = ReadBE16(header + offset + 2 * i);
        if (unit == 0)
            break;
        name->units[name->length++] = unit;
    }
    if (name->length && name->units[name->length - 1] >= 0xD800 &&
        name->units[name->length - 1] <= 0xDBFF)
        name->length--;
}

void SetAscii(Name* name, const char* text)
{
    name->length = 0;
    while (*text && name->length < NAME_UNITS)
        name->units[name->length++] = (unsigned char)*text++;
}

void ReadSongName(const unsigned char* header, unsigned int offset, const char* fallback,
                  Name* name)
{
    ReadSongString(header, offset, name);
    if (name->length == 0)
        SetAscii(name, fallback);
}

int CompareNumbers(unsigned int a, unsigned int b)
{
    return a < b ? -1 : (a > b ? 1 : 0);
}

}  // namespace

const char* ResultText(Result result)
{
    switch (result) {
    case RESULT_OK:          return "ok";
    case RESULT_DUPLICATE:   return "already in the library";
    case RESULT_NOT_FMIM:    return "not a song file made by x360music";
    case RESULT_BAD_LIBRARY: return "the music library isn't in the expected format";
    case RESULT_FULL:        return "the music library is full";
    case RESULT_NO_MEMORY:   return "out of memory";
    }
    return "unknown error";
}

bool IsSongHeader(const unsigned char* header, unsigned int size)
{
    return size >= FMIM_HEADER_SIZE && memcmp(header, "FMIM", 4) == 0;
}

int CompareSongs(const unsigned char* a, const unsigned char* b)
{
    // Same order as import_order() in x360music/mindex.py.
    Name x, y;
    int result;

    Name artistX, artistY;
    ReadSongName(a, FMIM_ARTIST, "Unknown Artist", &artistX);
    ReadSongName(b, FMIM_ARTIST, "Unknown Artist", &artistY);
    ReadSongString(a, FMIM_ALBUM_ARTIST, &x);
    ReadSongString(b, FMIM_ALBUM_ARTIST, &y);
    if ((result = CompareNames(x.length ? x : artistX, y.length ? y : artistY)) != 0)
        return result;

    ReadSongName(a, FMIM_ALBUM, "Unknown Album", &x);
    ReadSongName(b, FMIM_ALBUM, "Unknown Album", &y);
    if ((result = CompareNames(x, y)) != 0)
        return result;

    result = CompareNumbers(ReadBE32(a + FMIM_TRACK_NUMBER), ReadBE32(b + FMIM_TRACK_NUMBER));
    if (result != 0)
        return result;

    ReadSongName(a, FMIM_TITLE, "Unknown Song", &x);
    ReadSongName(b, FMIM_TITLE, "Unknown Song", &y);
    return CompareNames(x, y);
}

// ---------------------------------------------------------------------------

Library::Library() : m_data(NULL), m_count(0), m_capacity(0)
{
    memset(m_heads, 0, sizeof(m_heads));
}

Library::~Library()
{
    free(m_data);
}

unsigned int Library::U32(unsigned int index, unsigned int offset) const
{
    return ReadBE32(m_data + index * RECORD_SIZE + offset);
}

void Library::SetU32(unsigned int index, unsigned int offset, unsigned int value)
{
    WriteBE32(m_data + index * RECORD_SIZE + offset, value);
}

void Library::GetName(unsigned int index, Name* name) const
{
    const unsigned char* p = m_data + index * RECORD_SIZE + NAME;
    name->length = 0;
    for (unsigned int i = 0; i <= NAME_UNITS; i++) {
        unsigned short unit = ReadBE16(p + 2 * i);
        if (unit == 0)
            break;
        name->units[name->length++] = unit;
    }
}

unsigned int Library::TrackNumber(unsigned int index) const
{
    return (m_data[index * RECORD_SIZE + TRACK_INFO + 3] >> 2) & 0x3F;
}

// Grows in small steps: a big library can be tens of megabytes, and the
// console doesn't have room for a doubled copy.
bool Library::Reserve(unsigned int records)
{
    if (records <= m_capacity)
        return true;
    unsigned int capacity = records + 256;
    if (capacity > MAX_RECORDS)
        capacity = records > MAX_RECORDS ? records : MAX_RECORDS;
    unsigned char* data = (unsigned char*)realloc(m_data, capacity * RECORD_SIZE);
    if (!data)
        return false;
    m_data = data;
    m_capacity = capacity;
    return true;
}

Result Library::CreateNew()
{
    m_count = 0;
    if (!Reserve(64))
        return RESULT_NO_MEMORY;
    memset(m_data, 0, 7 * RECORD_SIZE);

    // Record 0 is the file header; it owns the list of list heads 1-6.
    SetU32(0, 0x00, TYPE_HEADER);
    memcpy(m_data + 4, " IMX", 4);
    SetU32(0, 0x08, 2);
    SetU32(0, 0x0C, 6);
    SetU32(0, 0x10, 1);
    SetU32(0, 0x14, 6);
    for (unsigned int kind = TYPE_FREE; kind <= TYPE_PLAYLIST; kind++) {
        unsigned int empty = kind == TYPE_PLAYLIST ? NONE : kind;
        SetU32(kind, 0x00, TYPE_LIST_HEAD);
        SetU32(kind, 0x04, kind - 1);
        SetU32(kind, 0x08, kind < TYPE_PLAYLIST ? kind + 1 : 0);
        SetU32(kind, 0x10, empty);
        SetU32(kind, 0x14, empty);
        SetU32(kind, 0x18, 0);
        SetU32(kind, 0x1C, kind);
    }
    m_count = 7;
    return FindHeads();
}

Result Library::Load(const unsigned char* data, unsigned int size)
{
    m_count = 0;
    if (size < RECORD_SIZE)
        return RESULT_BAD_LIBRARY;
    // A partial record at the end (never seen in console files) is dropped.
    unsigned int count = size / RECORD_SIZE;
    if (count > MAX_RECORDS)
        return RESULT_BAD_LIBRARY;
    if (!Reserve(count))
        return RESULT_NO_MEMORY;
    memcpy(m_data, data, count * RECORD_SIZE);
    m_count = count;
    if (Type(0) != TYPE_HEADER || memcmp(m_data + 4, " IMX", 4) != 0)
        return RESULT_BAD_LIBRARY;
    return FindHeads();
}

Result Library::FindHeads()
{
    memset(m_heads, 0, sizeof(m_heads));
    for (unsigned int index = 0; index < m_count; index++) {
        if (Type(index) == TYPE_LIST_HEAD) {
            unsigned int kind = U32(index, 0x1C);
            if (kind >= TYPE_FREE && kind <= TYPE_PLAYLIST && !m_heads[kind])
                m_heads[kind] = index;
        }
    }
    for (unsigned int kind = TYPE_TRACK; kind <= TYPE_GENRE; kind++) {
        if (!m_heads[kind])
            return RESULT_BAD_LIBRARY;
    }
    return RESULT_OK;
}

// Lowest record of this type with this name (and the given fields), or 0.
unsigned int Library::Find(unsigned int type, const Name& name, unsigned int offset1,
                           unsigned int value1, unsigned int offset2, unsigned int value2) const
{
    Name other;
    for (unsigned int index = 0; index < m_count; index++) {
        if (Type(index) != type)
            continue;
        GetName(index, &other);
        if (CompareNames(other, name) == 0 &&
            (!offset1 || U32(index, offset1) == value1) &&
            (!offset2 || U32(index, offset2) == value2))
            return index;
    }
    return 0;
}

unsigned int Library::FindAlbum(const Name& album, unsigned int albumArtist) const
{
    return albumArtist ? Find(TYPE_ALBUM, album, 0x74, albumArtist) : 0;
}

// Same title, album, artists and (when the song has one) track number.
unsigned int Library::FindSong(const Song& song) const
{
    unsigned int artist = Find(TYPE_ARTIST, song.artist);
    unsigned int album = FindAlbum(song.album, Find(TYPE_ARTIST, song.albumArtist));
    if (!artist || !album)
        return 0;
    unsigned int number = song.trackNumber < MAX_TRACK_NUMBER ? song.trackNumber : MAX_TRACK_NUMBER;
    Name other;
    for (unsigned int index = 0; index < m_count; index++) {
        if (Type(index) != TYPE_TRACK || U32(index, 0x68) != album || U32(index, 0x74) != artist)
            continue;
        GetName(index, &other);
        if (CompareNames(other, song.title) == 0 && (number == 0 || TrackNumber(index) == number))
            return index;
    }
    return 0;
}

Result Library::ParseSong(const unsigned char* header, Song* song) const
{
    if (!IsSongHeader(header, FMIM_HEADER_SIZE))
        return RESULT_NOT_FMIM;
    ReadSongName(header, FMIM_TITLE, "Unknown Song", &song->title);
    ReadSongName(header, FMIM_ALBUM, "Unknown Album", &song->album);
    ReadSongName(header, FMIM_ARTIST, "Unknown Artist", &song->artist);
    ReadSongString(header, FMIM_ALBUM_ARTIST, &song->albumArtist);
    if (song->albumArtist.length == 0)
        song->albumArtist = song->artist;
    ReadSongName(header, FMIM_GENRE, "Unknown Genre", &song->genre);
    song->lengthMs = ReadBE32(header + FMIM_LENGTH_MS);
    song->trackNumber = ReadBE32(header + FMIM_TRACK_NUMBER);
    return RESULT_OK;
}

Result Library::Plan(const Song& song, unsigned int* index) const
{
    if (FindSong(song))
        return RESULT_DUPLICATE;
    unsigned int added = 0;
    if (!Find(TYPE_ARTIST, song.artist))
        added++;
    unsigned int albumArtist = Find(TYPE_ARTIST, song.albumArtist);
    if (!albumArtist && CompareNames(song.albumArtist, song.artist) != 0)
        added++;
    if (!Find(TYPE_GENRE, song.genre))
        added++;
    if (!FindAlbum(song.album, albumArtist))
        added++;
    *index = m_count + added;
    return RESULT_OK;
}

Result Library::PlanSong(const unsigned char* fmimHeader, unsigned int* index)
{
    Song song;
    Result result = ParseSong(fmimHeader, &song);
    if (result != RESULT_OK)
        return result;
    return Plan(song, index);
}

// Follows a whole list, checking every link (members() in mindex.py).
Result Library::CheckList(unsigned int owner, const ListKind& kind) const
{
    unsigned int count = U32(owner, kind.count);
    if (count == 0)
        return RESULT_OK;
    unsigned int prev = owner, node = U32(owner, kind.first), seen = 0;
    while (node != owner) {
        if (node >= m_count || seen >= count)
            return RESULT_BAD_LIBRARY;
        if (U32(node, kind.prev) != prev || U32(node, kind.owner) != owner)
            return RESULT_BAD_LIBRARY;
        seen++;
        prev = node;
        node = U32(node, kind.next);
    }
    if (seen != count || U32(owner, kind.last) != prev)
        return RESULT_BAD_LIBRARY;
    return RESULT_OK;
}

bool Library::Greater(unsigned int a, unsigned int b, Key key) const
{
    if (key == KEY_ALBUM_TRACK) {
        int result = CompareNumbers(TrackNumber(a), TrackNumber(b));
        if (result != 0)
            return result > 0;
    }
    Name x, y;
    GetName(a, &x);
    GetName(b, &y);
    return CompareNames(x, y) > 0;
}

// Links `item` in before the first member that sorts after it.
Result Library::Insert(unsigned int owner, const ListKind& kind, unsigned int item, Key key)
{
    Result result = CheckList(owner, kind);
    if (result != RESULT_OK)
        return result;
    unsigned int count = U32(owner, kind.count);
    unsigned int prev = owner, node = count ? U32(owner, kind.first) : owner;
    while (node != owner && !Greater(node, item, key)) {
        prev = node;
        node = U32(node, kind.next);
    }
    SetU32(item, kind.prev, prev);
    SetU32(item, kind.next, node);
    SetU32(item, kind.owner, owner);
    if (prev == owner)
        SetU32(owner, kind.first, item);
    else
        SetU32(prev, kind.next, item);
    if (node == owner)
        SetU32(owner, kind.last, item);
    else
        SetU32(node, kind.prev, item);
    SetU32(owner, kind.count, count + 1);
    return RESULT_OK;
}

// Space has already been reserved by AddSong.
unsigned int Library::NewRecord(unsigned int type, const Name& name)
{
    unsigned int index = m_count++;
    unsigned char* record = m_data + index * RECORD_SIZE;
    memset(record, 0, RECORD_SIZE);
    WriteBE32(record, type);
    for (unsigned int i = 0; i < name.length; i++) {
        record[NAME + 2 * i] = (unsigned char)(name.units[i] >> 8);
        record[NAME + 2 * i + 1] = (unsigned char)name.units[i];
    }
    return index;
}

void Library::OwnerInit(unsigned int index, const ListKind& kind)
{
    SetU32(index, kind.last, index);
    SetU32(index, kind.first, index);
    SetU32(index, kind.count, 0);
}

Result Library::FindOrCreate(unsigned int type, const Name& name, unsigned int* index)
{
    *index = Find(type, name);
    if (*index)
        return RESULT_OK;
    *index = NewRecord(type, name);
    // Artists and genres both own a track list (0x60) and an album list (0x6C).
    OwnerInit(*index, ARTIST_TRACKS);
    OwnerInit(*index, ARTIST_ALBUMS);
    return Insert(m_heads[type], GLOBAL, *index, KEY_NAME);
}

Result Library::AddSong(const unsigned char* fmimHeader, unsigned int* index)
{
    Song song;
    unsigned int planned;
    Result result = ParseSong(fmimHeader, &song);
    if (result == RESULT_OK)
        result = Plan(song, &planned);
    if (result != RESULT_OK)
        return result;
    if (planned >= MAX_RECORDS)
        return RESULT_FULL;
    if (!Reserve(planned + 1))
        return RESULT_NO_MEMORY;

    unsigned int artist, albumArtist, genre;
    if ((result = FindOrCreate(TYPE_ARTIST, song.artist, &artist)) != RESULT_OK ||
        (result = FindOrCreate(TYPE_ARTIST, song.albumArtist, &albumArtist)) != RESULT_OK ||
        (result = FindOrCreate(TYPE_GENRE, song.genre, &genre)) != RESULT_OK)
        return result;

    unsigned int album = FindAlbum(song.album, albumArtist);
    if (!album) {
        album = NewRecord(TYPE_ALBUM, song.album);
        OwnerInit(album, ALBUM_TRACKS);
        SetU32(album, 0x74, albumArtist);
        SetU32(album, 0x80, genre);
        if ((result = Insert(m_heads[TYPE_ALBUM], GLOBAL, album, KEY_NAME)) != RESULT_OK ||
            (result = Insert(albumArtist, ARTIST_ALBUMS, album, KEY_NAME)) != RESULT_OK ||
            (result = Insert(genre, GENRE_ALBUMS, album, KEY_NAME)) != RESULT_OK)
            return result;
    }

    unsigned int number = song.trackNumber;
    if (number == 0)
        number = U32(album, ALBUM_TRACKS.count) + 1;
    if (number > MAX_TRACK_NUMBER)
        number = MAX_TRACK_NUMBER;
    unsigned int halfMs = song.lengthMs > 0x7FFFFF ? 0xFFFFFF : song.lengthMs * 2;
    unsigned int track = NewRecord(TYPE_TRACK, song.title);
    SetU32(track, 0x84, NONE);
    SetU32(track, 0x88, NONE);
    SetU32(track, 0x8C, 0);
    SetU32(track, TRACK_INFO, (halfMs << 8) | (1 + 4 * number));
    if ((result = Insert(m_heads[TYPE_TRACK], GLOBAL, track, KEY_NAME)) != RESULT_OK ||
        (result = Insert(album, ALBUM_TRACKS, track, KEY_ALBUM_TRACK)) != RESULT_OK ||
        (result = Insert(artist, ARTIST_TRACKS, track, KEY_NAME)) != RESULT_OK ||
        (result = Insert(genre, GENRE_TRACKS, track, KEY_NAME)) != RESULT_OK)
        return result;
    if (track != planned)
        return RESULT_BAD_LIBRARY;
    *index = track;
    return RESULT_OK;
}

}  // namespace MusicLibrary
