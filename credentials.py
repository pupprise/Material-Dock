import hashlib
import hmac
import secrets


def password_record(code):
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(code.encode("utf-8"), salt=salt, n=16384, r=8, p=1).hex()
    return f"scrypt${salt.hex()}${value}"


def password_matches(code, record):
    if not record.startswith("scrypt$"):
        return hmac.compare_digest(record, hashlib.sha256(code.encode("utf-8")).hexdigest())
    _, salt, expected = record.split("$")
    value = hashlib.scrypt(code.encode("utf-8"), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(value, expected)
