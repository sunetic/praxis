#!/usr/bin/env bash
#
# Generate per-feature demo GIFs from raw recordings.
# Trims only long idle waits; keeps all meaningful content at normal speed.
#
# Usage: bash tools/make-demo-gif.sh
#   Env overrides: WIDTH=1280 FPS=12
#   Source dir: /Users/weixiao/Documents/Praxis/recordings/v2
#
set -euo pipefail

SRC="/Users/weixiao/Documents/Praxis/recordings/v2"
OUT="assets"
WIDTH=${WIDTH:-1280}
FPS=${FPS:-12}

echo "=== Praxis Demo GIF Builder ==="
echo "  Width=${WIDTH}  FPS=${FPS}"

clip_mp4() {
  local src="$1" start="$2" duration="$3" dst="$4"
  ffmpeg -y -ss "$start" -i "$src" -t "$duration" \
    -vf "scale=${WIDTH}:-2:flags=lanczos" \
    -an -r "$FPS" "$dst" 2>/dev/null
}

to_mp4() {
  local src="$1" dst="$2"
  ffmpeg -y -i "$src" \
    -vf "scale=${WIDTH}:-2:flags=lanczos" \
    -an -r "$FPS" "$dst" 2>/dev/null
}

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

# ── GIF 1: Chat — Database Health Check ──
# seg1: 79s total. Three cuts:
#   t14-t25: pure "..." idle wait (LLM thinking)
#   t40-t60: tool call counter incrementing (visually static)
# Keep [0-14] + [25-40] + [60-79] → ~49s
echo ""
echo "── demo-chat.gif ──"
echo "  seg1: [0-14] + [25-40] + [60-79] (trim idle + static mid-section)"
clip_mp4 "$SRC/seg1-health-check.webm"  0 14 "$TMPDIR_MAIN/s1a.mp4"
clip_mp4 "$SRC/seg1-health-check.webm" 25 15 "$TMPDIR_MAIN/s1b.mp4"
clip_mp4 "$SRC/seg1-health-check.webm" 60 19 "$TMPDIR_MAIN/s1c.mp4"
finish_gif "demo-chat.gif" "$TMPDIR_MAIN/s1a.mp4" "$TMPDIR_MAIN/s1b.mp4" "$TMPDIR_MAIN/s1c.mp4"

# ── GIF 2: Agent — Save + Run ──
# seg2: 26s (save as agent). seg3: 24s (run agent).
# Both start with ~2s white flash from new browser context.
echo ""
echo "── demo-agent.gif ──"
echo "  seg2-save-agent.webm: [2-26] (skip white flash)"
echo "  seg3-run-agent.webm: [2-24] (skip white flash)"
clip_mp4 "$SRC/seg2-save-agent.webm" 2 24 "$TMPDIR_MAIN/s2.mp4"
clip_mp4 "$SRC/seg3-run-agent.webm"  2 22 "$TMPDIR_MAIN/s3.mp4"
finish_gif "demo-agent.gif" "$TMPDIR_MAIN/s2.mp4" "$TMPDIR_MAIN/s3.mp4"

# ── GIF 3: Scheduler ──
# seg4: 19s. Skip 1s white flash at start.
echo ""
echo "── demo-scheduler.gif ──"
echo "  seg4-scheduler.webm: [1-19] (skip white flash)"
clip_mp4 "$SRC/seg4-scheduler.webm" 1 18 "$TMPDIR_MAIN/s4.mp4"
finish_gif "demo-scheduler.gif" "$TMPDIR_MAIN/s4.mp4"

echo ""
echo "=== All GIFs built ==="
ls -lh "$OUT"/demo-*.gif
