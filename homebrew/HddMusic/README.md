# HddMusic – music from the hard drive while you play

A DashLaunch plugin for RGH/JTAG Xbox 360 consoles. It copies your music from a
USB stick to the internal hard drive once, then plays it in the background
while you play games. The USB stick doesn't need to stay plugged in.

It's for dashboards where the Music Player has no **Copy to Hard Drive**
option.

> **Untested build.** This was written without a console to test on. Expect to
> fix a few compile errors against your XDK's headers (see
> [If it doesn't build](#if-it-doesnt-build)).

## Controls

Hold **Back** on any controller and press:

| Button        | Action |
|---------------|--------|
| D-pad **Up**    | Play / pause |
| D-pad **Right** | Next song |
| D-pad **Left**  | Previous song |
| D-pad **Down**  | Stop |
| **X**           | Copy music from the USB stick to the hard drive |

A notification shows what happened. If music is playing when you launch a
game, it starts again about 6 seconds after the game loads.

## Building (Visual Studio 2010 + XDK)

1. Open `HddMusic.sln` in Visual Studio 2010 with the Xbox 360 XDK installed.
2. Pick **Release | Xbox 360** and build.
3. The plugin is `Release\HddMusic.xex`.

The project is an Xbox 360 **DLL**, so it doesn't deploy to a dev kit. `xex.xml`
marks it as a system DLL (`<sysdll/>`), which DashLaunch needs to load it as a
plugin.

## Installing

1. Copy `HddMusic.xex` to `Hdd:\Plugins\HddMusic.xex` (by FTP from Aurora/FSD,
   or with a file manager).
2. In DashLaunch's `launch.ini`, under `[Plugins]`, add it to a free slot:
   ```ini
   [Plugins]
   plugin1 = Hdd:\Plugins\HddMusic.xex
   ```
3. Reboot the console.

## Adding music

**From USB (no PC needed):**

1. On your PC, put your songs in a folder called `Music` at the root of the USB
   stick. Subfolders are fine (for example `Music\Artist\Album\01 Song.mp3`).
2. Plug the stick into the 360 and hold **Back + X**.
3. Wait for *"copied N songs … You can remove the USB."*, then unplug it.

Running the import again copies only new songs.

**By FTP:** copy files straight into `Hdd:\Music\` (create it if needed). The
plugin rescans when you press play.

File requirements:

- `.mp3` or `.wma` with no DRM (`.m4a`/AAC isn't supported here).
- Use plain English letters and numbers in file and folder names. Accented or
  non-Latin characters may stop a song from playing.
- Up to 2000 songs, nested up to 4 folders deep.

## Things to know

- **Some games block custom music.** If a game turns off custom soundtracks,
  you'll see *"couldn't start (this game may block custom music)"*. It works in
  most retail games. WWE '13 may still use its own audio for entrances.
- Music starts in folder order. To shuffle, set `HM_SHUFFLE` to `1` at the top
  of `HddMusic.cpp` and rebuild.
- If you stop music from the Guide instead of with Back + D-pad Down, the
  plugin still thinks it's on and starts it again at the next game launch.
  Stop it with the combo instead.
- To use a different button combo, change `HM_COMBO_HOLD` and
  `HandleButtons()` in `HddMusic.cpp`.

## If it doesn't build

These parts of the code are most likely to need adjusting:

- **XMP functions** (`XMPCreateTitlePlaylist`, `XMPPlayTitlePlaylist`,
  `XMPSetPlaybackBehavior`). If the compiler rejects an argument, compare the
  call with the declaration in your XDK's `xbox.h` / `xmp.h`.
  `XMP_SONGFORMAT_MP3` must exist in your XDK. If it doesn't, convert your
  songs to `.wma` and remove the `.mp3` line in `SongFormat()`.
- **Kernel imports** (`ExCreateThread`, `ObCreateSymbolicLink`,
  `XexGetProcedureAddress`). These come from `xboxkrnl.lib` and are declared at
  the top of the file. If one is already declared in your headers, delete the
  duplicate declaration.
- **XAM ordinals.** `XamGetCurrentTitleId` (463) and `XNotifyQueueUI` (656) are
  looked up by ordinal at runtime. If notifications don't appear, check these
  numbers.
- **Base address.** If DashLaunch fails to load the plugin alongside other
  plugins, change `baseaddr` in `xex.xml`.
