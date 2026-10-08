// HddMusic - DashLaunch plugin that "rips" songs from a USB stick into the
// Xbox 360's hard-drive music library, as if they came from an audio CD.
//
//   1. On a PC, "x360music convert" turns MP3s into WMA song files in
//      <USB>\HddMusic\*.fmim (the format the console's CD ripper uses).
//   2. Plug the stick in, hold BACK and press X on any controller.
//   3. The songs are added to Hdd:\mindex, the library the dashboard rips CDs
//      into. After a restart they show up under Music Player > Hard Drive and
//      can be played in any game through the Guide, like ripped CDs.
//
// The library format code is in MusicLibrary.cpp, which is tested on a PC
// against the Python version in x360music/mindex.py.
//
// Build as an Xbox 360 DLL with the XDK (see README.md) and load it with
// DashLaunch (plugin1 = Hdd:\Plugins\HddMusic.xex in launch.ini).

#include <xtl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "MusicLibrary.h"

using namespace MusicLibrary;

// Not in the Xbox 360 headers.
#ifndef INVALID_FILE_ATTRIBUTES
#define INVALID_FILE_ATTRIBUTES ((DWORD)-1)
#endif
#ifndef INVALID_FILE_SIZE
#define INVALID_FILE_SIZE ((DWORD)-1)
#endif

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

#define HM_COMBO_HOLD       XINPUT_GAMEPAD_BACK
#define HM_COMBO_PRESS      XINPUT_GAMEPAD_X
#define HM_BOOT_DELAY_MS    10000   // let the dashboard finish loading first
#define HM_POLL_MS          50
#define HM_BATCH            128     // song headers read and sorted at a time

// ---------------------------------------------------------------------------
// Kernel / XAM imports that the public XDK headers don't declare
// ---------------------------------------------------------------------------

typedef struct _HM_STRING {
    USHORT Length;
    USHORT MaximumLength;
    PCHAR  Buffer;
} HM_STRING;

extern "C" {
    LONG  ObCreateSymbolicLink(HM_STRING* linkName, HM_STRING* devicePath);
    LONG  ObDeleteSymbolicLink(HM_STRING* linkName);
    DWORD ExCreateThread(PHANDLE handle, DWORD stackSize, LPDWORD threadId,
                         VOID* apiThreadStartup, LPTHREAD_START_ROUTINE start,
                         LPVOID param, DWORD flags);
    VOID  XapiThreadStartup(VOID (__cdecl *start)(VOID*), VOID* context);
    DWORD XexGetModuleHandle(PSZ moduleName, PHANDLE handle);
    DWORD XexGetProcedureAddress(HANDLE module, DWORD ordinal, PVOID* address);
}

#define EX_CREATE_FLAG_SUSPENDED 0x1
#define EX_CREATE_FLAG_SYSTEM    0x2
#define XAM_ORD_XNOTIFYQUEUEUI  656

typedef VOID (*PFN_XNOTIFYQUEUEUI)(DWORD type, DWORD userIndex, ULONGLONG areas,
                                   LPCWSTR text, PVOID context);

static PFN_XNOTIFYQUEUEUI pXNotifyQueueUI = NULL;
static volatile BOOL      g_running = TRUE;

static const char* USB_FOLDERS[] = { "HmUsb0:\\HddMusic", "HmUsb1:\\HddMusic", "HmUsb2:\\HddMusic" };
static const char* LIBRARY_DIRS[] = { "HmHdd:\\mindex", "HmHdd:\\Content\\mindex" };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Called straight from our system thread, as other working plugins do.
static void Notify(LPCWSTR fmt, ...)
{
    if (!pXNotifyQueueUI)
        return;
    WCHAR text[160];
    va_list args;
    va_start(args, fmt);
    _vsnwprintf_s(text, 160, _TRUNCATE, fmt, args);
    va_end(args);
    pXNotifyQueueUI(14, 0xFF, 2, text, NULL);  // custom text, any user, high priority
}

static void Mount(const char* link, const char* device)
{
    char full[64];
    sprintf_s(full, "\\System??\\%s", link);

    HM_STRING linkName, devicePath;
    linkName.Buffer = full;
    linkName.Length = (USHORT)strlen(full);
    linkName.MaximumLength = linkName.Length + 1;
    devicePath.Buffer = (PCHAR)device;
    devicePath.Length = (USHORT)strlen(device);
    devicePath.MaximumLength = devicePath.Length + 1;

    ObDeleteSymbolicLink(&linkName);  // left over if the plugin was reloaded
    ObCreateSymbolicLink(&linkName, &devicePath);
}

static void ResolveXamImports()
{
    HANDLE xam = NULL;
    if (XexGetModuleHandle("xam.xex", &xam) != 0 || !xam)
        return;
    XexGetProcedureAddress(xam, XAM_ORD_XNOTIFYQUEUEUI, (PVOID*)&pXNotifyQueueUI);
}

