#!/usr/bin/env bash
#
# Generate per-feature demo GIFs from raw recordings.
# Usage: bash tools/make-demo-gif.sh
#
set -euo pipefail

SRC="/Users/weixiao/Documents/Praxis/recordings"
OUT="assets"
WIDTH=${WIDTH:-1280}
FPS=${FPS:-12}
SPEED=${SPEED:-1}
SETPTS=$(python3 -c "print(round(1/$SPEED, 4))")

echo "=== Praxis Demo GIF Builder ==="
echo "  Width=${WIDTH}  FPS=${FPS}  Speed=${SPEED}x"

build_gif() {
  local name="$1"
  shift
  local clips=("$@")

  local tmpdir
  tmpdir=$(mktemp -d)
  local concat_list="$tmpdir/concat.txt"
  > "$concat_list"

  echo ""
  echo "── $name ──"

  local i=0
  while [ $i -lt ${#clips[@]} ]; do
    local file="${clips[$i]}"
    local start="${clips[$((i+1))]}"
    local end="${clips[$((i+2))]}"
    i=$((i+3))

    local duration=$(echo "$end - $start" | bc)
    local clip_out="$tmpdir/clip-$(printf '%03d' $i).mp4"

    ffmpeg -y -ss "$start" -i "$SRC/$file" -t "$duration" \
      -vf "setpts=${SETPTS}*PTS,scale=${WIDTH}:-2:flags=lanczos" \
      -an -r "$FPS" "$clip_out" 2>/dev/null

    echo "file '$clip_out'" >> "$concat_list"
    echo "  $file [$start-$end] (${duration}s)"
  done

  local concat_mp4="$tmpdir/concat.mp4"
  ffmpeg -y -f concat -safe 0 -i "$concat_list" \
    -c:v libx264 -preset fast -crf 18 "$concat_mp4" 2>/dev/null

  local palette="$tmpdir/palette.png"
  ffmpeg -y -i "$concat_mp4" \
    -vf "fps=${FPS},scale=${WIDTH}:-1:flags=lanczos,palettegen=stats_mode=diff" \
    "$palette" 2>/dev/null

  ffmpeg -y -i "$concat_mp4" -i "$palette" \
    -lavfi "fps=${FPS},scale=${WIDTH}:-1:flags=lanczos [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
    "$OUT/$name" 2>/dev/null

  local size
  size=$(du -h "$OUT/$name" | cut -f1)
  local dur
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$concat_mp4" 2>/dev/null | cut -d. -f1)
  echo "  -> $OUT/$name  ${size}  ~${dur}s"

  rm -rf "$tmpdir"
}

# ── GIF 1: Chat ──
build_gif "demo-chat.gif" \
  "seg1-chat.webm"      0  8  \
  "seg1-chat.webm"     34 38  \
  "seg2-followup.webm"  2  6  \
  "seg2-followup.webm" 46 50

# ── GIF 2: Agent ──
build_gif "demo-agent.gif" \
  "seg3-save-agent.webm"  2  6  \
  "seg3-save-agent.webm" 19 23  \
  "seg4-run-agent.webm"   0  6  \
  "seg4-run-agent.webm"  10 14  \
  "seg4-run-agent.webm"  70 74

# ── GIF 3: Scheduler ──
build_gif "demo-scheduler.gif" \
  "seg5-scheduler.webm" 0 10

echo ""
echo "=== All GIFs built ==="
ls -lh "$OUT"/demo-*.gif
