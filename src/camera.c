/* SPDX-License-Identifier: GPL-2.0-only */
/* GPL-2.0-only. LAIN launcher for the Ricoh GR IIIx, firmware 1.60.
 * Based on the GR III 2.10 DOOM launcher from ricoh-gr3-doom (see NOTICE.md).
 * Every fixed address below is a GR IIIx 1.60 address; tools/bake.py refuses any other
 * firmware and checks the instruction word at each patched site.
 */
#include "runtime-offsets.h"
typedef unsigned int u32;
typedef unsigned char u8;
extern const u8 doom_image[], doom_wad[], gb_image[], gb_rom[], splash_image[], splash_picture[];
extern const u32 doom_fixups[], gb_fixups[], splash_fixups[];
#define OWNER (*(void *volatile *)0x5580438cu)
#define MAGIC 0x4d4f4f44u
#define CONTROLLER_VT 0x53b2f9e8u
#define ARENA_BYTES (8u * 1024u * 1024u)
#define STACK_BYTES (128u * 1024u)
#define AUDIO_CHUNK 2880u            /* frames per DMA chunk: 60 ms at 48 kHz stereo 16-bit */
#define AUDIO_FIFO 8192u             /* frames */
#define AUDIO_TARGET (2u * AUDIO_CHUNK + 800u)   /* queue level the game is paced to keep */
#define AUDIO_BYTES (AUDIO_FIFO * 4u + 4u * AUDIO_CHUNK * 4u + 64u)
typedef struct {
    u32 vtable, controller, timer, armed;
    u32 vt[4];
    u32 magic, active, busy, owned, runtime, stack, arena;
    /* Input is state-based: the event hook only records which buttons are wanted down
     * (plus a latch so a tap shorter than one tick is still seen); the tick applies the
     * difference to the game. The hook never runs game code, so no event is ever dropped
     * or raced while a tick/blit is busy (that dropped releases -> stuck keys, and
     * dropped presses -> laggy input). */
    u32 want, latch, applied, quit;
    u32 app;            /* index into apps[] */
    /* sound: frames waiting in fifo, four DMA chunks used in rotation */
    u32 fifo, fifo_frames, chunk[4], next_chunk, sounding, stalled;
} Session;
static u32 word(const void *p, u32 off) { return *(const u32 *)((const u8 *)p + off); }
static void put(void *p, u32 off, u32 v) { *(u32 *)((u8 *)p + off) = v; }
static int ptr(u32 p) { return p >= 0x40000000u && p < 0xc0000000u && !(p & 3); }
static void *owner(void) {
    void *c = OWNER;
    return ptr((u32)c) && word(c, 0) == CONTROLLER_VT && word(c, 0x6a0) == MAGIC ? c : 0;
}
static Session *session(void *c) {
    Session *s = (Session *)word(c, 0x6a4);
    return ptr((u32)s) && s->magic == ((u32)s ^ MAGIC) && s->controller == (u32)c ? s : 0;
}
static void *timers(void) {
    void *factory = ((void *(*)(void))0x5320ad88)();
    return ((void *(*)(void *))0x5320b01c)(factory);
}
static int shooting(void) {
    void *factory = ((void *(*)(void))0x5320ad88)();
    void *manager = ((void *(*)(void *))0x5320aefc)(factory);
    u32 id = ((u32 (*)(void *))0x53228c4c)(manager);
    return id >= 0x08000000u && id <= 0x08000002u;
}
static void dirty(void *c) {
    ((u8 *)c)[0x14e] = 1;
    void *root = (void *)word(c, 0x15c);
    if (ptr((u32)root)) ((void (*)(void *))0x533ab98c)(root);
}
/* Call game code on a private stack. Camera task stacks are not sized for DOOM.
 * Six input arguments: the last two are on the original stack at +24/+28. */
