# iCloud Photo Reducer — Implementation Spec

Purpose: a re-runnable CLI tool that finds near-duplicate photo/video "moments" older
than 6 months in the user's macOS Photos library (synced with iCloud), proposes which
items to archive, lets the user review in a browser, exports approved originals to a
local archive folder, and stages them in a Photos album for manual deletion.

The user runs this every ~6 months to keep iCloud under quota. iCloud is 50 GB,
~35 GB is photos/videos.

**This spec contains all design decisions. Implement it as written; don't re-litigate
choices. If something is genuinely impossible, note it and pick the closest alternative.**

---

## Hard constraints & safety rules

1. **Never modify the Photos library** except: creating one album and adding photos to
   it (final stage only). No deletion — macOS does not allow scripted photo deletion;
   the user deletes manually from the staging album.
2. **Never archive anything the user hasn't approved** in the review step.
3. **Only add an item to the staging album after its export is verified on disk.**
4. All analysis must run on local metadata and local preview derivatives — do NOT
   trigger iCloud downloads of originals during scan/cluster/report. Downloads happen
   only during `export` (unavoidable and expected there).
5. Archive folder: `~/Desktop/Icloud_photos_archive` — create at process start if
   missing. User moves it to an external drive afterwards, so never assume previous
   exports are still present on disk; track archived state in the local state DB instead.

## Environment / stack

