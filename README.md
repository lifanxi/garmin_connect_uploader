# Garmin Connect Uploader

Garmin Connect Uploader (`gcu`) is a cross-platform GPS track uploader for
Garmin Connect. It provides both a desktop GUI and a CLI over the same sync
engine.

The tool is designed for historical GPS track archives: it parses local track
files, converts them to FIT activities, uploads them to Garmin Connect, and uses
stable tokens to avoid duplicate uploads.

## Features

- Desktop GUI built with PySide6.
- CLI for scripting, batch sync, conversion, backup, purge, and diagnostics.
- Columbus CSV, GPX, FIT, and NMEA RMC input support.
- FIT activity generation with a stable tool device signature.
- Duplicate detection by stable `[gcu:v1:...]` activity-name tokens.
- Legacy duplicate matching by time, duration, and coordinates.
- Local pre-check for duplicate files, overlapping points, conflicting points,
  and parse errors.
- Per-file task states in the GUI: upload, skip, backfill Token, conflict,
  queued, uploading, and completed.
- Safe purge of activities uploaded by this tool only by default.
- Automatic display timezone and city resolution from track coordinates.
- Windows installer build with bundled Python runtime and dependencies.

## Supported Track Formats

### Columbus CSV

Example:

```text
INDEX,TAG,DATE,TIME,LATITUDE N/S,LONGITUDE E/W,HEIGHT,SPEED,HEADING
1,T,251201,000001,30.0000010N,120.0000010E,1,3.6,80
```

CSV timestamps are interpreted as UTC by default.

### NMEA RMC

Raw text files containing `$GPRMC` or `$GNRMC` sentences are supported. Common
extensions include `.txt`, `.nmea`, and `.log`. NMEA timestamps are interpreted
as UTC.

### GPX

Standard GPX track files (`.gpx`, `.GPX`) with `trk/trkseg/trkpt` points are
supported. Track point `lat`, `lon`, `ele`, and `time` fields are parsed. GPX
timestamps are interpreted as UTC when they use `Z` or an explicit timezone
offset.

### FIT

Existing FIT activity files (`.fit`, `.FIT`) can be used as input. Record
timestamps, coordinates, altitude, speed, and heading are parsed and then flow
through the same duplicate-detection and upload pipeline as the text formats.

## Duplicate And Safety Rules

Uploaded activity names include a stable token:

```text
Hangzhou Track Me [gcu:v1:ec3a118bea0021bf]
```

Token matching has priority. If a remote activity already contains the same
token, the local file is skipped.

For older activities without a token, the tool can match by timestamp, duration,
and coordinates. If the matched remote activity was uploaded by this tool, the
name is updated to the current title format. If it was uploaded by another tool,
only the Token is appended or replaced.

Generated FIT files use this device signature:

```text
manufacturer = HOLUX
deviceId = 0x12345678
```

Destructive or modifying operations, such as CLI purge and edits to activities
identified as this tool's uploads, check this signature before changing remote
activities. CLI purge only deletes signed activities. In the GUI maintenance
dialog, unsigned activities can only be included after selecting the explicit
option and accepting an additional warning.

## Installation For Development

Python 3.11 or newer is recommended.

macOS/Linux:

