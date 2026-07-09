# iCloud Photo Reducer

A local, re-runnable tool that finds near-duplicate photo/video "moments" in
your macOS Photos library (bursts, near-identical shots, redundant videos of
the same scene) and helps you archive the extras to free up iCloud storage —
without deleting your memories.

It reads your Photos library read-only, uses Apple's own on-device quality
scores (the same ones Photos uses internally) to judge which shot in a burst
is the best one, lets you review its proposals in a simple local web UI, and
then exports the originals you approve to a local archive folder before
staging them in a Photos album for you to delete yourself.

**Nothing is ever deleted automatically.** macOS does not allow scripted
photo deletion, and this tool doesn't try to work around that — the last
step is always a manual, deliberate action you take in Photos.

## Why

If you've had iCloud Photos on for years, you likely have many "moments"
where you took 5–20 near-identical photos to pick the best one later, plus
short videos that duplicate a photo of the same scene. Individually small,
collectively these can be a large fraction of your library. This tool finds
those moments and proposes trimming the extras while always keeping at least
one (usually the best-scored) representative of each.

## How it works

1. **`scan`** — reads your Photos library metadata (capture time, file size,
   Apple's quality/aesthetic scores, favorite/edited/album flags) for
   everything older than a cutoff age (default 6 months). Only reads local
   thumbnails already cached by Photos — never triggers iCloud downloads.
2. **`cluster`** — groups items into "moments": photos/videos taken within
   ~90 seconds of each other, split further by visual similarity (perceptual
   hashing) so unrelated shots taken in the same minute aren't grouped
   together.
3. **`propose`** — within each moment, ranks items using Apple's quality
   score plus your own signals (favorited, edited, in an album, burst pick)
   and proposes which to keep vs. archive.
4. **`review`** — opens a local web page (nothing leaves your machine) where
   you see each moment as a row of thumbnails, keep/archive proposals
   pre-marked, and can override any of them with a click. Decisions save
   immediately.
5. **`export`** — copies the full-resolution originals you approved for
   archiving (with all metadata, Live Photo pairs, RAW+JPEG pairs, edited
   versions) into a local folder, verifying each file after copying.
6. **`finalize`** — adds the verified, exported items to a Photos album
   called **"Archived - safe to delete"**. You review that album and delete
   it yourself — this is the one step that has to be manual, and it's what
   actually frees up iCloud space (after also emptying "Recently Deleted").

Every stage is idempotent and safe to re-run. Decisions you make are
remembered permanently, so running this every few months only shows you
what's new.

## Requirements

- macOS with Photos library synced via iCloud Photos
- [Homebrew](https://brew.sh) and [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- **Full Disk Access** granted to whatever terminal/app runs this tool
  (System Settings → Privacy & Security → Full Disk Access). Without this,
  `scan` will fail with a clear error telling you to grant it.
- Photos.app installed (used for staging the album, and for downloading any
  iCloud-only originals during export)

## Setup

```bash
git clone <this-repo>
cd icloud-photo-reducer
./run.sh status
```

The first run installs a Python virtual environment via `uv` automatically.

## Usage

```bash
./run.sh scan             # read the library into local state (state.db)
./run.sh cluster          # group into moments
./run.sh propose          # rank and propose keep/archive
./run.sh review           # opens a browser to approve/override proposals
./run.sh export           # copy approved originals to ~/Desktop/Icloud_photos_archive
./run.sh finalize         # stage verified exports in a Photos album for manual deletion
./run.sh status           # show progress and estimated savings at any point
```

Running `./run.sh` with no arguments is the same as `./run.sh status`, which
also tells you the suggested next command.

After the archive folder has content, move it wherever you actually keep
photo backups (e.g. an external drive) — the tool tracks what's already been
exported in its own state, not by checking the archive folder's contents, so
it's safe to move or even delete files from there afterward.

### Recovering iCloud space

Exporting and staging doesn't free any space by itself. To actually reclaim
storage:

1. Open Photos → Albums → **Archived - safe to delete**
2. Select All → Delete
3. Go to **Recently Deleted** → Select All → Delete Immediately (otherwise
   space isn't freed for 30 days)

### Useful flags

- `./run.sh scan --months 12` — change the age cutoff (default 6 months)
- `./run.sh export --dry-run` — see what would be exported without doing it
- `./run.sh export --limit 20` — export only the next 20 items (useful for a
  first test run)

## Safety notes

- The tool never modifies your Photos library except adding one album and
  adding photos to it in the `finalize` step.
- Nothing is archived without your explicit approval in the `review` step.
- Export verifies every file (existence, size, checksum) before it's ever
  considered "safe to stage" — a failed export is retried on the next run,
  not silently marked as done.
- `export` needs Photos.app to have an actual open window to download
  iCloud-only originals; it will quit and relaunch Photos itself if needed.
  If Photos is doing something else when you run `export`, expect it to be
  restarted.

## Limitations

- Screenshots are excluded from clustering by default.
- No video content analysis — clustering for videos is time-based only.
- Deletion is always manual; this is a macOS restriction, not a choice made
  by this tool.
- Works against the local Photos library database; very large libraries will
  make `scan`/`cluster` slower but everything is designed to be safely
  re-run and resumed.

## Project layout

```
photo_reducer/
  scan.py       # stage 1: read Photos library metadata into state.db
  cluster.py    # stage 2: group into moments
  propose.py    # stage 3: rank and propose keep/archive
  review.py     # stage 4: local web review UI (stdlib http.server, no deps)
  export.py     # stage 5: export approved originals to the archive folder
  finalize.py   # stage 6: stage exports in a Photos album
  status.py     # progress/summary command
  db.py         # SQLite schema and connection helper
  cli.py        # argparse entrypoint
SPEC.md         # full design spec and rationale for every decision above
```

See [SPEC.md](SPEC.md) for the detailed design rationale behind the
clustering thresholds, scoring formula, and edge-case handling.

## License

MIT (or whatever you choose to publish under).