- macOS, Photos library at `~/Pictures/Photos Library.photoslibrary`, iCloud Photos on
  (possibly "Optimize Mac Storage" — that's why rule 4 matters).
- Python ≥ 3.11 (system python is 3.9 — too old). Recommend `uv` for env management:
  `brew install uv`, then a `pyproject.toml` project with a locked venv. Provide a
  `./run.sh` wrapper so the user only ever types `./run.sh <command>`.
- Dependencies:
  - `osxphotos` — read-only access to Photos DB: metadata, Apple's internal scores,
    paths to local derivatives, export with metadata. Core dependency.
  - `photoscript` (by the same author, or osxphotos' built-in album support) — create
    the staging album and add photos to it.
  - `imagehash` + `Pillow` — perceptual hashing of preview derivatives.
  - stdlib `http.server` for the review UI (no Flask, no npm, no build step).
- Terminal needs Full Disk Access (user has agreed). First run should detect a
  permission error when opening the library and print the exact System Settings path
  to fix it, then exit cleanly.

## Architecture

Single Python package `photo_reducer/` with a CLI (`argparse` subcommands) and a
SQLite state DB at `./state.db` (in the project dir, NOT in the archive folder — see
constraint 5).

Pipeline subcommands, designed to be run in order but each independently re-runnable:

```
./run.sh scan       # read Photos library metadata -> state.db
./run.sh cluster    # group into moments, compute similarity -> state.db
./run.sh propose    # rank within clusters, mark keep/archive candidates
./run.sh review     # serve web UI on localhost; user approves/overrides
./run.sh export     # export approved originals to archive folder, verify
./run.sh finalize   # add verified-exported items to staging album, print instructions
./run.sh status     # show counts + estimated GB recoverable at each stage
```

A bare `./run.sh` (no args) prints status and the next suggested command.

### State DB schema (SQLite)

- `assets`: uuid (PK), kind (photo/video), date, original_filesize, width/height,
  duration (video), is_favorite, is_edited, is_hidden, in_user_album (bool),
  burst_uuid, burst_selected (bool), score_overall, score_curation,
  is_screenshot, live_photo (bool), preview_path, last_seen_scan_id.
- `clusters`: id, start_time, end_time, item_count, total_bytes.
- `cluster_members`: cluster_id, asset_uuid, phash, rank, proposal
  (`keep` / `archive` / `unsure`).
- `decisions`: asset_uuid, decision (`keep`/`archive`), decided_at, cluster_id.
  Persisted forever — a cluster whose members all have decisions is never re-proposed
  on later runs (this is what makes 6-monthly re-runs pleasant).
- `exports`: asset_uuid, export_paths (JSON list), bytes, sha256_ok (bool),
  exported_at, in_staging_album (bool).
- `runs`: scan_id, timestamp, cutoff_date, counts.

`scan` upserts; re-running any stage is idempotent.

## Stage details

### 1. scan

- Query osxphotos `PhotosDB` for all assets with `date < now - 6 months` (cutoff
  configurable via `--months`, default 6).
- Exclude: hidden items, items already in `decisions`, items already exported
  (per `exports`), shared-library/shared-album items not owned by the user.
- Record `photo.score.overall` and `photo.score.curation` (osxphotos `ScoreInfo`) —
  these are Apple's ML quality/aesthetic scores, precomputed, free to read.
- Record `photo.path_derivatives` — pick the smallest derivative ≥ ~300px as
  `preview_path` (used for hashing and the review UI). If no local derivative exists,
  still record the asset; flag `preview_path = NULL` (UI shows a placeholder).
- Record whether the asset is in any user-created album (signal the user curated it).

### 2. cluster

Two-level grouping, photos and videos clustered together (a burst of photos + one
video of the same scene is one moment):

1. **Time clustering:** sort by capture time; start a new cluster when the gap to the
   previous item exceeds **90 seconds** (`--gap` configurable). Bursts (same
   `burst_uuid`) always land in one cluster regardless of gap.
2. **Visual sub-splitting (photos only):** within a time cluster, compute
   `imagehash.dhash` (or phash) on `preview_path`. Split the cluster into connected
   components where two photos are "similar" if hash distance ≤ **10** (tune later).
   This prevents "same minute, different subject" false groupings.
   Videos: no hashing; a video belongs to the cluster by time alone.
3. Discard clusters with < 2 members — nothing to thin there. Also discard clusters
   whose potential savings are trivial (< 3 MB total archive candidates) to keep the
   review UI focused.

### 3. propose

Within each cluster, rank photos by a keeper score:

```
keeper_score = score_overall (Apple, primary signal)
  + 10.0 if is_favorite        # user said so — effectively always keep
  +  3.0 if is_edited          # user invested effort
  +  2.0 if in_user_album      # user curated it
  +  1.0 if burst_selected     # Apple's burst pick
  +  0.5 * (megapixels / max_megapixels_in_cluster)   # mild tiebreak
```

Proposal rules:
- **Photos:** keep the top-ranked photo, plus every photo with favorite/edited/album
  flags. Propose `archive` for the rest. If a cluster has ≥ 8 photos, keep the top 2
  (long bursts deserve a second keeper).
- **Videos:** videos are the big bytes. If a cluster contains multiple videos, keep
  the longest one (or the favorite) and propose the rest. A lone video in a photo
  cluster: always `keep` by default (a video of a moment is usually the best memory) —
  but still show it in the UI so the user can override, since one override can save
  hundreds of MB.
- Mark `unsure` instead of `archive` when the score gap between keeper and candidate
  is small AND hash distance is large (> 16) — i.e., grouped by time but visually
  different. `unsure` defaults to keep in the UI.

### 4. review

- `./run.sh review` starts a stdlib HTTP server on `localhost:8765` and opens the
  browser (`webbrowser.open`).
- Server endpoints: `GET /` (the app), `GET /thumb/<uuid>` (serves `preview_path`
  bytes — never copy thumbnails anywhere), `POST /decide` (JSON body:
  `{uuid: "keep"|"archive", ...}` — writes to `decisions` immediately, so closing the
  browser mid-review loses nothing).
- UI: one card per cluster — date/time headline, row of thumbnails, each thumbnail
  outlined green (keep) or red (archive) per proposal, click to toggle. Badges for
  video (with duration + size), favorite ★, edited, low-quality. Per-cluster
  "keep all" button. Header shows running total: items to archive and estimated GB.
  Sort clusters by potential savings, largest first. Buttons: "Accept all proposals
  on this page", pagination ~50 clusters/page.
- Keyboard-light, mouse-driven, single HTML file with inline CSS/JS served by the
  Python server. No frameworks.

### 5. export

- Create `~/Desktop/Icloud_photos_archive/` if missing.
- For every `decisions.decision == 'archive'` not yet successfully exported:
  - Export via osxphotos export API: **original file** (`--download-missing`
    equivalent: use `use_photos_export`/`download_missing` option so iCloud-only
    originals get fetched), plus the **edited version if one exists**. Live Photos:
    export the paired `.mov` too. Preserve EXIF/metadata (originals keep it
    inherently; also write an `--sidecar json` per item for date/location/albums so
    nothing is lost even for formats with poor metadata support).
  - Destination layout: `Icloud_photos_archive/YYYY/YYYY-MM/` with original
    filenames, collision-suffixed.
  - Verify: file exists, size > 0, and matches expected size where osxphotos reports
    it; compute sha256 and store. Mark `sha256_ok`.
- Batch with progress output (this is the slow, network-bound stage). Resumable:
  re-run continues where it stopped. `--dry-run` prints what would be exported.
- Print a warning up front with the estimated download size and a disk-space check.

### 6. finalize

- For all verified exports not yet staged: add to Photos album
  **"Archived – safe to delete"** (create if missing) via photoscript.
- Print exact manual instructions: open Photos → that album → Select All → Delete →
  then Photos → Recently Deleted → Delete All. Note that iCloud space frees only
  after Recently Deleted is emptied (or 30 days).
- After the user confirms deletion (next run's `scan` will notice the assets are
  gone), keep their `exports`/`decisions` rows forever so they're never re-proposed.

## Edge cases to handle

- **Live Photos**: photo+video pair is ONE asset — never split the pair; export both
  components together.
- **RAW+JPEG pairs**: treat as one asset; export both files.
- **Screenshots**: excluded from moment clustering by default (they cluster by time
  but aren't "memories"). Optional separate command later — out of scope v1.
- **Missing derivatives** (`preview_path NULL`): skip hashing, cluster by time only,
  show placeholder in UI, never auto-propose `archive` for them (user can't see what
  they're approving) — mark `unsure`.
- **Timezone**: use the photo's local capture time from osxphotos for clustering and
  folder layout.
- **Photos app open during finalize**: photoscript needs Photos running; launch it.
- **Library changed between stages**: `export` must re-check the asset still exists
  in the library; if gone, mark and skip.

## Testing / verification plan

- After implementing `scan` + `cluster` + `propose`, run `./run.sh status` and sanity-
  check numbers with the user before building the rest (expected: tens of thousands of
  assets, thousands of clusters, single-digit-GB estimated savings).
- `export --dry-run` before the first real export.
- First real export: run with `--limit 20`, manually open a few exported files and
  compare against Photos, then remove the limit.

## Out of scope (v1)

- Scripted deletion (impossible on macOS), screenshot cleanup, ML de-duplication
  beyond perceptual hash, video content analysis, iCloud web API access.
