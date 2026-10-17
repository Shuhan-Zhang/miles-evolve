#!/bin/bash
# Mux the silent demo video with one music bed under the whole thing (ducked under the voice),
# the three-try progression clips and the voiceover. Runs on the Mac; audio never leaves it.
# usage: mux4.sh VIDEO BED.wav TRY1.wav TRY2.wav TRY3.wav VO_DIR OUT.mp4   (env O1 O2 O3 = start offset in each try, s)
set -euo pipefail
VIDEO=$1 BED=$2 T1=$3 T2=$4 T3=$5 VO=$6 OUT=$7; O1=${O1:-0} O2=${O2:-0} O3=${O3:-0}
LINES="n01:0.2 n03:3.4 n04:8.6 n06:13.4 n07:19.4 v05:21.6 v06:26.4 n08:31.3 n09:35.0 v11:40.25 v12:53.8 v13:56.0"
A="aformat=sample_rates=48000:channel_layouts=stereo"
p(){ python3 -c "print($1)"; }
inputs=(-i "$VIDEO" -i "$BED" -i "$T1" -i "$T2" -i "$T3")
# music from frame 1 to the three tries, then back under the end card
filt="[1:a]$A,asplit=2[b1][b2];"
filt+="[b1]atrim=0:44.6,asetpts=N/SR/TB,afade=t=in:d=0.3,afade=t=out:st=44.0:d=0.6,volume=0.55[bedA];"
filt+="[b2]atrim=44.6:51,asetpts=N/SR/TB,afade=t=in:d=0.6,afade=t=out:st=5.0:d=1.0,adelay=54000|54000,volume=0.55[bedB];"
filt+="[2:a]$A,atrim=$O1:$(p "$O1+3.1"),asetpts=N/SR/TB,afade=t=in:d=0.05,afade=t=out:st=2.8:d=0.3,adelay=44600|44600,volume=0.9[t1];"
filt+="[3:a]$A,atrim=$O2:$(p "$O2+3.1"),asetpts=N/SR/TB,afade=t=in:d=0.05,afade=t=out:st=2.8:d=0.3,adelay=47700|47700,volume=0.9[t2];"
filt+="[4:a]$A,atrim=$O3:$(p "$O3+3.2"),asetpts=N/SR/TB,afade=t=in:d=0.05,afade=t=out:st=2.9:d=0.3,adelay=50800|50800,volume=0.9[t3];"
vmix=""; n=0; k=5
for pair in $LINES; do
  id=${pair%%:*}; at=${pair##*:}; f="$VO/$id.wav"; [ -f "$f" ] || continue
  ms=$(p "int($at*1000)")
  inputs+=(-i "$f"); filt+="[$k:a]$A,adelay=$ms|$ms,volume=1.8[vo$k];"
  vmix+="[vo$k]"; n=$((n+1)); k=$((k+1))
done
filt+="${vmix}amix=inputs=$n:normalize=0:duration=longest,asplit=2[vo][vsc];"
# duck the music under the voice
filt+="[bedA][vsc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=350[bedd];"
filt+="[bedd][bedB][t1][t2][t3][vo]amix=inputs=6:normalize=0:duration=longest,atrim=0:60,alimiter=limit=0.95[a]"
ffmpeg -y -v error "${inputs[@]}" -filter_complex "$filt" -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -shortest "$OUT"
echo "wrote $OUT"
