/* SPDX-License-Identifier: GPL-2.0-only */
/* Boot splash "app" for the camera launcher: shows one 720x480 picture for ~2.5 s after
 * power-on (any game key dismisses it). Same runtime ABI as the games. The picture is
 * supplied by the builder: 256 palette entries (u32, R,G,B,A bytes) then 720x480 indices. */
typedef unsigned int u32;
typedef unsigned char u8;
typedef unsigned short u16;
#define PICTURE_BYTES (1024u + 720u * 480u)
static const u8 *picture;
static u32 ticks, ready;

int gr3_init(void *memory, u32 bytes, const void *data, u32 length) {
    (void)memory; (void)bytes;
    if (!data || length != PICTURE_BYTES || ((u32)data & 3u)) return 0;
    picture = data; ticks = 0; ready = 1; return 1;
}
int gr3_tick(void) { return ready && ++ticks < 90u; }
int gr3_key(u32 id, u32 down) { (void)id; if (down) ticks = 1000u; return 1; }
void gr3_release_keys(void) {}
int gr3_blit(void *descriptor) {
    if (!ready || !descriptor) return 0;
    const u8 *b = descriptor;
    const u16 *dim = (const u16 *)(b + 0x18);
    u32 address = *(const u32 *)(b + 8);
    if (b[2] != 5 || (dim[0] != 720 && dim[0] != 736) || dim[1] != 480 || dim[2] != 720 || dim[3] != 480 ||
        address < 0x40000000u || address >= 0xc0000000u - dim[0] * 480u * 4u || (address & 3)) return 0;
    const u32 *palette = (const u32 *)picture;
    const u8 *pixels = picture + 1024;
    u32 *dst = (u32 *)address;
    for (u32 y = 0; y < 480; ++y) {
        u32 *row = dst + y * dim[0];
        for (u32 x = 0; x < 720; ++x) row[x] = palette[*pixels++];
    }
    return 1;
}
