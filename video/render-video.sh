#!/usr/bin/env bash
set -euo pipefail

video_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${video_root}/.." && pwd)"
scenes="${video_root}/scenes"
segments="${video_root}/segments"
output="${video_root}/aarchtune-devpost-demo.mp4"
regular="/usr/share/fonts/noto/NotoSans-Regular.ttf"
bold="/usr/share/fonts/noto/NotoSans-Bold.ttf"
mono="/usr/share/fonts/noto/NotoSansMono-Bold.ttf"

rtk mkdir -p "${scenes}" "${segments}"

rtk magick -size 1920x1080 xc:"#07111f" \
  -fill "#0c1a2d" -draw "roundrectangle 70,70 1850,970 34,34" \
  -fill "#5eead4" -draw "roundrectangle 90,90 104,950 7,7" \
  \( "${repo_root}/docs/screenshots/01-project-overview-cloud-ai.png" \
     -crop 1320x810+20+135 +repage -resize 1040x638 \
     -bordercolor "#253b55" -border 2 \) \
  -geometry +760+178 -composite \
  -font "${bold}" -fill "#5eead4" -pointsize 30 \
  -annotate +145+180 "ARM CREATE 2026  /  CLOUD AI" \
  -fill "#f5f8ff" -pointsize 82 -interline-spacing 4 \
  -annotate +145+315 $'AArchTune\nQuality-gated\nArm inference' \
  -font "${regular}" -fill "#a9b8cd" -pointsize 34 \
  -annotate +145+670 $'Faster is useful only when\ncorrectness survives.' \
  -fill "#5eead4" -draw "roundrectangle 145,785 540,848 18,18" \
  -font "${bold}" -fill "#07111f" -pointsize 26 \
  -annotate +180+827 "NATIVE ARM64  •  GGUF" \
  "${scenes}/01-problem.png"

rtk magick -size 1920x1080 xc:"#07111f" \
  -fill "#0c1a2d" -draw "roundrectangle 70,70 1850,970 34,34" \
  -font "${bold}" -fill "#5eead4" -pointsize 30 \
  -annotate +120+155 "WORKING PROJECT  /  RELEASE GATE" \
  -fill "#f5f8ff" -pointsize 72 \
  -annotate +120+255 "Verified before it recommends" \
  -fill "#10243d" -draw "roundrectangle 120,330 690,650 28,28" \
  -fill "#10243d" -draw "roundrectangle 720,330 1290,650 28,28" \
  -fill "#10243d" -draw "roundrectangle 1320,330 1800,650 28,28" \
  -font "${mono}" -fill "#5eead4" -pointsize 104 \
  -annotate +175+485 "518" \
  -font "${bold}" -fill "#f5f8ff" -pointsize 32 \
  -annotate +175+555 "automated tests" \
  -font "${mono}" -fill "#5eead4" -pointsize 104 \
  -annotate +775+485 "90%" \
  -font "${bold}" -fill "#f5f8ff" -pointsize 32 \
  -annotate +775+555 "aggregate coverage" \
  -font "${mono}" -fill "#5eead4" -pointsize 78 \
  -annotate +1375+475 "3.11" \
  -annotate +1375+555 "3.12" \
  -font "${bold}" -fill "#f5f8ff" -pointsize 30 \
  -annotate +1375+610 "CI matrix" \
  -font "${regular}" -fill "#a9b8cd" -pointsize 31 \
  -annotate +120+755 $'Tests  •  strict typing  •  lint  •  formatting  •  CLI validation\nlicense checks  •  artifact checks  •  secret scanning' \
  -fill "#5eead4" -draw "roundrectangle 120,740 505,795 16,16" \
  -font "${bold}" -fill "#07111f" -pointsize 24 \
  -annotate +150+777 "COMPLETE RELEASE VALIDATION" \
  "${scenes}/02-working-project.png"

rtk magick -size 1920x1080 xc:"#07111f" \
  -fill "#0c1a2d" -draw "roundrectangle 70,70 1850,970 34,34" \
  -font "${bold}" -fill "#5eead4" -pointsize 30 \
  -annotate +120+155 "NATIVE EVIDENCE  /  RUN 30119492016" \
  -fill "#f5f8ff" -pointsize 76 \
  -annotate +120+260 "Measured on real Arm64" \
  -fill "#10243d" -draw "roundrectangle 120,340 900,720 30,30" \
  -fill "#10243d" -draw "roundrectangle 940,340 1800,720 30,30" \
  -font "${bold}" -fill "#f5f8ff" -pointsize 44 \
  -annotate +180+430 "Linux AArch64" \
  -annotate +180+515 "4 × Neoverse-N2 cores" \
  -annotate +180+600 "CPU-only llama.cpp" \
  -annotate +180+685 "n_gpu_layers = 0" \
  -fill "#5eead4" -pointsize 40 \
  -annotate +1000+430 "PINNED PROVENANCE" \
  -font "${regular}" -fill "#a9b8cd" -pointsize 34 \
  -annotate +1000+515 $'runtime source + binary\nmodel + workload\nquality policy + evidence' \
  -fill "#ffca6a" -draw "roundrectangle 120,720 1800,820 24,24" \
  -font "${bold}" -fill "#231705" -pointsize 29 \
  -annotate +170+765 $'KleidiAI compiled in  •  no Q4_K kernel observed\nNo unsupported acceleration claim' \
  "${scenes}/03-native-arm.png"

