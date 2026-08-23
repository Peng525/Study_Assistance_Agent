"""安全工具：密码 hash、JWT 签发/校验、API Key 加密。"""

import base64
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

# ---- 密码 hash ----


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---- JWT ----


def create_access_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=settings.jwt_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    """解码 JWT，无效或过期返回 None。"""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ---- API Key AES-GCM 加密 ----


def _derive_key() -> bytes:
    """从 APP_SECRET 派生 32 字节 AES 密钥（若无 APP_SECRET 则回退 JWT_SECRET）。"""
    secret = getattr(settings, "app_secret", None) or settings.jwt_secret or "dev-fallback-secret"
    digest = hashes.Hash(hashes.SHA256())
    digest.update(secret.encode("utf-8"))
    return digest.finalize()


def encrypt_api_key(plain: str) -> str:
    """AES-GCM 加密，返回 base64(nonce + ciphertext)。"""
    nonce = os.urandom(12)
    aesgcm = AESGCM(_derive_key())
    ciphertext = aesgcm.encrypt(nonce, plain.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("utf-8")


def decrypt_api_key(encrypted: str) -> str:
    """AES-GCM 解密，失败返回空串。"""
    try:
        raw = base64.b64decode(encrypted.encode("utf-8"))
        nonce, ciphertext = raw[:12], raw[12:]
        aesgcm = AESGCM(_derive_key())
        return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
    except Exception:
        return ""
