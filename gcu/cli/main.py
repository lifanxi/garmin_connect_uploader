from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from gcu.app.models import RemoteActivity
from gcu.app.sync_service import SyncOptions, SyncService
from gcu.cli.output import print_decisions, print_local_tracks, print_purge_summary
from gcu.duplicate.matcher import MatchOptions
from gcu.export.fit_writer import write_fit
from gcu.formats.base import FormatOptions
from gcu.garmin import GarminClient


class EmptyGarmin:
    def list_activities(self, start_date: date, end_date: date) -> list[RemoteActivity]:
        return []

    def upload_activity(self, file_path: Path):
        raise RuntimeError("offline mode cannot upload")

    def update_activity_name(self, activity_id: int, activity_name: str):
        raise RuntimeError("offline mode cannot update Garmin metadata")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        if getattr(args, "json", False):
            import json

            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gcu", description="Garmin Connect Uploader CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Parse tracks and show local metadata")
    add_common_file_args(inspect_parser)
    inspect_parser.set_defaults(func=cmd_inspect)

    convert_parser = subparsers.add_parser("convert", help="Convert track files to FIT")
    add_common_file_args(convert_parser)
    convert_parser.add_argument("--output", type=Path, help="Output FIT path for a single input file")
    convert_parser.add_argument("--output-dir", type=Path, help="Directory for converted FIT files")
    convert_parser.set_defaults(func=cmd_convert)

    sync_parser = subparsers.add_parser("sync", help="Upload new tracks and skip duplicates")
    add_common_file_args(sync_parser)
    add_garmin_args(sync_parser)
    add_match_args(sync_parser)
    sync_parser.add_argument("--dry-run", action="store_true", help="Plan only; do not upload or update")
    sync_parser.add_argument("--offline", action="store_true", help="Do not query Garmin; assume no remote activities")
    sync_parser.add_argument("--keep-fit", action="store_true", help="Keep rendered FIT files")
    sync_parser.add_argument("--output-dir", type=Path, help="Directory for rendered FIT files")
    sync_parser.add_argument(
        "--post-upload-max-wait-s",
        type=int,
        default=180,
        help="Maximum seconds to wait for a newly uploaded activity to appear",
    )
    sync_parser.add_argument(
        "--post-upload-wait-base-s",
        type=int,
        default=30,
        help="Base seconds for post-upload activity lookup wait estimation",
    )
    sync_parser.add_argument(
        "--post-upload-wait-per-1000-points-s",
        type=int,
        default=5,
        help="Extra wait seconds per 1000 track points for post-upload lookup",
    )
    sync_parser.add_argument(
        "--post-upload-tag-workers",
        type=int,
        default=4,
        help="Background workers for locating and tagging uploaded activities",
    )
    sync_parser.set_defaults(func=cmd_sync)

    backfill_parser = subparsers.add_parser("backfill", help="Add duplicate tokens to existing Garmin activities")
    add_common_file_args(backfill_parser)
    add_garmin_args(backfill_parser)
    add_match_args(backfill_parser)
    backfill_parser.add_argument("--dry-run", action="store_true", help="Plan only; do not update")
    backfill_parser.set_defaults(func=cmd_backfill)

    purge_parser = subparsers.add_parser(
        "purge",
        help="Delete all Garmin activities uploaded by this tool",
    )
    add_garmin_args(purge_parser)
    purge_parser.add_argument(
        "--start-date",
        type=_date_arg,
        default=date(1970, 1, 1),
        help="First activity date to scan, default: 1970-01-01",
    )
    purge_parser.add_argument(
        "--end-date",
        type=_date_arg,
        default=None,
        help="Last activity date to scan, default: today",
    )
    purge_parser.add_argument("--dry-run", action="store_true", help="Show matching activities without deleting")
    purge_parser.add_argument("--yes", action="store_true", help="Actually delete signed GCU activities")
    purge_parser.add_argument("--json", action="store_true", help="Emit JSON")
    purge_parser.set_defaults(func=cmd_purge)

    auth_parser = subparsers.add_parser("auth", help="Manage Garmin authentication")
    auth_sub = auth_parser.add_subparsers(dest="auth_command", required=True)
    login_parser = auth_sub.add_parser("login", help="Log in and save Garmin session")
    add_garmin_args(login_parser)
    login_parser.set_defaults(func=cmd_auth_login)
    status_parser = auth_sub.add_parser("status", help="Check whether Garmin session can be resumed")
    add_garmin_args(status_parser)
    status_parser.set_defaults(func=cmd_auth_status)

    return parser


def add_common_file_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("files", nargs="+", type=Path, help="Track files")
    parser.add_argument("--format", default="auto", help="Input format, default: auto")
    parser.add_argument("--timezone", default="UTC", help="Source timezone for formats without timezone data")
    parser.add_argument(
        "--display-timezone",
        default="auto",
        help="Timezone used for human-readable names and display dates; default: auto from coordinates",
    )
    parser.add_argument(
        "--display-timezone-fallback",
        default="Asia/Shanghai",
        help="Fallback display timezone when automatic coordinate lookup cannot decide",
    )
    parser.add_argument(
        "--display-city",
        default="auto",
        help="City name used in human-readable activity titles; default: auto from middle track coordinates",
    )
    parser.add_argument(
        "--display-city-min-population",
        type=int,
        default=300_000,
        help="Minimum population for automatic display city lookup",
    )
    parser.add_argument("--name-template", help="Activity name template")
    parser.add_argument("--json", action="store_true", help="Emit JSON")


