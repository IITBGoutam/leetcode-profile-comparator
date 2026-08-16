"""
credstore.py — Save the local config (usernames + session cookies) to disk so
the web UI only has to ask once.

Cookies are *not* written in the clear. On Windows they go through DPAPI
(``CryptProtectData``), which encrypts them against the logged-in Windows user
account: a copied ``config.json`` is useless on another machine or under another
account. On Linux/macOS there is no equivalent without pulling in a dependency —
this project is deliberately stdlib-only — so the value is stored as-is and the
file is chmod'ed to 0600 instead. ``protected_kind()`` reports which of the two
you actually got, and the README says so plainly.

Stored values are tagged with their scheme so old files keep working and the
reader never has to guess:

    dpapi:<base64>   encrypted, Windows, current user
    plain:<value>    not encrypted (non-Windows, or DPAPI unavailable)
    <value>          legacy hand-written config.json — read, never written
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "config.json")

# The four keys this module owns. Everything else in config.json is preserved
# untouched when we rewrite it.
COOKIE_KEYS = ("me_cookie", "friend_cookie")
NAME_KEYS = ("me", "friend")


# --------------------------------------------------------------------------- #
# Windows DPAPI via ctypes (no pywin32, no cryptography)
# --------------------------------------------------------------------------- #
_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

        @classmethod
        def of(cls, data: bytes) -> "_Blob":
            buf = ctypes.create_string_buffer(data, len(data))
            return cls(len(data), ctypes.cast(buf,
                                              ctypes.POINTER(ctypes.c_char)))

        def take(self) -> bytes:
            """Copy the payload out and free the buffer CryptoAPI gave us."""
            out = ctypes.string_at(self.pbData, self.cbData)
            ctypes.windll.kernel32.LocalFree(self.pbData)
            return out

    _crypt32 = ctypes.windll.crypt32
    _DESCRIPTION = "leetcode-comparator session cookie"


def _dpapi_encrypt(data: bytes) -> bytes | None:
    if not _IS_WINDOWS:
        return None
    blob_in, blob_out = _Blob.of(data), _Blob()
    # Flags = 0 -> current-user scope. Never CRYPTPROTECT_LOCAL_MACHINE, which
    # would let any account on this box decrypt the cookie.
    ok = _crypt32.CryptProtectData(ctypes.byref(blob_in), _DESCRIPTION,
                                   None, None, None, 0, ctypes.byref(blob_out))
    return blob_out.take() if ok else None


def _dpapi_decrypt(data: bytes) -> bytes | None:
    if not _IS_WINDOWS:
        return None
    blob_in, blob_out = _Blob.of(data), _Blob()
    ok = _crypt32.CryptUnprotectData(ctypes.byref(blob_in), None,
                                     None, None, None, 0,
                                     ctypes.byref(blob_out))
    return blob_out.take() if ok else None


def protected_kind() -> str:
    """What protect() will actually do here: "dpapi" or "plain"."""
    return "dpapi" if _IS_WINDOWS else "plain"


# --------------------------------------------------------------------------- #
# protect / unprotect
# --------------------------------------------------------------------------- #
def protect(plaintext: str) -> str:
    """Encode a secret for storage. Falls back to "plain:" if DPAPI is absent."""
    if not plaintext:
        return ""
    blob = _dpapi_encrypt(plaintext.encode("utf-8"))
    if blob is None:
        return "plain:" + plaintext
    return "dpapi:" + base64.b64encode(blob).decode("ascii")


def unprotect(stored: str | None) -> str | None:
    """Decode a stored secret, or None if it can't be read on this machine.

    Returning None rather than raising is deliberate: a config copied from
    another Windows account decrypts to nothing, and the caller should treat
    that as "no cookie saved" and ask for a fresh one — not crash.
    """
    if not stored:
        return None
    if stored.startswith("plain:"):
        return stored[len("plain:"):] or None
    if stored.startswith("dpapi:"):
        try:
            raw = base64.b64decode(stored[len("dpapi:"):], validate=True)
        except (ValueError, TypeError):
            return None
        out = _dpapi_decrypt(raw)
        if out is None:
            return None
        try:
            return out.decode("utf-8") or None
        except UnicodeDecodeError:
            return None
    # Untagged: a legacy hand-written config.json. Read it as-is.
    return stored or None


def hint(cookie: str | None) -> str:
    """A safe-to-display tail of a cookie, e.g. "…9f2a"."""
    if not cookie:
        return ""
    tail = cookie.strip()[-4:]
    return "…" + tail if tail else ""


# --------------------------------------------------------------------------- #
# config.json read / write
# --------------------------------------------------------------------------- #
def read_config_raw() -> dict:
    """The file exactly as stored — encrypted values still encrypted."""
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"warning: could not read config.json ({e})", file=sys.stderr)
        return {}


def save_config(cfg: dict) -> None:
    """Write config.json atomically, owner-readable only.

    Atomic because a half-written config.json would silently log the user out;
    a temp file in the same directory keeps os.replace on the same filesystem.
    """
    directory = os.path.dirname(CONFIG_PATH)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        try:
            os.chmod(tmp, 0o600)  # no-op in practice on Windows ACLs
        except OSError:
            pass
        os.replace(tmp, CONFIG_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_config(*, me: str | None = None, friend: str | None = None,
                  me_cookie: str | None = None,
                  friend_cookie: str | None = None,
                  clear: bool = False) -> dict:
    """Merge credential fields into config.json and save.

    Per field: a non-empty string is stored, an empty string/None leaves the
    existing value alone, and ``clear=True`` drops all four. That "empty means
    unchanged" rule is what lets the UI leave the cookie box blank on every
    visit after the first without wiping what's saved.
    """
    cfg = read_config_raw()
    if clear:
        for key in NAME_KEYS + COOKIE_KEYS:
            cfg.pop(key, None)
            cfg.pop(key + "_enc", None)
        save_config(cfg)
        return cfg

    for key, value in (("me", me), ("friend", friend)):
        if value:
            cfg[key] = value.strip()

    for key, value in (("me_cookie", me_cookie),
                       ("friend_cookie", friend_cookie)):
        # A hand-written plaintext cookie gets encrypted the first time we
        # touch the file, so people upgrading from an older config.json stop
        # keeping a live session token in the clear without doing anything.
        secret = value.strip() if value else cfg.get(key)
        if not secret:
            continue
        cfg[key + "_enc"] = protect(secret)
        # Drop the plaintext twin so there's exactly one source of truth.
        cfg.pop(key, None)

    save_config(cfg)
    return cfg
