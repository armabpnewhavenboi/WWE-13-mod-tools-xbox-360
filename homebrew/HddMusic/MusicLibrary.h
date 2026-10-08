// MusicLibrary - adds songs to the Xbox 360 hard-drive music library
// (Hdd:\mindex\mindex.xmi), the same way the dashboard does when it rips a CD.
//
// Plain C++ with no Xbox headers so it also builds on a PC: the tests in
// tests/test_music.py compile it with g++ and check that it writes exactly
// the same bytes as the Python version in x360music/mindex.py, which
// documents the file format.

#ifndef MUSICLIBRARY_H
#define MUSICLIBRARY_H

namespace MusicLibrary {

const unsigned int RECORD_SIZE      = 600;
const unsigned int FMIM_HEADER_SIZE = 0xD08;
const unsigned int MAX_RECORDS      = 0x10000;  // song files are named with 4 hex digits

enum Result {
    RESULT_OK = 0,
    RESULT_DUPLICATE,    // the song is already in the library
    RESULT_NOT_FMIM,     // not a song file made by x360music
    RESULT_BAD_LIBRARY,  // mindex.xmi isn't laid out the way we expect
    RESULT_FULL,         // no free record numbers left
    RESULT_NO_MEMORY
};

const char* ResultText(Result result);

// A name as stored in the library: at most 39 UTF-16 characters.
struct Name {
    unsigned short units[40];
    unsigned int length;
};

struct ListKind;  // one kind of linked list, see MusicLibrary.cpp

// True if the buffer starts with an FMIM song header.
bool IsSongHeader(const unsigned char* header, unsigned int size);

// Orders FMIM headers album by album, in track order (for qsort).
int CompareSongs(const unsigned char* a, const unsigned char* b);

class Library {
public:
    Library();
    ~Library();

    Result CreateNew();
    Result Load(const unsigned char* data, unsigned int size);

    const unsigned char* Data() const { return m_data; }
    unsigned int Size() const { return m_count * RECORD_SIZE; }
    unsigned int RecordCount() const { return m_count; }

    // The record number AddSong would give this song (its file goes in
    // media\0000\%04X), or RESULT_DUPLICATE if the library already has it.
    Result PlanSong(const unsigned char* fmimHeader, unsigned int* index);
    // After an error other than RESULT_DUPLICATE the library may be half
    // changed: throw it away rather than saving it.
    Result AddSong(const unsigned char* fmimHeader, unsigned int* index);

private:
    struct Song {
        Name title, album, artist, albumArtist, genre;
        unsigned int lengthMs, trackNumber;
    };
    enum Key { KEY_NAME, KEY_ALBUM_TRACK };

    unsigned int U32(unsigned int index, unsigned int offset) const;
    void SetU32(unsigned int index, unsigned int offset, unsigned int value);
    unsigned int Type(unsigned int index) const { return U32(index, 0); }
    void GetName(unsigned int index, Name* name) const;
    unsigned int TrackNumber(unsigned int index) const;

    bool Reserve(unsigned int records);
    Result FindHeads();
    Result Validate() const;
    unsigned int Find(unsigned int type, const Name& name,
                      unsigned int offset1 = 0, unsigned int value1 = 0,
                      unsigned int offset2 = 0, unsigned int value2 = 0) const;
    unsigned int FindAlbum(const Name& album, unsigned int albumArtist) const;
    unsigned int FindSong(const Song& song) const;
    Result ParseSong(const unsigned char* header, Song* song) const;
    Result Plan(const Song& song, unsigned int* index) const;

    Result CheckList(unsigned int owner, const ListKind& kind) const;
    bool Greater(unsigned int a, unsigned int b, Key key) const;
    Result Insert(unsigned int owner, const ListKind& kind, unsigned int item, Key key);
    unsigned int NewRecord(unsigned int type, const Name& name);
    void OwnerInit(unsigned int index, const ListKind& kind);
    Result FindOrCreate(unsigned int type, const Name& name, unsigned int* index);

    unsigned char* m_data;
    unsigned int m_count;
    unsigned int m_capacity;
    unsigned int m_heads[7];  // list head record for each type 1-6, 0 = none
};

}  // namespace MusicLibrary

#endif
