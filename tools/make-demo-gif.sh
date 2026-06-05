#!/usr/bin/env bash
#
# Generate per-feature demo GIFs from raw recordings.
# Only trims long idle waits; keeps all meaningful content at normal speed.
#
# Usage: bash tools/make-demo-gif.sh
#   Env overrides: WIDTH=1280 FPS=12
#
set -euo pipefail

SRC="/Users/weixiao/Documents/Praxis/recordings"
OUT="assets"
WIDTH=${WIDTH:-1280}
FPS=${FPS:-12}

echo "=== Praxis Demo GIF Builder ==="
echo "  Width=${WIDTH}  FPS=${FPS}"

# Convert a single webm to scaled mp4
to_mp4() {
  local src="$1" dst="$2"
  ffmpeg -y -i "$src" \
    -vf "scale=${WIDTH}:-2:flags=lanczos" \
    -an -r "$FPS" "$dst" 2>/dev/null
}

# Extract a clip from webm
clip_mp4() {
  local src="$1" start="$2" duration="$3" dst="$4"
  ffmpeg -y -ss "$start" -i "$src" -t "$duration" \
    -vf "scale=${WIDTH}:-2:flags=lanczos" \
    -an -r "$FPS" "$dst" 2>/dev/null
}

# Concat mp4s and convert to GIF
finish_gif() {
  local name="$1"
  shift
  local parts=("$@")

  local tmpdir
  tmpdir=$(mktemp -d)
  local concat_list="$tmpdir/concat.txt"
  for p in "${parts[@]}"; do
    echo "file '$p'" >> "$concat_list"
  done

  local concat_mp4="$tmpdir/concat.mp4"
  if [ ${#parts[@]} -eq 1 ]; then
    cp "${parts[0]}" "$concat_mp4"
  else
    ffmpeg -y -f concat -safe 0 -i "$concat_list" \
      -c:v libx264 -preset fast -crf 18 "$concat_mp4" 2>/dev/null
  fi

  local palette="$tmpdir/palette.png"
  ffmpeg -y -i "$concat_mp4" \
    -vf "fps=${FPS},scale=${WIDTH}:-1:flags=lanczos,palettegen=stats_mode=diff" \
    "$palette" 2>/dev/null

  ffmpeg -y -i "$concat_mp4" -i "$palette" \
    -lavfi "fps=${FPS},scale=${WIDTH}:-1:flags=lanczos [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
    "$OUT/$name" 2>/dev/null

  local size dur
  size=$(du -h "$OUT/$name" | cut -f1)
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$concat_mp4" 2>/dev/null | cut -d. -f1)
  echo "  -> $OUT/$name  ${size}  ~${dur}s"

  rm -rf "$tmpdir"
}

TMPDIR_MAIN=$(mktemp -d)
trap "rm -rf $TMPDIR_MAIN" EXIT

# ── GIF 1: Chat (seg1 only, trim idle wait) ──
echo ""
echo "── demo-chat.gif ──"
echo "  seg1-chat.webm: [0-12] + [26-38] (trim 14s idle wait)"
clip_mp4 "$SRC/seg1-chat.webm" 0 12 "$TMPDIR_MAIN/s1a.mp4"
clip_mp4 "$SRC/seg1-chat.webm" 26 12 "$TMPDIR_MAIN/s1b.mp4"
finish_gif "demo-chat.gif" "$TMPDIR_MAIN/s1a.mp4" "$TMPDIR_MAIN/s1b.mp4"

# ── GIF 2: Agent (seg3 up to save success + seg4 agent page → run) ──
echo ""
echo "── demo-agent.gif ──"
echo "  seg3-save-agent.webm: [0-20] (cut before page jump to avoid double-enter)"
echo "  seg4-run-agent.webm: [1-20] (skip white flash, show agent auto-executing)"
clip_mp4 "$SRC/seg3-save-agent.webm" 0 20 "$TMPDIR_MAIN/s3.mp4"
clip_mp4 "$SRC/seg4-run-agent.webm"  1 19 "$TMPDIR_MAIN/s4.mp4"
finish_gif "demo-agent.gif" "$TMPDIR_MAIN/s3.mp4" "$TMPDIR_MAIN/s4.mp4"

# ── GIF 3: Scheduler (seg5 full) ──
echo ""
echo "── demo-scheduler.gif ──"
echo "  seg5-scheduler.webm: full (10s)"
to_mp4 "$SRC/seg5-scheduler.webm" "$TMPDIR_MAIN/s5.mp4"
finish_gif "demo-scheduler.gif" "$TMPDIR_MAIN/s5.mp4"

echo ""
echo "=== All GIFs built ==="
ls -lh "$OUT"/demo-*.gif
