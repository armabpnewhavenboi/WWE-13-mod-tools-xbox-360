// HddMusic - DashLaunch plugin that plays music from the internal hard drive
// while you play games, without the USB stick plugged in.
//
//   Hdd:\Music\...       songs it plays (.mp3 / .wma, subfolders are fine)
//   Usb:\Music\...       copied to Hdd:\Music by the "import" combo
//
// Controls (any controller, hold BACK and press):
//   D-pad Up      play / pause
//   D-pad Right   next song
//   D-pad Left    previous song
//   D-pad Down    stop
//   X             import music from USB, then you can remove the stick
//
// Songs are played through the system media player (XMP) as a title
// playlist, the same mechanism games use for their own soundtracks. Because a
// title playlist belongs to the running game, the plugin rebuilds it after
// each game launch and carries on playing if music was on.
//
// Build as an Xbox 360 DLL with the XDK (see README.md) and load it with
// DashLaunch (plugin1 = Hdd:\Plugins\HddMusic.xex in launch.ini).

#include <xtl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

#define HM_SHUFFLE          0       // 1 = shuffle, 0 = play in folder order
#define HM_COMBO_HOLD       XINPUT_GAMEPAD_BACK
#define HM_MAX_SONGS        2000
#define HM_MAX_DEPTH        4       // how many subfolder levels to scan
#define HM_BOOT_DELAY_MS    10000   // let the dashboard finish loading first
#define HM_RESUME_DELAY_MS  6000    // wait after a game launch before resuming
#define HM_POLL_MS          50

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

#define EX_CREATE_FLAG_SYSTEM       0x2
#define XAM_ORD_GETCURRENTTITLEID   463
#define XAM_ORD_XNOTIFYQUEUEUI      656

typedef DWORD (*PFN_XAMGETCURRENTTITLEID)(VOID);
typedef VOID  (*PFN_XNOTIFYQUEUEUI)(DWORD type, DWORD userIndex, ULONGLONG areas,
                                    LPCWSTR text, PVOID context);

static PFN_XAMGETCURRENTTITLEID pXamGetCurrentTitleId = NULL;
static PFN_XNOTIFYQUEUEUI       pXNotifyQueueUI       = NULL;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

struct Song {
    WCHAR*         path;    // HmHdd:\Music\...  (what XMP opens)
    WCHAR*         title;   // file name without extension
    WCHAR*         folder;  // parent folder name, shown as the album
    XMP_SONGFORMAT format;
};

static volatile BOOL g_running = TRUE;
static Song*         g_songs = NULL;
static DWORD         g_songCount = 0;
static XMP_HANDLE    g_playlist = NULL;
static XMP_HANDLE*   g_songHandles = NULL;
static BOOL          g_wantPlaying = FALSE;  // music was on when the game changed
static DWORD         g_resumeAt = 0;
static DWORD         g_titleId = 0;

static const char* HDD_MUSIC = "HmHdd:\\Music";
static const char* USB_MUSIC[] = { "HmUsb0:\\Music", "HmUsb1:\\Music", "HmUsb2:\\Music" };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static void Notify(LPCWSTR fmt, ...)
{
    if (!pXNotifyQueueUI)
        return;
    WCHAR text[128];
    va_list args;
    va_start(args, fmt);
    _vsnwprintf_s(text, _countof(text), _TRUNCATE, fmt, args);
    va_end(args);
    pXNotifyQueueUI(14, 0, 2, text, NULL);  // 14 = custom text, 2 = high priority
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
    XexGetProcedureAddress(xam, XAM_ORD_GETCURRENTTITLEID, (PVOID*)&pXamGetCurrentTitleId);
    XexGetProcedureAddress(xam, XAM_ORD_XNOTIFYQUEUEUI, (PVOID*)&pXNotifyQueueUI);
}

static DWORD CurrentTitleId()
{
    return pXamGetCurrentTitleId ? pXamGetCurrentTitleId() : 0;
}

// FAT names on the 360 are single-byte; widen them byte for byte.
static WCHAR* Widen(const char* s, size_t len)
{
    WCHAR* w = (WCHAR*)malloc((len + 1) * sizeof(WCHAR));
    if (!w)
        return NULL;
    for (size_t i = 0; i < len; i++)
        w[i] = (WCHAR)(unsigned char)s[i];
    w[len] = 0;
    return w;
}

