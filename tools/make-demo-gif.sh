#!/usr/bin/env bash
#
# Praxis Demo GIF Builder — smart trim version
#
# Cuts out LLM waiting/streaming sections, keeps user actions + final results.
# Concatenates at normal speed, converts to optimized GIF.
#
# Usage: bash tools/make-demo-gif.sh
#   Env overrides: WIDTH=720 FPS=12 SPEED=1.5
#
set -euo pipefail

RECORDINGS_DIR="assets/recordings"
OUTPUT_GIF="assets/demo.gif"
WIDTH=${WIDTH:-720}
FPS=${FPS:-12}
SPEED=${SPEED:-1.5}
SETPTS=$(python3 -c "print(round(1/$SPEED, 4))")

echo "=== Praxis Demo GIF Builder ==="
echo "  Width=${WIDTH}  FPS=${FPS}  Speed=${SPEED}x (setpts=${SETPTS})"

# ── Trim table: file  start  end  (seconds) ──
# Each line = one clip to keep. Multiple clips per segment are fine.
CLIPS=(
  # seg1: open page + select datasource + type question
  "seg1-chat.webm        0    8"
  # seg1: final result table (top cities)
  "seg1-chat.webm       36   38"
  # seg2: type follow-up question
  "seg2-followup.webm    2    6"
  # seg2: trend analysis result
  "seg2-followup.webm   48   50"
  # seg3: type "save as new agent"
  "seg3-save-agent.webm  2    6"
  # seg3: save success + jump to Agent page
  "seg3-save-agent.webm 19   23"
  # seg4: Agent page + click Run + dialog
  "seg4-run-agent.webm   0    6"
  # seg4: type question in agent chat
  "seg4-run-agent.webm  10   14"
  # seg4: refund rate result table
  "seg4-run-agent.webm  70   74"
  # seg5: scheduler config (keep all)
  "seg5-scheduler.webm   0   10"
)

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

# ── Step 1: Extract and scale each clip ──
echo "Extracting ${#CLIPS[@]} clips..."
concat_list="$TMPDIR/concat.txt"
> "$concat_list"

for i in "${!CLIPS[@]}"; do
  read -r file start end <<< "${CLIPS[$i]}"
  src="$RECORDINGS_DIR/$file"
  out="$TMPDIR/clip-$(printf '%03d' $i).mp4"

  if [ ! -f "$src" ]; then
    echo "  WARN: $src not found, skipping"
    continue
  fi

  duration=$(echo "$end - $start" | bc)
  ffmpeg -y -ss "$start" -i "$src" -t "$duration" \
    -vf "setpts=${SETPTS}*PTS,scale=${WIDTH}:-2:flags=lanczos" \
    -an -r "$FPS" \
    "$out" 2>/dev/null

  echo "file '$out'" >> "$concat_list"
  echo "  clip $i: $file [$start-$end] (${duration}s)"
done

# ── Step 2: Concatenate ──
echo "Concatenating..."
concat_mp4="$TMPDIR/concat.mp4"
ffmpeg -y -f concat -safe 0 -i "$concat_list" \
  -c:v libx264 -preset fast -crf 18 \
  "$concat_mp4" 2>/dev/null

duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$concat_mp4" 2>/dev/null | cut -d. -f1)
echo "  Concatenated: ~${duration}s"

# ── Step 3: GIF with palette optimization ──
echo "Generating optimized GIF..."
palette="$TMPDIR/palette.png"

ffmpeg -y -i "$concat_mp4" \
  -vf "fps=${FPS},scale=${WIDTH}:-1:flags=lanczos,palettegen=stats_mode=diff" \
  "$palette" 2>/dev/null

ffmpeg -y -i "$concat_mp4" -i "$palette" \
  -lavfi "fps=${FPS},scale=${WIDTH}:-1:flags=lanczos [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
  "$OUTPUT_GIF" 2>/dev/null

# ── Report ──
size=$(du -h "$OUTPUT_GIF" | cut -f1)
echo ""
echo "=== Done ==="
echo "  Output:   $OUTPUT_GIF"
echo "  Size:     $size"
echo "  Duration: ~${duration}s"
echo "  Speed:    ${SPEED}x"

size_bytes=$(wc -c < "$OUTPUT_GIF" | tr -d ' ')
if [ "$size_bytes" -gt 10485760 ]; then
  echo ""
  echo "WARNING: GIF > 10MB. Try: SPEED=2 WIDTH=640 FPS=10 bash tools/make-demo-gif.sh"
fi