def add_garmin_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--domain", default="garmin.cn", help="Garmin domain, for example garmin.cn")
    parser.add_argument("--session-dir", type=Path, help="Directory for garth session tokens")
    parser.add_argument("--username", help="Garmin username; can also use GARMIN_USERNAME")
    parser.add_argument("--password", help="Garmin password; can also use GARMIN_PASSWORD")


def add_match_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coord-tolerance-deg", type=float, default=0.001)
    parser.add_argument("--time-tolerance-s", type=int, default=60)
    parser.add_argument("--duration-tolerance-s", type=int, default=120)


def cmd_inspect(args) -> int:
    service = SyncService()
    tracks = service.inspect(_existing_files(args.files), _sync_options(args, dry_run=True))
    print_local_tracks(tracks, as_json=args.json)
    return 0


def cmd_convert(args) -> int:
    service = SyncService()
    local_tracks = service.inspect(_existing_files(args.files), _sync_options(args, dry_run=True))
    if args.output and len(local_tracks) != 1:
        raise ValueError("--output can only be used with one input file")
    output_paths: list[Path] = []
    for local_track in local_tracks:
        if args.output:
            output_path = args.output
        else:
            output_dir = args.output_dir or local_track.track_file.source_path.parent
            output_path = output_dir / local_track.track_file.source_path.with_suffix(".fit").name
        write_fit(local_track.track_file.track, output_path, local_track.planned_name)
        output_paths.append(output_path)
    if args.json:
        import json

        print(json.dumps([str(path) for path in output_paths], ensure_ascii=False, indent=2))
    else:
        for path in output_paths:
            print(path)
    return 0


def cmd_sync(args) -> int:
    service = SyncService()
    options = _sync_options(args, dry_run=args.dry_run)
    garmin = EmptyGarmin() if args.offline else _garmin(args)
    if not args.offline:
        garmin.ensure_session(args.username, args.password)
    decisions = service.sync(_existing_files(args.files), garmin, options)
    print_decisions(decisions, as_json=args.json)
    return _exit_code(decisions)


def cmd_backfill(args) -> int:
    service = SyncService()
    options = _sync_options(args, dry_run=args.dry_run)
    garmin = _garmin(args)
    garmin.ensure_session(args.username, args.password)
    decisions = service.backfill(_existing_files(args.files), garmin, options)
    print_decisions(decisions, as_json=args.json)
    return _exit_code(decisions)


def cmd_purge(args) -> int:
    if not args.dry_run and not args.yes:
        raise ValueError("purge is destructive; pass --dry-run to preview or --yes to delete")
    garmin = _garmin(args)
    garmin.ensure_session(args.username, args.password)
    summary = SyncService().purge(
        garmin,
        start_date=args.start_date,
        end_date=args.end_date or date.today(),
        dry_run=args.dry_run,
    )
    print_purge_summary(summary, as_json=args.json)
    return 0


def cmd_auth_login(args) -> int:
    garmin = _garmin(args)
    garmin.ensure_session(args.username, args.password)
    print(f"Garmin session saved in {garmin.session_dir}")
    return 0


def cmd_auth_status(args) -> int:
    garmin = _garmin(args)
    garmin.ensure_session(args.username, args.password)
    print(f"Garmin session is usable for {args.domain}")
    return 0


def _sync_options(args, dry_run: bool) -> SyncOptions:
    return SyncOptions(
        format_options=FormatOptions(
            timezone_name=args.timezone,
            display_timezone_name=args.display_timezone,
            display_timezone_fallback=args.display_timezone_fallback,
            display_city_name=args.display_city,
            display_city_min_population=args.display_city_min_population,
            explicit_format=args.format,
        ),
        match_options=MatchOptions(
            coord_tolerance_deg=args.coord_tolerance_deg if hasattr(args, "coord_tolerance_deg") else 0.001,
            time_tolerance_s=args.time_tolerance_s if hasattr(args, "time_tolerance_s") else 60,
            duration_tolerance_s=args.duration_tolerance_s if hasattr(args, "duration_tolerance_s") else 120,
        ),
        dry_run=dry_run,
        name_template=args.name_template,
        keep_fit=getattr(args, "keep_fit", False),
        output_dir=getattr(args, "output_dir", None),
        post_upload_wait_base_s=getattr(args, "post_upload_wait_base_s", 30),
        post_upload_wait_per_1000_points_s=getattr(args, "post_upload_wait_per_1000_points_s", 5),
        post_upload_max_wait_s=getattr(args, "post_upload_max_wait_s", 180),
        post_upload_tag_workers=getattr(args, "post_upload_tag_workers", 4),
    )


def _garmin(args) -> GarminClient:
    return GarminClient(domain=args.domain, session_dir=args.session_dir)


def _existing_files(paths: list[Path]) -> list[Path]:
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing files: {', '.join(str(path) for path in missing)}")
    return paths


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}; expected YYYY-MM-DD") from exc


def _exit_code(decisions) -> int:
    failure_statuses = {"failed", "ambiguous", "upload-conflict"}
    return 2 if any(item.status in failure_statuses for item in decisions) else 0


if __name__ == "__main__":
    raise SystemExit(main())
