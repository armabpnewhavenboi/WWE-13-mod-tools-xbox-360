// Test driver for MusicLibrary.cpp, used by tests/test_music.py:
//
//   MusicLibraryTest OUT.xmi (IN.xmi | new) SONG...
//
// Adds the songs in import order, like the plugin does, prints one line per
// song ("<file> <record>" or "<file> duplicate") and writes the library.

#include "../MusicLibrary.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

using namespace MusicLibrary;

struct Entry {
    const char* name;
    unsigned char header[FMIM_HEADER_SIZE];
};

static int CompareEntries(const void* a, const void* b)
{
    const Entry* x = (const Entry*)a;
    const Entry* y = (const Entry*)b;
    int result = CompareSongs(x->header, y->header);
    return result ? result : strcmp(x->name, y->name);
}

static unsigned char* ReadFile(const char* path, unsigned int* size, unsigned int limit)
{
    FILE* f = fopen(path, "rb");
    if (!f)
        return NULL;
    fseek(f, 0, SEEK_END);
    long length = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (limit && (unsigned long)length > limit)
        length = limit;
    unsigned char* data = (unsigned char*)malloc(length ? length : 1);
    *size = (unsigned int)fread(data, 1, length, f);
    fclose(f);
    return data;
}

static const char* BaseName(const char* path)
{
    const char* slash = strrchr(path, '/');
    const char* back = strrchr(path, '\\');
    if (back && (!slash || back > slash))
        slash = back;
    return slash ? slash + 1 : path;
}

int main(int argc, char** argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s OUT.xmi (IN.xmi | new) SONG...\n", argv[0]);
        return 2;
    }
    Library library;
    Result result;
    if (strcmp(argv[2], "new") == 0) {
        result = library.CreateNew();
    } else {
        unsigned int size;
        unsigned char* data = ReadFile(argv[2], &size, 0);
        if (!data) {
            fprintf(stderr, "can't read %s\n", argv[2]);
            return 2;
        }
        result = library.Load(data, size);
        free(data);
    }
    if (result != RESULT_OK) {
        printf("error %s\n", ResultText(result));
        return 1;
    }

    int count = argc - 3;
    Entry* entries = (Entry*)calloc(count ? count : 1, sizeof(Entry));
    for (int i = 0; i < count; i++) {
        unsigned int size;
        unsigned char* data = ReadFile(argv[3 + i], &size, FMIM_HEADER_SIZE);
        if (!data || !IsSongHeader(data, size)) {
            printf("%s not-fmim\n", BaseName(argv[3 + i]));
            return 1;
        }
        entries[i].name = BaseName(argv[3 + i]);
        memcpy(entries[i].header, data, FMIM_HEADER_SIZE);
        free(data);
    }
    qsort(entries, count, sizeof(Entry), CompareEntries);

    for (int i = 0; i < count; i++) {
        unsigned int planned = 0, index = 0;
        result = library.PlanSong(entries[i].header, &planned);
        if (result == RESULT_OK)
            result = library.AddSong(entries[i].header, &index);
        if (result == RESULT_DUPLICATE) {
            printf("%s duplicate\n", entries[i].name);
        } else if (result == RESULT_OK && index == planned) {
            printf("%s %u\n", entries[i].name, index);
        } else {
            printf("error %s\n", result == RESULT_OK ? "plan mismatch" : ResultText(result));
            return 1;
        }
    }

    FILE* out = fopen(argv[1], "wb");
    if (!out || fwrite(library.Data(), 1, library.Size(), out) != library.Size()) {
        fprintf(stderr, "can't write %s\n", argv[1]);
        return 2;
    }
    fclose(out);
    free(entries);
    return 0;
}
