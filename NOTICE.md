# Attribution and third-party material

Two licences apply, file by file (each file names its own in an `SPDX` line):

| Files | Licence |
| --- | --- |
| `tools/bake.py`, `tools/make_test_rom.py`, `tests/`, `setup.sh`, `build.sh`, `src/gb_shim/`, `docs/`, `README.md` | **WTFPL** — [LICENSE](LICENSE) |
| `src/camera.c`, `src/gr3_doom.c`, `src/gr3_gb.c`, `src/gr3_splash.c`, `tools/build_gr3x.py`, `tools/verify.py` | **GPL-2.0-only** — [LICENSE.GPL-2.0](LICENSE.GPL-2.0) |

The second group is, or contains code copied from, GPL-2.0-only work by others (below),
which cannot be relicensed. A firmware built from this repository combines both groups
with GPL code and is therefore GPL-2.0 as a whole.

## Derived from ricoh-gr3-doom

`src/camera.c`, `src/gr3_doom.c`, `tools/build_gr3x.py` and `tools/verify.py` are
modified versions of files from, and `src/gr3_gb.c` and `src/gr3_splash.c` reuse
routines (the escape checkpoint, the screen-buffer checks) from,
[Nielk74/ricoh-gr3-doom](https://github.com/Nielk74/ricoh-gr3-doom) (GPL-2.0-only),
which runs DOOM on the GR III 2.10. Changes made here, November 2026: retargeted to the
GR IIIx 1.60; launch from a Setup submenu instead of the Fn button; a multi-app
launcher; state-based button handling; sound output; SD card access; the Game Boy and
splash apps; the firmware patcher and the end-to-end test. That project in turn builds
on [Maxwooow/Ricoh-GRMod](https://github.com/Maxwooow/Ricoh-GRMod) (GPL-2.0-only),
whose published research on the GR III / IIIx menus, text tables and submenu
controllers this project also relies on. Neither upstream endorses this project.

The small "NIELK74" mark drawn in the corner of the DOOM picture is the original
port's and is kept as credit.

## Fetched at setup, not stored in this repository

`setup.sh` downloads these pinned versions into `deps/`:

| Dependency | Version | Licence |
| --- | --- | --- |
| [Daivuk/PureDOOM](https://github.com/Daivuk/PureDOOM) (`PureDOOM.h`) | commit `355cfbd16fac119718879239336ee2ea408886bd` | DOOM Source Code License (see the header) |
| [deltabeard/Peanut-GB](https://github.com/deltabeard/Peanut-GB) (`peanut_gb.h`, `minigb_apu.c/.h`) | commit `d0bcca771c83a2638c93a9ae61f3d51f226dc905` | MIT (text in each file) |
| [DoYitNow/gr-custom-tool](https://github.com/DoYitNow/gr-custom-tool) (firmware container codec) | commit `0c15c8f9a97b464ac23ab1641740a8263e0068b0` | GPL-2.0 |

PureDOOM's licence is separate from this repository's GPL. No licence to redistribute
a combined firmware file is asserted: build it for your own camera.

## Not included

The official Ricoh firmware, the DOOM IWAD and Game Boy ROMs are local inputs supplied
by the user. They, and every firmware file built from them, are excluded from this
repository (`.gitignore`) and must not be added to it.

## Artwork

`assets/lain.png` is artwork by **@umy0**, used for the boot splash. It is not covered
by the GPL grant; rights remain with the artist. The character depicted belongs to its
respective rights holders.

`tools/make_test_rom.py` generates an original Game Boy test ROM (no third-party code
or data); it is WTFPL like the rest of our own files.

RICOH, GR, DOOM and Game Boy are trademarks of their respective owners.
