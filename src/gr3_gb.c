/* SPDX-License-Identifier: GPL-2.0-only */
/* Freestanding Game Boy (DMG) app for the camera launcher, built on Peanut-GB
 * (MIT, Mahyar Koshkouei; see peanut_gb.h). Same runtime ABI as gr3_doom.c:
 * gr3_init / gr3_tick / gr3_key / gr3_release_keys / gr3_blit. No operating-system
 * calls and no fixed camera addresses of its own: the launcher passes a small host table
 * (list ROMs / load a ROM from the SD card's ROM folder, plus a built-in test ROM).
 * A picker screen lists the ROMs; cartridge RAM lives in this image and is lost on exit. */
#define ENABLE_SOUND 1
#define ENABLE_LCD 1
#define AUDIO_SAMPLE_RATE 48000
#define MINIGB_APU_AUDIO_FORMAT_S16SYS
#include "minigb_apu.h"
static struct minigb_apu_ctx apu;
static uint8_t audio_read(uint16_t address) { return minigb_apu_audio_read(&apu, address); }
static void audio_write(uint16_t address, uint8_t value) { minigb_apu_audio_write(&apu, address, value); }
#include "peanut_gb.h"
#include "minigb_apu.c"

typedef unsigned int u32;
typedef unsigned char u8;
typedef unsigned short u16;

void *memset(void *dst, int value, size_t count) {
    u8 *p = dst; while (count--) *p++ = (u8)value; return dst;
}
void *memcpy(void *dst, const void *src, size_t count) {
    u8 *d = dst; const u8 *s = src; while (count--) *d++ = *s++; return dst;
}

static struct gb_s gb;
static u8 cart_ram[0x20000];
static u8 frame[LCD_HEIGHT * LCD_WIDTH];
typedef struct {
    u32 (*list)(char *names, u32 max);
    u32 (*load)(const char *name, u8 *dst, u32 cap);
    const u8 *builtin; u32 builtin_bytes;
    void (*probe)(char *records, u32 *info);
} GbHost;
#define MAX_ROMS 20u
static const GbHost *host;
static u8 *arena;
static u32 arena_size;
static char names[MAX_ROMS][16];
static char probe_records[12][16];
static u32 probe_info[12];
static u32 rom_count, cursor, playing, message;   /* message: 0 none, 1 load failed, 2 not a DMG ROM */
static const u8 *rom;
static u32 rom_size, ready, failed, held, pad, phase, frames, draw;
static u32 escape_context[26];
/* Sound: one APU buffer per emulated frame (48 kHz stereo), queued for the launcher. */
#define SOUND_QUEUE 4096u
static short sound[SOUND_QUEUE * 2];
static u32 sound_frames, paced, pace_frames, behind;

__attribute__((naked, returns_twice)) int gr3_checkpoint(u32 *p __attribute__((unused))) {
    __asm__ volatile("stmia r0, {r4-r11,sp,lr}\nadd r1,r0,#40\nvstmia r1,{d8-d15}\nmov r0,#0\nbx lr");
}
__attribute__((naked, noreturn)) void gr3_escape(u32 *p __attribute__((unused))) {
    __asm__ volatile("add r1,r0,#40\nvldmia r1,{d8-d15}\nldmia r0, {r4-r11,sp,lr}\nmov r0,#1\nbx lr");
}
static void halt(u32 code) { failed = code ? code : 1; ready = 0; gr3_escape(escape_context); }
void abort(void) { halt(900); for (;;) {} }

static uint8_t rom_read(struct gb_s *g, const uint_fast32_t address) {
    (void)g; return address < rom_size ? rom[address] : 0xff;
}
static uint8_t ram_read(struct gb_s *g, const uint_fast32_t address) {
    (void)g; return address < sizeof(cart_ram) ? cart_ram[address] : 0xff;
}
static void ram_write(struct gb_s *g, const uint_fast32_t address, const uint8_t value) {
    (void)g; if (address < sizeof(cart_ram)) cart_ram[address] = value;
}
/* Peanut-GB treats the error callback as non-returning: leave through the checkpoint. */
static void error(struct gb_s *g, const enum gb_error_e code, const uint16_t address) {
    (void)g; (void)address; halt(100u + (u32)code);
}
static void draw_line(struct gb_s *g, const uint8_t *pixels, const uint_fast8_t line) {
    (void)g;
    if (!draw || line >= LCD_HEIGHT) return;
    u8 *row = frame + (u32)line * LCD_WIDTH;
    for (u32 x = 0; x < LCD_WIDTH; ++x) row[x] = pixels[x] & 3u;
}