__attribute__((naked)) u32 camera_call(u32 top __attribute__((unused)), u32 entry __attribute__((unused)),
    u32 a __attribute__((unused)), u32 b __attribute__((unused)), u32 c __attribute__((unused)), u32 d __attribute__((unused))) {
    __asm__ volatile("push {r4-r6,lr}\nmov r4,sp\nmov r6,r0\nmov r5,r1\nmov r0,r2\nmov r1,r3\n"
                     "ldr r2,[r4,#16]\nldr r3,[r4,#20]\nmov sp,r6\nblx r5\nmov sp,r4\npop {r4-r6,pc}");
}
static u32 run(Session *s, u32 offset, u32 a, u32 b, u32 c, u32 d) {
    return camera_call(s->stack, s->runtime + offset, a, b, c, d);
}

/* ---- SD card access for apps: the camera's own filesystem object (the one its debug
 * shell commands med_fopen/med_fread/med_find use). fs = *(0x535847d8() + 0x14):
 *   vt[0x1c] open(fs, path, 1=read, &handle, &err)      vt[0x20] close(fs, handle, &err)
 *   vt[0x30] size(fs, handle, &size, 0)                 vt[0x24] read(fs, handle, buf, n, &err)
 *   vt[0x58] findfirst(fs, dir, key, 0, entry, &err)    vt[0x5c] findnext(fs, entry, &err)
 *   vt[0x60] findclose(fs, entry, 0)
 * A find entry holds a file-info object at +0x18 (constructor 0x53594990): name (8.3,
 * NUL-terminated) at +4, attribute at +0x11 (0x10 = directory). Hardware-unverified. */
#define ROM_DIR "C:\\LAIN\\GAMEBOY\\ROM"
static void *filesystem(void) {
    void *factory = ((void *(*)(void))0x535847d8)();
    if (!ptr((u32)factory)) return 0;
    void *fs = (void *)word(factory, 0x14);
    return ptr((u32)fs) && ptr(word(fs, 0)) ? fs : 0;
}
static u32 method(void *object, u32 slot) { return word((void *)word(object, 0), slot); }
static int is_rom_name(const char *name) {
    u32 n = 0; while (n < 13 && name[n]) ++n;
    if (n < 4 || n > 12) return 0;
    char a = name[n - 3], b = name[n - 2], c = name[n - 1];
    return a == '.' && (b == 'G' || b == 'g') && (c == 'B' || c == 'b');
}
/* List a directory into out[max][16] records: name (NUL-terminated, <= 12 chars) and the
 * attribute in byte 13. roms_only keeps just the *.GB files. Returns how many. */
static u32 scan(const char *dir, const char *key, char *out, u32 max, int roms_only, u32 *first, u32 *error) {
    void *fs = filesystem(); if (!fs || !out) return 0;
    u32 entry[0x100]; u8 *e = (u8 *)entry;
    for (u32 i = 0; i < sizeof entry; ++i) e[i] = 0;
    ((void (*)(void *))0x53594990)(e + 0x18);
    u32 err = 0, count = 0;
    u32 more = ((u32 (*)(void *, const char *, const char *, u32, void *, u32 *))method(fs, 0x58))(fs, dir, key, 0, e, &err);
    if (first) *first = more;
    if (error) *error = err;
    for (u32 guard = 0; more && guard < 512 && count < max; ++guard) {
        const char *name = (const char *)e + 0x18 + 4;
        u8 attribute = e[0x18 + 0x11];
        if (!roms_only || (attribute != 0x10 && is_rom_name(name))) {
            char *record = out + count++ * 16; u32 i = 0;
            for (; i < 12 && name[i]; ++i) record[i] = name[i];
            for (; i < 16; ++i) record[i] = 0;
            if (!roms_only) record[13] = (char)attribute;
        }
        err = 0;
        more = ((u32 (*)(void *, void *, u32 *))method(fs, 0x5c))(fs, e, &err);
    }
    ((void (*)(void *, void *, u32))method(fs, 0x60))(fs, e, 0);
    return count;
}
/* Fill names[max][16] with the *.GB files in the ROM folder; returns how many. */
static u32 sd_list(char *names, u32 max) {
    u32 count = scan(ROM_DIR, "*.*", names, max, 1, 0, 0);
    if (!count) count = scan(ROM_DIR, "*", names, max, 1, 0, 0);
    return count;
}
/* Diagnostics for an empty list: try four directory/pattern forms and report, for each,
 * findfirst's return, its error code and the entry count (info[0..11]); records[0..5]
 * are the first raw entries of the card root, records[6..11] of the ROM folder. */
