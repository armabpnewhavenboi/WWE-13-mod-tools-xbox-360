"""Loading the console KeyVault (KV.bin) used to sign CON packages.

A decrypted KV is 0x4000 bytes (or 0x3FF0 when the 0x10-byte digest header
was stripped).  The pieces needed for signing saves are:

    0x298  XECRYPT_RSAPRV_1024  console private key (0x1D0 bytes)
    0x9C8  XE_CONSOLE_CERTIFICATE (0x1A8 bytes), signed by Microsoft

Offsets above are for the 0x4000 layout.  A raw KV pulled straight out of a
NAND dump is still RC4-encrypted with the console's CPU key; pass the CPU key
and it is decrypted here.
"""

from . import xecrypt

KV_SIZE = 0x4000
KV_SIZE_NO_HEADER = 0x3FF0

PRIVATE_KEY_OFFSET = 0x298
PRIVATE_KEY_SIZE = 0x1D0
CERTIFICATE_OFFSET = 0x9C8
CERTIFICATE_SIZE = 0x1A8
SERIAL_OFFSET = 0xB0
SERIAL_SIZE = 0xC

# Offsets inside the 0x1A8-byte console certificate.
CERT_CONSOLE_ID = 0x02
CERT_PART_NUMBER = 0x07
CERT_CONSOLE_TYPE = 0x18
CERT_DATE = 0x1C
CERT_EXPONENT = 0x24
CERT_MODULUS = 0x28

CONSOLE_TYPES = {1: "DevKit", 2: "Retail"}


class KeyVaultError(Exception):
    pass


def parse_cpu_key(text):
    cleaned = "".join(text.split()).replace("-", "")
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    try:
        key = bytes.fromhex(cleaned)
    except ValueError:
        raise KeyVaultError("CPU key must be 32 hex characters") from None
    if len(key) != 0x10:
        raise KeyVaultError("CPU key must be 32 hex characters (16 bytes), got %d" % len(key))
    return key


def decrypt_keyvault(data, cpu_key):
    """Decrypt a raw (NAND) KV with the console's CPU key."""
    if len(data) != KV_SIZE:
        raise KeyVaultError("encrypted KV must be exactly 0x4000 bytes")
    rc4_key = xecrypt.hmac_sha1(cpu_key, data[:0x10])[:0x10]
    plain = bytes(data[:0x10]) + xecrypt.rc4(rc4_key, data[0x10:])
    digest = xecrypt.hmac_sha1(cpu_key, plain[0x10:], b"\x07\x12")[:0x10]
    if digest != plain[:0x10]:
        raise KeyVaultError("CPU key does not match this KV (digest check failed)")
    return plain


def encrypt_keyvault(plain, cpu_key):
    """Inverse of decrypt_keyvault (used by the tests)."""
    plain = bytearray(plain)
    plain[:0x10] = xecrypt.hmac_sha1(cpu_key, bytes(plain[0x10:]), b"\x07\x12")[:0x10]
    rc4_key = xecrypt.hmac_sha1(cpu_key, bytes(plain[:0x10]))[:0x10]
    return bytes(plain[:0x10]) + xecrypt.rc4(rc4_key, bytes(plain[0x10:]))


