"""Xbox 360 STFS package (CON / LIVE / PIRS) reader, rehasher and resigner.

Layout reference: Free60 STFS notes, Velocity (XboxInternals/Stfs) and
emoose's stfschk.  Everything is done on an in-memory copy of the package;
call ``to_bytes()`` / ``save()`` to get the result.

Header map (offsets from the start of the file):

    0x000  magic "CON " / "LIVE" / "PIRS"
    0x004  CON: console certificate (0x1A8)   LIVE/PIRS: RSA-2048 signature (0x100)
    0x1AC  CON: package signature (0x80, PKCS#1 SHA-1 over 0x22C..0x344)
    0x22C  license entries (16 x 0x10)
    0x32C  header SHA-1 (covers 0x344 .. end of header block)
    0x340  header size
    0x344  content type          0x360 title ID        0x36C console ID (5)
    0x371  profile ID (8)        0x379 volume descriptor (0x24)
    0x3A9  descriptor type (0 = STFS, 1 = SVOD)
    0x3FD  device ID (0x14)      0x411 display names (UTF-16BE)
"""

import os
import re
import struct

from .xecrypt import bnqw_to_int, sha1, xbox_pkcs1_verify

BLOCK_SIZE = 0x1000
HASH_ENTRY_SIZE = 0x18
ENTRIES_PER_TABLE = 0xAA
BLOCKS_PER_LEVEL = (0xAA, 0x70E4, 0x4AF768)  # data blocks covered by one table at level 0/1/2
END_OF_CHAIN = 0xFFFFFF

MAGIC_CON = b"CON "
MAGIC_LIVE = b"LIVE"
MAGIC_PIRS = b"PIRS"
MAGICS = (MAGIC_CON, MAGIC_LIVE, MAGIC_PIRS)

OFF_CERTIFICATE = 0x004
CERTIFICATE_SIZE = 0x1A8
OFF_CERT_CONSOLE_ID = 0x006
OFF_CERT_EXPONENT = 0x028
OFF_CERT_MODULUS = 0x02C
OFF_SIGNATURE = 0x1AC
SIGNATURE_SIZE = 0x80
OFF_LICENSES = 0x22C
OFF_HEADER_HASH = 0x32C
OFF_HEADER_SIZE = 0x340
OFF_HASHED_HEADER = 0x344
OFF_CONTENT_TYPE = 0x344
OFF_METADATA_VERSION = 0x348
OFF_CONTENT_SIZE = 0x34C
OFF_MEDIA_ID = 0x354
OFF_VERSION = 0x358
OFF_TITLE_ID = 0x360
OFF_SAVEGAME_ID = 0x368
OFF_CONSOLE_ID = 0x36C
OFF_PROFILE_ID = 0x371
OFF_VOLUME_DESCRIPTOR = 0x379
OFF_VD_FLAGS = 0x37B
OFF_VD_DIR_BLOCK_COUNT = 0x37C
OFF_VD_DIR_FIRST_BLOCK = 0x37E
OFF_VD_ROOT_HASH = 0x381
OFF_VD_TOTAL_BLOCKS = 0x395
OFF_VD_FREE_BLOCKS = 0x399
OFF_DESCRIPTOR_TYPE = 0x3A9
OFF_DEVICE_ID = 0x3FD
OFF_DISPLAY_NAME = 0x411
OFF_DISPLAY_DESCRIPTION = 0xD11
OFF_PUBLISHER = 0x1611
OFF_TITLE_NAME = 0x1691
OFF_TRANSFER_FLAGS = 0x1711
OFF_THUMBNAIL_SIZE = 0x1712
OFF_TITLE_THUMBNAIL_SIZE = 0x1716
OFF_THUMBNAIL = 0x171A
OFF_TITLE_THUMBNAIL = 0x571A
MIN_HEADER_BYTES = 0x1712