static BOOL Exists(const char* path)
{
    return GetFileAttributes(path) != INVALID_FILE_ATTRIBUTES;
}

// Reads at most `limit` bytes (0 = the whole file) into a malloc'd buffer.
static unsigned char* ReadWholeFile(const char* path, DWORD limit, DWORD* size)
{
    HANDLE file = CreateFile(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                             FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE)
        return NULL;
    DWORD length = GetFileSize(file, NULL);
    if (length == INVALID_FILE_SIZE || (limit && length < limit)) {
        CloseHandle(file);
        return NULL;
    }
    if (limit)
        length = limit;
    unsigned char* data = (unsigned char*)malloc(length ? length : 1);
    DWORD read = 0;
    if (!data || !ReadFile(file, data, length, &read, NULL) || read != length) {
        free(data);
        data = NULL;
    }
    CloseHandle(file);
    *size = length;
    return data;
}

static BOOL WriteWholeFile(const char* path, const unsigned char* data, DWORD size)
{
    HANDLE file = CreateFile(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                             FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE)
        return FALSE;
    DWORD written = 0;
    BOOL ok = WriteFile(file, data, size, &written, NULL) && written == size;
    CloseHandle(file);
    if (!ok)
        DeleteFile(path);
    return ok;
}

#define HM_COPY_BUFFER_SIZE (256 * 1024)

static BOOL CopyOneFile(const char* src, const char* dst, BYTE* buffer)
{
    HANDLE in = CreateFile(src, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (in == INVALID_HANDLE_VALUE)
        return FALSE;
    HANDLE out = CreateFile(dst, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                            FILE_ATTRIBUTE_NORMAL, NULL);
    if (out == INVALID_HANDLE_VALUE) {
        CloseHandle(in);
        return FALSE;
    }

    BOOL ok = TRUE;
    for (;;) {
        DWORD read = 0, written = 0;
        if (!ReadFile(in, buffer, HM_COPY_BUFFER_SIZE, &read, NULL)) { ok = FALSE; break; }
        if (read == 0)
            break;
        if (!WriteFile(out, buffer, read, &written, NULL) || written != read) { ok = FALSE; break; }
    }
    CloseHandle(out);
    CloseHandle(in);
    if (!ok)
        DeleteFile(dst);  // don't leave half a song behind
    return ok;
}

// ---------------------------------------------------------------------------
// Ripping from USB
// ---------------------------------------------------------------------------

struct SongFile {
    char name[MAX_PATH];
    unsigned char header[FMIM_HEADER_SIZE];
};

static int CompareSongFiles(const void* a, const void* b)
{
    const SongFile* x = (const SongFile*)a;
    const SongFile* y = (const SongFile*)b;
    int result = CompareSongs(x->header, y->header);
    return result ? result : strcmp(x->name, y->name);
}

struct Import {
    Library      library;
    char         usbDir[64];
    char         libraryDir[64];
    BYTE*        copyBuffer;
    DWORD*       copied;       // record numbers of song files copied this run
    DWORD        copiedCount;
    DWORD        added, skipped, failed;
    Result       error;
};

static void MediaPath(const Import& import, DWORD index, char* path, size_t size)
{
    sprintf_s(path, size, "%s\\media\\0000\\%04X", import.libraryDir, index);
}

static void DeleteCopiedSongs(Import& import)
{
    char path[MAX_PATH];
    for (DWORD i = 0; i < import.copiedCount; i++) {
        MediaPath(import, import.copied[i], path, sizeof(path));
        DeleteFile(path);
    }
    import.copiedCount = 0;
}

// Adds one batch of songs, sorted album by album.  Returns FALSE to stop.
static BOOL AddBatch(Import& import, SongFile* songs, DWORD count)
{
    qsort(songs, count, sizeof(SongFile), CompareSongFiles);
    for (DWORD i = 0; i < count; i++) {
        unsigned int planned, index;
        Result result = import.library.PlanSong(songs[i].header, &planned);
        if (result == RESULT_DUPLICATE) {
            import.skipped++;
            continue;
        }
        if (result == RESULT_OK && planned >= MAX_RECORDS)
            result = RESULT_FULL;
        if (result == RESULT_NOT_FMIM) {
            import.failed++;
            continue;
        }
        if (result != RESULT_OK) {
            import.error = result;
            return FALSE;
        }

        // Copy the song first, so a failed copy never ends up in the library.
        char from[MAX_PATH], to[MAX_PATH];
        sprintf_s(from, "%s\\%s", import.usbDir, songs[i].name);
        MediaPath(import, planned, to, sizeof(to));
        if (!CopyOneFile(from, to, import.copyBuffer)) {
            import.failed++;
            continue;
        }
        import.copied[import.copiedCount++] = planned;

        result = import.library.AddSong(songs[i].header, &index);
        if (result != RESULT_OK || index != planned) {
            import.error = result != RESULT_OK ? result : RESULT_BAD_LIBRARY;
            return FALSE;
        }
        import.added++;
    }
    return TRUE;
}

static BOOL FolderHasFiles(const char* folder)
{
    char pattern[MAX_PATH];
    sprintf_s(pattern, "%s\\*", folder);
    WIN32_FIND_DATA fd;
    HANDLE find = FindFirstFile(pattern, &fd);
    if (find == INVALID_HANDLE_VALUE)
        return FALSE;
    BOOL found = FALSE;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY))
            found = TRUE;
    } while (!found && FindNextFile(find, &fd));
    FindClose(find);
    return found;
}

