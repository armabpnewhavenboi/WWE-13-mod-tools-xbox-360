# WWE '13 Save Resigner (x360resign)

A one-click batch tool that **rehashes and resigns Xbox 360 saves to your own
profile and console**. It was built for the *Monday Night War – The Ultimate
Save* pack for WWE '13 (Xbox 360), which never came with instructions. It works
on any Xbox 360 save (`CON` package): CAWs, Universe, created arenas,
championships, story designer, and saves from other games too.

Point it at the folder where you kept the mod files and it:

1. Finds every Xbox 360 package in that folder and its subfolders, including
   ones inside `.zip` files.
2. Checks each original (hash tree and signature), so you know your copies
   aren't damaged.
3. Writes in **your Profile ID**, **Console ID** and (optionally) **Device ID**.
4. **Rehashes** the whole STFS hash tree and the header hash.
5. **Resigns** each package with your console's KeyVault (`KV.bin`).
6. Checks its own output, then writes a ready-to-copy `Content\…` folder for
   your USB stick, plus a `resign_report.txt` covering every file.

Your original files are never modified.

---

## What you need

| | |
|---|---|
| **The mod files** | The folder you kept. `.zip` files are read directly. Extract `.rar` / `.7z` first (for example with 7-Zip). |
| **Your Profile ID** | 16 hex characters, usually starting with `E000…`. See [step 2](#2-find-your-profile-id). |
| **Your KV.bin** | Your console's KeyVault, used to sign the saves. See [step 3](#3-get-your-kvbin). |
| **A USB stick** | Formatted by the Xbox 360 on dashboard 2.0.17349 or newer, so Windows can open it as FAT32. |

### About the KeyVault

An Xbox 360 only loads a save whose signature was made with a **console's
private key**. That key lives in the console's KeyVault, and a profile ID or
console ID alone isn't enough to make a valid signature. So:

| Your console | What you get |
|---|---|
| **RGH / JTAG** (you can dump your NAND / KV) | Everything in one go: IDs, rehash, resign. Load the saves and play. |
| **Unmodded retail** (no KV) | The tool still swaps in your IDs, rehashes, sorts the files into the right folders and flags problems. Untick **Sign with KV** (or pass `--no-sign` on the command line). The last step, the signature, then has to be done by a resigner that has its own KV (for example Horizon's *Rehash & Resign*). Until that's done, a retail console rejects the saves. |

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

The easiest way, which also gets the right Device ID:

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
view.

### 3. Get your KV.bin

If your console is RGH/JTAG, you made a NAND dump when it was modded. The
KeyVault comes from that dump (tools like J-Runner extract it as `KV.bin`).
The tool accepts:

- a decrypted `KV.bin`: 16 384 bytes (`0x4000`) or 16 368 bytes (`0x3FF0`)
- an **encrypted** KV taken straight from the NAND, plus your **CPU key**
  (32 hex characters). It's decrypted in memory and never written to disk.

Press **Check KV** (or run `x360resign kvinfo KV.bin`) to confirm it loads and
to see its Console ID, console type and manufacture date.

> ⚠️ **Never share or upload your KV.bin or CPU key.** They're unique to your
> console. This repo's `.gitignore` already excludes `*.bin`, `KV*` and the
> settings file, so they don't get committed by accident.

### 4. Check the mod files (optional, recommended)

Click **Check mod files**, or run `x360resign verify "D:\Monday Night War"`.
Every package is listed with its name, title ID, current profile, and whether
its hashes and signature are intact. If the originals check out, your copies
aren't damaged.

### 5. Resign everything

Window version:

1. **Mod files folder** – the folder with the mod saves.
2. **Output folder** – any empty folder (for example `resigned_saves`).
3. **Profile ID** – yours. **Console ID** – leave blank to use the KV's.
   **Device ID** – optional (filled in by *Read IDs…*).
4. **KeyVault** – your `KV.bin`, plus the CPU key only if the KV is encrypted.
5. Press **RESIGN ALL**.

Your entries are saved to `x360resign.ini` next to the program, so next time
you only press the button.

Each file gets one of these results:

| Result | Meaning |
|---|---|
| `RESIGNED` | IDs swapped, rehashed, signed, and passed the self-check. |
| `REHASHED, NOT SIGNED` | No KV was used. IDs and hashes are fixed, but it still needs signing. |
| `COPIED UNCHANGED` | `LIVE`/`PIRS` content (DLC). It's signed by Microsoft, isn't tied to a profile, and can't be resigned. |
| `SKIPPED` | Not a save (for example a gamer profile package), or not a usable package. |
| `FAILED` | Something went wrong. The reason is in the log and in `resign_report.txt`. |

### 6. Copy to the USB stick

Copy the **`Content`** folder from the output folder to the **root** of the USB
stick and merge it with the `Content` folder that's already there. The layout
is the one the console uses:

```
Content\<your profile ID>\<title ID>\00000001\<save files>      ← saves
Content\0000000000000000\<title ID>\...                         ← shared / DLC content
```

### 7. Play

Plug the stick into the Xbox, sign in with your profile and start WWE '13. When
the game asks for a storage device, choose the USB stick. You can also move the
saves to the hard drive first in **System Settings → Storage**.

---

## Command line

```
x360resign resign "D:\Monday Night War" -o "D:\resigned" --profile-id E0000123456789AB --kv "D:\keys\KV.bin"
x360resign resign "D:\Monday Night War" -o "D:\resigned" --ids-from "E:\Content\E0000123456789AB\<TitleID>\00000001\MYSAVE" --kv KV.bin
x360resign resign "D:\Monday Night War" -o "D:\resigned" --profile-id E0000123456789AB --kv nand_kv.bin --cpu-key 0123456789ABCDEF0123456789ABCDEF
x360resign resign "D:\Monday Night War" -o "D:\resigned" --profile-id E0000123456789AB --no-sign
x360resign verify "D:\resigned"
x360resign info SAVEFILE --files-list
x360resign extract SAVEFILE -o extracted
x360resign kvinfo KV.bin
x360resign ids MYSAVE
```

Useful `resign` flags:

| Flag | |
|---|---|
| `--profile-id`, `--console-id`, `--device-id` | IDs to write. Console ID defaults to the KV's console ID. |
| `--ids-from SAVE` | Take all the IDs from one of your own saves. |
| `--kv`, `--cpu-key` | KeyVault (plus the CPU key if it's encrypted). |
| `--no-sign` | Run without a KV (IDs and rehash only). |
| `--patch-embedded-ids` | Also replace the old IDs if they're stored *inside* the save data (see below). |
| `--layout flat` | Put all output files in one folder instead of the `Content\…` tree. |
| `--include-profiles` | Also process gamer-profile packages (skipped by default). |
| `--save-config` | Remember the IDs and KV path for next time. |

Exit codes: `0` = all good, `1` = some files failed, `2` = bad arguments,
missing KV and the like.

---

## Troubleshooting

**The game says the save is corrupt or doesn't list it**
- Open `resign_report.txt`. Every package should say `RESIGNED` and
  `output : header hash OK, hash tree OK, signature OK`.
- Make sure the Profile ID is the one for the profile you're signed in with. It
  must match the `Content\<profile>` folder that your own saves are in.
- Run `x360resign kvinfo KV.bin`. If it prints a *certificate does not match*
  warning, the KV is damaged or mixed up with another console's.
- Some games also store the owner's ID *inside* the save data. The tool always
  scans for this, and the report says when it finds one, for example
  `old profile ID found inside … at offset 0x…`. If the game still rejects the
  save, run again with **Also patch old IDs inside the save data** ticked
  (`--patch-embedded-ids`).

**"KV must be 16384 … bytes" / "KV looks encrypted"**
That isn't a decrypted KeyVault. Either point the tool at the decrypted
`KV.bin`, or give the raw NAND KV together with your CPU key.

**DLC wrestlers or arenas are missing**
DLC (`LIVE` packages) is signed by Microsoft and needs a license, meaning the
DLC has to be bought for your profile or console. The tool copies DLC files
unchanged and can't make them work without that license.

**A file shows `SKIPPED – this is a gamer profile`**
Some packs include the author's gamer profile. You don't need it, so it's left
out. Use `--include-profiles` if you really want it processed.

---

## How it works

An Xbox 360 save is an **STFS package**. The fields the tool changes:

| Offset | Size | Field |
|---|---|---|
| `0x0004` | `0x1A8` | Console certificate (copied from your KV, offset `0x9C8`) |
| `0x01AC` | `0x80` | RSA-1024 PKCS#1 v1.5 SHA-1 signature over `0x22C–0x344`, stored byte-reversed |
| `0x032C` | `0x14` | SHA-1 of the header from `0x344` to the end of the header block |
| `0x036C` | `5` | Console ID |
| `0x0371` | `8` | Profile ID |
| `0x0381` | `0x14` | SHA-1 of the top-level hash table (root of the hash tree) |
| `0x03FD` | `0x14` | Device ID |

Under the header, every 4 KB data block is hashed into level-0 hash tables. Up
to two more levels of tables hash those tables. In read/write packages each
table has two copies, and a status bit in the parent says which copy is active.
The rehash recomputes every hash from the bottom up, writes only into the
*active* copies, and leaves everything else byte-for-byte the same.

The signing key is the console private key from the KV (`XECRYPT_RSAPRV_1024`
at `0x298`, numbers stored as reversed 64-bit words). Signatures are made with
plain PKCS#1 v1.5 + SHA-1 and stored byte-reversed, matching what the console's
`XeKeysPkcs1Create` produces.

Source layout:

```
x360resign/stfs.py      STFS parsing, hash tree, rehash, verify, extract
x360resign/xecrypt.py   RSA / PKCS#1 in Xbox byte order, RC4, HMAC (pure Python)
x360resign/keyvault.py  KV loading, decryption with the CPU key, certificate
x360resign/resigner.py  the batch pipeline and the report
x360resign/cli.py       command line;  x360resign/gui.py  window version
tests/                  synthetic packages and keys, independent layout model
```

### Tests

```
python -m unittest discover -s tests -t . -v
```

The tests build STFS packages from scratch: all three hash-tree levels (one is
about 120 MB), both table formats, random active table copies, and scattered
block chains. The block layout in the tests comes from a separate, sequential
model of the format, so it cross-checks the tool's own address math. The
signature format is checked independently against a stock RSA library. The
tests also cover tampering, truncation, encrypted KVs, embedded IDs, zips,
DLC, profiles and the whole CLI.

Set `X360RESIGN_SLOW=0` to skip the 120 MB test.

### References

The format work follows the public documentation and open-source tools of the
Xbox 360 scene: the Free60 STFS notes, Velocity / XboxInternals (hetelek),
stfschk (emoose) and Xbox_360_Crypto (HYXHost).
