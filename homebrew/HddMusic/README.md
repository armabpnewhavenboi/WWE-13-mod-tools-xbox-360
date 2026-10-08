# HddMusic – rip your MP3s into the Xbox 360 music library

The Xbox 360 only lets you put music on its hard drive by ripping an audio CD.
There's no **Copy to Hard Drive** for songs on a USB stick. HddMusic adds
your own songs to the hard drive the same way a CD rip would:

1. **On your PC**, `x360music` converts your MP3s to the format the console's
   CD ripper uses: WMA at 192 kbps, 44.1 kHz, stereo, with the song details
   in the console's own song-file header. It puts them on a USB stick.
2. **On the Xbox**, the HddMusic DashLaunch plugin adds them to the
   hard-drive music library (`Hdd:\mindex`), the same place ripped CDs go.

Afterwards the songs work like a ripped CD. They show up in **Music Player >
Hard Drive** with their artists, albums and genres. You can play them during
any game that allows custom soundtracks (Guide > Media > Music Player), and
the USB stick can stay in the drawer.

> **Not yet tested on a console.** The library format comes from community
> reverse engineering: the Free60 wiki and the Xbox-360-Mindex project, whose
> generated libraries have been shown working on a console (with one report of
> songs skipping, see below). The library code is tested here
> against that project's output, byte for byte. The plugin's Xbox-specific
> parts (threads, notifications, file access) have not been run yet. The
> plugin backs up the library before every change, so it can always be
> restored ([see below](#undo)).

## 1. Get the converter

Pick one:

- **Windows .exe**: download `HddMusic-Converter.exe` from this repository's
  **Actions** tab (latest *Test and build Windows exe* run → artifact) or from
  a release.
- **Python 3.8+**: double-click `HddMusic-Converter.bat` in the repository
  folder, or run `python -m x360music` from the repository folder.

The zip also has `x360music.exe`, the command-line version used in the
examples below (with Python, type `python -m x360music` instead of
`x360music`).

Either way you also need **ffmpeg**. Download a Windows build from
<https://ffmpeg.org/download.html>, and put `ffmpeg.exe` and `ffprobe.exe` in
the same folder as the converter (or on your PATH).

## 2. Convert your music

**Window version:** open `HddMusic-Converter.exe`. Pick your music folder and
the USB stick, then press **CONVERT**.

**Command line:**

```bat
x360music convert "D:\Music" -o E:\
```

- Subfolders are searched. MP3, WMA, M4A/AAC, FLAC, OGG, Opus and WAV all
  work.
- Title, artist, album, album artist, genre and track number come from the
  file's tags. Songs without tags use the file name (`07 - Song.mp3` becomes
  track 7, "Song") and the name of the folder they're in as the album
  ("Unknown Album" for files directly in the folder you picked).
- The songs go in `E:\HddMusic\` as `.fmim` files, one per song, and
  `songs.txt` lists what each file holds.
- Running it again only converts new songs. The same song in two places is
  converted once.
- If you change a song's tags and convert again, the old version stays in
  `HddMusic` too. Delete the `HddMusic` folder to start fresh.
- The library shows names up to 39 characters. Longer names are cut off on
  the console.

## 3. Build and install the plugin

1. Open `HddMusic.sln` in **Visual Studio 2010** with the Xbox 360 XDK and
   build **Release | Xbox 360**. The plugin is `Release\HddMusic.xex`.
2. Copy it to `Hdd:\Plugins\HddMusic.xex`, for example by FTP from
   Aurora/FSD.
3. Add it to a free plugin slot in DashLaunch's `launch.ini`:
   ```ini
   [Plugins]
   plugin1 = Hdd:\Plugins\HddMusic.xex
   ```
4. Reboot.

## 4. Rip from USB

1. Boot to the dashboard and plug in the USB stick. Don't open the Music
   Player yet.
2. Hold **Back** and press **X** on any controller. (The combo works anywhere,
   including in games, but the dashboard is the safe place to use it.)
3. Wait for the notification, for example *"HddMusic: added 42 songs (0
   already there). Restart the console to see them."* Big imports take a
   while, because every song is copied to the hard drive.
4. Restart the console. The songs are in **Music Player > Hard Drive**. You
   can remove the USB stick.

Running it again only adds songs that aren't in the library yet. If you
haven't ripped any CDs before, the plugin creates the library.

## What it changes

- **`Hdd:\mindex\mindex.xmi`**: the library index. Before saving it, the
  plugin copies the old one to `mindex.xmi.bak`.
- **`Hdd:\mindex\media\0000\XXXX`**: one file per new song, named after its
  library record number, exactly as for ripped CDs.

If anything goes wrong partway through, the plugin removes the song files it
copied and leaves the library as it was. The message ends with *"Nothing was
changed."*

### Undo

Using FTP or a file manager:

1. Go to `Hdd1:\mindex\`.
2. Delete `mindex.xmi` and rename `mindex.xmi.bak` to `mindex.xmi`.
3. Restart the console.

This puts the library back to how it was before the last import. If the
console had no music before your first import there is no `.bak`: delete the
whole `mindex` folder instead.

## Messages

| Message | What it means |
|---|---|
| *no HddMusic folder on the USB stick* | Run the converter first, and use the stick's top folder as the output. |
| *songs found without mindex.xmi* | There are song files but no library index, so starting a new library would overwrite them. Restore `mindex.xmi.bak` ([Undo](#undo)), or delete the `mindex` folder if you don't need those songs. |
| *SAVING FAILED* | The new library couldn't be saved and the old one couldn't be put back. Rename `mindex.xmi.bak` to `mindex.xmi` by FTP. |
| *the music library isn't in the expected format. Nothing was changed.* | Your `mindex.xmi` doesn't match the format the plugin knows. Copy `Hdd1:\mindex\mindex.xmi` to your PC, run `x360music inspect mindex.xmi --records`, and share the output so the plugin can be fixed. |
| *can't save the music library (is the Music Player open?)* | The dashboard was using the library. Restart, then press Back + X straight away at the dashboard. |
| *added N songs, M FAILED* | M song files couldn't be read or copied. Check the stick and the free space on the hard drive. |
| *the music library is full* | The library holds at most 65,536 entries (songs + albums + artists + genres). |

## If songs skip or won't play

One user of the Xbox-360-Mindex tool reported songs jumping to the next track
after a few seconds. That tool makes its WMA files with ffmpeg as well, and
the cause isn't known yet. If you see this:

- Try `x360music convert ... --bitrate 128 --force`, restore the library
  from before the import ([Undo](#undo)), and rip again. Without the undo
  the plugin skips the songs as already in the library.
- **Help pin it down:** rip one real audio CD on the console. Copy one of
  its song files (`Hdd1:\mindex\media\0000\000A` or similar) and one of ours
  to your PC, then run `x360music inspect` on both and compare. The output
  shows the song details and the WMA settings side by side.

## Without the plugin

`x360music build` does the same job on the PC:

```bat
x360music build E:\HddMusic -o out --library mindex.xmi
```

1. Copy the console's `Hdd1:\mindex\mindex.xmi` to your PC first, and pass it
   with `--library` so your existing songs are kept. Leave `--library` out
   if the console has no music.
2. FTP the resulting `out\mindex` folder to the root of the hard drive
   (`Hdd1:\`), replacing `mindex.xmi`.
3. Restart the console.

## If the plugin doesn't build

- **Kernel imports**: `ExCreateThread`, `ObCreateSymbolicLink` and
  `XexGetProcedureAddress` come from `xboxkrnl.lib` and are declared at the
  top of `HddMusic.cpp`. If your XDK headers already declare one, delete the
  duplicate.
- **Notifications**: `XNotifyQueueUI` is looked up as xam ordinal 656. If no
  notifications appear, check that number.
- **Base address**: if DashLaunch won't load the plugin alongside your other
  plugins, change `baseaddr` in `xex.xml`.
- **`INVALID_FILE_ATTRIBUTES` undeclared**: fixed. Pull the latest version.

`MusicLibrary.cpp` is plain C++ and builds with any compiler.

## How it works / tests

- `MusicLibrary.cpp` reads and writes the library index. It's a line-for-line
  port of `x360music/mindex.py`, whose docstring describes the file format.
- `tests/test_music.py` compiles `MusicLibrary.cpp` with g++ and checks that it
  writes exactly the same bytes as the Python version. The checks cover
  randomized song lists, existing libraries and damaged libraries.
- The tests also pin the Python output to the output of the Xbox-360-Mindex
  tool for the same songs.

Run the tests from the repository's top folder with:

```sh
python -m unittest discover -s tests -t .
```

Format references: the FMIM page of the [Free60 wiki](https://free60.org/System-Software/Formats/FMIM/)
and [Lyall-A/Xbox-360-Mindex](https://github.com/Lyall-A/Xbox-360-Mindex).
No code was copied from either.