CONTENT_TYPES = {
    0x00000001: "Saved Game",
    0x00000002: "Marketplace Content",
    0x00000003: "Publisher",
    0x00001000: "Xbox 360 Title",
    0x00002000: "IPTV Pause Buffer",
    0x00004000: "Installed Game",
    0x00005000: "Xbox Original Game",
    0x00007000: "Games on Demand",
    0x00008000: "Avatar Asset Pack",
    0x00009000: "Avatar Item",
    0x00010000: "Profile",
    0x00020000: "Gamer Picture",
    0x00030000: "Theme",
    0x00040000: "Cache File",
    0x00050000: "Storage Download",
    0x00060000: "Xbox Saved Game",
    0x00070000: "Xbox Download",
    0x00080000: "Game Demo",
    0x00090000: "Video",
    0x000A0000: "Game Title",
    0x000B0000: "Installer / Title Update",
    0x000C0000: "Game Trailer",
    0x000D0000: "Arcade Title",
    0x000E0000: "XNA",
    0x000F0000: "License Store",
    0x00100000: "Movie",
    0x00200000: "TV",
    0x00300000: "Music Video",
    0x00400000: "Game Video",
    0x00500000: "Podcast Video",
    0x00600000: "Viral Video",
    0x02000000: "Community Game",
}

CONTENT_TYPE_SAVED_GAME = 0x00000001
CONTENT_TYPE_PROFILE = 0x00010000


class StfsError(Exception):
    pass


def _be24(b):
    return (b[0] << 16) | (b[1] << 8) | b[2]


def _le24(b):
    return b[0] | (b[1] << 8) | (b[2] << 16)


def _utf16_string(raw):
    text = raw.decode("utf-16-be", "replace")
    return text.split("\0", 1)[0]


def is_stfs_magic(head):
    return bytes(head[:4]) in MAGICS


def decode_ms_time(value):
    """FAT-style packed timestamp -> 'YYYY-MM-DD HH:MM:SS' (empty if zero)."""
    if not value:
        return ""
    year = ((value >> 25) & 0x7F) + 1980
    month = (value >> 21) & 0xF
    day = (value >> 16) & 0x1F
    hour = (value >> 11) & 0x1F
    minute = (value >> 5) & 0x3F
    second = (value & 0x1F) * 2
    return "%04d-%02d-%02d %02d:%02d:%02d" % (year, month, day, hour, minute, second)


class FileEntry:
    __slots__ = ("index", "name", "flags", "valid_blocks", "allocated_blocks", "first_block",
                 "parent", "size", "created", "modified", "path")

    def __init__(self, index, raw):
        self.index = index
        name_len = raw[0x28] & 0x3F
        self.name = raw[:name_len].decode("ascii", "replace")
        self.flags = raw[0x28] >> 6
        self.valid_blocks = _le24(raw[0x29:0x2C])
        self.allocated_blocks = _le24(raw[0x2C:0x2F])
        self.first_block = _le24(raw[0x2F:0x32])
        self.parent = struct.unpack_from(">H", raw, 0x32)[0]
        self.size = struct.unpack_from(">I", raw, 0x34)[0]
        self.created = struct.unpack_from(">I", raw, 0x38)[0]
        self.modified = struct.unpack_from(">I", raw, 0x3C)[0]
        self.path = self.name

    @property
    def is_directory(self):
        return bool(self.flags & 2)

    @property
    def is_contiguous(self):
        return bool(self.flags & 1)

    @property
    def block_count(self):
        return (self.size + BLOCK_SIZE - 1) // BLOCK_SIZE


