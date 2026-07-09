"""Stage 3: rank cluster members and propose keep/archive/unsure."""

from . import db

UNSURE_SCORE_GAP = 0.05
UNSURE_HASH_DISTANCE = 16
LARGE_BURST_THRESHOLD = 8


def _keeper_score(asset: dict, max_megapixels: float) -> float:
    score = asset["score_overall"] if asset["score_overall"] is not None else 0.0
    if asset["is_favorite"]:
        score += 10.0
    if asset["is_edited"]:
        score += 3.0
    if asset["in_user_album"]:
        score += 2.0
    if asset["burst_selected"]:
        score += 1.0
    if max_megapixels > 0:
        mp = ((asset["width"] or 0) * (asset["height"] or 0)) / 1_000_000
        score += 0.5 * (mp / max_megapixels)
    return score


def _hash_distance(h1: str | None, h2: str | None) -> int:
    if not h1 or not h2:
        return 999
    import imagehash

    return imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)


def run() -> dict:
    conn = db.connect()

    cluster_ids = [r["id"] for r in conn.execute("SELECT id FROM clusters")]

    total_keep = 0
    total_archive = 0
    total_unsure = 0

    for cluster_id in cluster_ids:
        members = conn.execute(
            """
            SELECT a.*, cm.phash AS phash
            FROM cluster_members cm JOIN assets a ON a.uuid = cm.asset_uuid
            WHERE cm.cluster_id = ?
            """,
            (cluster_id,),
        ).fetchall()
        members = [dict(m) for m in members]

        photos = [m for m in members if m["kind"] == "photo"]
        videos = [m for m in members if m["kind"] == "video"]

        max_mp = 0.0
        for m in photos:
            mp = ((m["width"] or 0) * (m["height"] or 0)) / 1_000_000
            max_mp = max(max_mp, mp)

        for m in photos:
            m["_score"] = _keeper_score(m, max_mp)
        photos.sort(key=lambda m: m["_score"], reverse=True)

        num_keepers = 2 if len(photos) >= LARGE_BURST_THRESHOLD else 1
        keeper_ids = set()
        for i, m in enumerate(photos):
            if i < num_keepers:
                keeper_ids.add(m["uuid"])
            elif m["is_favorite"] or m["is_edited"] or m["in_user_album"]:
                keeper_ids.add(m["uuid"])

        best = photos[0] if photos else None
        for rank, m in enumerate(photos):
            if m["uuid"] in keeper_ids:
                proposal = "keep"
            else:
                gap = (best["_score"] - m["_score"]) if best else 999
                dist = _hash_distance(best["phash"], m["phash"]) if best else 999
                if gap < UNSURE_SCORE_GAP and dist > UNSURE_HASH_DISTANCE:
                    proposal = "unsure"
                else:
                    proposal = "archive"

            conn.execute(
                "UPDATE cluster_members SET rank = ?, proposal = ? "
                "WHERE cluster_id = ? AND asset_uuid = ?",
                (rank, proposal, cluster_id, m["uuid"]),
            )
            if proposal == "keep":
                total_keep += 1
            elif proposal == "archive":
                total_archive += 1
            else:
                total_unsure += 1

        if videos:
            videos.sort(
                key=lambda m: (m["is_favorite"], m["duration"] or 0), reverse=True
            )
            video_keeper = videos[0]["uuid"]
            multiple_videos = len(videos) > 1
            for rank, m in enumerate(videos):
                if not multiple_videos or m["uuid"] == video_keeper:
                    proposal = "keep"
                else:
                    proposal = "archive"
                conn.execute(
                    "UPDATE cluster_members SET rank = ?, proposal = ? "
                    "WHERE cluster_id = ? AND asset_uuid = ?",
                    (rank, proposal, cluster_id, m["uuid"]),
                )
                if proposal == "keep":
                    total_keep += 1
                else:
                    total_archive += 1

    conn.commit()
    conn.close()

    return {"keep": total_keep, "archive": total_archive, "unsure": total_unsure}