class KeyVault:
    def __init__(self, certificate, private_key_blob, serial=b"", source=None, was_encrypted=False):
        if len(certificate) != CERTIFICATE_SIZE:
            raise KeyVaultError("bad certificate size")
        self.certificate = bytes(certificate)
        self.source = source
        self.was_encrypted = was_encrypted

        cert_size = int.from_bytes(self.certificate[0:2], "big")
        if cert_size != CERTIFICATE_SIZE:
            raise KeyVaultError(
                "console certificate header is 0x%X, expected 0x1A8 - the KV is encrypted, "
                "corrupt, or not a KV at all" % cert_size)

        cqw = int.from_bytes(private_key_blob[0:4], "big")
        if cqw != 0x10:
            raise KeyVaultError("console private key header is wrong (cqw=0x%X) - the KV is "
                                "encrypted or corrupt" % cqw)
        exponent = int.from_bytes(private_key_blob[4:8], "big")
        n_raw = private_key_blob[0x10:0x90]
        n = xecrypt.bnqw_to_int(n_raw)
        p = xecrypt.bnqw_to_int(private_key_blob[0x90:0xD0])
        q = xecrypt.bnqw_to_int(private_key_blob[0xD0:0x110])
        try:
            self.key = xecrypt.RsaPrivateKey(n, exponent, p, q)
        except ValueError as exc:
            raise KeyVaultError(str(exc)) from None

        # The certificate's public key should be the same key (same XeCrypt byte
        # order).  A mismatch means a mixed-up or damaged KV: signing still runs,
        # but the console would reject the result, so it is reported loudly.
        cert_modulus = bytes(self.certificate[CERT_MODULUS:CERT_MODULUS + 0x80])
        cert_exponent = int.from_bytes(self.certificate[CERT_EXPONENT:CERT_EXPONENT + 4], "big")
        self.cert_matches_key = cert_exponent == exponent and n in (
            xecrypt.bnqw_to_int(cert_modulus),
            int.from_bytes(cert_modulus, "big"),
            int.from_bytes(cert_modulus, "little"),
        )

        self.serial = bytes(serial)

    @property
    def console_id(self):
        return self.certificate[CERT_CONSOLE_ID:CERT_CONSOLE_ID + 5]

    @property
    def part_number(self):
        raw = self.certificate[CERT_PART_NUMBER:CERT_PART_NUMBER + 0xB]
        return raw.split(b"\0")[0].decode("ascii", "replace")

    @property
    def console_type(self):
        value = int.from_bytes(self.certificate[CERT_CONSOLE_TYPE:CERT_CONSOLE_TYPE + 4], "big")
        return CONSOLE_TYPES.get(value & 3, "Unknown (0x%X)" % value)

    @property
    def manufacture_date(self):
        return self.certificate[CERT_DATE:CERT_DATE + 8].split(b"\0")[0].decode("ascii", "replace")

    @property
    def serial_number(self):
        text = self.serial.split(b"\0")[0]
        if text and all(0x20 <= c < 0x7F for c in text):
            return text.decode("ascii")
        return ""

    def sign(self, message):
        return xecrypt.xbox_pkcs1_sign(self.key, message)

    def describe(self):
        lines = [
            "Console ID      : %s" % self.console_id.hex().upper(),
            "Console type    : %s" % self.console_type,
            "Part number     : %s" % self.part_number,
            "Manufactured    : %s" % self.manufacture_date,
        ]
        if self.serial_number:
            lines.append("Serial number   : %s" % self.serial_number)
        if self.was_encrypted:
            lines.append("(KV was encrypted and has been decrypted with the CPU key)")
        if not self.cert_matches_key:
            lines.append("WARNING: the certificate in this KV does not match its private key - "
                         "packages signed with it will probably be rejected.")
        return "\n".join(lines)


def _from_plain(data, source, was_encrypted):
    base = 0 if len(data) == KV_SIZE else -0x10
    cert = data[CERTIFICATE_OFFSET + base:CERTIFICATE_OFFSET + base + CERTIFICATE_SIZE]
    prv = data[PRIVATE_KEY_OFFSET + base:PRIVATE_KEY_OFFSET + base + PRIVATE_KEY_SIZE]
    serial = data[SERIAL_OFFSET + base:SERIAL_OFFSET + base + SERIAL_SIZE]
    return KeyVault(cert, prv, serial, source=source, was_encrypted=was_encrypted)


def load_keyvault(data, cpu_key=None, source=None):
    """Load a KV from bytes.  ``cpu_key`` (16 bytes) is only needed for encrypted KVs."""
    data = bytes(data)
    if len(data) not in (KV_SIZE, KV_SIZE_NO_HEADER):
        raise KeyVaultError(
            "KV must be 16384 (0x4000) or 16368 (0x3FF0) bytes, this file is %d bytes" % len(data))
    try:
        return _from_plain(data, source, False)
    except KeyVaultError as plain_error:
        if len(data) != KV_SIZE:
            raise
        if cpu_key is None:
            raise KeyVaultError(
                "%s.\nIf this KV came straight out of a NAND dump it is still encrypted - "
                "supply your CPU key so it can be decrypted." % plain_error) from None
    return _from_plain(decrypt_keyvault(data, cpu_key), source, True)


def load_keyvault_file(path, cpu_key=None):
    with open(path, "rb") as f:
        data = f.read()
    return load_keyvault(data, cpu_key=cpu_key, source=str(path))
