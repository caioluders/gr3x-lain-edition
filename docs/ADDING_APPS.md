# Adding an app to the LAIN menu

An app is a small freestanding C program compiled into a relocatable image. The
launcher (`camera.c`) copies it into camera RAM, calls its entry points from a timer,
feeds it buttons, shows its picture and plays its sound. The app never calls the
camera directly.

Read `gr3_splash.c` first (60 lines, the smallest complete app), then `gr3_gb.c`.

## 1. Write the app: `src/gr3_myapp.c`

Rules: no libc, no OS calls, no fixed addresses, no floating point library calls.
Global variables are fine (the image is copied fresh on every launch, zeroed).
Every exported function name must start with `gr3_`.

| Entry point | Called | Must do |
| --- | --- | --- |
| `int gr3_init(void *arena, u32 arena_bytes, const void *data, u32 data_bytes)` | once at launch | set up; return 1, or 0 to refuse |
| `int gr3_tick(void)` | ~35 times a second | advance the app; return 0 to quit |
| `int gr3_key(u32 id, u32 down)` | before a tick, on a change | record a button going down (1) or up (0) |
| `void gr3_release_keys(void)` | on exit | drop all held buttons |
| `int gr3_blit(void *descriptor)` | when the screen redraws | draw the full 720x480 picture |
| `u32 gr3_audio(short *dst, u32 max_frames)` | after each tick (optional) | write up to `max_frames` of 48 kHz stereo 16-bit; return the count |
| `void gr3_pace(u32 frames)` | before each tick (with audio) | how many sound frames the queue is short of |

Details that matter:

- **Screen**: copy the validation block and pixel loop from `gr3_splash.c`. The buffer
  is 720x480, 32-bit, bytes R,G,B,A (alpha 0xff), row pitch `dim[0]` (720 or 736).
- **Buttons** (`id`): 9 up, 10 down, 11 left, 12 right, 8 OK, 30 ADJ pushed in, 27 Fn,
  4 DISP. Only these reach the app. MENU, shutter and playback always exit.
- **Timing**: the timer is not exact. If the app has sound, use `gr3_pace`: run as many
  steps as needed to produce the frames asked for, so the speaker is the clock (see
  `gr3_tick` in `gr3_gb.c`). Without sound, one step per tick is fine.
- **Memory**: `arena` is yours for the session (size set in the app table). There is a
  128 KB stack.
- **Errors**: return 0 from `gr3_tick` to exit cleanly. Never loop forever.

## 2. Build it: `tools/build_gr3x.py`

Copy `build_splash` as `build_myapp`: it copies the source into a work folder, calls
`build_image(...)` (compile, link at two addresses, derive pointer fixups) and writes
three files into the build folder: `myapp-runtime.bin`, `myapp-relocations.bin` and
your data file (or none).

Then in `build_camera`:

- add `MYAPP_IMAGE_BYTES`, `MYAPP_FIXUP_COUNT`, `MYOFF_<entry>` lines to the header
- add your files to the two asset lists (`myapp_image`, `myapp_fixups`, `myapp_data`)
- add them to `asset_addresses`
- include your hash in the `embedded content` comment (forces a rebuild when it changes)

Call `build_myapp` from `main` and pass the result to `build_camera`.

## 3. Register it in the launcher: `src/camera.c`

```c
extern const u8 myapp_image[], myapp_data[];
extern const u32 myapp_fixups[];

#define APP_COUNT 4u
static const App apps[APP_COUNT] = {
    ...
    { myapp_image, myapp_fixups, MYAPP_IMAGE_BYTES, MYAPP_FIXUP_COUNT,
      myapp_data, MYAPP_DATA_BYTES, /* arena bytes */ 1u << 20,
      MYOFF_gr3_init, MYOFF_gr3_tick, MYOFF_gr3_key, MYOFF_gr3_release_keys,
      MYOFF_gr3_blit, /* audio */ 0, /* pace */ 0 },
};
```

Menu line → app: each line has its own id. Add one (next free: `0x020001a2`):

- a handler `static u32 lain_ok_myapp(void *c, u32 id) { return launch(3); }`
- its `lain_vtable_myapp` / `lain_object_myapp` (copy the Game Boy pair)
- a compare + return in `camera_factory` (copy the `0x01a1` lines and label `4:`)

`launch(n)` flags app `n` and sends the camera back to the shooting screen, where the
first redraw starts it.

To let the app read the SD card, pass it a small table of launcher functions as its
`data`, the way the Game Boy app gets `gb_host` (`sd_list`, `sd_load`).

## 4. Add the menu line: `tools/bake.py`

- add the label to `NEW_TEXTS` (it gets the next text id, `TEXT_COUNT + index`)
- in "record 17": raise the line count (`+6`), set the line's text (`+8 + 2*line`),
  its id (`+0x14 + 4*line`) and x position 540 (`+0x28 + 4*line`)

A submenu record holds **4 lines at most**. For more apps, add a second record and a
second submenu id (see how `LAIN_SUBMENU_ID` is wired: factory remap, key map,
`camera_sub_setcurrent`).

## 5. Test before flashing

```
./build.sh test
```

Add a section for your app to `tests/lain_e2e.py`: launch it
(`cam.w(PENDING, MAGIC + n); cam.draw()`), tick, push buttons, compare screenshots.
The test runs the real baked firmware code against mocks of the camera services, so it
catches launcher mistakes; it cannot prove the camera behaves like the mocks.

## Things that will bite you

- **Press and release**: the camera sends 2 for pushed and 1 for let go. The launcher
  already handles this; apps only see `down` = 1 or 0.
- **Do not allocate or block in the menu hooks.** The camera calls the controller
  factory on every event and redraw.
- **Read-only code**: the firmware image (`0x53…`/`0x54…`) cannot be written at run
  time. Writable state lives in the session the launcher allocates.
- **Size**: the baked RTOS must end below `0x55000000`; the bake prints the headroom.
- **Compiler**: PureDOOM must stay at `-Os` (clang crashes at `-O2`); other apps can
  use `-O2`.
- **Long file names** are not listed by the SD directory call; use 8.3 names.
