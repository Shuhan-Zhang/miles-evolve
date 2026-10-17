#!/bin/bash
# Mux the silent demo video (v4, value-first) with voiceover, the trailer cue, a bed and the before/after pair.
# Runs on the Mac; audio never leaves it.
# usage: mux2.sh VIDEO CUE.wav BED.wav BEFORE.wav AFTER.wav VO_DIR OUT.mp4
set -euo pipefail
VIDEO=$1 CUE=$2 BED=$3 BEFORE=$4 AFTER=$5 VO=$6 OUT=$7
# voice line -> start second on the video's timeline
LINES="n01:0.3 n02:2.0 n03:5.6 n04:9.5 n05:12.2 n06:14.9 n07:19.75 v05:21.6 v06:25.3 n08:30.6 n09:34.6 v10:39.0 v11:42.6 v12:54.0 v13:56.5"
A="aformat=sample_rates=48000:channel_layouts=stereo"
inputs=(-i "$VIDEO" -i "$CUE" -i "$CUE" -i "$BED" -i "$BEFORE" -i "$AFTER")
# trailer cue: in from 5.5 s under the tagline, up when the cue card plays (9.2 s), out at 12.4 s
filt="[1:a]$A,atrim=0:7,asetpts=N/SR/TB,afade=t=in:d=0.6,afade=t=out:st=6.2:d=0.7,adelay=5500|5500,volume='if(lt(t,9.2),0.35,0.85)':eval=frame[cue];"
# reprise under the end card
filt+="[2:a]$A,atrim=0:3.6,asetpts=N/SR/TB,afade=t=in:d=0.4,afade=t=out:st=2.6:d=1.0,adelay=56400|56400,volume=0.6[rep];"
# bed under the screen demo, muted for the before/after pair
filt+="[3:a]$A,atrim=0:44,asetpts=N/SR/TB,afade=t=in:d=1.0,afade=t=out:st=31.2:d=0.8,adelay=12400|12400,volume=0.16[bed];"
filt+="[4:a]$A,atrim=0:4.8,afade=t=out:st=4.4:d=0.4,adelay=44600|44600,volume=0.9[bef];"
filt+="[5:a]$A,atrim=0:4.6,afade=t=out:st=4.2:d=0.4,adelay=49400|49400,volume=0.9[aft];"
mix="[cue][rep][bed][bef][aft]"; n=5; k=6
for pair in $LINES; do
  id=${pair%%:*}; at=${pair##*:}; f="$VO/$id.wav"; [ -f "$f" ] || continue
  ms=$(python3 -c "print(int($at*1000))")
  inputs+=(-i "$f"); filt+="[$k:a]$A,adelay=$ms|$ms,volume=1.6[vo$k];"
  mix+="[vo$k]"; n=$((n+1)); k=$((k+1))
done
filt+="${mix}amix=inputs=$n:normalize=0:duration=longest,atrim=0:60,alimiter=limit=0.95[a]"
ffmpeg -y -v error "${inputs[@]}" -filter_complex "$filt" -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -shortest "$OUT"
echo "wrote $OUT"