class VerifyReport:
    def __init__(self):
        self.header_hash_ok = None
        self.root_hash_ok = None
        self.bad_tables = []      # (level, index)
        self.bad_blocks = []      # data block numbers whose hash does not match
        self.signature_ok = None  # True / False / None (not checkable)
        self.signature_note = ""
        self.problems = []

    @property
    def hashes_ok(self):
        return bool(self.header_hash_ok and self.root_hash_ok and not self.bad_tables
                    and not self.bad_blocks)

    @property
    def ok(self):
        return self.hashes_ok and self.signature_ok is not False and not self.problems

    def summary(self):
        parts = []
        parts.append("header hash %s" % ("OK" if self.header_hash_ok else "BAD"))
        if self.root_hash_ok is not None:
            tree_ok = self.root_hash_ok and not self.bad_tables and not self.bad_blocks
            text = "hash tree %s" % ("OK" if tree_ok else "BAD")
            if self.bad_blocks:
                text += " (%d bad block hash%s)" % (len(self.bad_blocks),
                                                    "" if len(self.bad_blocks) == 1 else "es")
            parts.append(text)
        if self.signature_ok is True:
            parts.append("signature OK")
        elif self.signature_ok is False:
            parts.append("signature BAD")
        elif self.signature_note:
            parts.append(self.signature_note)
        return ", ".join(parts)


