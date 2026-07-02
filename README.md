# Garmin Connect Uploader

Garmin Connect Uploader (`gcu`) is a Python CLI and desktop GUI for syncing GPS
track files to Garmin Connect. It rewrites the old uploader around a
format-neutral core so the CLI and GUI share the same application services.

## Features

- Parse Columbus CSV track files.
- Parse raw NMEA RMC text files (`.txt`, `.nmea`, `.log`).
- Convert tracks to FIT activities.
- Upload to Garmin Connect through `garth`.
- Detect duplicates with stable `[gcu:v1:...]` activity-name tokens.
- Backfill tokens only on activities signed as this tool's uploads.
- Purge only activities signed as this tool's uploads.
- Resolve display timezone from the first five minutes of track coordinates.
- Include an offline city estimate from the middle segment in activity titles.

## Upload Signature

Generated FIT files use this device signature:

```text
manufacturer = HOLUX
deviceId = 0x12345678
```

Remote modification commands, including backfill, tagging, and purge, require
the Garmin activity list to report exactly that signature. Unsigned activities
are never modified or deleted.

## Installation

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

## Usage

Start the desktop GUI:

```bash
python gcu_gui.py
```

The GUI is a single-window workflow:

- Login controls are at the top. On startup the GUI checks `.garth_session` in
  the current directory. If the saved session is valid, file and cleanup actions
  are enabled and the login button changes to Logout. If not, only login remains
  enabled. Logging out removes `.garth_session`.
- Files are managed in the middle table. Inspect runs local pre-check,
  per-file metadata inspection, and dry-run planning; Run performs the actual
  sync. The table can be sorted by clicking headers and defaults to sorting
  inspected files by Start UTC.
- Cleanup is at the bottom. Clean Uploaded Tracks first previews signed GCU
  activities in the selected date range, shows them in a confirmation dialog,
  and requires typing `DELETE` before deletion.
- The bottom status log is shared by login, inspect, sync, and cleanup actions.

The domain selector offers Global (`garmin.com`) and China (`garmin.cn`),
defaulting to China for Simplified Chinese system locales and Global otherwise.
The interface currently includes Simplified Chinese and English text.

Inspect local tracks:

```bash
python gcu_cli.py inspect tracks/*.CSV
python gcu_cli.py inspect 080323-UnixCenter.txt --json
```

The CLI expands glob patterns itself, so commands such as `tracks/*.CSV` work in
Windows shells as well as macOS/Linux shells.

Pre-check local batches for duplicate tracks, overlapping points, and
same-timestamp coordinate conflicts:

```bash
python gcu_cli.py pre-check tracks/*.CSV
python gcu_cli.py pre-check tracks/*.CSV --json
```

Convert to FIT:

```bash
python gcu_cli.py convert input.CSV --output output.fit
```

Authenticate and sync:

```bash
python gcu_cli.py auth login --domain garmin.cn --session-dir garth_session
python gcu_cli.py auth status --domain garmin.cn --session-dir garth_session --json
python gcu_cli.py sync tracks/*.CSV --domain garmin.cn --session-dir garth_session
```

`auth login` and `auth status` report the authenticated Garmin username when the
profile API is available.

Preview duplicate decisions without uploading:

```bash
python gcu_cli.py sync tracks/*.CSV --dry-run --offline
```

Backfill tokens onto existing signed GCU activities:

```bash
python gcu_cli.py backfill tracks/*.CSV --dry-run --session-dir garth_session
python gcu_cli.py backfill tracks/*.CSV --session-dir garth_session
```

Delete all signed GCU activities from Garmin Connect:

```bash
python gcu_cli.py purge --dry-run --session-dir garth_session
python gcu_cli.py purge --yes --session-dir garth_session
```

Limit purge by date:

```bash
python gcu_cli.py purge --dry-run --start-date 2025-01-01 --end-date 2026-07-01
python gcu_cli.py purge --yes --chunk-days 31
```

## Supported Formats

### Columbus CSV

```text
INDEX,TAG,DATE,TIME,LATITUDE N/S,LONGITUDE E/W,HEIGHT,SPEED,HEADING
1,T,251201,000001,30.0000010N,120.0000010E,1,3.6,80
```

CSV timestamps are interpreted as UTC by default. Use `--timezone` only when
importing a source file that is known to use a different timezone.

### NMEA RMC

Raw text files containing `$GPRMC` or `$GNRMC` sentences are supported. NMEA
timestamps are UTC.

## Activity Names

Default uploaded activity names use the offline city estimate and a stable
duplicate token:

```text
Nanjing Track Me [gcu:v1:...]
```

If no city can be resolved, the base name is `Track Me`. Use `--name-template`
to override the default base name.

## Tests

```bash
python -m unittest discover -s tests
```

## Repository Layout

```text
gcu/app        application services and models
gcu/cli        command-line interface
gcu/duplicate  duplicate fingerprints and matching
gcu/export     FIT writer
gcu/formats    input format readers and display resolvers
gcu/garmin     Garmin Connect client
gcu/gui        desktop GUI
tests          unit tests
docs           design notes
```
