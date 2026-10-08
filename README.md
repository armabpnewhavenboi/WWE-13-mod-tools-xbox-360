# WWE '13 Save Resigner (x360resign)

A one-click batch tool that **resigns Xbox 360 saves to your own profile**. It
was built for the *Monday Night War – The Ultimate Save* pack for WWE '13
(Xbox 360), which never came with instructions, and it has been tested with
that pack on a real Xbox 360. It works on any Xbox 360 save (`CON` package):
CAWs, Universe, created arenas, championships, story designer, and saves from
other games too.

Point it at the folder where you kept the mod files and it:

1. Finds every Xbox 360 package in that folder and its subfolders, including
   ones inside `.zip` files.
2. Checks each original's hashes, so you know your copies aren't damaged.
3. Writes in **your Profile ID**, plus a Console ID and Device ID if you give
   them.
4. **Rehashes** the whole file so the Xbox accepts the changes.
5. Checks its own output, then writes a ready-to-copy `Content\…` folder for
   your USB stick, plus a `resign_report.txt` covering every file.
6. Lists any **DLC packs** it found, which you'll need too (see
   [step 5](#5-copy-to-the-usb-stick)).

Your original files are never modified.

---

## What you need

| | |
|---|---|
| **The mod files** | The folder you kept. `.zip` files are read directly. Extract `.rar` / `.7z` first (for example with 7-Zip). |
| **Your Profile ID** | 16 hex characters, usually starting with `E000…`. See [step 2](#2-find-your-profile-id). |
| **A USB stick** | Formatted by the Xbox 360 on dashboard 2.0.17349 or newer, so Windows can open it as FAT32. |

---

## Getting the program

**Windows .exe (no Python needed):** open this repository's **Actions** tab,
choose the latest successful *Test and build Windows exe* run, and download the
**WWE13-Resigner-windows** artifact. Inside are:

- `WWE13-Resigner.exe` – the window version. Double-click it.
- `x360resign.exe` – the command-line version.

If a version tag (`v1.0.0`, …) was pushed, the same zip is also attached to
**Releases**.

**From source:** install Python 3.8 or newer from python.org, then double-click
`WWE13-Resigner.bat`, or run:

```
python -m x360resign            # opens the window
python -m x360resign --help     # command-line help
```

It only uses the Python standard library, so there's nothing to `pip install`.

---

## Step by step

### 1. Back up

Copy the whole mod folder somewhere safe. The tool doesn't change originals,
but backups are cheap.

### 2. Find your Profile ID

The easiest way, which also gets your Console ID and Device ID:

1. On the Xbox, go to **System Settings → Storage**, pick any one of your own
   saves (any game, ideally WWE '13) and **copy it to the USB stick**.
2. Plug the stick into the PC. The save is at
   `USB:\Content\<YOUR PROFILE ID>\<title id>\00000001\<save file>`.
   The 16-character folder name is your Profile ID.
3. In the program, click **Read IDs from one of my saves…** and pick that save
   file. Profile ID, Console ID and Device ID are filled in for you.
   On the command line, use `--ids-from "path\to\that\save"`, or run
   `x360resign ids "path\to\that\save"` to print the IDs.

You can also type the Profile ID by hand, for example from Horizon's profile
view. Console ID and Device ID are optional; leave them blank to keep the
ones already in the saves. (The window version remembers whatever is in the
boxes, so clear them if you want that.)

### 3. Check the mod files (optional, recommended)

Click **Check mod files**, or run `x360resign verify "D:\Monday Night War"`.
Every package is listed with its name, title ID, current profile, and whether
its hashes are intact. If the originals check out, your copies aren't damaged.

### 4. Resign everything

Window version:

1. **Mod files folder** – the folder with the mod saves.
2. **Output folder** – any empty folder (for example `resigned_saves`).
3. **Profile ID** – yours. **Console ID** and **Device ID** – optional
   (filled in by *Read IDs…*).
4. Press **RESIGN ALL**.

Your entries are saved to `x360resign.ini` next to the program, so next time
you only press the button.

Each file gets one of these results:

| Result | Meaning |
|---|---|
| `RESIGNED` | Your IDs are in, the file is rehashed, and it passed the self-check. |
| `COPIED UNCHANGED` | `LIVE`/`PIRS` content (DLC). It isn't tied to a profile, so it's copied as it is. |
| `SKIPPED` | Not a save (for example a gamer profile package), or not a usable package. |
| `FAILED` | Something went wrong. The reason is in the log and in `resign_report.txt`. |

### 5. Copy to the USB stick

Copy the **`Content`** folder from the output folder to the **root** of the USB
stick and merge it with the `Content` folder that's already there. The layout
is the one the console uses:

```
Content\<your profile ID>\<title ID>\00000001\<save files>      ← your saves
Content\0000000000000000\<title ID>\...                         ← DLC and shared content
```

**Don't forget the DLC.** Many mod saves were made with WWE '13 DLC (extra
wrestlers, moves, belts). If the mod folder contained DLC packs, the log and
the report list them under *DLC packages in this pack*, and they're in the
`Content\0000000000000000` folder above. Copy that folder too. If a save needs
DLC that wasn't in the pack, you have to install that DLC yourself.

### 6. Play

Plug the stick into the Xbox, sign in with your profile and start WWE '13. When
the game asks for a storage device, choose the USB stick. You can also move the
saves to the hard drive first in **System Settings → Storage**.

---

## Command line

```
x360resign resign "D:\Monday Night War" -o "D:\resigned" --profile-id E0000123456789AB
x360resign resign "D:\Monday Night War" -o "D:\resigned" --ids-from "E:\Content\E0000123456789AB\<TitleID>\00000001\MYSAVE"
x360resign verify "D:\resigned"
x360resign info SAVEFILE --files-list
x360resign extract SAVEFILE -o extracted
x360resign ids MYSAVE
```

Useful `resign` flags:

| Flag | |
|---|---|
| `--profile-id`, `--console-id`, `--device-id` | IDs to write. Console and Device ID are optional; without them the saves keep the ones they have (unless they're remembered in `x360resign.ini`). |
| `--ids-from SAVE` | Take all the IDs from one of your own saves. |
| `--patch-embedded-ids` | Also replace the old IDs if they're stored *inside* the save data (see below). |
| `--layout flat` | Put all output files in one folder instead of the `Content\…` tree. |
| `--include-profiles` | Also process gamer-profile packages (skipped by default). |
| `--save-config` | Remember the IDs for next time. |

Exit codes: `0` = all good, `1` = some files failed or no packages were found,
`2` = bad arguments and the like.

---

## Troubleshooting

**"This save data can't be used due to missing or damaged downloadable content"**
The save itself loaded fine. It was made with DLC that isn't on your console
yet. Copy the DLC packs from the mod folder (see [step 5](#5-copy-to-the-usb-stick)).
If you bought WWE '13 DLC yourself, you can re-download it from
**Settings → Account → Download History** while signed in to Xbox Live. Don't
let the game overwrite the save while the DLC is missing.

**The save doesn't show up**
Make sure the Profile ID is the one for the profile you're signed in with. It
must match the `Content\<profile>` folder that your own saves are in.

**The save shows up but the game rejects it**
Some games also store the owner's ID *inside* the save data. The tool always
scans for this, and the report says when it finds one, for example
`old profile ID found inside … at offset 0x…`. Run again with **Also patch old
IDs inside the save data** ticked (`--patch-embedded-ids`).

**The Xbox dashboard says the save is corrupted**
Open `resign_report.txt`. Every save should say `RESIGNED` (DLC says
`COPIED UNCHANGED`) and `output : header hash OK, hash tree OK`. The tool
doesn't re-sign saves; on the console it was tested on, that wasn't needed. If
your console says the save is corrupted anyway, open it in a resigner like
Horizon and resign it there.

**A file shows `SKIPPED – this is a gamer profile`**
Some packs include the author's gamer profile. You don't need it, so it's left
out. Use `--include-profiles` if you really want it processed.

---

## How it works

An Xbox 360 save is an **STFS package**. The fields the tool changes:

| Offset | Size | Field |
|---|---|---|
| `0x032C` | `0x14` | SHA-1 of the header from `0x344` to the end of the header block |
| `0x036C` | `5` | Console ID (only if you give one) |
| `0x0371` | `8` | Profile ID |
| `0x0381` | `0x14` | SHA-1 of the top-level hash table (root of the hash tree) |
| `0x03FD` | `0x14` | Device ID (only if you give one) |

Everything before `0x22C` (the original signature block) is left exactly as it
was.

Under the header, every 4 KB data block is hashed into level-0 hash tables. Up
to two more levels of tables hash those tables. In read/write packages each
table has two copies, and a status bit in the parent says which copy is active.
The rehash recomputes every hash from the bottom up, writes only into the
*active* copies, and leaves everything else byte-for-byte the same.

Source layout:

```
x360resign/stfs.py      STFS parsing, hash tree, rehash, verify, extract
x360resign/resigner.py  the batch pipeline and the report
x360resign/ids.py       parsing and checking profile / console / device IDs
x360resign/cli.py       command line;  x360resign/gui.py  window version
tests/                  synthetic packages, independent layout model
```

### Tests

```
python -m unittest discover -s tests -t . -v
```

The tests build STFS packages from scratch: all three hash-tree levels (one is
about 120 MB), both table formats, random active table copies, and scattered
block chains. The block layout in the tests comes from a separate, sequential
model of the format, so it cross-checks the tool's own address math. The tests
also cover tampering, truncation, embedded IDs, zips, DLC, profiles and the
whole CLI.

Set `X360RESIGN_SLOW=0` to skip the 120 MB test.

### References

The format work follows the public documentation and open-source tools of the
Xbox 360 scene: the Free60 STFS notes, Velocity / XboxInternals (hetelek) and
stfschk (emoose).

---

## Extra: your own music on the Xbox hard drive

[`homebrew/HddMusic`](homebrew/HddMusic/README.md) puts your MP3s in the
console's hard-drive music library, the same place ripped CDs go. They then
play from **Music Player > Hard Drive** and, through the Guide, during any
game that allows custom soundtracks, with no USB stick needed.

- **`x360music`** (in this repository; `HddMusic-Converter.exe` on Windows)
  converts MP3s to the WMA format the console's CD ripper uses and puts them
  on a USB stick. It needs ffmpeg.
- **The HddMusic DashLaunch plugin** (Visual Studio 2010 + XDK) adds those
  songs to the library when you hold **Back** and press **X**, like ripping a
  CD.

See [homebrew/HddMusic/README.md](homebrew/HddMusic/README.md) for the steps.
