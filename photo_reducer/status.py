from pathlib import Path

from . import db

ARCHIVE_DIR = Path.home() / "Desktop" / "Icloud_photos_archive"


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def run() -> None:
    if not db.DB_PATH.exists():
        print("No state DB yet. Start with: ./run.sh scan")
        return

    conn = db.connect()

    n_assets = conn.execute("SELECT COUNT(*) c FROM assets").fetchone()["c"]
    n_clusters = conn.execute("SELECT COUNT(*) c FROM clusters").fetchone()["c"]

    proposed = conn.execute(
        "SELECT proposal, COUNT(*) c FROM cluster_members GROUP BY proposal"
    ).fetchall()
    proposed_counts = {r["proposal"]: r["c"] for r in proposed}

    decided = conn.execute(
        "SELECT decision, COUNT(*) c FROM decisions GROUP BY decision"
    ).fetchall()
    decided_counts = {r["decision"]: r["c"] for r in decided}

    archive_bytes_proposed = conn.execute(
        """
        SELECT COALESCE(SUM(a.original_filesize), 0) s
        FROM cluster_members cm JOIN assets a ON a.uuid = cm.asset_uuid
        WHERE cm.proposal = 'archive'
        """
    ).fetchone()["s"]

    archive_bytes_decided = conn.execute(
        """
        SELECT COALESCE(SUM(a.original_filesize), 0) s
        FROM decisions d JOIN assets a ON a.uuid = d.asset_uuid
        WHERE d.decision = 'archive'
        """
    ).fetchone()["s"]

    n_exported = conn.execute(
        "SELECT COUNT(*) c FROM exports WHERE sha256_ok = 1"
    ).fetchone()["c"]
    n_staged = conn.execute(
        "SELECT COUNT(*) c FROM exports WHERE in_staging_album = 1"
    ).fetchone()["c"]

    conn.close()

    print(f"Assets scanned (not yet decided):  {n_assets}")
    print(f"Moments/clusters found:             {n_clusters}")
    print(
        f"Proposals: keep={proposed_counts.get('keep', 0)} "
        f"archive={proposed_counts.get('archive', 0)} "
        f"unsure={proposed_counts.get('unsure', 0)}"
    )
    print(f"Estimated recoverable (proposed):   {_human(archive_bytes_proposed)}")
    print(
        f"Decisions made: keep={decided_counts.get('keep', 0)} "
        f"archive={decided_counts.get('archive', 0)}"
    )
    print(f"Confirmed recoverable (decided):    {_human(archive_bytes_decided)}")
    print(f"Exported & verified:                {n_exported}")
    print(f"Staged in Photos album:             {n_staged}")
    print(f"Archive folder:                     {ARCHIVE_DIR}")
    print()

    if n_assets == 0:
        print("Next: ./run.sh scan")
    elif n_clusters == 0:
        print("Next: ./run.sh cluster")
    elif not proposed_counts:
        print("Next: ./run.sh propose")
    elif not decided_counts:
        print("Next: ./run.sh review")
    elif decided_counts.get("archive", 0) > n_exported:
        print("Next: ./run.sh export")
    elif n_exported > n_staged:
        print("Next: ./run.sh finalize")
    else:
        print("Everything decided is exported and staged. Re-run scan later for new photos.")
