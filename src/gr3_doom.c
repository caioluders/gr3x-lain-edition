/* SPDX-License-Identifier: GPL-2.0-only */
/* Experimental, freestanding PureDOOM adapter. This file is GPL-2.0-only.
 * PureDOOM is a separately obtained dependency under its own source licence.
 * No operating-system calls, SD writes or fixed camera addresses in this module.
 */
#define DOOM_IMPLEMENTATION
#include "PureDOOM.h"

typedef unsigned int u32;
typedef unsigned char u8;
typedef unsigned short u16;

static u8 *arena;
static u32 arena_size, arena_used, ticks, ready, failed, held;
static u32 paced, pace_frames, tics_to_sound;   /* sound-clock pacing, see gr3_tick */
void gr3_pace(u32 frames) { pace_frames = frames; paced = 1; }
static const u8 *wad;
static u32 wad_size;
static char log_text[1024];
static u32 log_used;
static u32 escape_context[26];
typedef struct { u32 position, used; } WadFile;
static WadFile files[4];

__attribute__((naked, returns_twice)) int gr3_checkpoint(u32 *p __attribute__((unused))) {
    __asm__ volatile("stmia r0, {r4-r11,sp,lr}\nadd r1,r0,#40\nvstmia r1,{d8-d15}\nmov r0,#0\nbx lr");
}
__attribute__((naked, noreturn)) void gr3_escape(u32 *p __attribute__((unused))) {
    __asm__ volatile("add r1,r0,#40\nvldmia r1,{d8-d15}\nldmia r0, {r4-r11,sp,lr}\nmov r0,#1\nbx lr");
}
static void halt(int code) {
    failed = code ? (u32)code : 1;
    ready = 0;
    gr3_escape(escape_context);
}
static void log_line(const char *s) {
    while (*s && log_used + 1 < sizeof(log_text)) log_text[log_used++] = *s++;
    log_text[log_used] = 0;
}
static void *allocate(int n) {
    if (n < 0 || (u32)n > arena_size - 15u || arena_used > arena_size - (u32)n - 15u) halt(1001);
    u32 start = arena_used;
    arena_used = (start + (u32)n + 15u) & ~15u;
    return arena + start;
}
/* DOOM's main zone owns its reusable allocations. Small init allocations live
 * for the entire session; this bounded arena is discarded with the runtime. */
static void release(void *p) { (void)p; }
static int same(const char *a, const char *b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}
static void *open_wad(const char *name, const char *mode) {
    if (!name || !mode || !same(mode, "rb")) return 0;
    const char *leaf = name;
    for (const char *p = name; *p; ++p) if (*p == '/' || *p == '\\') leaf = p + 1;
    if (!same(leaf, "doom1.wad")) return 0;
    for (u32 i = 0; i < 4; ++i) if (!files[i].used) {
        files[i].position = 0; files[i].used = 1; return &files[i];
    }
    return 0;
}
static void close_wad(void *p) { if (p) ((WadFile *)p)->used = 0; }
static int read_wad(void *p, void *out, int n) {
    WadFile *f = p;
    if (!f || !f->used || n < 0 || f->position > wad_size) return -1;
    u32 count = (u32)n;
    if (count > wad_size - f->position) count = wad_size - f->position;
    for (u32 i = 0; i < count; ++i) ((u8 *)out)[i] = wad[f->position + i];
    f->position += count;
    return (int)count;
}
static int write_none(void *p, const void *data, int n) { (void)p; (void)data; (void)n; return -1; }
static int seek_wad(void *p, int offset, doom_seek_t origin) {
    WadFile *f = p;
    if (!f || !f->used) return -1;
    int base;
    if (origin == DOOM_SEEK_SET) base = 0;
    else if (origin == DOOM_SEEK_CUR) base = (int)f->position;
    else if (origin == DOOM_SEEK_END) base = (int)wad_size;
    else return -1;
    if (offset < -base || offset > (int)wad_size - base) return -1;
    f->position = (u32)(base + offset); return 0;
}
static int tell_wad(void *p) { return p ? (int)((WadFile *)p)->position : -1; }
static int eof_wad(void *p) { return !p || ((WadFile *)p)->position >= wad_size; }
static char *environment(const char *name) {
    return same(name, "HOME") || same(name, "DOOMWADDIR") ? "." : 0;
}
static void game_time(int *sec, int *usec) {
    /* One explicit native timer callback = one game tic; no busy wait. */
    *sec = (int)(ticks / 35u);
    *usec = (int)((ticks % 35u) * 1000000u / 35u);
}

