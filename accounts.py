import hashlib
import hmac
from contextlib import closing

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    name TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    recovery_hash TEXT NOT NULL,
    setup_required INTEGER NOT NULL DEFAULT 1,
    revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS account_devices (
    token_hash TEXT PRIMARY KEY,
    name TEXT NOT NULL REFERENCES accounts(name),
    revision INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS account_sessions (
    token_hash TEXT PRIMARY KEY,
    name TEXT NOT NULL REFERENCES accounts(name),
    revision INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_account_sessions_expiry ON account_sessions(expires_at);
"""


def key_digest(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def key_matches(key, account):
    return bool(key and account) and hmac.compare_digest(key_digest(key), account["recovery_hash"])


def validate_roster(members, recovery_hashes):
    if (not isinstance(members, dict) or list(members.values()).count("materials") != 1
            or "owner" not in members.values() or set(members.values()) - {"owner", "materials"}
            or any(not name or name != name.strip() or len(name) > 40 for name in members)
            or set(recovery_hashes) != set(members)):
        raise ValueError("Configure at least one designer, exactly one materials coordinator, and one recovery hash per member.")


def seed_accounts(db, members, recovery_hashes, initial_password_hash):
    existing = {row["name"]: row["role"] for row in db.execute("SELECT name, role FROM accounts")}
    if any(members.get(name) != role for name, role in existing.items()):
        raise ValueError("The configured roster cannot remove or change the role of an existing account.")
    for name, role in members.items():
        if name not in existing:
            db.execute("INSERT INTO accounts(name, role, password_hash, recovery_hash) VALUES (?, ?, ?, ?)",
                       (name, role, initial_password_hash, recovery_hashes[name]))


def fetch_account(database, name):
    with closing(database()) as db:
        return db.execute("SELECT * FROM accounts WHERE name = ?", (name,)).fetchone()
