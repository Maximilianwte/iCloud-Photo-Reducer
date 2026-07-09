"""Stage 5: export approved originals to the local archive folder.

The slow, network-bound stage: iCloud-only ("optimized storage") originals
must be downloaded. Plain osxphotos export() only works for files already
local; for anything else we go through Photos itself via AppleScript
(use_photos_export), which requires Photos to have an actual open window -
a headless-launched Photos process will hang indefinitely. See run() for
the up-front check.
"""

import concurrent.futures
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from . import db

ARCHIVE_DIR = Path.home() / "Desktop" / "Icloud_photos_archive"
PER_ITEM_TIMEOUT_SECONDS = 90


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


PHOTOS_LOAD_GRACE_SECONDS = 10


def _ensure_photos_window() -> None:
    """Photos must have a real, open window for use_photos_export to work -
    a Photos process launched headlessly (e.g. by an earlier Apple Event)
    never opens one and export() hangs indefinitely against it. Photos'
    AppleScript dictionary doesn't expose window objects to query, so
    instead of detecting the (non-)existence of a window, unconditionally
    quit and cleanly relaunch it via `open -a`, which reliably reopens its
    last-used library window, then give it a grace period to load."""
    subprocess.run(
        ["osascript", "-e", 'tell application "Photos" to quit'], capture_output=True
    )
    time.sleep(2)
    subprocess.run(["open", "-a", "Photos"], capture_output=True)
    time.sleep(PHOTOS_LOAD_GRACE_SECONDS)


def _import_osxphotos():
    try:
        import osxphotos
    except ImportError:
        print("osxphotos is not installed. Run: uv sync", file=sys.stderr)
        raise SystemExit(1)
    return osxphotos


def _dest_dir(date_str: str) -> Path:
    year = date_str[:4]
    month = date_str[:7]
    d = ARCHIVE_DIR / year / month
    d.mkdir(parents=True, exist_ok=True)
    return d


def _export_one(photo, dest_dir: Path) -> list[str]:
    """Export original (+ edited/live/raw companions) for one photo.
    Tries the fast local-copy path first, falls back to Photos/AppleScript
    for iCloud-only originals."""
    kwargs = dict(
        live_photo=photo.live_photo,
        raw_photo=photo.path_raw is not None,
        sidecar_json=True,
        increment=True,
    )

    if not photo.ismissing:
        paths = photo.export(str(dest_dir), **kwargs)
    else:
        paths = photo.export(str(dest_dir), use_photos_export=True, timeout=60, **kwargs)

    if photo.hasadjustments:
        try:
            paths += photo.export(str(dest_dir), edited=True, increment=True)
        except Exception:
            pass

    return paths


def run(dry_run: bool = False, limit: int | None = None) -> dict:
    osxphotos = _import_osxphotos()
    conn = db.connect()

    to_export = conn.execute(
        """
        SELECT d.asset_uuid, a.date, a.original_filesize
        FROM decisions d
        JOIN assets a ON a.uuid = d.asset_uuid
        LEFT JOIN exports e ON e.asset_uuid = d.asset_uuid AND e.sha256_ok = 1
        WHERE d.decision = 'archive' AND e.asset_uuid IS NULL
        ORDER BY a.date
        """
    ).fetchall()

    if limit:
        to_export = to_export[:limit]

    if not to_export:
        print("Nothing to export.")
        return {"exported": 0, "failed": 0, "skipped_missing_from_library": 0}

    total_bytes = sum(r["original_filesize"] or 0 for r in to_export)
    print(f"{len(to_export)} items to export, ~{total_bytes / (1024**3):.2f} GB")

    if dry_run:
        for r in to_export:
            print(f"  would export {r['asset_uuid']} ({r['date']})")
        return {"exported": 0, "failed": 0, "skipped_missing_from_library": 0}

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    photosdb = osxphotos.PhotosDB()

    any_missing = False
    for r in to_export:
        p = photosdb.get_photo(r["asset_uuid"])
        if p is not None and p.ismissing:
            any_missing = True
            break

    if any_missing:
        print("Some originals are iCloud-only; restarting Photos to ensure it has a window open...")
        _ensure_photos_window()

    exported = 0
    failed = 0
    skipped_missing_from_library = 0

    for i, row in enumerate(to_export, 1):
        uuid = row["asset_uuid"]
        photo = photosdb.get_photo(uuid)
        if photo is None:
            print(f"[{i}/{len(to_export)}] {uuid}: no longer in library, skipping")
            skipped_missing_from_library += 1
            continue

        dest_dir = _dest_dir(row["date"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_export_one, photo, dest_dir)
            try:
                paths = future.result(timeout=PER_ITEM_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                print(
                    f"[{i}/{len(to_export)}] {uuid}: timed out after "
                    f"{PER_ITEM_TIMEOUT_SECONDS}s, skipping (will retry next run)"
                )
                failed += 1
                continue
            except Exception as e:
                print(f"[{i}/{len(to_export)}] {uuid}: export failed: {e}")
                failed += 1
                continue

        if not paths:
            print(f"[{i}/{len(to_export)}] {uuid}: export produced no files, skipping")
            failed += 1
            continue

        ok = True
        total_size = 0
        for p_str in paths:
            path = Path(p_str)
            if not path.exists() or path.stat().st_size == 0:
                ok = False
                break
            total_size += path.stat().st_size
            try:
                _sha256(path)
            except OSError:
                ok = False
                break

        if not ok:
            print(f"[{i}/{len(to_export)}] {uuid}: verification failed")
            failed += 1
            continue

        conn.execute(
            """
            INSERT INTO exports (asset_uuid, export_paths, bytes, sha256_ok, exported_at)
            VALUES (?, ?, ?, 1, datetime('now'))
            ON CONFLICT(asset_uuid) DO UPDATE SET
                export_paths=excluded.export_paths, bytes=excluded.bytes,
                sha256_ok=1, exported_at=excluded.exported_at
            """,
            (uuid, json.dumps([str(p) for p in paths]), total_size),
        )
        conn.commit()
        exported += 1
        print(f"[{i}/{len(to_export)}] {uuid}: exported {len(paths)} file(s), {total_size} bytes")

    conn.close()

    return {
        "exported": exported,
        "failed": failed,
        "skipped_missing_from_library": skipped_missing_from_library,
    }
