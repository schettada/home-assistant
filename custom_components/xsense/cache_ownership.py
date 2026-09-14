"""Durable ownership of path-stable recording caches.

Callers serialize reads/updates using the resolved media root maintenance lock.
Invalid metadata is an error, never an empty replacement ledger.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from stat import S_ISREG
from typing import Any

LEDGER_NAME = ".xsense-cache-ownership.json"
LEDGER_VERSION = 1


class OwnershipError(ValueError):
    """Ownership cannot safely be established or persisted."""


class CacheIdentityConflict(OwnershipError):
    """Existing bytes cannot be attributed to the requested raw camera."""


def load_ledger(root: Path) -> dict[str, Any]:
    if not root.exists():
        return {"version": LEDGER_VERSION, "clips": {}}
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fd = os.open(LEDGER_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            return {"version": LEDGER_VERSION, "clips": {}}
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            if not S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OwnershipError("Ownership ledger is not a regular file")
            ledger = json.load(stream)
        validate_ledger(ledger)
        return ledger
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        raise OwnershipError("Ownership ledger cannot be read") from err
    finally:
        os.close(directory)


def validate_ledger(ledger: Any) -> None:
    if (
        not isinstance(ledger, dict)
        or ledger.get("version") != LEDGER_VERSION
        or not isinstance(ledger.get("clips"), dict)
    ):
        raise OwnershipError("Invalid ownership ledger schema")
    for key, record in ledger["clips"].items():
        if (
            not isinstance(key, str)
            or not key
            or "/" in key
            or "\\" in key
            or key in {".", ".."}
        ):
            raise OwnershipError("Invalid ownership clip key")
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("serial"), str)
            or not record["serial"]
            or not isinstance(record.get("owners"), dict)
        ):
            raise OwnershipError("Invalid ownership record")
        if type(record.get("conflict", False)) is not bool:
            raise OwnershipError("Invalid ownership conflict")
        for owner, claim in record["owners"].items():
            if (
                not isinstance(owner, str)
                or not owner
                or not isinstance(claim, dict)
                or claim.get("state") not in ("active", "released")
                or type(claim.get("suppressed")) is not bool
            ):
                raise OwnershipError("Invalid ownership claim")


def save_ledger(root: Path, ledger: dict[str, Any]) -> None:
    validate_ledger(ledger)
    root.mkdir(parents=True, exist_ok=True)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    temp = f"{LEDGER_NAME}.{secrets.token_hex(12)}.tmp"
    try:
        try:
            info = os.stat(LEDGER_NAME, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not S_ISREG(info.st_mode):
                raise OwnershipError("Ownership ledger is not a regular file")
        fd = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(ledger, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, LEDGER_NAME, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except OSError as err:
        raise OwnershipError("Ownership ledger could not be saved") from err
    finally:
        try:
            os.unlink(temp, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def add_claim(
    ledger: dict,
    key: str,
    serial: str,
    entry_id: str,
    *,
    activate: bool = False,
    suppressed: bool = False,
) -> None:
    record = ledger["clips"].setdefault(key, {"serial": serial, "owners": {}})
    if record["serial"] != serial:
        record["conflict"] = True
        return
    owners = record["owners"]
    if entry_id not in owners:
        owners[entry_id] = {"state": "active", "suppressed": suppressed}
    elif activate:
        owners[entry_id] = {"state": "active", "suppressed": False}


def active_owners(record: dict) -> set[str]:
    return {
        owner for owner, claim in record["owners"].items() if claim["state"] == "active"
    }


def owned_keys(ledger: dict, entry_id: str | None) -> set[str]:
    return {
        key
        for key, record in ledger["clips"].items()
        if not record.get("conflict")
        and (
            bool(active_owners(record))
            if entry_id is None
            else entry_id in active_owners(record)
        )
    }


def release_claims(
    ledger: dict, keys: set[str], entry_id: str | None, *, suppress: bool
) -> tuple[set[str], int]:
    deletable = set()
    shared = 0
    for key in keys:
        record = ledger["clips"].get(key)
        if record is None or record.get("conflict"):
            continue
        for owner, claim in record["owners"].items():
            if entry_id is None or owner == entry_id:
                claim["state"] = "released"
                claim["suppressed"] = claim["suppressed"] or suppress
        if active_owners(record):
            shared += 1
        else:
            deletable.add(key)
    return deletable, shared