static void sd_probe(char *records, u32 *info) {
    char scratch[16 * 6];
    for (u32 i = 0; i < 16 * 12; ++i) records[i] = 0;
    info[2] = scan("C:\\", "*.*", records, 6, 0, &info[0], &info[1]);
    info[5] = scan(ROM_DIR, "*.*", records + 16 * 6, 6, 0, &info[3], &info[4]);
    info[8] = scan(ROM_DIR, "*", scratch, 6, 0, &info[6], &info[7]);
    info[11] = scan(ROM_DIR "\\", "*.*", scratch, 6, 0, &info[9], &info[10]);
    if (!info[5] && info[8]) scan(ROM_DIR, "*", records + 16 * 6, 6, 0, 0, 0);
    if (!info[5] && !info[8] && info[11]) scan(ROM_DIR "\\", "*.*", records + 16 * 6, 6, 0, 0, 0);
}
/* Read <ROM folder>\<name> into dst (at most cap bytes); returns its size, 0 on failure. */
static u32 sd_load(const char *name, u8 *dst, u32 cap) {
    void *fs = filesystem(); if (!fs || !name || !dst) return 0;
    char path[48]; u32 n = 0;
    for (const char *d = ROM_DIR "\\"; *d; ++d) path[n++] = *d;
    for (u32 i = 0; i < 12 && name[i]; ++i) path[n++] = name[i];
    path[n] = 0;
    u32 handle = 0, err = 0, size = 0, done = 0;
    if (!((u32 (*)(void *, const char *, u32, u32 *, u32 *))method(fs, 0x1c))(fs, path, 1, &handle, &err)) return 0;
    ((void (*)(void *, u32, u32 *, u32))method(fs, 0x30))(fs, handle, &size, 0);
    if (size && size <= cap) {
        while (done < size) {
            u32 chunk = size - done; if (chunk > 0x10000u) chunk = 0x10000u;
            if (!((u32 (*)(void *, u32, void *, u32, u32 *))method(fs, 0x24))(fs, handle, dst + done, chunk, &err)) break;
            done += chunk;
        }
    }
    ((void (*)(void *, u32, u32 *))method(fs, 0x20))(fs, handle, &err);
    return done == size ? size : 0;
}
/* What the Game Boy app receives as its data pointer. */
typedef struct {
    u32 (*list)(char *names, u32 max);
    u32 (*load)(const char *name, u8 *dst, u32 cap);
    const u8 *builtin; u32 builtin_bytes;
    void (*probe)(char *records, u32 *info);
} GbHost;
__attribute__((used)) static const GbHost gb_host = { sd_list, sd_load, gb_rom, GB_ROM_BYTES, sd_probe };

/* ---- Apps: each is a relocatable runtime image with the same five entry points. */
typedef struct {
    const u8 *image; const u32 *fixups; u32 image_bytes, fixup_count;
    const void *data; u32 data_bytes, arena_bytes;
    u32 init, tick, key, release, blit, audio, pace;
} App;
#define APP_COUNT 3u
#define APP_SPLASH 2u                /* not in the menu: shown once after power-on */
static const App apps[APP_COUNT] = {
    { doom_image, doom_fixups, DOOM_IMAGE_BYTES, DOOM_FIXUP_COUNT, doom_wad, DOOM_WAD_BYTES, ARENA_BYTES,
      OFFSET_gr3_init, OFFSET_gr3_tick, OFFSET_gr3_key, OFFSET_gr3_release_keys, OFFSET_gr3_blit, OFFSET_gr3_audio, OFFSET_gr3_pace },
    { gb_image, gb_fixups, GB_IMAGE_BYTES, GB_FIXUP_COUNT, &gb_host, sizeof(GbHost), 4u * 1024u * 1024u,
      GBOFF_gr3_init, GBOFF_gr3_tick, GBOFF_gr3_key, GBOFF_gr3_release_keys, GBOFF_gr3_blit, GBOFF_gr3_audio, GBOFF_gr3_pace },
    { splash_image, splash_fixups, SPLASH_IMAGE_BYTES, SPLASH_FIXUP_COUNT, splash_picture, SPLASH_PICTURE_BYTES, 4096u,
      SPOFF_gr3_init, SPOFF_gr3_tick, SPOFF_gr3_key, SPOFF_gr3_release_keys, SPOFF_gr3_blit, 0, 0 },
};

