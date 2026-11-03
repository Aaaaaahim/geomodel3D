"""工程包口令加密测试。"""
from __future__ import annotations

import pytest

pytest.importorskip("cryptography")

from geomodel3d.dataio.encryption import (
    EncryptionError,
    decrypt_bytes,
    encrypt_bytes,
    is_encrypted,
    is_encrypted_bytes,
)


def test_encrypt_decrypt_roundtrip():
    plain = b"PK\x03\x04synthetic project bytes"
    blob = encrypt_bytes(plain, "secret")
    assert is_encrypted_bytes(blob)
    assert decrypt_bytes(blob, "secret") == plain


def test_wrong_password_rejected():
    blob = encrypt_bytes(b"data", "secret")
    with pytest.raises(EncryptionError):
        decrypt_bytes(blob, "wrong")


def test_is_encrypted_file(tmp_path):
    path = tmp_path / "demo.gm3d"
    path.write_bytes(encrypt_bytes(b"data", "secret"))
    assert is_encrypted(path)
    path.write_bytes(b"PK\x03\x04plain")
    assert not is_encrypted(path)