int gr3_init(void *memory, u32 bytes, const void *iwad, u32 length) {
    if (!memory || ((u32)memory & 15u) || bytes < 8u * 1024u * 1024u || !iwad || length < 12 || length > 0x10000000u) return 0;
    arena = memory; arena_size = bytes; arena_used = ticks = ready = failed = held = log_used = 0;
    wad = iwad; wad_size = length;
    if (gr3_checkpoint(escape_context)) return 0;
    doom_set_print(log_line);
    doom_set_malloc(allocate, release);
    doom_set_file_io(open_wad, close_wad, read_wad, write_none, seek_wad, tell_wad, eof_wad);
    doom_set_gettime(game_time); doom_set_exit(halt); doom_set_getenv(environment);
    static char *args[] = {"gr3-doom", "-iwad", "doom1.wad", "-nomusic"};
    doom_init(4, args, DOOM_FLAG_HIDE_MOUSE_OPTIONS | DOOM_FLAG_HIDE_SOUND_OPTIONS | DOOM_FLAG_HIDE_MUSIC_OPTIONS);
    ready = 1; return 1;
}

int gr3_tick(void) {
    if (!ready || failed) return 0;
    if (gr3_checkpoint(escape_context)) return 0;
    u32 count = 1;
    if (paced) {
        /* The speaker is the clock (48000 frames/s, 48000/35 per game tic): run as many
         * tics as the launcher's sound queue is short of, so game speed is exact. */
        count = (pace_frames + 685u) / 1371u;
        if (count > 3u) count = 3u;
        paced = 0;
    }
    for (u32 i = 0; i < count; ++i) {
        nodrawers = i + 1u != count;           /* catch-up tics run the game but skip drawing */
        ++ticks; doom_force_update();
    }
    nodrawers = 0;
    tics_to_sound += count;
    return 1;
}

/* Press/release pairs use the native switch IDs established by the UI probes.
 * MENU (5) belongs to the camera wrapper and exits; shutter is never remapped. */
int gr3_key(u32 id, u32 down) {
    if (!ready || down > 1u || id >= 32u) return 0;
    doom_key_t key;
    switch (id) {
        case 9: key = DOOM_KEY_UP_ARROW; break;
        case 10: key = DOOM_KEY_DOWN_ARROW; break;
        case 11: key = DOOM_KEY_LEFT_ARROW; break;   /* 11 = left, 12 = right: in the camera's own menus */
        case 12: key = DOOM_KEY_RIGHT_ARROW; break;  /* 11 acts as "back" (like MENU) and 12 as "enter" (like OK) */
        case 8:   /* OK: optional backup */
        case 30:  /* ADJ-P: press the ADJ lever inward */
            key = DOOM_KEY_ENTER; break;
        case 27: key = DOOM_KEY_SPACE; break;
        case 4: key = DOOM_KEY_TAB; break;
        default: return 0;
    }
    u32 mask = 1u << id;
    u32 confirm_buttons = (1u << 8) | (1u << 30);
    int confirm = (mask & confirm_buttons) != 0;
    if (down) {
        if (held & mask) return 1;
        u32 previous = held;
        held |= mask;
        if (confirm && (previous & confirm_buttons)) return 1;
        doom_key_down(key);
        /* Confirm also fires. Shared key state stays down until both physical
         * confirm buttons are released, even across a game-menu transition. */
        if (confirm) doom_key_down(DOOM_KEY_CTRL);
    } else {
        if (!(held & mask)) return 1;
        held &= ~mask;
        if (confirm && (held & confirm_buttons)) return 1;
        doom_key_up(key);
        if (confirm) doom_key_up(DOOM_KEY_CTRL);
    }
    return 1;
}
/* Sound effects: DOOM mixes 512 stereo frames at 11025 Hz per call; resample to the
 * camera's 48 kHz and hand the launcher one tick's worth (48000/35 frames). Music is
 * MIDI and is not played. */