int gr3_init(void *memory, u32 bytes, const void *data, u32 length) {
    if (!memory || !data || length != sizeof(GbHost)) return 0;
    host = data; arena = memory; arena_size = bytes;
    ready = failed = held = phase = frames = draw = playing = cursor = message = 0; pad = 0xffu;
    rom_count = host->list ? host->list(&names[0][0], MAX_ROMS) : 0;
    if (rom_count > MAX_ROMS) rom_count = MAX_ROMS;
    if (!rom_count && host->probe) host->probe(&probe_records[0][0], probe_info);
    ready = 1; return 1;
}

/* Start the entry under the cursor: an SD ROM, or (last entry) the built-in test ROM. */
static void start(void) {
    const u8 *image; u32 size;
    if (cursor < rom_count) {
        size = host->load ? host->load(names[cursor], arena, arena_size) : 0;
        image = arena;
    } else { image = host->builtin; size = host->builtin_bytes; }
    if (!image || size < 0x150u) { message = 1; return; }
    u8 check = 0;
    for (u32 i = 0x134; i < 0x14d; ++i) check = (u8)(check - image[i] - 1u);
    if (check != image[0x14d] || image[0x143] == 0xc0u) { message = 2; return; }
    rom = image; rom_size = size;
    if (gr3_checkpoint(escape_context)) { failed = 0; ready = 1; message = 1; return; }
    if (gb_init(&gb, rom_read, ram_read, ram_write, error, 0) != GB_INIT_NO_ERROR) { message = 2; return; }
    gb_init_lcd(&gb, draw_line);
    minigb_apu_audio_init(&apu); sound_frames = 0;
    pad = 0xffu; phase = 0; playing = 1;
}

/* One camera timer callback is nominally 1/35 s; the Game Boy runs 59.73 frames/s.
 * Run 1 or 2 emulated frames per callback (phase accumulator) and draw only the last. */
int gr3_tick(void) {
    if (!ready || failed) return 0;
    if (!playing) return 1;
    if (gr3_checkpoint(escape_context)) return 0;
    u32 count = 0;
    if (paced) {
        /* The speaker is the clock: it plays exactly 48000 frames a second, and each
         * emulated frame makes AUDIO_SAMPLES of them. Run as many frames as the launcher's
         * sound queue is short of, so game speed is exact whatever the timer does. */
        count = (pace_frames + AUDIO_SAMPLES / 2u) / AUDIO_SAMPLES;
        if (count > 4u) { count = 4u; behind = 70; }   /* cannot keep up: flag it on screen */
        else if (behind) --behind;
        paced = 0;
    } else {
        phase += 5973u;
        while (phase >= 3500u) { phase -= 3500u; ++count; }
        if (count > 3u) count = 3u;
    }
    for (u32 i = 0; i < count; ++i) {
        draw = i + 1u == count;
        gb.direct.joypad = (u8)pad;
        gb_run_frame(&gb);
        ++frames;
        if (sound_frames + AUDIO_SAMPLES > SOUND_QUEUE) sound_frames = 0;   /* nobody listening: drop */
        minigb_apu_audio_callback(&apu, sound + sound_frames * 2);
        sound_frames += AUDIO_SAMPLES;
    }
    return 1;
}