// Loads the library (or starts a new one).  Shows its own message on failure.
static BOOL OpenLibrary(Import& import, BOOL* existed)
{
    // Use whichever library folder exists, or make the standard one.
    strcpy_s(import.libraryDir, LIBRARY_DIRS[0]);
    for (int i = 0; i < _countof(LIBRARY_DIRS); i++) {
        if (Exists(LIBRARY_DIRS[i])) {
            strcpy_s(import.libraryDir, LIBRARY_DIRS[i]);
            break;
        }
    }

    char path[MAX_PATH];
    sprintf_s(path, "%s\\mindex.xmi", import.libraryDir);
    *existed = Exists(path);
    char media[MAX_PATH];
    sprintf_s(media, "%s\\media\\0000", import.libraryDir);
    Result result;
    if (*existed) {
        DWORD size = 0;
        unsigned char* data = ReadWholeFile(path, 0, &size);
        if (!data) {
            Notify(L"HddMusic: can't read the music library. Restart and try again.");
            return FALSE;
        }
        result = import.library.Load(data, size);
        free(data);
    } else if (FolderHasFiles(media)) {
        // Songs but no index: starting a new library would overwrite them.
        Notify(L"HddMusic: songs found without mindex.xmi. Restore mindex.xmi.bak first. "
               L"Nothing was changed.");
        return FALSE;
    } else {
        result = import.library.CreateNew();
    }
    if (result != RESULT_OK) {
        Notify(L"HddMusic: %S. Nothing was changed.", ResultText(result));
        return FALSE;
    }

    CreateDirectory(import.libraryDir, NULL);
    sprintf_s(path, "%s\\media", import.libraryDir);
    CreateDirectory(path, NULL);
    CreateDirectory(media, NULL);
    if (!Exists(media)) {
        Notify(L"HddMusic: can't create the music folder on the hard drive.");
        return FALSE;
    }
    return TRUE;
}

