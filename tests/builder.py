"""Test helpers: build synthetic STFS packages and KeyVaults from scratch.

The physical block layout here is produced by walking the package
sequentially (an independent formulation from the closed-form math in
x360resign.stfs), so the tests cross-check the two.
"""

import hashlib
import random
import struct

from x360resign import xecrypt

BLOCK = 0x1000
PER = (0xAA, 0x70E4, 0x4AF768)


def sha1(data):
    return hashlib.sha1(data).digest()


# ------------------------------------------------------------------ RSA / KV

def _is_probable_prime(n, rng):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(32):
        a = rng.randrange(2, n - 2)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits, rng, e=0x10001):
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if candidate % e != 1 and _is_probable_prime(candidate, rng):
            return candidate


def make_rsa_1024(seed):
    rng = random.Random(seed)
    e = 0x10001
    while True:
        p = _random_prime(512, rng)
        q = _random_prime(512, rng)
        if p != q and (p * q).bit_length() == 1024:
            return p * q, e, p, q


def make_keyvault(seed=1, console_id=bytes.fromhex("0123456789"), header=True,
                  serial=b"123456789012", mismatched_cert=False):
    n, e, p, q = make_rsa_1024(seed)
    d = pow(e, -1, (p - 1) * (q - 1))
    kv = bytearray(0x4000)
    rng = random.Random(seed + 1000)
    kv[0:0x18] = bytes(rng.getrandbits(8) for _ in range(0x18))
    kv[0xB0:0xBC] = serial
    prv = bytearray()
    prv += struct.pack(">IIQ", 0x10, e, 0)
    prv += xecrypt.int_to_bnqw(n, 0x80)
    prv += xecrypt.int_to_bnqw(p, 0x40)
    prv += xecrypt.int_to_bnqw(q, 0x40)
    prv += xecrypt.int_to_bnqw(d % (p - 1), 0x40)
    prv += xecrypt.int_to_bnqw(d % (q - 1), 0x40)
    prv += xecrypt.int_to_bnqw(pow(q, -1, p), 0x40)
    assert len(prv) == 0x1D0
    kv[0x298:0x298 + 0x1D0] = prv

    cert = bytearray()
    cert += struct.pack(">H", 0x1A8)
    cert += console_id
    cert += b"X803949-001"            # part number (0xB)
    cert += b"\0\0\0\0"               # reserved
    cert += struct.pack(">H", 0)      # privileges
    cert += struct.pack(">I", 2)      # retail
    cert += b"09-18-08"
    cert += struct.pack(">I", e)
    modulus = n if not mismatched_cert else make_rsa_1024(seed + 7)[0]
    cert += xecrypt.int_to_bnqw(modulus, 0x80)
    cert += bytes(rng.getrandbits(8) for _ in range(0x100))  # Microsoft's signature (opaque)
    assert len(cert) == 0x1A8
    kv[0x9C8:0x9C8 + 0x1A8] = cert
    data = bytes(kv)
    return data if header else data[0x10:]


# ------------------------------------------------------------------ STFS

def top_level_for(total):
    if total <= PER[0]:
        return 0
    if total <= PER[1]:
        return 1
    return 2


