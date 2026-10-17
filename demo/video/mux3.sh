#!/bin/bash
# Mux the silent demo video (v4, value-first) with voiceover, the trailer cue, a bed and the before/after pair.
# Runs on the Mac; audio never leaves it.
# usage: mux3.sh VIDEO CUE.wav BED.wav TRY1.wav TRY2.wav TRY3.wav VO_DIR OUT.mp4   (env O1 O2 O3 = start offset in each try, s)
set -euo pipefail
VIDEO=$1 CUE=$2 BED=$3 T1=$4 T2=$5 T3=$6 VO=$7 OUT=$8; O1=${O1:-0} O2=${O2:-0} O3=${O3:-0}
# voice line -> start second on the video's timeline
LINES="n01:0.2 n03:3.4 n04:8.6 n06:13.4 n07:19.4 v05:21.6 v06:26.4 n08:31.3 n09:35.0 v11:40.6 v12:53.8 v13:56.0"
A="aformat=sample_rates=48000:channel_layouts=stereo"
inputs=(-i "$VIDEO" -i "$CUE" -i "$CUE" -i "$BED" -i "$T1" -i "$T2" -i "$T3")
# trailer cue: in from 5.0 s (after the Make click), up when the track and checks show (9.6 s), out at 13.6 s
filt="[1:a]$A,atrim=0:8.6,asetpts=N/SR/TB,afade=t=in:d=0.6,afade=t=out:st=7.8:d=0.8,adelay=5000|5000,volume='if(lt(t,9.2),0.3,0.42)':eval=frame[cue];"
# reprise under the end card
filt+="[2:a]$A,atrim=0:3.6,asetpts=N/SR/TB,afade=t=in:d=0.4,afade=t=out:st=2.6:d=1.0,adelay=56400|56400,volume=0.6[rep];"
# bed under the screen demo (pass the cue again to keep one piece of music), out before the before/after pair
filt+="[3:a]$A,atrim=8:39,asetpts=N/SR/TB,afade=t=in:d=1.0,afade=t=out:st=30.0:d=1.0,adelay=13400|13400,volume=0.18[bed];"
filt+="[4:a]$A,atrim=$O1:$(python3 -c "print($O1+3.1)"),asetpts=N/SR/TB,afade=t=in:d=0.05,afade=t=out:st=2.8:d=0.3,adelay=44600|44600,volume=0.9[t1];"
filt+="[5:a]$A,atrim=$O2:$(python3 -c "print($O2+3.1)"),asetpts=N/SR/TB,afade=t=in:d=0.05,afade=t=out:st=2.8:d=0.3,adelay=47700|47700,volume=0.9[t2];"
filt+="[6:a]$A,atrim=$O3:$(python3 -c "print($O3+3.2)"),asetpts=N/SR/TB,afade=t=in:d=0.05,afade=t=out:st=2.9:d=0.3,adelay=50800|50800,volume=0.9[t3];"
mix="[cue][rep][bed][t1][t2][t3]"; n=6; k=7
for pair in $LINES; do
  id=${pair%%:*}; at=${pair##*:}; f="$VO/$id.wav"; [ -f "$f" ] || continue
  ms=$(python3 -c "print(int($at*1000))")
  inputs+=(-i "$f"); filt+="[$k:a]$A,adelay=$ms|$ms,volume=1.8[vo$k];"
  mix+="[vo$k]"; n=$((n+1)); k=$((k+1))
done
filt+="${mix}amix=inputs=$n:normalize=0:duration=longest,atrim=0:60,alimiter=limit=0.95[a]"
ffmpeg -y -v error "${inputs[@]}" -filter_complex "$filt" -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -shortest "$OUT"
echo "wrote $OUT"
