"""工程包口令加密（AES-256-GCM + PBKDF2）。

加密后的 ``.gm3d`` 仍使用同一扩展名，文件头为 ``GM3DENC1``。未加密工程
保持为普通 Zip，因此旧工程与未安装 ``cryptography`` 的环境不受影响。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

MAGIC = b"GM3DENC1"
SALT_LEN = 16
NONCE_LEN = 12
KDF_ITERATIONS = 200_000


class EncryptionError(Exception):
    """加密/解密失败的统一异常。"""


def crypto_available() -> bool:
    try:
        import cryptography  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def is_encrypted_bytes(data: bytes) -> bool:
    return data[: len(MAGIC)] == MAGIC


def is_encrypted(path: Path) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return is_encrypted_bytes(handle.read(len(MAGIC)))
    except OSError:
        return False


def _derive_key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    if not password:
        raise EncryptionError("工程密码不能为空。")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_bytes(data: bytes, password: str) -> bytes:
    """用口令加密任意字节串，返回带文件头的密文。"""
    if not crypto_available():
        raise EncryptionError(
            "工程加密需要 cryptography；请安装 geomodel3d[crypto] 后重试。"
        )
    if not password:
        raise EncryptionError("工程密码不能为空。")

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = _derive_key(password, salt)
    ciphertext = AESGCM(key).encrypt(nonce, data, MAGIC)
    return MAGIC + salt + nonce + ciphertext


def decrypt_bytes(data: bytes, password: Optional[str]) -> bytes:
    """解密 ``encrypt_bytes`` 的产物；口令错误/文件损坏抛 ``EncryptionError``。"""
    if not is_encrypted_bytes(data):
        raise EncryptionError("不是 GeoModel3D 加密工程。")
    minimum = len(MAGIC) + SALT_LEN + NONCE_LEN
    if len(data) <= minimum:
        raise EncryptionError("加密工程内容不完整。")
    if not crypto_available():
        raise EncryptionError(
            "工程已加密，但当前环境未安装 cryptography；请安装 geomodel3d[crypto]。"
        )
    if not password:
        raise EncryptionError("工程已加密，需要密码。")

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt_start = len(MAGIC)
    nonce_start = salt_start + SALT_LEN
    body_start = nonce_start + NONCE_LEN
    salt = data[salt_start:nonce_start]
    nonce = data[nonce_start:body_start]
    key = _derive_key(password, salt)
    try:
        return AESGCM(key).decrypt(nonce, data[body_start:], MAGIC)
    except InvalidTag as exc:
        raise EncryptionError("工程密码错误或文件已损坏。") from exc
    except Exception as exc:  # noqa: BLE001
        raise EncryptionError(f"解密工程失败: {exc}") from exc


__all__ = [
    "EncryptionError",
    "MAGIC",
    "crypto_available",
    "is_encrypted",
    "is_encrypted_bytes",
    "encrypt_bytes",
    "decrypt_bytes",
]