def simulate_layout(total, read_only):
    """Walk the package in order; return (data_phys list, {(level, index): phys})."""
    tps = 1 if read_only else 2
    top = top_level_for(total)
    data_phys = []
    tables = {}
    pos = 0

    def emit_table(level, index):
        nonlocal pos
        tables[(level, index)] = pos
        pos += tps

    for b in range(total):
        if b % PER[0] == 0:
            if b == 0:
                emit_table(0, 0)
            else:
                if b % PER[1] == 0:
                    if b == PER[1] and top >= 2:
                        emit_table(2, 0)
                    emit_table(1, b // PER[1])
                elif b == PER[0] and top >= 1:
                    emit_table(1, 0)
                emit_table(0, b // PER[0])
        data_phys.append(pos)
        pos += 1
    # Every table should have been placed by the walk; if one was not, park it
    # at the end so the cross-check against the library fails loudly.
    for level in range(top + 1):
        for index in range((total + PER[level] - 1) // PER[level] if total else 1):
            if (level, index) not in tables:
                emit_table(level, index)
    return data_phys, tables, pos


def _dir_entry(name, flags, blocks, first, parent, size, ts=0x3D2A1234):
    raw = bytearray(0x40)
    enc = name.encode("ascii")
    raw[0:len(enc)] = enc
    raw[0x28] = (flags << 6) | len(enc)
    raw[0x29:0x2C] = blocks.to_bytes(3, "little")
    raw[0x2C:0x2F] = blocks.to_bytes(3, "little")
    raw[0x2F:0x32] = first.to_bytes(3, "little")
    struct.pack_into(">HIII", raw, 0x32, parent, size, ts, ts)
    return bytes(raw)


def build_package(files, *, magic=b"CON ", read_only=False, title_id=0x545107FC,
                  profile_id=bytes.fromhex("E0000AAAAAAAAAAA"),
                  console_id=bytes.fromhex("FFEEDDCCBB"),
                  device_id=bytes(range(0x14)), content_type=1,
                  display_name="Test Save", title_name="WWE '13",
                  extra_blocks=0, kv=None, seed=0, scatter=False, header_size=0x971A):
    """files: list of (path, bytes).  Folders are created for 'a/b' style paths.

    ``scatter`` interleaves file blocks so chains are non-contiguous.
    ``kv`` (KeyVault object) signs the package; otherwise signature is random.
    """
    rng = random.Random(seed)

    # ---- directory entries ----
    folders = []
    for path, _ in files:
        parts = path.split("/")[:-1]
        for i in range(len(parts)):
            folder = "/".join(parts[:i + 1])
            if folder not in folders:
                folders.append(folder)
    entry_paths = folders + [p for p, _ in files]
    dir_blocks = max(1, (len(entry_paths) + 0x3F) // 0x40)

    # ---- data block assignment ----
    file_blocks = []
    next_free = dir_blocks
    per_file_counts = [(len(content) + BLOCK - 1) // BLOCK for _, content in files]
    if scatter:
        slots = list(range(dir_blocks, dir_blocks + sum(per_file_counts)))
        rng.shuffle(slots)
        it = iter(slots)
        for count in per_file_counts:
            file_blocks.append([next(it) for _ in range(count)])
        next_free += sum(per_file_counts)
    else:
        for count in per_file_counts:
            file_blocks.append(list(range(next_free, next_free + count)))
            next_free += count
    total = next_free + extra_blocks

    block_data = {}
    next_ptr = {}
    for i in range(dir_blocks):
        next_ptr[i] = i + 1 if i + 1 < dir_blocks else 0xFFFFFF
    for (path, content), blocks in zip(files, file_blocks):
        for k, b in enumerate(blocks):
            block_data[b] = content[k * BLOCK:(k + 1) * BLOCK].ljust(BLOCK, b"\0")
            next_ptr[b] = blocks[k + 1] if k + 1 < len(blocks) else 0xFFFFFF
    for b in range(next_free, total):
        block_data[b] = bytes(rng.getrandbits(8) for _ in range(64)) * (BLOCK // 64)

    index_of = {p: i for i, p in enumerate(entry_paths)}
    dir_raw = bytearray(dir_blocks * BLOCK)
    for i, path in enumerate(entry_paths):
        parent_path = path.rsplit("/", 1)[0] if "/" in path else None
        parent = index_of[parent_path] if parent_path else 0xFFFF
        name = path.rsplit("/", 1)[-1]
        if path in folders:
            raw = _dir_entry(name, 2, 0, 0, parent, 0)
        else:
            fi = [p for p, _ in files].index(path)
            blocks = file_blocks[fi]
            contiguous = blocks == list(range(blocks[0], blocks[0] + len(blocks))) if blocks else True
            raw = _dir_entry(name, 1 if contiguous else 0, len(blocks),
                             blocks[0] if blocks else 0, parent, len(files[fi][1]))
        dir_raw[i * 0x40:(i + 1) * 0x40] = raw
    for i in range(dir_blocks):
        block_data[i] = bytes(dir_raw[i * BLOCK:(i + 1) * BLOCK])

    # ---- physical layout ----
    data_phys, tables, phys_count = simulate_layout(total, read_only)
    first_table = (header_size + BLOCK - 1) & ~(BLOCK - 1)
    out = bytearray(first_table + phys_count * BLOCK)
    for b in range(total):
        off = first_table + data_phys[b] * BLOCK
        out[off:off + BLOCK] = block_data[b]

    top = top_level_for(total)
    tps = 1 if read_only else 2
    # choose which copy of each table is active (random for read-write packages)
    active = {key: (0 if read_only else rng.randrange(2)) for key in tables}

    def table_bytes(level, index):
        raw = bytearray(BLOCK)
        if level == 0:
            count = min(0xAA, total - index * 0xAA)
            for e in range(count):
                b = index * 0xAA + e
                raw[e * 0x18:e * 0x18 + 0x14] = sha1(block_data[b])
                raw[e * 0x18 + 0x14] = 0x80
                raw[e * 0x18 + 0x15:e * 0x18 + 0x18] = next_ptr.get(b, 0xFFFFFF).to_bytes(3, "big")
        else:
            child_count = (total + PER[level - 1] - 1) // PER[level - 1]
            count = min(0xAA, child_count - index * 0xAA)
            for e in range(count):
                child = (level - 1, index * 0xAA + e)
                raw[e * 0x18:e * 0x18 + 0x14] = sha1(built[child])
                raw[e * 0x18 + 0x14] = 0x40 if active[child] else 0x00
            covered = min(PER[level], total - index * PER[level])
            struct.pack_into(">I", raw, 0xFF0, covered)
        return bytes(raw)

    built = {}
    for level in range(top + 1):
        for (lvl, index) in sorted(k for k in tables if k[0] == level):
            built[(lvl, index)] = table_bytes(lvl, index)
            base = first_table + tables[(lvl, index)] * BLOCK
            for slot in range(tps):
                off = base + slot * BLOCK
                if slot == active[(lvl, index)]:
                    out[off:off + BLOCK] = built[(lvl, index)]
                else:
                    out[off:off + BLOCK] = b"\xEE" * BLOCK  # stale copy - must never be used
    root = sha1(built[(top, 0)])

    # ---- header ----
    out[0:4] = magic
    if magic == b"CON ":
        if kv is not None:
            out[4:4 + 0x1A8] = kv.certificate
        else:
            out[4:4 + 0x1A8] = bytes(rng.getrandbits(8) for _ in range(0x1A8))
    else:
        out[4:4 + 0x100] = bytes(rng.getrandbits(8) for _ in range(0x100))
    struct.pack_into(">QII", out, 0x22C, 0xFFFFFFFFFFFFFFFF, 0, 0)
    struct.pack_into(">III", out, 0x340, header_size, content_type, 2)
    struct.pack_into(">Q", out, 0x34C, len(out) - first_table)
    struct.pack_into(">I", out, 0x360, title_id)
    out[0x364] = 2
    out[0x36C:0x371] = console_id
    out[0x371:0x379] = profile_id
    out[0x379] = 0x24
    out[0x37B] = (1 if read_only else 0) | (2 if (not read_only and active[(top, 0)]) else 0)
    struct.pack_into("<H", out, 0x37C, dir_blocks)
    out[0x37E:0x381] = (0).to_bytes(3, "little")
    out[0x381:0x395] = root
    struct.pack_into(">II", out, 0x395, total, 0)
    struct.pack_into(">I", out, 0x3A9, 0)
    out[0x3FD:0x411] = device_id
    name = display_name.encode("utf-16-be")
    out[0x411:0x411 + len(name)] = name
    tname = title_name.encode("utf-16-be")
    out[0x1691:0x1691 + len(tname)] = tname
    png = b"\x89PNG\r\n\x1a\nfake-thumbnail"
    struct.pack_into(">II", out, 0x1712, len(png), 0)
    out[0x171A:0x171A + len(png)] = png

    out[0x32C:0x340] = sha1(bytes(out[0x344:first_table]))
    if magic == b"CON ":
        if kv is not None:
            out[0x1AC:0x22C] = kv.sign(bytes(out[0x22C:0x344]))
        else:
            out[0x1AC:0x22C] = bytes(rng.getrandbits(8) for _ in range(0x80))
    return bytes(out)