/* Native switch ids -> Game Boy pad. Joypad bits are active-low. */
int gr3_key(u32 id, u32 down) {
    if (!ready || down > 1u || id >= 32u) return 0;
    if (!playing) {                      /* ROM picker: up/down move, OK starts */
        if (!down) return 1;
        message = 0;
        if (id == 9) cursor = cursor ? cursor - 1u : rom_count;
        else if (id == 10) cursor = cursor < rom_count ? cursor + 1u : 0;
        else if (id == 8) start();
        return 1;
    }
    u32 bit;
    switch (id) {
        case 9: bit = JOYPAD_UP; break;
        case 10: bit = JOYPAD_DOWN; break;
        case 11: bit = JOYPAD_LEFT; break;
        case 12: bit = JOYPAD_RIGHT; break;
        case 8: bit = JOYPAD_A; break;         /* OK */
        case 30: bit = JOYPAD_B; break;        /* ADJ lever press */
        case 27: bit = JOYPAD_START; break;    /* Fn */
        case 4: bit = JOYPAD_SELECT; break;
        default: return 0;
    }
    if (down) { held |= 1u << id; pad &= ~bit; }
    else { held &= ~(1u << id); pad |= bit; }
    return 1;
}
void gr3_release_keys(void) { held = 0; pad = 0xffu; }

/* Called before a tick when sound is running: how many frames the sound queue wants. */
void gr3_pace(u32 frames) { pace_frames = frames; paced = 1; }
/* Hand the launcher up to max_frames of 48 kHz stereo sound; returns how many. */
u32 gr3_audio(short *dst, u32 max_frames) {
    if (!ready || !playing || !dst) return 0;
    u32 count = sound_frames < max_frames ? sound_frames : max_frames;
    for (u32 i = 0; i < count * 2; ++i) dst[i] = sound[i];
    for (u32 i = count * 2; i < sound_frames * 2; ++i) sound[i - count * 2] = sound[i];
    sound_frames -= count;
    return count;
}

