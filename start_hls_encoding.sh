#!/bin/bash

# ─────────────────────────────────────────────
# Dummy / Simulated Live HLS Stream via FFmpeg
# - ffmpeg runs in background, HTTP server in foreground
# - Ctrl+C stops both
# ─────────────────────────────────────────────

HLS_DIR="./hls_output"
PLAYLIST="$HLS_DIR/stream.m3u8"
SEGMENT_DURATION=2       # seconds per segment
SEGMENT_LIST_SIZE=5      # number of segments kept in playlist (live window)
PORT=8080                # HTTP port to serve the stream (optional, see bottom)
CLEANUP_DONE=0
mkdir -p "$HLS_DIR"

# ── Cleanup on exit (Ctrl+C or normal exit) ───
cleanup() {
  if [ "$CLEANUP_DONE" -eq 1 ]; then
    return
  fi
  CLEANUP_DONE=1

  echo ""
  echo "Stopping ffmpeg (PID $FFMPEG_PID)..."
  kill "$FFMPEG_PID" 2>/dev/null
  wait "$FFMPEG_PID" 2>/dev/null
  rm -rf "$HLS_DIR"
  echo "Done."
}
trap cleanup EXIT INT TERM

# ── 1. Start ffmpeg in the background ─────────
echo "Starting ffmpeg in background..."
ffmpeg \
  -re \
  -f lavfi -i "testsrc2=size=1280x720:rate=30" \
  -f lavfi -i "sine=frequency=440:sample_rate=44100" \
  -c:v libx264 \
    -preset veryfast \
    -tune zerolatency \
    -b:v 1500k \
    -maxrate 1500k \
    -bufsize 3000k \
    -g 60 \
    -keyint_min 60 \
    -sc_threshold 0 \
    -pix_fmt yuv420p \
  -c:a aac \
    -b:a 128k \
    -ar 44100 \
  -f hls \
    -hls_time "$SEGMENT_DURATION" \
    -hls_list_size "$SEGMENT_LIST_SIZE" \
    -hls_flags delete_segments \
    -hls_delete_threshold 10 \
    -hls_segment_filename "$HLS_DIR/segment_%05d.ts" \
  "$PLAYLIST" \
  > /dev/null 2>&1 &

FFMPEG_PID=$!
echo "ffmpeg started (PID $FFMPEG_PID)"

# Give ffmpeg a moment to produce the first segment
sleep 2

# ── 2. Start HTTP server (foreground) ─────────
python3 hls_http_server.py "$PORT" "$HLS_DIR"

# cleanup() is called automatically when python exits