/* ---- Sound: the camera's audio output driver, as its own beep and WAV playback use it
 * (AudioService 0x53566e90 / 0x53567004). driver = *(service + 0xbc), service =
 * 0x53568ec0(0x53568e58()):
 *   0x53714330(driver, stereo)  select output      0x537144d4(driver)  clear "last buffer"
 *   0x537144e0(driver, pcm, bytes, 0)  set the NEXT buffer (slot at driver+0x8c/+0x90)
 *   0x53714d60(driver)  start: plays the slot's buffer and empties the slot
 *   0x5371451c(driver) + 0x5371457c(driver)  stop
 * While a buffer plays the slot takes the following one (double buffering), so the tick
 * refills the slot whenever it is empty. PCM is 48 kHz stereo signed 16-bit (the 1 s
 * self-timer beep is 0x2ee18 bytes). Hardware-unverified. */
static void *audio_driver(void) {
    void *factory = ((void *(*)(void))0x53568e58)();
    if (!ptr((u32)factory)) return 0;
    void *service = ((void *(*)(void *))0x53568ec0)(factory);
    if (!ptr((u32)service)) return 0;
    void *driver = (void *)word(service, 0xbc);
    return ptr((u32)driver) ? driver : 0;
}
static void audio_stop(Session *s) {
    if (!s->sounding) return;
    s->sounding = 0; s->stalled = 0;
    void *driver = audio_driver(); if (!driver) return;
    ((void (*)(void *))0x5371451c)(driver);
    ((void (*)(void *))0x5371457c)(driver);
}
/* Move one chunk from the fifo into the next DMA buffer (silence-padded) and return it. */
static u32 audio_chunk(Session *s) {
    short *out = (short *)s->chunk[s->next_chunk]; s->next_chunk = (s->next_chunk + 1u) & 3u;
    short *fifo = (short *)s->fifo;
    u32 take = s->fifo_frames < AUDIO_CHUNK ? s->fifo_frames : AUDIO_CHUNK;
    for (u32 i = 0; i < take * 2; ++i) out[i] = fifo[i];
    for (u32 i = take * 2; i < AUDIO_CHUNK * 2; ++i) out[i] = 0;
    for (u32 i = take * 2; i < s->fifo_frames * 2; ++i) fifo[i - take * 2] = fifo[i];
    s->fifo_frames -= take;
    /* the DMA reads memory, not the CPU cache: write the chunk back */
    for (u32 at = (u32)out & ~63u; at < (u32)out + AUDIO_CHUNK * 4u; at += 32u)
        __asm__ volatile("mcr p15,0,%0,c7,c10,1" :: "r"(at) : "memory");
    __asm__ volatile("dsb" ::: "memory");
    return (u32)out;
}
static void audio_pump(Session *s) {
    const App *app = &apps[s->app];
    if (!app->audio || !s->fifo) return;
    s->fifo_frames += run(s, app->audio, s->fifo + s->fifo_frames * 4u, AUDIO_FIFO - s->fifo_frames, 0, 0);
    if (s->fifo_frames > AUDIO_FIFO) s->fifo_frames = AUDIO_FIFO;
    void *driver = audio_driver(); if (!driver) return;
    if (!s->sounding) {
        if (s->fifo_frames < 2u * AUDIO_CHUNK) return;
        ((void (*)(void *, u32))0x53714330)(driver, 1);
        ((void (*)(void *))0x537144d4)(driver);
        ((void (*)(void *, u32, u32, u32))0x537144e0)(driver, audio_chunk(s), AUDIO_CHUNK * 4u, 0);
        ((void (*)(void *))0x53714d60)(driver);
        ((void (*)(void *, u32, u32, u32))0x537144e0)(driver, audio_chunk(s), AUDIO_CHUNK * 4u, 0);
        s->sounding = 1; s->stalled = 0;
        return;
    }
    if (word(driver, 0x8c) == 0) {          /* the queued chunk started playing: queue the next */
        ((void (*)(void *, u32, u32, u32))0x537144e0)(driver, audio_chunk(s), AUDIO_CHUNK * 4u, 0);
        s->stalled = 0;
    } else if (++s->stalled > 12u) audio_stop(s);   /* output ran dry and halted: restart later */
}
static void cancel(Session *s) {
    if (s->armed) ((void (*)(void *, u32))0x5322b1ec)(timers(), s->timer);
    s->armed = 0;
}
/* MENU / mode change: fully EXIT the game (free the session) so the next launch is a
 * fresh game, not a resumed one. Guarded so it is safe to call more than once. */
