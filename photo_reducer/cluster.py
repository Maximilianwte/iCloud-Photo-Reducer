"""Stage 2: group scanned assets into "moments" - bursts of near-identical
shots taken close together in time, optionally split by visual dissimilarity.
"""

from datetime import datetime

from . import db

TIME_GAP_SECONDS = 90
HASH_DISTANCE_THRESHOLD = 10
MIN_CLUSTER_SAVINGS_BYTES = 3 * 1024 * 1024


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _hash_photo(path: str):
    import imagehash
    from PIL import Image

    try:
        with Image.open(path) as im:
            return imagehash.dhash(im)
    except Exception:
        return None


def _time_group(assets: list[dict], gap_seconds: int) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    prev_date = None
    prev_burst_key = None

    for asset in assets:
        date = datetime.fromisoformat(asset["date"])
        same_burst = (
            asset["burst_key"] is not None and asset["burst_key"] == prev_burst_key
        )
        if current and not same_burst and prev_date is not None:
            if (date - prev_date).total_seconds() > gap_seconds:
                groups.append(current)
                current = []
        current.append(asset)
        prev_date = date
        prev_burst_key = asset["burst_key"]

    if current:
        groups.append(current)
    return groups


def _visual_split(photo_assets: list[dict], hashes: dict[str, object]) -> list[list[dict]]:
    """Split a time group's photos into connected components by dhash
    similarity. Assets with no local preview form their own singleton
    component (never merged blindly). Populates `hashes` (uuid -> hash) as
    a side effect so callers can reuse them without re-hashing."""
    hashable = []
    for a in photo_assets:
        if not a["preview_path"]:
            continue
        h = _hash_photo(a["preview_path"])
        if h is not None:
            hashes[a["uuid"]] = h
            hashable.append(a)

    uf = UnionFind(len(hashable))
    for i in range(len(hashable)):
        for j in range(i + 1, len(hashable)):
            dist = hashes[hashable[i]["uuid"]] - hashes[hashable[j]["uuid"]]
            if dist <= HASH_DISTANCE_THRESHOLD:
                uf.union(i, j)

    components: dict[int, list[dict]] = {}
    for i, a in enumerate(hashable):
        components.setdefault(uf.find(i), []).append(a)

    result = list(components.values())

    no_preview = [a for a in photo_assets if a["uuid"] not in hashes]
    for a in no_preview:
        result.append([a])

    return result


def run() -> dict:
    conn = db.connect()
    conn.execute("DELETE FROM cluster_members")
    conn.execute("DELETE FROM clusters")

    decided = {r["asset_uuid"] for r in conn.execute("SELECT asset_uuid FROM decisions")}
    rows = conn.execute("SELECT * FROM assets ORDER BY date").fetchall()
    assets = [dict(r) for r in rows if r["uuid"] not in decided]

    time_groups = _time_group(assets, TIME_GAP_SECONDS)

    clusters_created = 0
    clusters_discarded = 0

    for group in time_groups:
        photos = [a for a in group if a["kind"] == "photo"]
        videos = [a for a in group if a["kind"] == "video"]

        hashes: dict[str, object] = {}
        sub_groups = _visual_split(photos, hashes) if photos else []

        if videos:
            if sub_groups:
                largest = max(sub_groups, key=len)
                largest.extend(videos)
            else:
                sub_groups = [videos]

        for members in sub_groups:
            if len(members) < 2:
                clusters_discarded += 1
                continue

            sizes = [m["original_filesize"] or 0 for m in members]
            total_bytes = sum(sizes)
            potential_savings = total_bytes - max(sizes)
            if potential_savings < MIN_CLUSTER_SAVINGS_BYTES:
                clusters_discarded += 1
                continue

            dates = [datetime.fromisoformat(m["date"]) for m in members]
            cur = conn.execute(
                "INSERT INTO clusters (start_time, end_time, item_count, total_bytes) "
                "VALUES (?, ?, ?, ?)",
                (min(dates).isoformat(), max(dates).isoformat(), len(members), total_bytes),
            )
            cluster_id = cur.lastrowid

            for m in members:
                hv = hashes.get(m["uuid"])
                h = str(hv) if hv is not None else None
                conn.execute(
                    "INSERT INTO cluster_members (cluster_id, asset_uuid, phash) "
                    "VALUES (?, ?, ?)",
                    (cluster_id, m["uuid"], h),
                )
            clusters_created += 1

    conn.commit()
    conn.close()

    return {"clusters_created": clusters_created, "clusters_discarded": clusters_discarded}
