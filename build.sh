#!/usr/bin/env bash
# SPDX-License-Identifier: WTFPL
# Build the LAIN firmware: apps + launcher -> bake into the stock firmware -> emulate.
# Usage: ./build.sh <name>      writes out/fwdc243b-<name>.bin
set -euo pipefail
cd "$(dirname "$0")"
NAME="${1:?usage: ./build.sh <name>}"
PY=.venv/bin/python
OUT="out/fwdc243b-$NAME.bin"
for f in input/fwdc243b.bin input/doom1.wad deps/PureDOOM.h; do
    [ -f "$f" ] || { echo "missing $f (see README: Build)"; exit 1; }
done
mkdir -p build out

# 1. apps (DOOM, Game Boy, splash) + launcher blob
$PY tools/build_gr3x.py --engine deps/PureDOOM.h --wad input/doom1.wad \
    --gb-core deps/peanut-gb/peanut_gb.h --splash assets/lain.png --out build 2>&1 \
    | grep -v 'deprecated\|push {\|^ *\^\|MIN\|warning generated\|In file included\|^ *[0-9]* |' || true
[ -f build/camera.bin ] || { echo "app build FAILED"; exit 1; }

# 2. DOOM engine check in emulation
(cd tools && ../$PY verify.py ../build > ../build/verify.log 2>&1) || { echo "DOOM verify FAILED, see build/verify.log"; exit 1; }

# 3. bake into the stock firmware (patches + appended blob, checksum, round-trip)
$PY -I tools/bake.py --out "$OUT" | grep 'wrote\|round-trip\|headroom'

# 4. launcher end-to-end emulation on the baked file
$PY tests/lain_e2e.py "$OUT" | grep -v '^OK'

echo "built $OUT  ->  copy it to the SD card root as fwdc243b.bin"