static BOOL SongFormat(const char* name, XMP_SONGFORMAT* format)
{
    const char* dot = strrchr(name, '.');
    if (!dot)
        return FALSE;
    if (_stricmp(dot, ".mp3") == 0) { *format = XMP_SONGFORMAT_MP3; return TRUE; }
    if (_stricmp(dot, ".wma") == 0) { *format = XMP_SONGFORMAT_WMA; return TRUE; }
    return FALSE;
}

static BOOL IsDots(const char* name)
{
    return strcmp(name, ".") == 0 || strcmp(name, "..") == 0;
}

// ---------------------------------------------------------------------------
// Song list
// ---------------------------------------------------------------------------

static void FreeSongs()
{
    for (DWORD i = 0; i < g_songCount; i++) {
        free(g_songs[i].path);
        free(g_songs[i].title);
        free(g_songs[i].folder);
    }
    free(g_songs);
    g_songs = NULL;
    g_songCount = 0;
}

static void AddSong(const char* fullPath, const char* fileName, const char* folder,
                    XMP_SONGFORMAT format)
{
    if (g_songCount >= HM_MAX_SONGS)
        return;
    if (g_songCount == 0)
        g_songs = (Song*)malloc(HM_MAX_SONGS * sizeof(Song));
    if (!g_songs)
        return;

    Song& s = g_songs[g_songCount];
    s.path = Widen(fullPath, strlen(fullPath));
    s.title = Widen(fileName, strrchr(fileName, '.') - fileName);
    s.folder = Widen(folder, strlen(folder));
    s.format = format;
    if (!s.path || !s.title || !s.folder) {
        free(s.path); free(s.title); free(s.folder);
        return;
    }
    g_songCount++;
}

static void ScanDir(const char* dir, const char* folderName, int depth)
{
    char pattern[MAX_PATH];
    sprintf_s(pattern, "%s\\*", dir);

    WIN32_FIND_DATA fd;
    HANDLE find = FindFirstFile(pattern, &fd);
    if (find == INVALID_HANDLE_VALUE)
        return;
    do {
        if (IsDots(fd.cFileName))
            continue;
        char full[MAX_PATH];
        sprintf_s(full, "%s\\%s", dir, fd.cFileName);

        XMP_SONGFORMAT format;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (depth < HM_MAX_DEPTH)
                ScanDir(full, fd.cFileName, depth + 1);
        } else if (SongFormat(fd.cFileName, &format)) {
            AddSong(full, fd.cFileName, folderName, format);
        }
    } while (FindNextFile(find, &fd));
    FindClose(find);
}

static int CompareSongs(const void* a, const void* b)
{
    return _wcsicmp(((const Song*)a)->path, ((const Song*)b)->path);
}

static void ScanSongs()
{
    FreeSongs();
    ScanDir(HDD_MUSIC, "Music", 0);
    if (g_songCount > 1)
        qsort(g_songs, g_songCount, sizeof(Song), CompareSongs);
}

// ---------------------------------------------------------------------------
// Playback
// ---------------------------------------------------------------------------

static void FreePlaylist()
{
    if (g_playlist)
        XMPDeleteTitlePlaylist(g_playlist);  // fails harmlessly if the game that owned it exited
    g_playlist = NULL;
    free(g_songHandles);
    g_songHandles = NULL;
}

static BOOL BuildPlaylist()
{
    FreePlaylist();
    if (g_songCount == 0)
        return FALSE;

    XMP_SONGDESCRIPTOR* desc = (XMP_SONGDESCRIPTOR*)calloc(g_songCount, sizeof(XMP_SONGDESCRIPTOR));
    g_songHandles = (XMP_HANDLE*)calloc(g_songCount, sizeof(XMP_HANDLE));
    if (!desc || !g_songHandles) {
        free(desc);
        FreePlaylist();
        return FALSE;
    }
    for (DWORD i = 0; i < g_songCount; i++) {
        desc[i].pwszFilePath = g_songs[i].path;
        desc[i].pwszTitle = g_songs[i].title;
        desc[i].pwszArtist = L"";
        desc[i].pwszAlbum = g_songs[i].folder;
        desc[i].pwszAlbumArtist = L"";
        desc[i].pwszGenre = L"";
        desc[i].dwTrackNumber = i + 1;
        desc[i].dwDuration = 0;
        desc[i].eSongFormat = g_songs[i].format;
    }
    DWORD result = XMPCreateTitlePlaylist(desc, g_songCount, XMP_CREATEPLAYLISTFLAG_NONE,
                                          L"HddMusic", g_songHandles, &g_playlist);
    free(desc);
    if (result != ERROR_SUCCESS) {
        g_playlist = NULL;
        FreePlaylist();
        return FALSE;
    }
    return TRUE;
}

