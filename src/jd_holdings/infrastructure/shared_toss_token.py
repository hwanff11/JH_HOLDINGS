from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_SHARED_TOKEN_MAX_AGE_SECONDS = 18 * 60 * 60


class SharedTossTokenCache:
    """Cross-process Toss OAuth token cache for services sharing one API client.

    Toss may revoke an older client-credentials token when another process obtains a
    replacement token for the same credentials. JH_HOLDINGS and the independent CCI
    bot can therefore invalidate each other when they use the same Toss app. This
    cache serializes token issuance with an OS file lock and lets every local process
    converge on the newest token.

    The cache is opt-in through ``TOSS_SHARED_TOKEN_CACHE``. Entries are keyed by a
    SHA-256 fingerprint of the credential pair, so different Toss apps can safely use
    the same cache file without seeing or overwriting one another's token. The file
    itself must live in a private directory shared only by the runtime user.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None,
        *,
        client_id: str | None,
        client_secret: str | None,
        max_age_seconds: int = DEFAULT_SHARED_TOKEN_MAX_AGE_SECONDS,
    ) -> None:
        self.path = Path(path).expanduser() if path else None
        self.max_age_seconds = max(60, int(max_age_seconds))
        source = f"{client_id or ''}\0{client_secret or ''}".encode()
        self._fingerprint = hashlib.sha256(source).hexdigest()

    @classmethod
    def from_env(
        cls,
        *,
        client_id: str | None,
        client_secret: str | None,
    ) -> SharedTossTokenCache:
        raw_age = os.getenv("TOSS_SHARED_TOKEN_MAX_AGE_SECONDS", "")
        try:
            max_age = int(raw_age) if raw_age else DEFAULT_SHARED_TOKEN_MAX_AGE_SECONDS
        except ValueError:
            max_age = DEFAULT_SHARED_TOKEN_MAX_AGE_SECONDS
        return cls(
            os.getenv("TOSS_SHARED_TOKEN_CACHE"),
            client_id=client_id,
            client_secret=client_secret,
            max_age_seconds=max_age,
        )

    @property
    def enabled(self) -> bool:
        return self.path is not None

    @contextmanager
    def locked(self) -> Iterator[None]:
        if self.path is None:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                os.chmod(lock_path, 0o600)
            except OSError:
                pass
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_all_locked(self) -> dict[str, dict[str, object]]:
        if self.path is None or not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        entries = payload.get("entries", payload)
        if not isinstance(entries, dict):
            return {}
        return {
            str(key): dict(value)
            for key, value in entries.items()
            if isinstance(value, dict)
        }

    def load_locked(self, *, exclude_token: str | None = None) -> str | None:
        entry = self._read_all_locked().get(self._fingerprint)
        if not entry:
            return None
        token = entry.get("access_token")
        issued_at = entry.get("issued_at")
        if not isinstance(token, str) or not token:
            return None
        if exclude_token is not None and token == exclude_token:
            return None
        try:
            age = time.time() - float(issued_at)
        except (TypeError, ValueError):
            return None
        if age < -300 or age > self.max_age_seconds:
            return None
        return token

    def discard_locked(self, token: str | None) -> None:
        if self.path is None or not token:
            return
        entries = self._read_all_locked()
        entry = entries.get(self._fingerprint)
        if not entry or entry.get("access_token") != token:
            return
        entries.pop(self._fingerprint, None)
        self._write_all_locked(entries)

    def store_locked(self, token: str) -> None:
        if self.path is None:
            return
        entries = self._read_all_locked()
        entries[self._fingerprint] = {
            "access_token": token,
            "issued_at": time.time(),
        }
        self._write_all_locked(entries)

    def _write_all_locked(self, entries: dict[str, dict[str, object]]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump({"entries": entries}, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