static void close_game(void *c, Session *s) {
    if (!s || s->magic != ((u32)s ^ MAGIC)) return;
    s->active = 0; cancel(s); audio_stop(s);
    run(s, apps[s->app].release, 0, 0, 0, 0);
    dirty(c);
    put(c, 0x6a4, 0); s->magic = 0;
    ((void (*)(void *))0x536af358)(s);
}
static void arm(Session *s) {
    s->armed = 1;
    /* 29 is a nominal 35 Hz interval if the service uses milliseconds. Actual
     * cadence and callback task context are expressly hardware-unverified. */
    s->timer = ((u32 (*)(void *, u32, void *))0x5322b468)(timers(), 29, s);
}
void camera_tick(Session *s) {
    if (!ptr((u32)s) || s->magic != ((u32)s ^ MAGIC)) return;
    s->armed = 0;
    void *c = owner();
    if (!c || s->controller != (u32)c || !s->active) return;
    if (s->busy) { arm(s); return; }
    if (!shooting()) { close_game(c, s); return; }
    s->busy = 1;
    /* apply button state BEFORE the tic so a press acts in this frame */
    u32 latch = s->latch; s->latch = 0;
    u32 target = s->want | latch, diff = target ^ s->applied;
    for (u32 i = 0; i < 32; i++) if (diff & (1u << i)) run(s, apps[s->app].key, i, (target >> i) & 1u, 0, 0);
    s->applied = target;
    /* with sound available the speaker paces the game: ask for what the queue is short of */
    if (apps[s->app].audio && s->fifo && audio_driver())
        run(s, apps[s->app].pace, s->fifo_frames < AUDIO_TARGET ? AUDIO_TARGET - s->fifo_frames : 0, 0, 0, 0);
    u32 success = run(s, apps[s->app].tick, 0, 0, 0, 0);
    if (success) audio_pump(s);
    s->busy = 0;
    if (!success || s->quit) { close_game(c, s); return; }
    dirty(c); arm(s);
}
static void sync_code(u32 address, u32 size) {
    /* ARMv7 clean-to-PoU + I-cache invalidate + barriers. These instructions
     * require a privileged RTOS task and executable heap mappings. */
    u32 ctr, zero = 0;
    __asm__ volatile("mrc p15,0,%0,c0,c0,1" : "=r"(ctr));
    u32 line = 4u << ((ctr >> 16) & 15u);
    for (u32 at = address & ~(line - 1u); at < address + size; at += line)
        __asm__ volatile("mcr p15,0,%0,c7,c11,1" :: "r"(at) : "memory");
    __asm__ volatile("dsb\nmcr p15,0,%0,c7,c5,0\ndsb\nisb" :: "r"(zero) : "memory");
}
static Session *create_session(void *c, u32 index) {
    if (index >= APP_COUNT) return 0;
    const App *app = &apps[index];
    u32 bytes = sizeof(Session) + 32u + app->image_bytes + app->arena_bytes + STACK_BYTES + AUDIO_BYTES;
    Session *s = ((Session *(*)(u32))0x536af31c)(bytes);
    if (!ptr((u32)s)) return 0;
    u8 *raw = (u8 *)s;
    for (u32 i = 0; i < sizeof(Session); ++i) raw[i] = 0;
    s->vtable = (u32)&s->vt[0]; s->controller = (u32)c;
    for (u32 i = 0; i < 4; ++i) s->vt[i] = (u32)&camera_tick;
    s->magic = (u32)s ^ MAGIC; s->app = index;
    s->runtime = ((u32)s + sizeof(Session) + 15u) & ~15u;
    s->arena = s->runtime + app->image_bytes;
    s->stack = s->arena + app->arena_bytes + STACK_BYTES;
    s->fifo = (s->stack + 63u) & ~63u;
    for (u32 i = 0; i < 4; ++i) s->chunk[i] = s->fifo + AUDIO_FIFO * 4u + i * AUDIO_CHUNK * 4u;
    for (u32 i = 0; i < app->image_bytes; ++i) ((u8 *)s->runtime)[i] = app->image[i];
    for (u32 i = 0; i < app->fixup_count; ++i) {
        u32 *p = (u32 *)(s->runtime + app->fixups[i]); *p += s->runtime;
    }
    sync_code(s->runtime, app->image_bytes);
    if (!run(s, app->init, s->arena, app->arena_bytes, (u32)app->data, app->data_bytes)) {
        ((void (*)(void *))0x536af358)(s); return 0;
    }
    put(c, 0x6a4, (u32)s);
    return s;
}
u32 camera_try_open(void) {
    void *c = owner();
    if (!c || !shooting()) return 0;
    Session *s = session(c);
    if (!s) s = create_session(c, 0);
    if (!s || s->busy) return 0;
    if (s->active) { close_game(c, s); return 1; }
    s->owned |= 1u << 27;  /* launch Fn press belongs to us through release */
    s->active = 1; dirty(c); arm(s); return 1;
}
u32 camera_construct(void *c) {
    u32 result = ((u32 (*)(void *))0x53173204)(c);
    for (u32 off = 0x69c; off < 0x6c0; off += 4) put(c, off, 0);
    put(c, 0x6a0, MAGIC); OWNER = c;
    return result;
}
void camera_cleanup(void *c) {
    if (OWNER != c) return;
    Session *s = session(c);
    if (s) {
        cancel(s); audio_stop(s); s->active = 0; s->magic = 0;
        ((void (*)(void *))0x536af358)(s);
    }
    put(c, 0x6a4, 0); OWNER = 0;
}
__attribute__((naked)) void camera_destroy(void *c __attribute__((unused))) {
    __asm__ volatile("push {r0-r3,r4,lr}\nbl camera_cleanup\npop {r0-r3,r4,lr}\nmov ip,sp\nldr pc,1f\n1: .word 0x53170b54");
}
/* Menu launch: a LAIN menu line sets this word to MAGIC + app index (see launch()). It
 * is consumed on the next live-view draw, so the app starts the moment the camera is
 * back on the shooting screen. The word lives in RAM reserved for this mod
 * (0x55804374..0x558043c0, cleared by the extended start-up fill); the magic means
 * stray values never launch anything. */