/* 5x7 font for the picker (rows top to bottom, bit 4 = left pixel). */
static const u8 *glyph(char c) {
    static const u8 letters[26][7] = {
        {0x0e,0x11,0x11,0x1f,0x11,0x11,0x11}, {0x1e,0x11,0x11,0x1e,0x11,0x11,0x1e}, {0x0e,0x11,0x10,0x10,0x10,0x11,0x0e},
        {0x1e,0x11,0x11,0x11,0x11,0x11,0x1e}, {0x1f,0x10,0x10,0x1e,0x10,0x10,0x1f}, {0x1f,0x10,0x10,0x1e,0x10,0x10,0x10},
        {0x0e,0x11,0x10,0x17,0x11,0x11,0x0f}, {0x11,0x11,0x11,0x1f,0x11,0x11,0x11}, {0x0e,0x04,0x04,0x04,0x04,0x04,0x0e},
        {0x07,0x02,0x02,0x02,0x02,0x12,0x0c}, {0x11,0x12,0x14,0x18,0x14,0x12,0x11}, {0x10,0x10,0x10,0x10,0x10,0x10,0x1f},
        {0x11,0x1b,0x15,0x15,0x11,0x11,0x11}, {0x11,0x19,0x15,0x13,0x11,0x11,0x11}, {0x0e,0x11,0x11,0x11,0x11,0x11,0x0e},
        {0x1e,0x11,0x11,0x1e,0x10,0x10,0x10}, {0x0e,0x11,0x11,0x11,0x15,0x12,0x0d}, {0x1e,0x11,0x11,0x1e,0x14,0x12,0x11},
        {0x0f,0x10,0x10,0x0e,0x01,0x01,0x1e}, {0x1f,0x04,0x04,0x04,0x04,0x04,0x04}, {0x11,0x11,0x11,0x11,0x11,0x11,0x0e},
        {0x11,0x11,0x11,0x11,0x11,0x0a,0x04}, {0x11,0x11,0x11,0x15,0x15,0x15,0x0a}, {0x11,0x11,0x0a,0x04,0x0a,0x11,0x11},
        {0x11,0x11,0x0a,0x04,0x04,0x04,0x04}, {0x1f,0x01,0x02,0x04,0x08,0x10,0x1f} };
    static const u8 digits[10][7] = {
        {0x0e,0x11,0x13,0x15,0x19,0x11,0x0e}, {0x04,0x0c,0x04,0x04,0x04,0x04,0x0e}, {0x0e,0x11,0x01,0x02,0x04,0x08,0x1f},
        {0x1f,0x02,0x04,0x02,0x01,0x11,0x0e}, {0x02,0x06,0x0a,0x12,0x1f,0x02,0x02}, {0x1f,0x10,0x1e,0x01,0x01,0x11,0x0e},
        {0x06,0x08,0x10,0x1e,0x11,0x11,0x0e}, {0x1f,0x01,0x02,0x04,0x08,0x08,0x08}, {0x0e,0x11,0x11,0x0e,0x11,0x11,0x0e},
        {0x0e,0x11,0x11,0x0f,0x01,0x02,0x0c} };
    static const u8 dot[7] = {0,0,0,0,0,0x0c,0x0c}, dash[7] = {0,0,0,0x1f,0,0,0}, under[7] = {0,0,0,0,0,0,0x1f},
        arrow[7] = {0x08,0x04,0x02,0x01,0x02,0x04,0x08}, colon[7] = {0,0x0c,0x0c,0,0x0c,0x0c,0},
        slash[7] = {0,0x10,0x08,0x04,0x02,0x01,0}, tilde[7] = {0,0,0x08,0x15,0x02,0,0}, box[7] = {0x1f,0x11,0x11,0x11,0x11,0x11,0x1f},
        blank[7] = {0,0,0,0,0,0,0};
    if (c >= 'a' && c <= 'z') c = (char)(c - 'a' + 'A');
    if (c >= 'A' && c <= 'Z') return letters[c - 'A'];
    if (c >= '0' && c <= '9') return digits[c - '0'];
    switch (c) {
        case ' ': return blank; case '.': return dot; case '-': return dash; case '_': return under;
        case '>': return arrow; case ':': return colon; case '\\': return slash; case '~': return tilde;
        default: return box;
    }
}
static void text(u32 *dst, u32 pitch, u32 x, u32 y, const char *string, u32 colour) {
    for (; *string && x + 15u <= 720u; ++string, x += 18u) {
        const u8 *g = glyph(*string);
        for (u32 row = 0; row < 7; ++row)
            for (u32 col = 0; col < 5; ++col) if (g[row] & (0x10u >> col))
                for (u32 dy = 0; dy < 3; ++dy)
                    for (u32 dx = 0; dx < 3; ++dx) dst[(y + row * 3u + dy) * pitch + x + col * 3u + dx] = colour;
    }
}
static void picker(u32 *dst, u32 pitch) {
    for (u32 y = 0; y < 480; ++y) for (u32 x = 0; x < 720; ++x) dst[y * pitch + x] = 0xff201808u;
    text(dst, pitch, 36, 24, "GAME BOY", 0xffd0f8e0u);
    text(dst, pitch, 36, 54, rom_count ? "LAIN\\GAMEBOY\\ROM   OK: START" : "NO .GB FILES IN LAIN\\GAMEBOY\\ROM", 0xff70c088u);
    u32 total = rom_count + 1u;
    u32 first = cursor >= 14u ? cursor - 13u : 0;
    for (u32 line = 0; line < 14u && first + line < total; ++line) {
        u32 index = first + line, y = 96u + line * 26u;
        u32 colour = index == cursor ? 0xffd0f8e0u : 0xff70c088u;
        if (index == cursor) text(dst, pitch, 36, y, ">", colour);
        text(dst, pitch, 72, y, index < rom_count ? names[index] : "TEST PATTERN", colour);
    }
    if (!rom_count) {                    /* why is the list empty? show what the card answered */
        static const char *const forms[4] = {"C:\\ *.*", "ROM *.*", "ROM *", "ROM\\ *.*"};
        for (u32 i = 0; i < 4; ++i) {
            char line[40]; u32 n = 0;
            for (const char *f = forms[i]; *f; ++f) line[n++] = *f;
            while (n < 10) line[n++] = ' ';
            static const char tags[3] = {'R', 'E', 'N'};
            for (u32 k = 0; k < 3; ++k) {
                u32 v = probe_info[i * 3 + k];
                line[n++] = tags[k];
                for (int shift = 28; shift >= 0; shift -= 4) {
                    u32 digit = (v >> shift) & 15u;
                    if (shift > 4 && !(v >> shift)) continue;
                    line[n++] = (char)(digit < 10 ? '0' + digit : 'A' + digit - 10);
                }
                line[n++] = ' ';
            }
            line[n] = 0;
            text(dst, pitch, 36, 150u + i * 26u, line, 0xff70c088u);
        }
        text(dst, pitch, 36, 262, "CARD ROOT", 0xffd0f8e0u); text(dst, pitch, 380, 262, "ROM FOLDER", 0xffd0f8e0u);
        for (u32 i = 0; i < 12; ++i) {
            const char *record = probe_records[i];
            if (!record[0] && !record[13]) continue;
            char line[20]; u32 n = 0;
            for (; n < 12 && record[n]; ++n) line[n] = record[n];
            if (!n) line[n++] = '?';
            line[n++] = ' ';
            u32 a = (u8)record[13];
            line[n++] = (char)((a >> 4) < 10 ? '0' + (a >> 4) : 'A' + (a >> 4) - 10);
            line[n++] = (char)((a & 15u) < 10 ? '0' + (a & 15u) : 'A' + (a & 15u) - 10);
            line[n] = 0;
            text(dst, pitch, i < 6 ? 36u : 380u, 288u + (i % 6u) * 26u, line, 0xff70c088u);
        }
    }
    if (message) text(dst, pitch, 36, 456 - 9, message == 1 ? "LOAD FAILED" : "NOT A GAME BOY ROM", 0xff6060ffu);
}