rtk magick -size 1920x1080 xc:"#07111f" \
  -fill "#0c1a2d" -draw "roundrectangle 70,70 1850,970 34,34" \
  -font "${bold}" -fill "#5eead4" -pointsize 30 \
  -annotate +120+155 "BOUNDED OPTIMIZATION PIPELINE" \
  -fill "#f5f8ff" -pointsize 70 \
  -annotate +120+255 "Quality gates before ranking" \
  -fill "#10243d" \
  -draw "roundrectangle 120,350 455,500 24,24 roundrectangle 505,350 840,500 24,24 roundrectangle 890,350 1225,500 24,24 roundrectangle 1275,350 1610,500 24,24" \
  -draw "roundrectangle 310,610 645,760 24,24 roundrectangle 695,610 1030,760 24,24 roundrectangle 1080,610 1415,760 24,24" \
  -font "${bold}" -fill "#f5f8ff" -pointsize 31 -gravity center \
  -annotate -575-115 "DETECT" -annotate -190-115 "BASELINE" \
  -annotate +195-115 "PLAN" -annotate +580-115 "DEDUPLICATE" \
  -annotate -385+145 "SCREEN" -annotate +0+145 "EVALUATE" \
  -annotate +385+145 "QUALITY + DRIFT" \
  -gravity northwest -font "${bold}" -fill "#5eead4" -pointsize 52 \
  -annotate +465+460 "→" -annotate +850+460 "→" -annotate +1235+460 "→" \
  -annotate +655+720 "→" -annotate +1040+720 "→" \
  -font "${regular}" -fill "#a9b8cd" -pointsize 31 \
  -annotate +120+820 "Only complete, correct candidates can enter performance ranking." \
  -font "${mono}" -fill "#5eead4" -pointsize 26 \
  -annotate +1280+820 "HASH-BOUND EVIDENCE" \
  "${scenes}/04-pipeline.png"

rtk magick -size 1920x1080 xc:"#07111f" \
  -fill "#0c1a2d" -draw "roundrectangle 70,70 1850,970 34,34" \
  -font "${bold}" -fill "#5eead4" -pointsize 30 \
  -annotate +120+155 "MEASURED NATIVE RESULT" \
  -fill "#f5f8ff" -pointsize 70 \
  -annotate +120+255 "The optimization funnel" \
  -font "${mono}" -fill "#5eead4" -pointsize 154 \
  -annotate +150+520 "132" -annotate +735+520 "11" -annotate +1285+520 "4" \
  -font "${bold}" -fill "#f5f8ff" -pointsize 31 \
  -annotate +150+585 "screening executions" \
  -annotate +735+585 "distinct signatures" \
  -annotate +1285+585 "evaluated profiles" \
  -fill "#5eead4" -pointsize 80 \
  -annotate +570+500 "→" -annotate +1120+500 "→" \
  -fill "#ffca6a" -draw "roundrectangle 120,650 1800,820 28,28" \
  -font "${mono}" -fill "#231705" -pointsize 48 \
  -annotate +185+730 "no_eligible_candidate" \
  -font "${bold}" -fill "#231705" -pointsize 29 \
  -annotate +185+785 "No speedup claim  •  No deployment recommendation" \
  "${scenes}/05-result.png"

rtk magick -size 1920x1080 xc:"#07111f" \
  -fill "#0c1a2d" -draw "roundrectangle 70,70 1850,970 34,34" \
  \( "${repo_root}/docs/screenshots/05-fastest-candidate-rejected.png" \
     -crop 1360x780+40+160 +repage -resize 1100x630 \
     -bordercolor "#253b55" -border 2 \) \
  -geometry +690+225 -composite \
  -font "${bold}" -fill "#ffca6a" -pointsize 28 \
  -annotate +120+155 "SYNTHETIC PRODUCT-BEHAVIOR EXAMPLE" \
  -fill "#f5f8ff" -pointsize 70 \
  -annotate +120+275 $'Fast is\nnot enough' \
  -font "${regular}" -fill "#a9b8cd" -pointsize 34 \
  -annotate +120+500 $'The speed leader is rejected\nwhen workload quality regresses.' \
  -fill "#5eead4" -draw "roundrectangle 120,665 575,730 18,18" \
  -font "${bold}" -fill "#07111f" -pointsize 25 \
  -annotate +155+708 "CORRECTNESS BEFORE RANKING" \
  -font "${regular}" -fill "#a9b8cd" -pointsize 25 \
  -annotate +120+825 $'Clearly labelled synthetic evidence.\nNever an Arm performance claim.' \
  "${scenes}/06-refusal.png"

