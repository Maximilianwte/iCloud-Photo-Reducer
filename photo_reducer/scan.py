"""Stage 1: read the Photos library (read-only) into the local state DB.

Never triggers iCloud downloads: only reads metadata and existing local
derivatives (thumbnails Photos already keeps on disk even when originals are
optimized away to iCloud).
"""

import json
import sys
from datetime import datetime, timedelta

from . import db

LIBRARY_HINT = (
    "Could not read the Photos library.\n\n"
    "This usually means Full Disk Access hasn't been granted to the app running "
    "this terminal session yet:\n"
    "  System Settings -> Privacy & Security -> Full Disk Access -> add the app, "
    "toggle it on, then fully quit and restart it.\n"
)


def _import_osxphotos():
    try:
        import osxphotos
    except ImportError:
        print("osxphotos is not installed. Run: uv sync", file=sys.stderr)
        raise SystemExit(1)
    return osxphotos


def _pick_preview_path(photo) -> str | None:
    """Pick the smallest local derivative that's still >= ~300px on its long
    side, to keep hashing/UI fast without needing a full-res download."""
    from PIL import Image

    candidates = []
    for path in photo.path_derivatives or []:
        try:
            with Image.open(path) as im:
                long_side = max(im.size)
            candidates.append((long_side, path))
        except Exception:
            continue

    if not candidates:
        return None

    big_enough = [c for c in candidates if c[0] >= 300]
    pool = big_enough if big_enough else candidates
    pool.sort(key=lambda c: c[0])
    return pool[0][1]


def run(months: int = 6) -> dict:
    osxphotos = _import_osxphotos()

    try:
        photosdb = osxphotos.PhotosDB()
    except Exception as e:
        print(LIBRARY_HINT, file=sys.stderr)
        print(f"Underlying error: {e}", file=sys.stderr)
        raise SystemExit(1)

    db.init_db()
    conn = db.connect()

    cutoff = datetime.now().astimezone() - timedelta(days=months * 30.44)

    already_decided = {
        row["asset_uuid"] for row in conn.execute("SELECT asset_uuid FROM decisions")
    }
    already_exported = {
        row["asset_uuid"] for row in conn.execute("SELECT asset_uuid FROM exports")
    }

    scanned = 0
    kept = 0
    skipped_hidden = 0
    skipped_shared = 0
    skipped_decided = 0
    skipped_recent = 0
    no_preview = 0

    timestamp = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO runs (timestamp, cutoff_date, counts) VALUES (?, ?, ?)",
        (timestamp, cutoff.isoformat(), "{}"),
    )
    scan_id = cur.lastrowid

    for photo in photosdb.photos():
        scanned += 1

        if photo.hidden:
            skipped_hidden += 1
            continue
        if photo.shared:
            skipped_shared += 1
            continue
        if photo.uuid in already_decided or photo.uuid in already_exported:
            skipped_decided += 1
            continue
        if photo.date > cutoff:
            skipped_recent += 1
            continue

        preview_path = _pick_preview_path(photo)
        if preview_path is None:
            no_preview += 1

        duration = None
        if photo.ismovie and photo.exif_info is not None:
            duration = photo.exif_info.duration

        score_overall = photo.score.overall if photo.score else None
        score_curation = photo.score.curation if photo.score else None

        conn.execute(
            """
            INSERT INTO assets (
                uuid, kind, date, original_filesize, width, height, duration,
                is_favorite, is_edited, is_hidden, in_user_album,
                burst_key, burst_selected, score_overall, score_curation,
                is_screenshot, live_photo, preview_path, last_seen_scan_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uuid) DO UPDATE SET
                kind=excluded.kind, date=excluded.date,
                original_filesize=excluded.original_filesize,
                width=excluded.width, height=excluded.height,
                duration=excluded.duration, is_favorite=excluded.is_favorite,
                is_edited=excluded.is_edited, is_hidden=excluded.is_hidden,
                in_user_album=excluded.in_user_album, burst_key=excluded.burst_key,
                burst_selected=excluded.burst_selected,
                score_overall=excluded.score_overall,
                score_curation=excluded.score_curation,
                is_screenshot=excluded.is_screenshot, live_photo=excluded.live_photo,
                preview_path=excluded.preview_path,
                last_seen_scan_id=excluded.last_seen_scan_id
            """,
            (
                photo.uuid,
                "video" if photo.ismovie else "photo",
                photo.date.isoformat(),
                photo.original_filesize,
                photo.width,
                photo.height,
                duration,
                int(photo.favorite),
                int(photo.hasadjustments),
                int(photo.hidden),
                int(bool(photo.albums)),
                photo.burst_key,
                int(photo.burst_selected) if photo.burst else 0,
                score_overall,
                score_curation,
                int(photo.screenshot),
                int(photo.live_photo),
                preview_path,
                scan_id,
            ),
        )
        kept += 1

    counts = {
        "scanned": scanned,
        "kept": kept,
        "skipped_hidden": skipped_hidden,
        "skipped_shared": skipped_shared,
        "skipped_already_decided_or_exported": skipped_decided,
        "skipped_too_recent": skipped_recent,
        "no_local_preview": no_preview,
    }
    conn.execute("UPDATE runs SET counts = ? WHERE scan_id = ?", (json.dumps(counts), scan_id))
    conn.commit()
    conn.close()

    return counts