#define DOOM_PENDING (*(volatile u32 *)0x55804390u)
#define BOOTED (*(volatile u32 *)0x55804394u)

/* The LAIN menu. tools/bake.py turns one Setup item into a submenu opener (id
 * 0x020001a3) and adds a submenu record whose lines carry our own controller ids:
 *  - camera_lain_draw draws the Setup line's title (text 786, "LAIN"), the same shape
 *    as the stock Setup line draws (0x5339de20 then 0x5339dc70(line, text, text)).
 *  - camera_factory (hooked at the controller factory 0x5316f3b8) answers our ids:
 *    the submenu id is handed to the stock factory as D-Range's submenu controller;
 *    a line id returns a tiny object whose vtable[0x24] is that line's OK handler.
 *  - camera_sub_setcurrent picks our submenu record; camera_keys lends it D-Range's
 *    key map. */
#define LAIN_ID 0x020001a0u

__attribute__((naked)) void camera_lain_draw(void) {
    __asm__ volatile(
        "mov ip,sp\npush {r3,r4,fp,ip,lr,pc}\nmov r4,r1\nmov r0,r4\nsub fp,ip,#4\nmov r1,#0\n"
        "movw r3,#0xde20\nmovt r3,#0x5339\nblx r3\n"            /* bl 0x5339de20 */
        "movw r1,#786\nmov r0,r4\nmov r2,r1\nsub sp,fp,#0x14\nldm sp,{r3,r4,fp,sp,lr}\n"
        "movw r3,#0xdc70\nmovt r3,#0x5339\nbx r3");             /* b  0x5339dc70 */
}

