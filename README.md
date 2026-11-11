# GR IIIx — LAIN EDITION

Custom firmware for the Ricoh GR IIIx (official **1.60**, `fwdc243b.bin`) that adds a
**LAIN** menu with apps — **DOOM** and a **Game Boy emulator** — plus a boot splash.
Everything is additive: the camera works as before.

[![Watch it running on the camera (YouTube)](https://img.youtube.com/vi/NkyJE5t3QmY/maxresdefault.jpg)](https://youtu.be/NkyJE5t3QmY)

**Video:** https://youtu.be/NkyJE5t3QmY

> **Warning:** this mod is fully vibecoded — the reverse engineering and all the code
> were written by an AI, with a human flashing and testing. It **was tested on a real
> GR IIIx**, build after build, but nobody has reviewed it line by line.

Unofficial, not affiliated with Ricoh. Flash at your own risk. No firmware, WAD or ROM
is included: you build the firmware yourself from your own official file.

## Use

1. Camera menu → Setup → **LAIN** (it replaces *Certification Marks*) → **DOOM** or
   **GAME BOY**. The camera drops to the shooting screen and the app starts there.
2. **MENU** (or shutter / playback) exits back to the camera.

Game Boy ROMs go on the SD card in `LAIN/GAMEBOY/ROM/`:

- original Game Boy `.gb` only (no Color-only ROMs), up to 4 MB, 20 listed
- **short file names**: 8 characters + `.GB` (`POKERED.GB`, not `Pokemon Red.gb`)

### Controls

| Button | Game Boy | DOOM |
| --- | --- | --- |
| D-pad | D-pad | Move / turn |
| OK | A | Fire / confirm |
| ADJ lever pushed in | B | Fire / confirm |
| Fn | START | Use / open doors |
| DISP | SELECT | Automap |
| MENU, shutter, playback | Exit | Exit |

Fn outside an app still does whatever it is assigned to in Customize Controls.

### Known limits

- Game Boy saves are lost when you exit.
- DOOM has sound effects but no music, and no strafe / weapon-switch buttons.
- The boot splash shows only when the camera powers on into the shooting screen.

## Build

Needs Linux or macOS with `python3`, `git` and `curl`.

```
./setup.sh                        # once: Python env + pinned third-party sources
cp /path/to/fwdc243b.bin input/   # official GR IIIx 1.60 firmware
cp /path/to/doom1.wad input/      # DOOM shareware IWAD
./build.sh mybuild                # -> out/fwdc243b-mybuild.bin
```

`build.sh` builds the apps, checks DOOM in emulation, patches the firmware and runs an
end-to-end emulation of the result. It must end with `ALL OK`; if it prints anything
else, do not flash that file. The expected input hashes are in `input/README.md`.

To put your own picture on the boot splash, replace `assets/lain.png`.

## Flash

1. Copy `out/fwdc243b-mybuild.bin` to the SD card root, renamed **`fwdc243b.bin`**.
2. Run the normal Ricoh firmware update (camera off, hold MENU, power on, confirm) with
   a charged battery.

**Back to stock:** do the same with the official `fwdc243b.bin`. Keep a copy of it.

If an app ever freezes the camera, take the battery out and put it back.

## Layout

| Path | What |
| --- | --- |
| `src/camera.c` | Launcher: menu hooks, app table, input, sound, SD access |
| `src/gr3_doom.c` | DOOM app (PureDOOM) |
| `src/gr3_gb.c` | Game Boy app (Peanut-GB + minigb_apu) |
| `src/gr3_splash.c` | Boot splash app |
| `tools/build_gr3x.py` | Builds the apps and the launcher blob |
| `tools/bake.py` | Patches the firmware: menu, texts, hooks, blob |
| `tools/verify.py` | DOOM engine check in emulation |
| `tests/lain_e2e.py` | End-to-end emulation of a patched firmware |
| `assets/lain.png` | Boot splash picture |

Adding another app: [docs/ADDING_APPS.md](docs/ADDING_APPS.md).

## Credits and licences

Do whatever you want with it: **WTFPL**, see [LICENSE](LICENSE).

The one thing that licence cannot cover is code that was not ours to relicense. The
launcher, the apps and two tools are based on GPL-2.0 code from ricoh-gr3-doom, so those
files stay **GPL-2.0-only** ([LICENSE.GPL-2.0](LICENSE.GPL-2.0)). Each file says which
one applies in its first lines; details in [NOTICE.md](NOTICE.md).

- Launcher and DOOM port: based on [ricoh-gr3-doom](https://github.com/Nielk74/ricoh-gr3-doom) by Nielk74
- Menu and firmware research: [Ricoh-GRMod](https://github.com/Maxwooow/Ricoh-GRMod)
- Firmware container codec: [gr-custom-tool](https://github.com/DoYitNow/gr-custom-tool)
- [PureDOOM](https://github.com/Daivuk/PureDOOM), [Peanut-GB](https://github.com/deltabeard/Peanut-GB) and minigb_apu
- Boot splash picture (`assets/lain.png`): art by **@umy0**