```bash
python -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

## Desktop GUI

Start the GUI:

```bash
python gcu_gui.py
```

The GUI is a single-window workflow:

- Login is at the top.
- File management and inspection/upload actions are in the middle.
- Maintenance actions are at the bottom.
- A shared status log is shown at the bottom of the window.

On startup, the GUI checks `.garth_session` in the current working directory. If
a valid Garmin session exists, the login area stays locked and file/maintenance
actions are enabled. If not, file and maintenance actions stay disabled until
login succeeds.

The domain selector supports:

- Global: `garmin.com`
- China: `garmin.cn`

For Simplified Chinese system locales, the default is China. Otherwise the
default is Global. After a successful login, the selected domain is saved in:

```text
.garth_session/gcu_account.json
```

The same file also stores the login username hint used for friendlier account
display. Logging out removes `.garth_session`.

### GUI File Workflow

Use **Add Files** or **Add Folder** to add tracks. When a selected folder has
subdirectories, the GUI asks whether to add files recursively.

Use **Inspect** to run:

1. Local pre-check.
2. Per-file metadata parsing.
3. Remote duplicate lookup and task planning.

If local pre-check finds issues, the affected files are marked directly in the
table. Valid files still continue into task planning.

Use **Run** to process only rows whose status is runnable, such as Upload or
Backfill Token. Run is serial: one file is uploaded, matched to an activity ID,
tagged, and completed before the next file starts. During a run, the button
changes to **Stop Run**. Stopping waits for the current file to finish, then
exits the remaining queue.

Use **Clear Completed** to remove completed or skipped rows from the table while
leaving problem rows for review.

Use **Backup** to download Garmin Connect activities in a selected date range.
The backup dialog can include activities uploaded by this tool, activities not
uploaded by this tool, or both.

Use **Settings** in the maintenance section to set a home city and activity name
template for GUI inspect/run operations. Empty values keep the defaults. The GUI
saves these values to `settings.yaml` in the working directory.

Use **Clean Uploaded Activities** to delete remote activities uploaded by this tool.
The GUI previews matching activities in a confirmation dialog, and deletion
requires typing `DELETE`. The dialog also has an unchecked option to include
activities not uploaded by this tool; enabling it requires accepting an
additional warning before scanning.

## CLI Usage

The CLI entrypoint is:

```bash
python gcu_cli.py <command>
```

The packaged Windows CLI is:

```powershell
gcu.exe <command>
```

The CLI expands glob patterns itself, so `tracks/*.CSV` works in Windows shells
as well as macOS/Linux shells.

### Inspect Local Files

```bash
python gcu_cli.py inspect tracks/*.CSV
python gcu_cli.py inspect tracks/*.gpx
python gcu_cli.py inspect 080323-UnixCenter.txt --json
```

### Pre-check A Batch

Pre-check reports duplicate track files, overlapping points, conflicting points,
and parse errors. It does not upload or modify anything.

```bash
python gcu_cli.py pre-check tracks/*.CSV
python gcu_cli.py pre-check tracks/*.CSV --json
```

### Convert To FIT

```bash
python gcu_cli.py convert input.CSV --output output.fit
python gcu_cli.py convert tracks/*.CSV --output-dir out-fit
```

### Authenticate

```bash
python gcu_cli.py auth login --domain garmin.cn
python gcu_cli.py auth status --domain garmin.cn --json
```

CLI sessions use garth's default session directory unless `--session-dir` is
provided. The GUI always uses `.garth_session` in the current directory.

Credentials can be passed through options or environment variables:

```bash
export GARMIN_USERNAME="name@example.com"
export GARMIN_PASSWORD="..."
python gcu_cli.py auth login --domain garmin.com
```

### Sync

Preview decisions without uploading:

```bash
python gcu_cli.py sync tracks/*.CSV --domain garmin.cn --dry-run
```

Upload and tag activities:

```bash
python gcu_cli.py sync tracks/*.CSV --domain garmin.cn
```

Run an offline planning pass without querying Garmin:

```bash
python gcu_cli.py sync tracks/*.CSV --dry-run --offline
```

Useful sync options:

```bash
--keep-fit
--output-dir fit-output
--post-upload-max-wait-s 180
--post-upload-wait-base-s 30
--post-upload-wait-per-1000-points-s 5
```

Post-upload wait time is estimated from point count and capped by
`--post-upload-max-wait-s`.

### Backfill Tokens

```bash
python gcu_cli.py backfill tracks/*.CSV --domain garmin.cn --dry-run
python gcu_cli.py backfill tracks/*.CSV --domain garmin.cn
```

Backfill finds matching remote activities and adds or replaces the stable token
in the activity name.

### Purge Tool Uploads

Preview:

```bash
python gcu_cli.py purge --domain garmin.cn --dry-run
```

Delete matching signed activities:

```bash
python gcu_cli.py purge --domain garmin.cn --yes
```

Limit by date or scan in smaller windows:

```bash
python gcu_cli.py purge --dry-run --start-date 2025-01-01 --end-date 2026-07-01
python gcu_cli.py purge --yes --chunk-days 31
```

### Backup Activities

Download Garmin Connect activities as original track files. If Garmin returns a
ZIP archive with one supported file, that file is saved with its original
format; if the ZIP contains multiple files, the original ZIP is preserved.
Supported track formats are `.tcx`, `.fit`, `.gpx`, `.xls`, `.xlsx`, `.csv`,
and `.xml`; multi-file archives are saved as `.zip`. Output filenames use
`YYYYMMDD_HHMMSS_activityId_activityName.ext`; unsupported filename characters
and GCU tokens in activity names are removed from the saved filename.

```bash
python gcu_cli.py backup --domain garmin.cn --start-date 2025-01-01 --end-date 2026-07-01 --output-dir garmin-backup
python gcu_cli.py backup --domain garmin.cn --include gcu --output-dir garmin-backup
python gcu_cli.py backup --domain garmin.cn --include non-gcu --output-dir garmin-backup
```

## Naming, Timezone, And City

Default activity names use:

```text
<City> Track Me [gcu:v1:<token>]
```

If the city cannot be resolved, the base name is:

```text
Track Me [gcu:v1:<token>]
```

Use `--name-template` to override the base naming rule. Supported placeholders
are `{date}`, `{duration}`, `{start}`, `{end}`, `{points}`, `{city}`,
`{country}`, and `{state}`.

Use `--home-city` with optional template blocks to shorten local activity names.
Square brackets mark optional blocks. When a track resolves to the same state as
the home city, optional `{country}` and `{state}` blocks are omitted. When it is
in a different state but the same country, optional `{country}` blocks are
omitted. Plain placeholders outside square brackets are always rendered. When
`{state}` and `{city}` resolve to the same value, optional occurrences are
omitted first; if both are optional or both are required, only `{city}` is kept.

Source timestamps are UTC by default. Human-readable display timezone is resolved
from coordinates when `--display-timezone auto` is used. If automatic lookup
cannot decide, the fallback is `Asia/Shanghai` unless overridden.

City resolution is offline. For a track, the resolver compares the start and end
cities first. If they differ, it progressively samples midpoint segments until a
city wins by majority. If all sampled points tie, the start city is used.
Country is resolved from the same offline city record. State/province names are
resolved from the bundled GeoNames `admin1CodesASCII.txt` data.

The bundled administrative-region data is from GeoNames and is licensed under
Creative Commons Attribution 4.0. See https://www.geonames.org/.

Useful format options:

```bash
--format auto
--timezone UTC
--display-timezone auto
--display-timezone-fallback Asia/Shanghai
--display-city auto
--display-city-min-population 300000
--home-city Hangzhou
--name-template "{country} {state} {city} Track Me"
--name-template "[{country} ][{state} ]{city} Track Me"
```

## Garmin HTTP Verbose Log

Set `GCU_GARMIN_VERBOSE_HTTP=1` to record Garmin HTTP request and response
exchanges.

Windows default log path:

```text
%LOCALAPPDATA%\GarminConnectUploader\garmin-connect-http.log
```

macOS/Linux default log path:

```text
garmin-connect-http.log
```

Override the path:

```bash
GCU_GARMIN_VERBOSE_HTTP=1 GCU_GARMIN_VERBOSE_HTTP_LOG=/tmp/gcu-http.log python gcu_gui.py
```

The verbose log includes URLs, methods, headers, and bodies. It may contain
Garmin credentials, cookies, bearer tokens, and uploaded FIT payloads. Enable it
only for local debugging and do not share the file.

## Windows Installer

The Windows installer bundles Python and all dependencies. End users do not need
to install Python first.

Build on Windows:

```powershell
winget install JRSoftware.InnoSetup
.\scripts\build_windows_installer.ps1 -Version 0.1.0
```

The script also detects Inno Setup installed under the current user's profile,
for example:

```text
%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
```

Output:

```text
dist\installer\GarminConnectUploader-0.1.0-Setup.exe
```

The installer includes:

- `GarminConnectUploader.exe`, the GUI.
- `gcu.exe`, the CLI.
- Python runtime and collected Python dependencies.

### Code Signing

Sign bundled executables and the installer with an Authenticode certificate:

```powershell
.\scripts\build_windows_installer.ps1 -Version 0.1.0 -Sign -CertThumbprint "<SHA1_THUMBPRINT>"
```

Or with a PFX:

```powershell
.\scripts\build_windows_installer.ps1 -Version 0.1.0 -PfxPath ".\codesign.pfx" -PfxPassword "<password>"
```

## Tests

Run the test suite:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

## Notes

- Garmin Connect is an external service and may change behavior without notice.
- The project uses `garth` for Garmin authentication and API access.
- The application overrides garth's default User-Agent in the application layer,
  including Garmin API requests and SSO page requests.
- Keep `.garth_session` private. It contains reusable Garmin session tokens.
