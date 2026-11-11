# SPDX-License-Identifier: WTFPL
"""Build a tiny original Game Boy test ROM (no third-party code or data).

It shows a 4-shade tile pattern; the d-pad scrolls it and holding A inverts the
palette and plays a tone. Used as the default cartridge when no ROM is supplied, and by the tests.
The logo area is left empty (only a real boot ROM checks it; the emulator does not).
"""
import sys


def build():
    code = bytearray(); labels = {}; fix = []
    def emit(*b): code.extend(b)
    def label(n): labels[n] = len(code)
    def jr(op, n): fix.append((len(code) + 1, n)); emit(op, 0)
    ORG = 0x150
    def abs16(n): return ('abs', n)
    def ld16(op, value):          # ld r16,imm16 ; value may be a label
        emit(op, 0, 0)
        if isinstance(value, str): fix.append((len(code) - 2, ('abs', value)))
        else: code[-2:] = bytes([value & 255, value >> 8])
    emit(0xf3)                    # di
    ld16(0x31, 0xfffe)            # ld sp,fffe
    label('wait0'); emit(0xf0, 0x44, 0xfe, 144); jr(0x38, 'wait0')   # wait LY>=144
    emit(0xaf, 0xe0, 0x40)        # LCDC=0 (off)
    ld16(0x21, 0x8000); ld16(0x11, 'tiles'); ld16(0x01, 64); emit(0xcd, 0, 0); fix.append((len(code) - 2, ('abs', 'copy')))
    ld16(0x21, 0x9800); ld16(0x11, 'map'); ld16(0x01, 1024); emit(0xcd, 0, 0); fix.append((len(code) - 2, ('abs', 'copy')))
    emit(0xaf, 0xe0, 0x42, 0xe0, 0x43)          # SCY=SCX=0
    emit(0x3e, 0xe4, 0xe0, 0x47)                # BGP
    emit(0x3e, 0x91, 0xe0, 0x40)                # LCD on, BG on, tiles at 8000
    label('main')
    label('vb'); emit(0xf0, 0x44, 0xfe, 144); jr(0x20, 'vb')         # wait LY==144
    emit(0x3e, 0x20, 0xe0, 0x00, 0xf0, 0x00, 0xf0, 0x00, 0x2f, 0xe6, 0x0f, 0x47)   # b = pressed d-pad
    emit(0xcb, 0x40); jr(0x28, 'nr'); emit(0xf0, 0x43, 0x3c, 0xe0, 0x43); label('nr')   # right: SCX++
    emit(0xcb, 0x48); jr(0x28, 'nl'); emit(0xf0, 0x43, 0x3d, 0xe0, 0x43); label('nl')   # left:  SCX--
    emit(0xcb, 0x50); jr(0x28, 'nu'); emit(0xf0, 0x42, 0x3d, 0xe0, 0x42); label('nu')   # up:    SCY--
    emit(0xcb, 0x58); jr(0x28, 'nd'); emit(0xf0, 0x42, 0x3c, 0xe0, 0x42); label('nd')   # down:  SCY++
    emit(0x3e, 0x10, 0xe0, 0x00, 0xf0, 0x00, 0xf0, 0x00, 0x2f, 0xe6, 0x01)          # a = A pressed
    jr(0x28, 'quiet')                                                # A held: inverted palette + a tone
    emit(0x3e, 0x1b, 0xe0, 0x47)
    emit(0x3e, 0x80, 0xe0, 0x26, 0x3e, 0x77, 0xe0, 0x24, 0x3e, 0xff, 0xe0, 0x25)   # NR52 on, NR50, NR51
    emit(0xf0, 0x12, 0xfe, 0xf0); jr(0x28, 'done')                   # already sounding
    emit(0x3e, 0x80, 0xe0, 0x11, 0x3e, 0xf0, 0xe0, 0x12, 0x3e, 0xd6, 0xe0, 0x13, 0x3e, 0x86, 0xe0, 0x14)  # ~440 Hz square
    jr(0x18, 'done')
    label('quiet'); emit(0x3e, 0xe4, 0xe0, 0x47, 0xaf, 0xe0, 0x12)   # normal palette, channel 1 off
    label('done')
    emit(0x3e, 0x30, 0xe0, 0x00)
    label('nvb'); emit(0xf0, 0x44, 0xfe, 144); jr(0x28, 'nvb')       # wait LY!=144
    jr(0x18, 'main')
    label('copy')                 # hl=dst de=src bc=len
    emit(0x1a, 0x22, 0x13, 0x0b, 0x78, 0xb1); jr(0x20, 'copy'); emit(0xc9)
    label('tiles')
    for lo, hi in [(0, 0), (0xff, 0), (0, 0xff), (0xff, 0xff)]: emit(*([lo, hi] * 8))
    label('map')
    for y in range(32):
        for x in range(32):
            ring = max(abs(x % 8 - 4), abs(y % 8 - 4))
            emit(ring & 3 if ring < 4 else (x + y) & 3)
    for at, target in fix:
        if isinstance(target, tuple):
            value = ORG + labels[target[1]]; code[at] = value & 255; code[at + 1] = value >> 8
        else:
            delta = labels[target] - (at + 1)
            if not -128 <= delta < 128: raise ValueError('jr range ' + target)
            code[at] = delta & 255
    rom = bytearray(0x8000)
    rom[0x100:0x104] = bytes([0x00, 0xc3, 0x50, 0x01])    # nop ; jp 0150
    rom[0x134:0x134 + 8] = b'LAINTEST'
    rom[0x147] = 0; rom[0x148] = 0; rom[0x149] = 0
    rom[0x150:0x150 + len(code)] = code
    check = 0
    for i in range(0x134, 0x14d): check = (check - rom[i] - 1) & 255
    rom[0x14d] = check
    return bytes(rom)


if __name__ == '__main__':
    open(sys.argv[1], 'wb').write(build())
