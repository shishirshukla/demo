"""File-backed Kite credentials, kept outside market-data exports."""

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

CREDENTIALS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "private" / "kite_credentials.json"
)


@dataclass(frozen=True)
class KiteCredentials:
    api_key: str = field(repr=False)
    access_token: str = field(repr=False)


def _credentials(payload):
    if not isinstance(payload, dict) or any(
        not isinstance(payload.get(key), str)
        or not payload[key].strip()
        or any(character.isspace() for character in payload[key].strip())
        for key in ("api_key", "access_token")
    ):
        raise ValueError(
            "Invalid Kite credential file. Connect with fresh credentials."
        )
    return KiteCredentials(payload["api_key"].strip(), payload["access_token"].strip())


def load_kite_credentials(*, path=None):
    """Read credentials without exposing file contents in parsing errors."""
    source = Path(path) if path is not None else CREDENTIALS_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeError):
        raise ValueError(
            "Invalid Kite credential file. Connect with fresh credentials."
        ) from None
    return _credentials(payload)


def save_kite_credentials(api_key, access_token, *, path=None):
    """Atomically replace the token file with owner-only access on POSIX."""
    credentials = _credentials({"api_key": api_key, "access_token": access_token})
    destination = Path(path) if path is not None else CREDENTIALS_PATH
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(
        prefix=".kite_credentials_", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    try:
        # mkstemp creates the file with mode 0600 before any secrets are written.
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "api_key": credentials.api_key,
                    "access_token": credentials.access_token,
                },
                stream,
                indent=2,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def clear_kite_credentials(*, path=None):
    destination = Path(path) if path is not None else CREDENTIALS_PATH
    destination.unlink(missing_ok=True)
