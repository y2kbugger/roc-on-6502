#!/usr/bin/env bash
# Build a Roc app for the 6502, and run it on the SQL 6502 or pack it into
# one SQL file.
#
#   ./build.sh apps/hello.roc --run     # build, then run it (run.py)
#   ./build.sh apps/pi.roc --sql        # build, then write pi.sql (mksql.py)
#
# Roc -> LLVM bitcode (wasm32, Roc's smallest target) -> retargeted to the
# 6502 -> llvm-mos clang LTOs it with the C host into one `sim` image.
# README.md says why each step is the way it is.
#
# Tools: roc, llvm-dis and llvm-mos's mos-sim-clang on PATH, or name them:
# ROC, LLVM_DIS, and LLVM_MOS for the SDK's directory.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
app=
run=0
sql=0
while (($#)); do
    case $1 in
        --run) run=1 ;;
        --sql) sql=1 ;;
        *.roc) app=$1 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
if [[ -z $app ]]; then
    echo "usage: $0 apps/<name>.roc [--run] [--sql]" >&2
    exit 2
fi

roc=${ROC:-roc}
llvm_dis=${LLVM_DIS:-llvm-dis}
mos_clang=${LLVM_MOS:+$LLVM_MOS/bin/}mos-sim-clang

name=$(basename "$app" .roc)
out=$here/out
mkdir -p "$out"

# 1. Roc to bitcode. --keep-temp keeps the bitcode roc writes before LLVM
#    compiles it; a fresh ROC_CACHE_DIR puts exactly one there.
cache=$out/roc-cache
rm -rf "$cache"
ROC_CACHE_DIR=$cache "$roc" build --target=wasm32 --no-cache --keep-temp \
    --output="$out/$name.wasm32.a" "$app" >/dev/null
mapfile -t bitcode < <(find "$cache" -name 'roc_app_llvm_wasm32_*.bc')
if ((${#bitcode[@]} != 1)); then
    echo "expected one bitcode file under $cache, found ${#bitcode[@]}" >&2
    exit 1
fi
cp "${bitcode[0]}" "$out/$name.wasm32.bc"
rm -rf "$cache"

# 2. Retarget: drop wasm32's triple and data layout so clang gives the
#    module the 6502's. The IR keeps Roc's 32-bit words (host.c says how).
"$llvm_dis" "$out/$name.wasm32.bc" -o - |
    grep -v -E '^target (datalayout|triple) =' >"$out/$name.mos.ll"

# 3. Compile and link with the host for llvm-mos's sim machine.
"$mos_clang" -Os -Wall -Wno-override-module \
    "$here/platform/host.c" "$out/$name.mos.ll" -o "$out/$name.sim"
echo "built out/$name.sim ($(stat -c %s "$out/$name.sim") bytes)" >&2

cd "$here"
if ((sql)); then
    python3 mksql.py "out/$name.sim" -o "$name.sql"
fi
if ((run)); then
    python3 run.py "out/$name.sim"
fi