/* OK on an app line: flag which app to launch (MAGIC + app index), then leave the menus
 * for the shooting screen by posting the UI message a shutter press posts from any menu
 * (0x86, via the UI queue 0x532288b0 -- the same queue the OK dispatcher itself posts
 * to). The app then starts on the first live-view draw. */
static u32 launch(u32 app) {
    DOOM_PENDING = MAGIC + app;
    void *factory = ((void *(*)(void))0x5320ad88)();
    void *manager = ((void *(*)(void *))0x5320aefc)(factory);
    ((void (*)(void *, u32, u32, u32))0x532288b0)(manager, 0x86, 0, 0);
    return 0;
}
static u32 lain_ok(void *ctrl, u32 id) { (void)ctrl; (void)id; return launch(0); }
static u32 lain_ok_gb(void *ctrl, u32 id) { (void)ctrl; (void)id; return launch(1); }
__attribute__((used)) static void * const lain_vtable[16] = { [9] = (void *)lain_ok };
__attribute__((used)) static void * const lain_object = (void *)lain_vtable;
__attribute__((used)) static void * const lain_vtable_gb[16] = { [9] = (void *)lain_ok_gb };
__attribute__((used)) static void * const lain_object_gb = (void *)lain_vtable_gb;

/* camera_factory (hooked at the controller factory 0x5316f3b8, displaced `movw r3,#0x13b`):
 *   0x020001a0 / 0x020001a1 (the DOOM / GAME BOY lines) -> that line's object.
 *   0x020001a3 (the LAIN submenu) -> continue into the stock factory asking for D-Range
 *     (0x020000ba), i.e. the camera's cached SubMenuController. Never build one here:
 *     the factory runs again on every event and redraw.
 *   anything else -> re-run the displaced movw and continue at 0x5316f3bc. */
__attribute__((naked)) void camera_factory(void) {
    __asm__ volatile(
        "movw r3,#0x01a0\nmovt r3,#0x0200\ncmp r1,r3\nbeq 2f\n"             /* LAIN_ID (DOOM line) ? */
        "movw r3,#0x01a1\nmovt r3,#0x0200\ncmp r1,r3\nbeq 4f\n"             /* GAME BOY line ? */
        "movw r3,#0x01a3\nmovt r3,#0x0200\ncmp r1,r3\nbeq 3f\n"             /* LAIN_SUBMENU_ID ? */
        "movw r3,#0x013b\nmovw r12,#0xf3bc\nmovt r12,#0x5316\nbx r12\n"     /* else continue factory */
        "2: movw r0,#:lower16:lain_object\nmovt r0,#:upper16:lain_object\nbx lr\n"
        "4: movw r0,#:lower16:lain_object_gb\nmovt r0,#:upper16:lain_object_gb\nbx lr\n"
        /* GRMod's method: ask the stock factory for D-Range's (cached) SubMenuController.
         * The factory is re-run for the current id on every event/draw (0x53228a90), so
         * building a fresh controller here leaked 0x180 per call and lost the record. */
        "3: movw r1,#0x00ba\nmovt r1,#0x0200\n"
        "movw r3,#0x013b\nmovw r12,#0xf3bc\nmovt r12,#0x5316\nbx r12");
}

/* SetCurrentMenu hook (0x531c5950, displaced `add r3,r1,#0xfe000000`): map the LAIN
 * submenu id (0x020001a3) to its appended SubMenuController record (index 17), GRMod's
 * subCurrent pattern (set the record byte at controller+0x174, return); else re-run the
 * displaced instruction and continue the original dispatch at +4. */
__attribute__((naked)) void camera_sub_setcurrent(void) {
    __asm__ volatile(
        "movw r3,#0x01a3\nmovt r3,#0x0200\ncmp r1,r3\nbne 1f\n"   /* id == LAIN_SUBMENU_ID ? */
        "mov r3,#17\nstrb r3,[r0,#0x174]\nbx lr\n"                /* record 17, return */
        "1: add r3,r1,#0xfe000000\n"                             /* re-run displaced */
        "movw r12,#0x5954\nmovt r12,#0x531c\nbx r12");           /* continue at 0x531c5954 */
}