static void StartPlayback()
{
    if (g_songCount == 0)
        ScanSongs();
    if (g_songCount == 0) {
        Notify(L"HddMusic: no .mp3/.wma files in Hdd:\\Music");
        return;
    }
    if (!g_playlist && !BuildPlaylist()) {
        Notify(L"HddMusic: couldn't create the playlist");
        return;
    }
    XMPSetPlaybackBehavior(HM_SHUFFLE ? XMP_PLAYBACKMODE_SHUFFLE : XMP_PLAYBACKMODE_INORDER,
                           XMP_REPEATMODE_PLAYLIST, 0, NULL);
    DWORD first = HM_SHUFFLE ? (GetTickCount() % g_songCount) : 0;
    if (XMPPlayTitlePlaylist(g_playlist, g_songHandles[first], NULL) == ERROR_SUCCESS) {
        g_wantPlaying = TRUE;
        Notify(L"HddMusic: playing %u songs", g_songCount);
    } else {
        // The game may have locked background music, or the handle went stale.
        FreePlaylist();
        Notify(L"HddMusic: couldn't start (this game may block custom music)");
    }
}

static void PlayPause()
{
    XMP_STATE state = XMP_STATE_IDLE;
    XMPGetStatus(&state);
    if (state == XMP_STATE_PLAYING) {
        XMPPause(NULL);
        g_wantPlaying = FALSE;
    } else if (state == XMP_STATE_PAUSED && g_playlist) {
        XMPContinue(NULL);
        g_wantPlaying = TRUE;
    } else {
        StartPlayback();
    }
}

static void Stop()
{
    XMPStop(NULL);
    g_wantPlaying = FALSE;
    g_resumeAt = 0;
}

// ---------------------------------------------------------------------------
// Import from USB
// ---------------------------------------------------------------------------

static BYTE* g_copyBuffer = NULL;
#define HM_COPY_BUFFER_SIZE (256 * 1024)

static BOOL FileSize(const char* path, DWORD* size)
{
    WIN32_FIND_DATA fd;
    HANDLE find = FindFirstFile(path, &fd);
    if (find == INVALID_HANDLE_VALUE)
        return FALSE;
    FindClose(find);
    *size = fd.nFileSizeLow;
    return TRUE;
}

static BOOL CopyOneFile(const char* src, const char* dst)
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
        if (!ReadFile(in, g_copyBuffer, HM_COPY_BUFFER_SIZE, &read, NULL)) { ok = FALSE; break; }
        if (read == 0)
            break;
        if (!WriteFile(out, g_copyBuffer, read, &written, NULL) || written != read) { ok = FALSE; break; }
    }
    CloseHandle(out);
    CloseHandle(in);
    if (!ok)
        DeleteFile(dst);  // don't leave half a song behind
    return ok;
}

static void CopyTree(const char* src, const char* dst, int depth,
                     DWORD* copied, DWORD* skipped, DWORD* failed)
{
    CreateDirectory(dst, NULL);

    char pattern[MAX_PATH];
    sprintf_s(pattern, "%s\\*", src);
    WIN32_FIND_DATA fd;
    HANDLE find = FindFirstFile(pattern, &fd);
    if (find == INVALID_HANDLE_VALUE)
        return;
    do {
        if (IsDots(fd.cFileName))
            continue;
        char from[MAX_PATH], to[MAX_PATH];
        sprintf_s(from, "%s\\%s", src, fd.cFileName);
        sprintf_s(to, "%s\\%s", dst, fd.cFileName);

        XMP_SONGFORMAT format;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (depth < HM_MAX_DEPTH)
                CopyTree(from, to, depth + 1, copied, skipped, failed);
        } else if (SongFormat(fd.cFileName, &format)) {
            DWORD existing;
            if (FileSize(to, &existing) && existing == fd.nFileSizeLow)
                (*skipped)++;
            else if (CopyOneFile(from, to))
                (*copied)++;
            else
                (*failed)++;
        }
    } while (FindNextFile(find, &fd));
    FindClose(find);
}

