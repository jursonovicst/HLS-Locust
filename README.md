# HLS-Locust

Locust-based HLS load tester with player-level KPIs.

## What this project does

- Runs Locust users that repeatedly fetch HLS playlists and media segments
- Simulates player behavior and tracks playback-facing KPIs
- Emits custom metrics such as startup time and buffer level
- Optionally includes local stream generation/serving helpers for development

## Project files

- `locustfile.py`: Locust user and custom metrics reporting
- `streaming.py`: HLS session loop (manifest polling + segment fetching)
- `player.py`: Buffer model and underrun logic
- `start_hls_encoding.sh`: Starts FFmpeg and HTTP server for a local live stream
- `hls_http_server.py`: HTTP server with explicit MIME mapping for HLS

## Prerequisites

- Python 3.10+
- Python dependencies from `requirements.txt`

Optional development tooling (for local stream generation only):

- FFmpeg available in `PATH`
- Bash (`start_hls_encoding.sh` is intended to run on Linux)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you are running from Windows PowerShell for dependency setup only:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -3 -m pip install -r requirements.txt
```

## Run locally

### 1) Start Locust against the HLS endpoint you want to test

```bash
locust -f locustfile.py --host http://<hls-host>/path/to/stream.m3u8
```

Open the Locust UI at `http://localhost:8089`, start a test, and watch request stats.

### 2) Optional: start a local HLS stream for development

This helper flow targets Linux and exists for local development convenience.

In terminal A:

```bash
./start_hls_encoding.sh
```

This script:

- Starts FFmpeg in the background
- Writes live HLS output into `hls_output/`
- Serves `hls_output/` on `http://localhost:8080/`
- Cleans up on exit (`Ctrl+C`)

In terminal B:

```bash
locust -f locustfile.py --host http://localhost:8080/stream.m3u8
```

## Custom metrics

At test end, `locustfile.py` prints a metrics matrix:

- `startup_time`: time from buffering start to playback start
- `buffer_level`: buffered media seconds seen during playback

Statistics columns in the matrix:

- `samples`: number of collected values
- `mean`: arithmetic average
- `median`: middle value of sorted samples
- `p05`: 5th-percentile sample (index-based from sorted samples)
- `p95`: 95th-percentile sample (index-based from sorted samples)
- `min`: smallest observed value
- `max`: largest observed value

Notes:

- Samples are kept in memory for the process lifetime

## MIME types for HLS

`hls_http_server.py` explicitly serves:

- `.m3u8` as `application/vnd.apple.mpegurl`
- `.ts` as `video/mp2t`

This improves interoperability for HLS tooling and players.

## RFC 8216 scope

This repo is a load-test harness, not a full RFC validator. It helps test practical HLS behavior, but does not automatically prove strict RFC 8216 compliance for every stream variant or deployment.

## Troubleshooting

- `ffmpeg: command not found`: install FFmpeg and add it to `PATH`
- Port conflicts: change `PORT` in `start_hls_encoding.sh`
- Immediate session stop in Locust: check stream URL and that `stream.m3u8` is reachable