/* Key-map id hook (the `bl 0x53228c4c` at 0x53221a4c, current-id getter `ldr r0,[r0]`):
 * the LAIN submenu uses D-Range's key map (GRMod's `keys` pattern). */
__attribute__((naked)) void camera_keys(void) {
    __asm__ volatile(
        "ldr r0,[r0]\nmovw r12,#0x01a3\nmovt r12,#0x0200\ncmp r0,r12\n"
        "movweq r0,#0x00ba\nmovteq r0,#0x0200\nbx lr");
}

u32 camera_draw(void *root) {
    u32 result = ((u32 (*)(void *))0x53391b14)(root);
    void *c = owner();
    if (!c || word(c, 0x15c) != (u32)root) return result;
    Session *s = session(c);
    /* Boot splash: BOOTED is in the RAM words the start-up fill clears, so this runs once
     * per power-on, on the first live-view draw of the shooting screen. */
    if (BOOTED != MAGIC && shooting()) {
        BOOTED = MAGIC;
        if (DOOM_PENDING - MAGIC >= APP_COUNT && !s) DOOM_PENDING = MAGIC + APP_SPLASH;
    }
    u32 pending = DOOM_PENDING - MAGIC;
    if (pending < APP_COUNT) {              /* a LAIN menu line asked to launch an app */
        DOOM_PENDING = 0;
        if (!s) s = create_session(c, pending);
        if (s && !s->busy && !s->active && shooting()) { s->active = 1; dirty(c); arm(s); }
    }
    if (!s || !s->active || s->busy) return result;
    s->busy = 1;
    run(s, apps[s->app].blit, word(root, 0xec), 0, 0, 0);
    s->busy = 0; return result;
}
__attribute__((naked)) void camera_open(void) {
    __asm__ volatile("push {r0-r3,r4,lr}\nbl camera_try_open\ncmp r0,#0\npop {r0-r3,r4,lr}\n"
                     "bxne lr\nmov ip,sp\nldr pc,1f\n1: .word 0x53227118");
}
u32 camera_filter(void *message) {
    void *c = owner(); if (!c || !ptr((u32)message)) return 0;
    Session *s = session(c); if (!s) return 0;
    u32 id = *(const unsigned short *)message;
    void *payload = (void *)word(message, 4);
    if (!ptr((u32)payload)) return 0;
    /* Payload byte: 2 = button pushed, 1 = button let go. (The stock handler 0x532231e4
     * acts on 2 for its act-once buttons -- OK, MENU, DISP -- and numbers each pair's
     * actions push < let-go, e.g. up: 2 -> 0x1a, 1 -> 0x1c. Reading them the other way
     * round made every key latch on release and clear on the next push.) */
    u32 phase = *(const u8 *)payload;
    enum { LET_GO = 1, PUSHED = 2 };
    u32 mask = id < 32 ? 1u << id : 0;
    if (phase == LET_GO && (s->owned & mask)) {
        s->owned &= ~mask; s->want &= ~mask;
        return 1;
    }
    if (!s->active) return (s->owned & mask) != 0;  /* suppress held repeats until release */
    if (id == 1 || id == 2 || id == 3 || id == 5 || id == 20 || id == 23 ||
        id == 24 || id == 26 || id == 32 || id == 38) {
        if (s->busy) s->quit = 1;       /* a tick/blit is running: let the tick close */
        else close_game(c, s);
        return 0;
    }
    if (id != 4 && id != 8 && id != 9 && id != 10 && id != 11 && id != 12 && id != 27 && id != 30) return 0;
    if (phase == PUSHED) {
        s->owned |= mask; s->want |= mask; s->latch |= mask;
    }
    return 1;
}
__attribute__((naked)) u32 camera_event(void *action __attribute__((unused)), void *message __attribute__((unused))) {
    __asm__ volatile("push {r0-r3,r4,lr}\nmov r0,r1\nbl camera_filter\ncmp r0,#0\npop {r0-r3,r4,lr}\n"
                     "movne r0,#1\nbxne lr\nldrh r3,[r1]\nldr pc,1f\n1: .word 0x532231e8");
}
