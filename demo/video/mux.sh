#!/bin/bash
# Mux the silent demo video with voiceover lines and beats. Runs on the Mac (audio never leaves it).
# usage: mux.sh VIDEO BED.wav BEFORE.wav AFTER.wav VO_DIR OUT.mp4
set -euo pipefail
VIDEO=$1 BED=$2 BEFORE=$3 AFTER=$4 VO=$5 OUT=$6
# line -> start second (matches the video's timeline)
declare -a IDS=(v01 v02 v03 v04 v05 v06 v07 v08 v09 v10 v11 v12 v13)
declare -a AT=(0.6 3.6 8.0 11.6 15.2 20.6 25.5 29.0 33.3 37.0 42.2 54.2 56.6)
inputs=(-i "$VIDEO" -i "$BED" -i "$BEFORE" -i "$AFTER")
filt="[1:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:60,asetpts=N/SR/TB,volume='if(between(t,44.4,54.0),0,0.22)':eval=frame,afade=t=in:d=0.8,afade=t=out:st=58.5:d=1.5[bed];"
filt+="[2:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:4.8,afade=t=out:st=4.4:d=0.4,adelay=44600|44600,volume=0.9[bef];"
filt+="[3:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:4.6,afade=t=out:st=4.2:d=0.4,adelay=49400|49400,volume=0.9[aft];"
mix="[bed][bef][aft]"; n=3; k=4
for i in "${!IDS[@]}"; do
  f="$VO/${IDS[$i]}.wav"; [ -f "$f" ] || continue
  ms=$(python3 -c "print(int(${AT[$i]}*1000))")
  inputs+=(-i "$f")
  filt+="[$k:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay=$ms|$ms,volume=1.6[vo$k];"
  mix+="[vo$k]"; n=$((n+1)); k=$((k+1))
done
filt+="${mix}amix=inputs=$n:normalize=0:duration=longest,atrim=0:60,alimiter=limit=0.95[a]"
ffmpeg -y -v error "${inputs[@]}" -filter_complex "$filt" -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -shortest "$OUT"
echo "wrote $OUT"