static short mixed[1024];
static u32 mix_position = 512u << 16, tick_fraction;
u32 gr3_audio(short *dst, u32 max_frames) {
    if (!ready || failed || !dst) return 0;
    if (gr3_checkpoint(escape_context)) return 0;
    u32 want = 0;                              /* one tic's worth per game tic run since last call */
    for (; tics_to_sound; --tics_to_sound) {
        want += 1371u; tick_fraction += 3u;
        if (tick_fraction >= 7u) { tick_fraction -= 7u; ++want; }
    }
    if (want > max_frames) want = max_frames;
    for (u32 i = 0; i < want; ++i) {
        if ((mix_position >> 16) >= 512u) {
            const short *source = doom_get_sound_buffer();
            for (u32 k = 0; k < 1024; ++k) mixed[k] = source[k];
            mix_position -= 512u << 16;
        }
        u32 at = mix_position >> 16;
        /* DOOM passes its 0..15 volume where the mixer expects 0..127, so the mix is
         * about 1/8 scale: bring it up, clipped. */
        for (u32 side = 0; side < 2; ++side) {
            int v = mixed[at * 2 + side] * 6;
            dst[i * 2 + side] = (short)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
        }
        mix_position += 15053u;                 /* 11025/48000 in 16.16 */
    }
    return want;
}
void gr3_release_keys(void) {
    for (u32 i = 0; i < 32; ++i) if (held & (1u << i)) gr3_key(i, 0);
}

/* Nielk74: 41x7 pixels at the top right of the game, plus a 1px dark outline.
 * Draw on the final surface so the mark stays fixed over every game frame. */
static void watermark(u32 *dst, u32 pitch) {
    static const u8 glyphs[7][7] = {
        {17,25,25,21,19,19,17}, {4,0,12,4,4,4,14},
        {0,0,14,17,31,16,14}, {12,4,4,4,4,4,14},
        {16,16,18,20,24,20,18}, {31,1,2,4,8,8,8}, {2,6,10,18,31,2,2}
    };
    for (u32 pass = 0; pass < 2; ++pass)
        for (u32 c = 0; c < 7; ++c)
            for (u32 y = 0; y < 7; ++y)
                for (u32 x = 0; x < 5; ++x) if (glyphs[c][y] & (16u >> x)) {
                    u32 at = (8u + y) * pitch + 632u + c * 6u + x;
                    if (pass) dst[at] = 0xffffffffu;
                    else for (int dy = -1; dy <= 1; ++dy)
                        for (int dx = -1; dx <= 1; ++dx)
                            dst[at + dy * (int)pitch + dx] = 0xff000000u;
                }
}

/* Native ScreenBuffer format 5 stores R,G,B,A bytes. Correct VGA's non-square
 * pixels to 4:3: 640x480 at x=40, with black pillarboxes. Keep row padding intact. */
int gr3_blit(void *descriptor) {
    if (!ready || !descriptor) return 0;
    const u8 *b = descriptor;
    const u16 *dim = (const u16 *)(b + 0x18);
    u32 address = *(const u32 *)(b + 8);
    if (b[2] != 5 || (dim[0] != 720 && dim[0] != 736) || dim[1] != 480 || dim[2] != 720 || dim[3] != 480 ||
        address < 0x40000000u || address >= 0xc0000000u - dim[0] * 480u * 4u || (address & 3)) return 0;
    const u8 *source = doom_get_framebuffer(4);
    u32 *dst = (u32 *)address;
    for (u32 y = 0; y < 480; ++y) {
        const u8 *row = source + (y * 5u / 12u) * 320u * 4u;
        for (u32 x = 0; x < 720; ++x) {
            u32 color = 0xff000000u;
            if (x >= 40 && x < 680) {
                const u8 *p = row + ((x - 40) / 2u) * 4u;
                color |= (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16);
            }
            dst[y * dim[0] + x] = color;
        }
    }
    watermark(dst, dim[0]);
    return 1;
}
const char *gr3_log(void) { return log_text; }
u32 gr3_status(u32 field) {
    if (field == 0) return ready;
    if (field == 1) return failed;
    if (field == 2) return arena_used;
    if (field == 3) return ticks;
    if (field == 4) return held;
    if (field == 5) return (u32)gamestate;
    if (field == 6) return (u32)menuactive;
    if (field == 7) return players[consoleplayer].mo ? (u32)players[consoleplayer].mo->x : 0;
    if (field == 8) return players[consoleplayer].mo ? (u32)players[consoleplayer].mo->y : 0;
    if (field == 9) return (u32)players[consoleplayer].ammo[am_clip];
    return 0;
}