class StfsPackage:
    """An STFS package held in memory."""

    def __init__(self, data, name=""):
        self.data = bytearray(data)
        self.name = name
        self._table_cache = {}
        self._parse()

    @classmethod
    def from_file(cls, path):
        with open(path, "rb") as f:
            return cls(f.read(), name=os.path.basename(path))

    # ------------------------------------------------------------------ parsing

    def _parse(self):
        d = self.data
        if len(d) < MIN_HEADER_BYTES:
            raise StfsError("file is too small to be an Xbox 360 package")
        self.magic = bytes(d[0:4])
        if self.magic not in MAGICS:
            raise StfsError("not an STFS package (magic %r)" % self.magic)
        self.header_size = self._u32(OFF_HEADER_SIZE)
        if not OFF_DESCRIPTOR_TYPE < self.header_size <= 0x10000:
            raise StfsError("implausible header size 0x%X" % self.header_size)
        self.descriptor_type = self._u32(OFF_DESCRIPTOR_TYPE)
        self.first_table_offset = (self.header_size + BLOCK_SIZE - 1) & ~(BLOCK_SIZE - 1)

        if not self.is_stfs:
            return  # SVOD (Games on Demand data) - header only

        flags = d[OFF_VD_FLAGS]
        self.read_only_format = bool(flags & 1)
        self.root_active_index = bool(flags & 2)
        self.dir_block_count = struct.unpack_from("<H", d, OFF_VD_DIR_BLOCK_COUNT)[0]
        self.dir_first_block = _le24(d[OFF_VD_DIR_FIRST_BLOCK:OFF_VD_DIR_FIRST_BLOCK + 3])
        self.total_blocks = self._u32(OFF_VD_TOTAL_BLOCKS)
        self.free_blocks = self._u32(OFF_VD_FREE_BLOCKS)

        if self.read_only_format:
            self.tables_per_slot = 1
            self.block_step = (0xAB, 0x718F)
        else:
            self.tables_per_slot = 2
            self.block_step = (0xAC, 0x723A)

        if self.total_blocks <= BLOCKS_PER_LEVEL[0]:
            self.top_level = 0
        elif self.total_blocks <= BLOCKS_PER_LEVEL[1]:
            self.top_level = 1
        elif self.total_blocks <= BLOCKS_PER_LEVEL[2]:
            self.top_level = 2
        else:
            raise StfsError("invalid allocated block count 0x%X" % self.total_blocks)

    def _u32(self, offset):
        return struct.unpack_from(">I", self.data, offset)[0]

    # ------------------------------------------------------------- header fields

    @property
    def is_con(self):
        return self.magic == MAGIC_CON

    @property
    def is_stfs(self):
        return self.descriptor_type == 0

    @property
    def content_type(self):
        return self._u32(OFF_CONTENT_TYPE)

    @property
    def content_type_name(self):
        return CONTENT_TYPES.get(self.content_type, "Unknown")

    @property
    def metadata_version(self):
        return self._u32(OFF_METADATA_VERSION)

    @property
    def title_id(self):
        return self._u32(OFF_TITLE_ID)

    @property
    def media_id(self):
        return self._u32(OFF_MEDIA_ID)

    @property
    def profile_id(self):
        return bytes(self.data[OFF_PROFILE_ID:OFF_PROFILE_ID + 8])

    @profile_id.setter
    def profile_id(self, value):
        self._put(OFF_PROFILE_ID, value, 8)

    @property
    def console_id(self):
        return bytes(self.data[OFF_CONSOLE_ID:OFF_CONSOLE_ID + 5])

    @console_id.setter
    def console_id(self, value):
        self._put(OFF_CONSOLE_ID, value, 5)

    @property
    def device_id(self):
        return bytes(self.data[OFF_DEVICE_ID:OFF_DEVICE_ID + 0x14])

    @device_id.setter
    def device_id(self, value):
        self._put(OFF_DEVICE_ID, value, 0x14)

    @property
    def certificate_console_id(self):
        if not self.is_con:
            return b""
        return bytes(self.data[OFF_CERT_CONSOLE_ID:OFF_CERT_CONSOLE_ID + 5])

    @property
    def display_name(self):
        return _utf16_string(self.data[OFF_DISPLAY_NAME:OFF_DISPLAY_NAME + 0x80])

    @property
    def display_description(self):
        return _utf16_string(self.data[OFF_DISPLAY_DESCRIPTION:OFF_DISPLAY_DESCRIPTION + 0x80])

    @property
    def title_name(self):
        return _utf16_string(self.data[OFF_TITLE_NAME:OFF_TITLE_NAME + 0x80])

    @property
    def publisher(self):
        return _utf16_string(self.data[OFF_PUBLISHER:OFF_PUBLISHER + 0x80])

    @property
    def transfer_flags(self):
        return self.data[OFF_TRANSFER_FLAGS]

    def thumbnail(self, title=False):
        size_off, img_off = ((OFF_TITLE_THUMBNAIL_SIZE, OFF_TITLE_THUMBNAIL) if title
                             else (OFF_THUMBNAIL_SIZE, OFF_THUMBNAIL))
        if len(self.data) < size_off + 4:
            return b""
        size = self._u32(size_off)
        if size == 0 or size > 0x4000 or img_off + size > min(len(self.data), self.header_size):
            return b""
        return bytes(self.data[img_off:img_off + size])

    def _put(self, offset, value, size):
        value = bytes(value)
        if len(value) != size:
            raise ValueError("expected %d bytes, got %d" % (size, len(value)))
        self.data[offset:offset + size] = value

    # ------------------------------------------------------- block addressing

    def _require_stfs(self):
        if not self.is_stfs:
            raise StfsError("SVOD packages are not supported (only STFS)")

    def backing_data_block(self, block):
        """Physical block index (after the header) of data block ``block``."""
        result = block
        base = BLOCKS_PER_LEVEL[0]
        for _ in range(3):
            result += self.tables_per_slot * ((block + base) // base)
            if block < base:
                break
            base *= ENTRIES_PER_TABLE
        return result

    def backing_hash_block(self, block, level):
        """Physical block index of the hash table at ``level`` covering data block ``block``."""
        tps = self.tables_per_slot
        if level == 0:
            if block < BLOCKS_PER_LEVEL[0]:
                return 0
            num = (block // BLOCKS_PER_LEVEL[0]) * self.block_step[0]
            num += ((block // BLOCKS_PER_LEVEL[1]) + 1) * tps
            if block < BLOCKS_PER_LEVEL[1]:
                return num
            return num + tps
        if level == 1:
            if block < BLOCKS_PER_LEVEL[1]:
                return self.block_step[0]
            return (block // BLOCKS_PER_LEVEL[1]) * self.block_step[1] + tps
        return self.block_step[1]

    def physical_offset(self, backing_block):
        return self.first_table_offset + backing_block * BLOCK_SIZE

    def data_block_offset(self, block):
        if block >= self.total_blocks:
            raise StfsError("reference to block %d past the end of the package (%d blocks)"
                            % (block, self.total_blocks))
        return self.physical_offset(self.backing_data_block(block))

    def table_count(self, level):
        per = BLOCKS_PER_LEVEL[level]
        return max(1, (self.total_blocks + per - 1) // per) if level <= self.top_level else 0

    def table_entry_count(self, level, index):
        if level == 0:
            children = self.total_blocks
        else:
            children = self.table_count(level - 1)
        return max(0, min(ENTRIES_PER_TABLE, children - index * ENTRIES_PER_TABLE))

    def table_offset(self, level, index):
        """File offset of the *active* copy of hash table ``index`` at ``level``."""
        key = (level, index)
        cached = self._table_cache.get(key)
        if cached is not None:
            return cached
        base = self.physical_offset(self.backing_hash_block(index * BLOCKS_PER_LEVEL[level], level))
        offset = base
        if not self.read_only_format:
            if level == self.top_level:
                active = self.root_active_index
            else:
                parent = self.table_offset(level + 1, index // ENTRIES_PER_TABLE)
                status_at = parent + (index % ENTRIES_PER_TABLE) * HASH_ENTRY_SIZE + 0x14
                active = bool(self._byte(status_at) & 0x40)
            if active:
                offset += BLOCK_SIZE
        self._table_cache[key] = offset
        return offset

    def _byte(self, offset):
        return self.data[offset] if offset < len(self.data) else 0

    def _read(self, offset, size):
        chunk = bytes(self.data[offset:offset + size])
        if len(chunk) < size:
            chunk += b"\0" * (size - len(chunk))
        return chunk

    def hash_entry_offset(self, block):
        return (self.table_offset(0, block // ENTRIES_PER_TABLE)
                + (block % ENTRIES_PER_TABLE) * HASH_ENTRY_SIZE)

    def next_block(self, block):
        off = self.hash_entry_offset(block)
        return _be24(self._read(off + 0x15, 3))

    def read_block(self, block):
        return self._read(self.data_block_offset(block), BLOCK_SIZE)

    def required_size(self):
        """Smallest file size that holds every allocated block and active hash table."""
        if not self.is_stfs:
            return len(self.data)
        end = self.first_table_offset
        if self.total_blocks:
            end = max(end, self.data_block_offset(self.total_blocks - 1) + BLOCK_SIZE)
        for level in range(self.top_level + 1):
            for index in range(self.table_count(level)):
                end = max(end, self.table_offset(level, index) + BLOCK_SIZE)
        return end

    # ------------------------------------------------------------ file system

    def files(self):
        """Return the directory listing as a list of FileEntry (paths use '/')."""
        self._require_stfs()
        entries = []
        block = self.dir_first_block
        seen = set()
        for x in range(self.dir_block_count):
            if block == END_OF_CHAIN or block >= self.total_blocks or block in seen:
                break
            seen.add(block)
            raw_block = self.read_block(block)
            for i in range(0x40):
                raw = raw_block[i * 0x40:(i + 1) * 0x40]
                if raw[0x28] & 0x3F == 0:
                    continue
                entries.append(FileEntry(x * 0x40 + i, raw))
            block = self.next_block(block)

        by_index = {e.index: e for e in entries}
        for entry in entries:
            parts = [entry.name]
            parent = entry.parent
            depth = 0
            while parent != 0xFFFF and parent in by_index and depth < 64:
                parts.append(by_index[parent].name)
                parent = by_index[parent].parent
                depth += 1
            entry.path = "/".join(reversed(parts))
        return entries

    def file_blocks(self, entry):
        """Data block numbers holding ``entry``'s contents, in order."""
        count = entry.block_count
        if count == 0:
            return []
        if entry.is_contiguous:
            return list(range(entry.first_block, entry.first_block + count))
        blocks = []
        block = entry.first_block
        while len(blocks) < count:
            if block == END_OF_CHAIN or block >= self.total_blocks:
                raise StfsError("broken block chain in %s" % entry.path)
            blocks.append(block)
            block = self.next_block(block)
        return blocks

    def read_file(self, entry):
        blocks = self.file_blocks(entry)
        content = b"".join(self.read_block(b) for b in blocks)
        return content[:entry.size]

    def write_file_bytes(self, entry, file_offset, new_bytes):
        """Overwrite bytes inside a contained file (same length).  Rehash afterwards."""
        blocks = self.file_blocks(entry)
        for i, byte in enumerate(new_bytes):
            pos = file_offset + i
            if pos >= entry.size:
                raise StfsError("write past end of %s" % entry.path)
            off = self.data_block_offset(blocks[pos // BLOCK_SIZE]) + pos % BLOCK_SIZE
            self.data[off] = byte

    def extract(self, out_dir):
        """Extract every file to ``out_dir``; returns the list of written paths."""
        written = []
        for entry in self.files():
            target = os.path.join(out_dir, *[_safe_component(p) for p in entry.path.split("/")])
            if entry.is_directory:
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "wb") as f:
                f.write(self.read_file(entry))
            written.append(target)
        return written

    # -------------------------------------------------------------- hashing

    def header_hash_range(self):
        end = min(len(self.data), self.first_table_offset)
        return OFF_HASHED_HEADER, end

    def compute_header_hash(self):
        start, end = self.header_hash_range()
        return sha1(bytes(self.data[start:end]))

    def fix_header_hash(self):
        self.data[OFF_HEADER_HASH:OFF_HEADER_HASH + 0x14] = self.compute_header_hash()

    def _child_bytes(self, level, table_index, entry):
        if level == 0:
            return self.read_block(table_index * ENTRIES_PER_TABLE + entry)
        return self._read(self.table_offset(level - 1, table_index * ENTRIES_PER_TABLE + entry),
                          BLOCK_SIZE)

    def rehash(self):
        """Recompute every hash in the hash tree bottom-up, then the header hash.

        Returns how many tree hashes changed (the header hash is not counted).
        """
        self._require_stfs()
        self._table_cache.clear()
        needed = self.required_size()
        if len(self.data) < needed:
            self.data.extend(b"\0" * (needed - len(self.data)))
        changed = 0
        for level in range(self.top_level + 1):
            for index in range(self.table_count(level)):
                table = self.table_offset(level, index)
                for entry in range(self.table_entry_count(level, index)):
                    digest = sha1(self._child_bytes(level, index, entry))
                    at = table + entry * HASH_ENTRY_SIZE
                    if self.data[at:at + 0x14] != digest:
                        self.data[at:at + 0x14] = digest
                        changed += 1
        root = sha1(self._read(self.table_offset(self.top_level, 0), BLOCK_SIZE))
        if self.data[OFF_VD_ROOT_HASH:OFF_VD_ROOT_HASH + 0x14] != root:
            self.data[OFF_VD_ROOT_HASH:OFF_VD_ROOT_HASH + 0x14] = root
            changed += 1
        self.fix_header_hash()
        return changed

    # ------------------------------------------------------------- signing

    def install_certificate(self, certificate):
        if not self.is_con:
            raise StfsError("only CON packages carry a console certificate")
        self._put(OFF_CERTIFICATE, certificate, CERTIFICATE_SIZE)

    def signed_region(self):
        return bytes(self.data[OFF_LICENSES:OFF_HASHED_HEADER])

    def sign(self, keyvault):
        """Install the KV's certificate, fix the header hash and sign (CON only)."""
        if not self.is_con:
            raise StfsError("%s packages are signed by Microsoft and cannot be resigned"
                            % self.magic.decode().strip())
        self.install_certificate(keyvault.certificate)
        self.fix_header_hash()
        self.data[OFF_SIGNATURE:OFF_SIGNATURE + SIGNATURE_SIZE] = keyvault.sign(self.signed_region())

    def verify_signature(self):
        """True/False for CON packages, None for LIVE/PIRS (Microsoft keys)."""
        if not self.is_con:
            return None
        exponent = self._u32(OFF_CERT_EXPONENT)
        raw_mod = bytes(self.data[OFF_CERT_MODULUS:OFF_CERT_MODULUS + 0x80])
        sig = bytes(self.data[OFF_SIGNATURE:OFF_SIGNATURE + SIGNATURE_SIZE])
        message = self.signed_region()
        for modulus in (bnqw_to_int(raw_mod), int.from_bytes(raw_mod, "big")):
            if modulus and xbox_pkcs1_verify(modulus, exponent, message, sig):
                return True
        return False

    # ------------------------------------------------------------ verification

    def verify(self, check_signature=True):
        report = VerifyReport()
        report.header_hash_ok = (self.compute_header_hash()
                                 == bytes(self.data[OFF_HEADER_HASH:OFF_HEADER_HASH + 0x14]))
        if not report.header_hash_ok:
            report.problems.append("header hash mismatch")
        if self.is_stfs:
            self._table_cache.clear()
            if len(self.data) < self.required_size():
                report.problems.append("file is truncated (%d bytes, needs %d)"
                                       % (len(self.data), self.required_size()))
            for level in range(self.top_level + 1):
                for index in range(self.table_count(level)):
                    table = self.table_offset(level, index)
                    for entry in range(self.table_entry_count(level, index)):
                        at = table + entry * HASH_ENTRY_SIZE
                        if sha1(self._child_bytes(level, index, entry)) != self._read(at, 0x14):
                            if level == 0:
                                report.bad_blocks.append(index * ENTRIES_PER_TABLE + entry)
                            else:
                                report.bad_tables.append((level - 1, index * ENTRIES_PER_TABLE + entry))
            root = sha1(self._read(self.table_offset(self.top_level, 0), BLOCK_SIZE))
            report.root_hash_ok = root == bytes(self.data[OFF_VD_ROOT_HASH:OFF_VD_ROOT_HASH + 0x14])
        if check_signature:
            report.signature_ok = self.verify_signature()
            if report.signature_ok is None:
                report.signature_note = "Microsoft-signed %s (not checked)" % self.magic.decode().strip()
        return report

    # ----------------------------------------------------------- embedded ids

    def find_in_files(self, needle):
        """Yield (entry, file_offset) for every occurrence of ``needle`` inside contained files."""
        if not needle or not any(needle):
            return
        for entry in self.files():
            if entry.is_directory or entry.size == 0:
                continue
            content = self.read_file(entry)
            start = 0
            while True:
                pos = content.find(needle, start)
                if pos < 0:
                    break
                yield entry, pos
                start = pos + 1

    # --------------------------------------------------------------- output

    def to_bytes(self):
        return bytes(self.data)

    def save(self, path):
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(self.data)
        os.replace(tmp, path)

    def describe(self):
        lines = [
            "Type            : %s (%s)" % (self.magic.decode().strip(), self.content_type_name),
            "Title ID        : %08X%s" % (self.title_id, ("  (%s)" % self.title_name) if self.title_name else ""),
            "Display name    : %s" % self.display_name,
            "Profile ID      : %s" % self.profile_id.hex().upper(),
            "Console ID      : %s" % self.console_id.hex().upper(),
            "Device ID       : %s" % self.device_id.hex().upper(),
        ]
        if self.is_con:
            lines.append("Signed by       : console %s" % self.certificate_console_id.hex().upper())
        if self.is_stfs:
            lines.append("Blocks          : %d allocated, %d free, %s format, hash levels: %d"
                         % (self.total_blocks, self.free_blocks,
                            "read-only" if self.read_only_format else "read-write",
                            self.top_level + 1))
        else:
            lines.append("Volume          : SVOD (not supported for rehashing)")
        return "\n".join(lines)


_BAD_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f]')


def _safe_component(name):
    name = _BAD_CHARS.sub("_", name).strip()
    if name in ("", ".", ".."):
        name = "_"
    return name
