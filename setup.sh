#!/usr/bin/env bash
# SPDX-License-Identifier: WTFPL
# One-time setup: Python environment + pinned third-party sources (into deps/).
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet -r requirements.txt

fetch() {  # fetch <url> <file> <sha256>
    if [ ! -f "$2" ] || ! echo "$3  $2" | sha256sum --check --status; then
        mkdir -p "$(dirname "$2")"
        curl -fsSL "$1" -o "$2"
        echo "$3  $2" | sha256sum --check --status || { echo "hash mismatch: $2"; exit 1; }
    fi
}
PUREDOOM=355cfbd16fac119718879239336ee2ea408886bd
PEANUT=d0bcca771c83a2638c93a9ae61f3d51f226dc905
CODEC=0c15c8f9a97b464ac23ab1641740a8263e0068b0
fetch "https://raw.githubusercontent.com/Daivuk/PureDOOM/$PUREDOOM/PureDOOM.h" deps/PureDOOM.h \
      ec16ce721436bf3fa4c338320afedd56a93c0b563a94ebf283718503c71ca780
fetch "https://raw.githubusercontent.com/deltabeard/Peanut-GB/$PEANUT/peanut_gb.h" deps/peanut-gb/peanut_gb.h \
      cf7777068492474d084f7dca0b00724d4dcdeab7f9a6c91a5d3a82e60d1cf728
fetch "https://raw.githubusercontent.com/deltabeard/Peanut-GB/$PEANUT/examples/sdl2/minigb_apu/minigb_apu.c" deps/peanut-gb/minigb_apu.c \
      cd173eb5979f1210de6eff6b75e76445db09643494f1cf471df43c5f223ec1f2
fetch "https://raw.githubusercontent.com/deltabeard/Peanut-GB/$PEANUT/examples/sdl2/minigb_apu/minigb_apu.h" deps/peanut-gb/minigb_apu.h \
      60d3c838f55653804d66f51d27a33cf14518498a68d1e0506625c67ca061fad1
if [ ! -d deps/gr-custom-tool/.git ]; then
    git clone --quiet https://github.com/DoYitNow/gr-custom-tool.git deps/gr-custom-tool
fi
git -C deps/gr-custom-tool checkout --quiet "$CODEC"

echo "setup done. Now put fwdc243b.bin and doom1.wad in input/ and run ./build.sh <name>"