static void RipFromUsb()
{
    Import* import = new Import();
    if (!import)
        return;
    import->copyBuffer = NULL;
    import->copied = NULL;
    import->copiedCount = import->added = import->skipped = import->failed = 0;
    import->error = RESULT_OK;

    import->usbDir[0] = 0;
    for (int i = 0; i < _countof(USB_FOLDERS); i++) {
        if (Exists(USB_FOLDERS[i])) {
            strcpy_s(import->usbDir, USB_FOLDERS[i]);
            break;
        }
    }
    if (!import->usbDir[0]) {
        Notify(L"HddMusic: no HddMusic folder on the USB stick. Run x360music convert first.");
        delete import;
        return;
    }

    BOOL existed = FALSE;
    SongFile* batch = (SongFile*)malloc(HM_BATCH * sizeof(SongFile));
    import->copyBuffer = (BYTE*)malloc(HM_COPY_BUFFER_SIZE);
    import->copied = (DWORD*)malloc(MAX_RECORDS * sizeof(DWORD));
    if (!batch || !import->copyBuffer || !import->copied) {
        Notify(L"HddMusic: out of memory");
        goto done;
    }
    if (!OpenLibrary(*import, &existed))
        goto done;

    Notify(L"HddMusic: ripping songs from USB to the hard drive...");
    {
        char path[MAX_PATH];
        sprintf_s(path, "%s\\*.fmim", import->usbDir);
        WIN32_FIND_DATA fd;
        HANDLE find = FindFirstFile(path, &fd);
        BOOL keepGoing = TRUE;
        DWORD count = 0;
        if (find != INVALID_HANDLE_VALUE) {
            do {
                if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
                    continue;
                if (strlen(fd.cFileName) > 200) {  // ours are 13 characters
                    import->failed++;
                    continue;
                }
                sprintf_s(path, "%s\\%s", import->usbDir, fd.cFileName);
                DWORD size;
                unsigned char* header = ReadWholeFile(path, FMIM_HEADER_SIZE, &size);
                if (!header || !IsSongHeader(header, size)) {
                    free(header);
                    import->failed++;
                    continue;
                }
                strcpy_s(batch[count].name, fd.cFileName);
                memcpy(batch[count].header, header, FMIM_HEADER_SIZE);
                free(header);
                if (++count == HM_BATCH) {
                    keepGoing = AddBatch(*import, batch, count);
                    count = 0;
                }
            } while (keepGoing && FindNextFile(find, &fd));
            FindClose(find);
        }
        if (keepGoing && count)
            AddBatch(*import, batch, count);
    }

    if (import->error != RESULT_OK) {
        DeleteCopiedSongs(*import);
        Notify(L"HddMusic: %S. Nothing was changed.", ResultText(import->error));
        goto done;
    }
    if (import->added == 0) {
        Notify(L"HddMusic: nothing new to add (%u already in the library, %u failed).",
               import->skipped, import->failed);
        goto done;
    }

    {
        // Write the new index in full first, back up the old one, then swap.
        char path[MAX_PATH], backup[MAX_PATH], fresh[MAX_PATH];
        sprintf_s(path, "%s\\mindex.xmi", import->libraryDir);
        sprintf_s(backup, "%s\\mindex.xmi.bak", import->libraryDir);
        sprintf_s(fresh, "%s\\mindex.xmi.new", import->libraryDir);
        if (!WriteWholeFile(fresh, import->library.Data(), import->library.Size())) {
            DeleteCopiedSongs(*import);
            Notify(L"HddMusic: can't write to the hard drive (is it full?). Nothing was changed.");
            goto done;
        }
        if (existed && !CopyOneFile(path, backup, import->copyBuffer)) {
            DeleteFile(fresh);
            DeleteCopiedSongs(*import);
            Notify(L"HddMusic: can't back up the music library. Nothing was changed.");
            goto done;
        }
        if (!CopyOneFile(fresh, path, import->copyBuffer)) {
            // A failed copy deletes the half-written mindex.xmi; put the old one back.
            BOOL restored = !existed || CopyOneFile(backup, path, import->copyBuffer);
            DeleteFile(fresh);
            DeleteCopiedSongs(*import);
            if (restored)
                Notify(L"HddMusic: can't save the music library (is the Music Player open?). "
                       L"Nothing was changed.");
            else
                Notify(L"HddMusic: SAVING FAILED. Copy mindex\\mindex.xmi.bak to mindex.xmi by FTP.");
            goto done;
        }
        DeleteFile(fresh);
    }
    if (import->failed)
        Notify(L"HddMusic: added %u songs, %u FAILED. Restart the console to see them.",
               import->added, import->failed);
    else
        Notify(L"HddMusic: added %u songs (%u already there). Restart the console to see them.",
               import->added, import->skipped);

done:
    free(batch);
    free(import->copyBuffer);
    free(import->copied);
    delete import;
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

static DWORD WINAPI MainThread(LPVOID)
{
    Sleep(HM_BOOT_DELAY_MS);

    ResolveXamImports();
    Mount("HmHdd:", "\\Device\\Harddisk0\\Partition1");
    Mount("HmUsb0:", "\\Device\\Mass0");
    Mount("HmUsb1:", "\\Device\\Mass1");
    Mount("HmUsb2:", "\\Device\\Mass2");

    WORD previous[XUSER_MAX_COUNT] = { 0 };
    while (g_running) {
        for (DWORD pad = 0; pad < XUSER_MAX_COUNT; pad++) {
            XINPUT_STATE state;
            WORD buttons = 0;
            if (XInputGetState(pad, &state) == ERROR_SUCCESS)
                buttons = state.Gamepad.wButtons;
            WORD pressed = buttons & ~previous[pad];
            previous[pad] = buttons;
            if ((buttons & HM_COMBO_HOLD) && (pressed & HM_COMBO_PRESS))
                RipFromUsb();
        }
        Sleep(HM_POLL_MS);
    }
    return 0;
}

BOOL APIENTRY DllMain(HANDLE module, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_ATTACH) {
        // A system thread survives title switches. Same flags as the hiddriver360 plugin.
        HANDLE thread = NULL;
        DWORD threadId;
        ExCreateThread(&thread, 0, &threadId, (VOID*)XapiThreadStartup,
                       (LPTHREAD_START_ROUTINE)MainThread, NULL,
                       EX_CREATE_FLAG_SUSPENDED | EX_CREATE_FLAG_SYSTEM | 0x18000424);
        if (thread) {
            XSetThreadProcessor(thread, 4);
            SetThreadPriority(thread, THREAD_PRIORITY_NORMAL);
            ResumeThread(thread);
            CloseHandle(thread);
        }
    } else if (reason == DLL_PROCESS_DETACH) {
        g_running = FALSE;
    }
    return TRUE;
}
