import unittest

from x360resign import xecrypt
from x360resign.keyvault import (KeyVaultError, encrypt_keyvault, load_keyvault, parse_cpu_key)

from tests.builder import make_keyvault, make_rsa_1024

CPU_KEY = bytes.fromhex("0F1E2D3C4B5A69788796A5B4C3D2E1F0")

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    HAVE_CRYPTOGRAPHY = True
except BaseException as exc:  # not installed, or a broken install (pyo3 panics) - optional check
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        raise
    HAVE_CRYPTOGRAPHY = False


class XeCryptTests(unittest.TestCase):
    def test_qword_reverse(self):
        data = bytes(range(16))
        self.assertEqual(xecrypt.qword_reverse(data), bytes(range(8, 16)) + bytes(range(8)))
        self.assertEqual(xecrypt.bnqw_to_int(xecrypt.int_to_bnqw(123456789, 16)), 123456789)

    def test_sign_verify_roundtrip(self):
        n, e, p, q = make_rsa_1024(5)
        key = xecrypt.RsaPrivateKey(n, e, p, q)
        sig = xecrypt.xbox_pkcs1_sign(key, b"hello header")
        self.assertEqual(len(sig), 0x80)
        self.assertTrue(xecrypt.xbox_pkcs1_verify(n, e, b"hello header", sig))
        self.assertFalse(xecrypt.xbox_pkcs1_verify(n, e, b"hello headeR", sig))
        self.assertFalse(xecrypt.xbox_pkcs1_verify(n, e, b"hello header", sig[::-1]))

    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography package not installed")
    def test_signature_is_standard_pkcs1_byte_reversed(self):
        """Independent check: reversed signature verifies with a stock RSA library."""
        n, e, p, q = make_rsa_1024(6)
        key = xecrypt.RsaPrivateKey(n, e, p, q)
        message = bytes(range(256)) * 2
        sig = xecrypt.xbox_pkcs1_sign(key, message)
        public = rsa.RSAPublicNumbers(e, n).public_key()
        public.verify(sig[::-1], message, padding.PKCS1v15(), hashes.SHA1())  # raises if wrong

    def test_rejects_bad_key(self):
        n, e, p, q = make_rsa_1024(7)
        with self.assertRaises(ValueError):
            xecrypt.RsaPrivateKey(n + 2, e, p, q)

    def test_rc4_known_vector(self):
        # RFC 6229 style vector: key "Key", plaintext "Plaintext"
        self.assertEqual(xecrypt.rc4(b"Key", b"Plaintext").hex(), "bbf316e8d940af0ad3")


class KeyVaultTests(unittest.TestCase):
    def test_plain_kv(self):
        kv = load_keyvault(make_keyvault(seed=3, console_id=bytes.fromhex("0A0B0C0D0E")))
        self.assertEqual(kv.console_id.hex().upper(), "0A0B0C0D0E")
        self.assertEqual(kv.console_type, "Retail")
        self.assertEqual(kv.manufacture_date, "09-18-08")
        self.assertEqual(kv.serial_number, "123456789012")
        self.assertTrue(kv.cert_matches_key)
        self.assertIn("0A0B0C0D0E", kv.describe())

    def test_kv_without_digest_header(self):
        kv = load_keyvault(make_keyvault(seed=3, header=False))
        self.assertTrue(kv.cert_matches_key)

    def test_encrypted_kv(self):
        plain = make_keyvault(seed=4)
        enc = encrypt_keyvault(plain, CPU_KEY)
        self.assertNotEqual(enc, plain)
        with self.assertRaises(KeyVaultError) as ctx:
            load_keyvault(enc)
        self.assertIn("CPU key", str(ctx.exception))
        kv = load_keyvault(enc, cpu_key=CPU_KEY)
        self.assertTrue(kv.was_encrypted)
        self.assertEqual(kv.certificate, load_keyvault(plain).certificate)
        with self.assertRaises(KeyVaultError):
            load_keyvault(enc, cpu_key=bytes(16))

    def test_bad_sizes_and_garbage(self):
        with self.assertRaises(KeyVaultError):
            load_keyvault(b"\0" * 100)
        with self.assertRaises(KeyVaultError):
            load_keyvault(b"\0" * 0x4000)

    def test_mismatched_certificate_is_flagged(self):
        kv = load_keyvault(make_keyvault(seed=8, mismatched_cert=True))
        self.assertFalse(kv.cert_matches_key)
        self.assertIn("WARNING", kv.describe())

    def test_parse_cpu_key(self):
        self.assertEqual(parse_cpu_key("0x" + CPU_KEY.hex()), CPU_KEY)
        self.assertEqual(parse_cpu_key(" ".join(CPU_KEY.hex()[i:i + 4] for i in range(0, 32, 4))),
                         CPU_KEY)
        with self.assertRaises(KeyVaultError):
            parse_cpu_key("1234")
        with self.assertRaises(KeyVaultError):
            parse_cpu_key("zz" * 16)


if __name__ == "__main__":
    unittest.main()