rtk magick -size 1920x1080 xc:"#07111f" \
  -fill "#0c1a2d" -draw "roundrectangle 70,70 1850,970 34,34" \
  -font "${bold}" -fill "#5eead4" -pointsize 30 \
  -annotate +120+155 "REUSABLE OUTPUT" \
  -fill "#f5f8ff" -pointsize 72 \
  -annotate +120+255 "Optimization Passport" \
  -fill "#10243d" -draw "roundrectangle 120,340 1800,820 30,30" \
  -font "${mono}" -fill "#5eead4" -pointsize 28 \
  -annotate +180+430 "PASSPORT  d5938e1ade96…" \
  -font "${bold}" -fill "#f5f8ff" -pointsize 36 \
  -annotate +180+530 "HARDWARE" -annotate +600+530 "RUNTIME" \
  -annotate +1020+530 "MODEL" -annotate +1400+530 "WORKLOAD" \
  -annotate +180+675 "POLICY" -annotate +600+675 "DRIFT" \
  -annotate +1020+675 "CLEANUP" -annotate +1400+675 "DECISION" \
  -fill "#5eead4" \
  -draw "circle 200,565 200,578 circle 620,565 620,578 circle 1040,565 1040,578 circle 1420,565 1420,578 circle 200,710 200,723 circle 620,710 620,723 circle 1040,710 1040,723 circle 1420,710 1420,723" \
  -font "${regular}" -fill "#a9b8cd" -pointsize 30 \
  -annotate +120+840 "Deployment files exist only when an eligible profile survives every gate." \
  "${scenes}/07-output.png"

rtk magick -size 1920x1080 gradient:"#07111f-#10243d" \
  -fill "#5eead4" -draw "roundrectangle 110,110 126,970 8,8" \
  -font "${bold}" -fill "#5eead4" -pointsize 30 \
  -annotate +180+190 "AARCHTUNE  /  NATIVE ARM64" \
  -fill "#f5f8ff" -pointsize 78 \
  -annotate +180+350 $'Measure natively.\nValidate the workload.\nDeploy only when both survive.' \
  -font "${regular}" -fill "#a9b8cd" -pointsize 34 \
  -annotate +180+660 "Open source  •  MIT  •  Verifiable evidence" \
  -fill "#5eead4" -draw "roundrectangle 180,725 1660,815 22,22" \
  -font "${mono}" -fill "#07111f" -pointsize 31 \
  -annotate +230+783 "github.com/Sombra-1/arm-agent-optimizer" \
  "${scenes}/08-close.png"

names=(
  "01-problem"
  "02-working-project"
  "03-native-arm"
  "04-pipeline"
  "05-result"
  "06-refusal"
  "07-output"
  "08-close"
)

for name in "${names[@]}"; do
  rtk ffmpeg -y -loop 1 -framerate 30 \
    -i "${scenes}/${name}.png" \
    -i "${video_root}/narration/${name}.mp3" \
    -vf "zoompan=z='min(zoom+0.00008,1.025)':d=1:s=1920x1080:fps=30,subtitles=${video_root}/narration/${name}.srt:original_size=1920x1080:force_style='FontName=Noto Sans,FontSize=16,PrimaryColour=&H00FFFFFF,BackColour=&HBB07111F,BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=24'" \
    -af "loudnorm=I=-16:LRA=7:TP=-1.5" \
    -c:v libx264 -preset medium -crf 19 -pix_fmt yuv420p \
    -c:a aac -b:a 192k -ar 48000 -shortest \
    -movflags +faststart "${segments}/${name}.mp4"
done

rtk ffmpeg -y \
  -i "${segments}/01-problem.mp4" \
  -i "${segments}/02-working-project.mp4" \
  -i "${segments}/03-native-arm.mp4" \
  -i "${segments}/04-pipeline.mp4" \
  -i "${segments}/05-result.mp4" \
  -i "${segments}/06-refusal.mp4" \
  -i "${segments}/07-output.mp4" \
  -i "${segments}/08-close.mp4" \
  -filter_complex "[0:v][0:a][1:v][1:a][2:v][2:a][3:v][3:a][4:v][4:a][5:v][5:a][6:v][6:a][7:v][7:a]concat=n=8:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" \
  -c:v libx264 -preset medium -crf 19 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -ar 48000 -movflags +faststart \
  "${output}"

rtk ffprobe -v error -show_entries format=duration,size \
  -show_entries stream=codec_name,width,height,r_frame_rate \
  -of json "${output}"
