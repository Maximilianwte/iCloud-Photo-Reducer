import argparse
import json
import sys

from . import cluster as cluster_stage
from . import db
from . import propose as propose_stage
from . import scan as scan_stage


def cmd_scan(args):
    counts = scan_stage.run(months=args.months)
    print(json.dumps(counts, indent=2))


def cmd_cluster(args):
    counts = cluster_stage.run()
    print(json.dumps(counts, indent=2))


def cmd_propose(args):
    counts = propose_stage.run()
    print(json.dumps(counts, indent=2))


def cmd_review(args):
    from . import review

    review.run(port=args.port)


def cmd_export(args):
    from . import export as export_stage

    export_stage.run(dry_run=args.dry_run, limit=args.limit)


def cmd_finalize(args):
    from . import finalize as finalize_stage

    finalize_stage.run()


def cmd_status(args):
    from . import status as status_stage

    status_stage.run()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="photo-reducer")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="Read the Photos library into the local state DB")
    p_scan.add_argument("--months", type=int, default=6, help="Cutoff age in months (default: 6)")
    p_scan.set_defaults(func=cmd_scan)

    p_cluster = sub.add_parser("cluster", help="Group scanned assets into moments")
    p_cluster.set_defaults(func=cmd_cluster)

    p_propose = sub.add_parser("propose", help="Rank moments and propose keep/archive")
    p_propose.set_defaults(func=cmd_propose)

    p_review = sub.add_parser("review", help="Open the browser review UI")
    p_review.add_argument("--port", type=int, default=8765)
    p_review.set_defaults(func=cmd_review)

    p_export = sub.add_parser("export", help="Export approved originals to the archive folder")
    p_export.add_argument("--dry-run", action="store_true")
    p_export.add_argument("--limit", type=int, default=None)
    p_export.set_defaults(func=cmd_export)

    p_finalize = sub.add_parser("finalize", help="Stage exported items in the Photos album")
    p_finalize.set_defaults(func=cmd_finalize)

    p_status = sub.add_parser("status", help="Show pipeline progress and estimated savings")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)

    if not args.command:
        from . import status as status_stage

        status_stage.run()
        return

    args.func(args)


if __name__ == "__main__":
    main()