/* Native ScreenBuffer format 5 (R,G,B,A bytes), 720x480. The 160x144 picture is
 * scaled 3x to 480x432 and centred; the border is black. Row padding is left alone. */
int gr3_blit(void *descriptor) {
    if (!ready || !descriptor) return 0;
    const u8 *b = descriptor;
    const u16 *dim = (const u16 *)(b + 0x18);
    u32 address = *(const u32 *)(b + 8);
    if (b[2] != 5 || (dim[0] != 720 && dim[0] != 736) || dim[1] != 480 || dim[2] != 720 || dim[3] != 480 ||
        address < 0x40000000u || address >= 0xc0000000u - dim[0] * 480u * 4u || (address & 3)) return 0;
    static const u32 shade[4] = {0xffd0f8e0u, 0xff70c088u, 0xff566834u, 0xff201808u};
    u32 *dst = (u32 *)address;
    if (!playing) { picker(dst, dim[0]); return 1; }
    const u8 *source = frame;
    u32 repeat = 0;
    for (u32 y = 0; y < 480; ++y) {
        u32 *row = dst + y * dim[0];
        if (y < 24 || y >= 456) { for (u32 x = 0; x < 720; ++x) row[x] = 0xff000000u; continue; }
        for (u32 x = 0; x < 120; ++x) row[x] = 0xff000000u;
        u32 *out = row + 120;
        for (u32 x = 0; x < LCD_WIDTH; ++x) { u32 c = shade[source[x]]; *out++ = c; *out++ = c; *out++ = c; }
        for (u32 x = 600; x < 720; ++x) row[x] = 0xff000000u;
        if (++repeat == 3) { repeat = 0; source += LCD_WIDTH; }
    }
    /* red square, top left: the emulator fell behind real time within the last ~2 s */
    if (behind) for (u32 y = 4; y < 20; ++y) for (u32 x = 4; x < 20; ++x) dst[y * dim[0] + x] = 0xff3030ffu;
    return 1;
}
u32 gr3_status(u32 field) {
    if (field == 0) return ready;
    if (field == 1) return failed;
    if (field == 2) return frames;
    if (field == 3) return held;
    if (field == 4) return pad;
    if (field == 5) return playing;
    if (field == 6) return rom_count;
    if (field == 7) return cursor;
    if (field == 8) return message;
    return 0;
}
