"""Stage 6: stage verified exports in a Photos album for manual deletion.

macOS gives no way to delete photos via script, so this is as far as
automation goes: put every successfully-exported item into one album so the
user can select-all and delete themselves.
"""

from . import db

ALBUM_NAME = "Archived - safe to delete"


def run() -> dict:
    conn = db.connect()

    rows = conn.execute(
        "SELECT asset_uuid FROM exports WHERE sha256_ok = 1 AND in_staging_album = 0"
    ).fetchall()

    if not rows:
        print("Nothing new to stage.")
        conn.close()
        return {"staged": 0}

    uuids = [r["asset_uuid"] for r in rows]

    import photoscript

    lib = photoscript.PhotosLibrary()
    album = lib.album(ALBUM_NAME) or lib.create_album(ALBUM_NAME)

    photos = list(lib.photos(uuid=uuids))
    found_uuids = {p.uuid for p in photos}
    album.add(photos)

    for uuid in found_uuids:
        conn.execute(
            "UPDATE exports SET in_staging_album = 1 WHERE asset_uuid = ?", (uuid,)
        )
    conn.commit()

    missing = set(uuids) - found_uuids
    if missing:
        print(f"{len(missing)} exported item(s) no longer found in the library, skipped.")

    conn.close()

    print(f"Staged {len(found_uuids)} item(s) in the '{ALBUM_NAME}' album.")
    print()
    print("To actually free up iCloud space:")
    print(f"  1. Open Photos -> Albums -> '{ALBUM_NAME}'")
    print("  2. Select All (Cmd+A) -> Delete")
    print("  3. Go to the 'Recently Deleted' album -> Select All -> Delete Immediately")
    print("     (space is not freed until this step, or after 30 days automatically)")

    return {"staged": len(found_uuids)}
