"""Xbox 360 crypto primitives, pure Python (standard library only).

The console stores RSA numbers in "XeCrypt BnQw" order: an array of 64-bit
words, least-significant word first, each word big-endian.  Package
signatures produced by XeKeysPkcs1Create end up fully byte-reversed
(little-endian) compared to a normal big-endian RSA signature.
"""

import hashlib
import hmac

# DER prefix of a PKCS#1 v1.5 DigestInfo for SHA-1.
SHA1_DIGEST_INFO = bytes.fromhex("3021300906052b0e03021a05000414")


def sha1(*parts):
    h = hashlib.sha1()
    for part in parts:
        h.update(part)
    return h.digest()


def qword_reverse(data):
    """Reverse the order of the 8-byte words in ``data`` (BnQw <-> big-endian)."""
    if len(data) % 8:
        raise ValueError("length must be a multiple of 8")
    return b"".join(data[i:i + 8] for i in range(len(data) - 8, -1, -8))


def bnqw_to_int(data):
    return int.from_bytes(qword_reverse(bytes(data)), "big")


def int_to_bnqw(value, size):
    return qword_reverse(value.to_bytes(size, "big"))


def pkcs1_sha1_encode(digest, size):
    """EMSA-PKCS1-v1_5 encoding of a SHA-1 digest into ``size`` bytes."""
    t = SHA1_DIGEST_INFO + digest
    if size < len(t) + 11:
        raise ValueError("RSA modulus too small")
    return b"\x00\x01" + b"\xff" * (size - len(t) - 3) + b"\x00" + t


class RsaPrivateKey:
    """Minimal RSA private key using CRT for signing."""

    def __init__(self, n, e, p, q):
        if p * q != n:
            raise ValueError("p * q does not equal n - key material is corrupt or still encrypted")
        self.n = n
        self.e = e
        self.p = p
        self.q = q
        phi = (p - 1) * (q - 1)
        self.d = pow(e, -1, phi)
        self.dp = self.d % (p - 1)
        self.dq = self.d % (q - 1)
        self.qinv = pow(q, -1, p)
        self.size = (n.bit_length() + 7) // 8

    def private_op(self, m):
        m1 = pow(m, self.dp, self.p)
        m2 = pow(m, self.dq, self.q)
        h = (self.qinv * (m1 - m2)) % self.p
        s = m2 + h * self.q
        # Guard against faults: re-check with the public exponent.
        if pow(s, self.e, self.n) != m:
            raise ArithmeticError("RSA self-check failed")
        return s


def xbox_pkcs1_sign(key, message):
    """Sign ``message`` the way XeKeysPkcs1Create does; returns the stored (byte-reversed) form."""
    em = pkcs1_sha1_encode(sha1(message), key.size)
    s = key.private_op(int.from_bytes(em, "big"))
    return s.to_bytes(key.size, "little")


def xbox_pkcs1_verify(modulus, exponent, message, stored_signature):
    """Check a stored (byte-reversed) PKCS#1 SHA-1 signature against an RSA public key."""
    size = (modulus.bit_length() + 7) // 8
    if len(stored_signature) != size or modulus <= 0:
        return False
    s = int.from_bytes(stored_signature, "little")
    if s >= modulus:
        return False
    m = pow(s, exponent, modulus)
    return m.to_bytes(size, "big") == pkcs1_sha1_encode(sha1(message), size)


def rc4(key, data):
    s = list(range(256))
    j = 0
    klen = len(key)
    for i in range(256):
        j = (j + s[i] + key[i % klen]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(data))
    i = j = 0
    for k, byte in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[k] = byte ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def hmac_sha1(key, *parts):
    h = hmac.new(key, digestmod=hashlib.sha1)
    for part in parts:
        h.update(part)
    return h.digest()
