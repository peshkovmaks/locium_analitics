"""Encryption utilities for sensitive data (API keys, tokens)."""

from cryptography.fernet import Fernet, InvalidToken
from app.config import get_settings

settings = get_settings()

_fernet = None


def get_fernet() -> Fernet:
    """Get or create Fernet instance."""
    global _fernet
    if _fernet is None:
        import base64
        key = settings.encryption_key[:32].ljust(32, "0")
        encoded_key = base64.urlsafe_b64encode(key.encode())
        _fernet = Fernet(encoded_key)
    return _fernet


def encrypt_value(value: str) -> str:
    """Encrypt a string value."""
    if not value:
        return value
    f = get_fernet()
    return f.encrypt(value.encode()).decode()


def decrypt_value(encrypted_value: str) -> str:
    """Decrypt an encrypted string value. Gracefully handles plain text."""
    if not encrypted_value:
        return encrypted_value
    # If it doesn't look like a Fernet token, return as-is
    if not encrypted_value.startswith("gAAAAAB"):
        return encrypted_value
    f = get_fernet()
    try:
        return f.decrypt(encrypted_value.encode()).decode()
    except InvalidToken:
        return encrypted_value


def encrypt_dict(data: dict) -> dict:
    """Encrypt all string values in a dictionary."""
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = encrypt_value(value)
        else:
            result[key] = value
    return result


def decrypt_dict(data: dict) -> dict:
    """Decrypt all string values in a dictionary."""
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = decrypt_value(value)
        else:
            result[key] = value
    return result
