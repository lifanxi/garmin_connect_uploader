# Garmin Connect Uploader

Garmin Connect Uploader (`gcu`) is a Python CLI for syncing GPS track files to
Garmin Connect. It rewrites the old uploader around a format-neutral core so the
same application service can later be wrapped by a GUI.

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

## Usage

Inspect local tracks:

```bash
python gcu_cli.py inspect tracks/*.CSV
python gcu_cli.py inspect 080323-UnixCenter.txt --json
```

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
python gcu_cli.py sync tracks/*.CSV --domain garmin.cn --session-dir garth_session
```

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
tests          unit tests
docs           design notes
```