static void ImportFromUsb()
{
    if (!g_copyBuffer)
        g_copyBuffer = (BYTE*)malloc(HM_COPY_BUFFER_SIZE);
    if (!g_copyBuffer)
        return;

    DWORD copied = 0, skipped = 0, failed = 0;
    BOOL found = FALSE;
    for (int i = 0; i < _countof(USB_MUSIC); i++) {
        if (GetFileAttributes(USB_MUSIC[i]) == INVALID_FILE_ATTRIBUTES)
            continue;
        if (!found)
            Notify(L"HddMusic: copying music from USB...");
        found = TRUE;
        CopyTree(USB_MUSIC[i], HDD_MUSIC, 0, &copied, &skipped, &failed);
    }
    free(g_copyBuffer);
    g_copyBuffer = NULL;

    if (!found) {
        Notify(L"HddMusic: no Music folder found on the USB stick");
        return;
    }
    if (failed)
        Notify(L"HddMusic: copied %u, %u already there, %u FAILED", copied, skipped, failed);
    else
        Notify(L"HddMusic: copied %u songs (%u already there). You can remove the USB.",
               copied, skipped);

    ScanSongs();
    if (!g_wantPlaying)
        FreePlaylist();  // pick up the new songs on the next play
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

static void WatchTitle()
{
    DWORD title = CurrentTitleId();
    if (title == g_titleId)
        return;
    g_titleId = title;

    // The old playlist belonged to the previous game.
    g_playlist = NULL;
    free(g_songHandles);
    g_songHandles = NULL;
    if (g_wantPlaying)
        g_resumeAt = GetTickCount() + HM_RESUME_DELAY_MS;
}

static void HandleButtons(WORD pressed)
{
    if (pressed & XINPUT_GAMEPAD_DPAD_UP)    PlayPause();
    if (pressed & XINPUT_GAMEPAD_DPAD_DOWN)  Stop();
    if (pressed & XINPUT_GAMEPAD_DPAD_RIGHT) { XMPNext(NULL); }
    if (pressed & XINPUT_GAMEPAD_DPAD_LEFT)  { XMPPrevious(NULL); }
    if (pressed & XINPUT_GAMEPAD_X)          ImportFromUsb();
}

static DWORD WINAPI MainThread(LPVOID)
{
    Sleep(HM_BOOT_DELAY_MS);

    ResolveXamImports();
    Mount("HmHdd:", "\\Device\\Harddisk0\\Partition1");
    Mount("HmUsb0:", "\\Device\\Mass0");
    Mount("HmUsb1:", "\\Device\\Mass1");
    Mount("HmUsb2:", "\\Device\\Mass2");
    CreateDirectory(HDD_MUSIC, NULL);
    g_titleId = CurrentTitleId();

    WORD previous[XUSER_MAX_COUNT] = { 0 };
    while (g_running) {
        WatchTitle();

        if (g_resumeAt && (LONG)(GetTickCount() - g_resumeAt) >= 0) {
            g_resumeAt = 0;
            StartPlayback();
        }

        for (DWORD pad = 0; pad < XUSER_MAX_COUNT; pad++) {
            XINPUT_STATE state;
            WORD buttons = 0;
            if (XInputGetState(pad, &state) == ERROR_SUCCESS)
                buttons = state.Gamepad.wButtons;
            WORD pressed = buttons & ~previous[pad];
            previous[pad] = buttons;
            if ((buttons & HM_COMBO_HOLD) && (pressed & ~HM_COMBO_HOLD))
                HandleButtons(pressed);
        }
        Sleep(HM_POLL_MS);
    }
    return 0;
}

BOOL APIENTRY DllMain(HANDLE module, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_ATTACH) {
        HANDLE thread;
        DWORD threadId;
        ExCreateThread(&thread, 0, &threadId, (VOID*)XapiThreadStartup,
                       (LPTHREAD_START_ROUTINE)MainThread, NULL,
                       EX_CREATE_FLAG_SYSTEM | CREATE_SUSPENDED);
        XSetThreadProcessor(thread, 4);
        ResumeThread(thread);
        CloseHandle(thread);
    } else if (reason == DLL_PROCESS_DETACH) {
        g_running = FALSE;
    }
    return TRUE;
}
