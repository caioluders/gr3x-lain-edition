# SPDX-License-Identifier: GPL-2.0-only
"""Run the actual ARM game and GR III blitter in Unicorn, with no engine stubs."""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_PROT_READ, UC_PROT_EXEC
from unicorn import arm_const as reg
from PIL import Image
from build_gr3x import relocate

BASE, ARENA, WAD = 0x68000000, 0x69000000, 0x70000000
STACK, STOP, DESC, PIXELS = 0x610ff000, 0x61000000, 0x64000000, 0x64001000


class Runtime:
    def __init__(self, directory, base=BASE):
        self.directory, self.base = directory, base
        self.manifest = json.loads((directory / 'runtime.json').read_text(encoding='utf-8'))
        self.raw = (directory / 'runtime.bin').read_bytes()
        self.wad = (directory / 'doom1.wad').read_bytes()
        assert hashlib.sha256(self.raw).hexdigest() == self.manifest['imageSha256']
        assert hashlib.sha256(self.wad).hexdigest() == self.manifest['wadSha256']
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.uc.ctl_set_cpu_model(reg.UC_CPU_ARM_CORTEX_A9)
        self.uc.mem_map(base, (len(self.raw) + 4095) & ~4095)
        self.uc.mem_write(base, relocate(self.raw, self.manifest['relocations'], base))
        self.uc.mem_map(ARENA, self.manifest['arenaBytes'])
        self.uc.mem_map(WAD, (len(self.wad) + 4095) & ~4095)
        self.uc.mem_write(WAD, self.wad)
        self.uc.mem_protect(WAD, (len(self.wad) + 4095) & ~4095, UC_PROT_READ)
        self.uc.mem_map(0x61000000, 0x100000)
        self.uc.mem_map(DESC, 0x200000)
        self.uc.reg_write(reg.UC_ARM_REG_C1_C0_2, 0xf00000)
        self.uc.reg_write(reg.UC_ARM_REG_FPEXC, 0x40000000)

    def call(self, name, *args):
        self.uc.reg_write(reg.UC_ARM_REG_CPSR, 0x13)
        self.uc.reg_write(reg.UC_ARM_REG_SP, STACK)
        self.uc.reg_write(reg.UC_ARM_REG_LR, STOP)
        for i, value in enumerate(args): self.uc.reg_write(getattr(reg, f'UC_ARM_REG_R{i}'), value)
        saved = {getattr(reg, f'UC_ARM_REG_R{i}'): 0x12340000 + i for i in range(4, 12)}
        saved.update({getattr(reg, f'UC_ARM_REG_D{i}'): 0x3ff0000000000000 + i for i in range(8, 16)})
        for key, value in saved.items(): self.uc.reg_write(key, value)
        try:
            self.uc.emu_start(self.base + self.manifest['exports'][name], STOP, timeout=20_000_000, count=100_000_000)
        except Exception:
            print('ARM fault', name, hex(self.uc.reg_read(reg.UC_ARM_REG_PC)))
            raise
        if self.uc.reg_read(reg.UC_ARM_REG_PC) != STOP: raise AssertionError(f'{name}: instruction/time bound')
        assert self.uc.reg_read(reg.UC_ARM_REG_SP) == STACK, name
        assert all(self.uc.reg_read(key) == value for key, value in saved.items()), f'{name}: AAPCS register preservation'
        return self.uc.reg_read(reg.UC_ARM_REG_R0)

    def init(self):
        result = self.call('gr3_init', ARENA, self.manifest['arenaBytes'], WAD, len(self.wad))
        if result != 1:
            address = self.call('gr3_log')
            raise AssertionError(bytes(self.uc.mem_read(address, 1024)).split(b'\0')[0].decode('ascii', errors='replace'))

    def tick(self, n=1):
        for _ in range(n): assert self.call('gr3_tick') == 1

    def tap(self, key):
        assert self.call('gr3_key', key, 1) == 1
        self.tick(2)
        assert self.call('gr3_key', key, 0) == 1
        self.tick(2)

    def frame(self, pitch=736):
        self.uc.mem_write(PIXELS - 256, b'\xa5' * (pitch * 480 * 4 + 512))
        desc = bytearray(0x28)
        desc[2] = 5
        struct.pack_into('<I', desc, 8, PIXELS)
        struct.pack_into('<4H', desc, 0x18, pitch, 480, 720, 480)
        self.uc.mem_write(DESC, bytes(desc))
        assert self.call('gr3_blit', DESC) == 1
        assert bytes(self.uc.mem_read(PIXELS - 256, 256)) == b'\xa5' * 256
        assert bytes(self.uc.mem_read(PIXELS + pitch * 480 * 4, 256)) == b'\xa5' * 256
        raw = bytes(self.uc.mem_read(PIXELS, pitch * 480 * 4))
        for y in range(480): assert raw[(y * pitch + 720) * 4:(y + 1) * pitch * 4] == b'\xa5' * ((pitch - 720) * 4)
        visible = b''.join(raw[y * pitch * 4:(y * pitch + 720) * 4] for y in range(480))
        for y in range(480):
            assert visible[y * 720 * 4:(y * 720 + 40) * 4] == b'\x00\x00\x00\xff' * 40
            assert visible[(y * 720 + 680) * 4:(y + 1) * 720 * 4] == b'\x00\x00\x00\xff' * 40
        return visible


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('directory', type=Path)
    a = ap.parse_args(); out = a.directory
    p = Runtime(out); p.init(); p.tick(4)
    title = p.frame(); Image.frombytes('RGBA', (720, 480), title).save(out / 'doom-title.png')
    # Native OK opens the title menu, then chooses New Game, Episode 1 and skill.
    for _ in range(4): p.tap(8)
    p.tick(80)
    assert p.call('gr3_status', 5) == 0, 'Did not enter a real map'
    assert p.call('gr3_status', 6) == 0, 'Menu still open'
    before = (p.call('gr3_status', 7), p.call('gr3_status', 8))
    gameplay = p.frame(); Image.frombytes('RGBA', (720, 480), gameplay).save(out / 'doom-gameplay.png')
    assert gameplay != title
    assert p.frame(720) == gameplay, '720 and 736 pitch differ'
    p.call('gr3_key', 9, 1); p.call('gr3_key', 9, 1); p.tick(16)
    p.call('gr3_key', 9, 0)
    after = (p.call('gr3_status', 7), p.call('gr3_status', 8))
    assert before != after, 'UP did not move the player'
    p.call('gr3_key', 8, 1); p.tick(8); p.call('gr3_release_keys')
    assert p.call('gr3_status', 4) == 0, 'Held keys remain after release'
    assert p.call('gr3_key', 99, 1) == 0
    assert p.call('gr3_key', 9, 3) == 0
    raw_desc = bytes(p.uc.mem_read(DESC, 0x28))
    for offset, value in ((2, b'\x08'), (0x18, struct.pack('<H', 721)), (0x1c, struct.pack('<H', 719)), (8, bytes(4))):
        bad = bytearray(raw_desc); bad[offset:offset + len(value)] = value
        p.uc.mem_write(DESC, bytes(bad))
        snapshot = bytes(p.uc.mem_read(PIXELS - 256, 736 * 480 * 4 + 512))
        assert p.call('gr3_blit', DESC) == 0
        assert bytes(p.uc.mem_read(PIXELS - 256, len(snapshot))) == snapshot
    p.uc.mem_write(DESC, raw_desc)
    second = Runtime(out, base=0x66000000); second.init(); second.tick(4)
    assert second.frame() == title, 'Relocated execution is not equivalent'
    failed = Runtime(out)
    failed.uc.mem_write(WAD, b'NOPE')  # fault injection in emulator memory only
    assert failed.call('gr3_init', ARENA, failed.manifest['arenaBytes'], WAD, len(failed.wad)) == 0
    assert failed.call('gr3_status', 0) == 0 and failed.call('gr3_status', 1) != 0
    assert failed.call('gr3_tick') == 0, 'Fatal initialization continued running'
    report = dict(schema=1, imageSha256=p.manifest['imageSha256'], wadSha256=p.manifest['wadSha256'],
                  actualArmEngineExecuted=True, realMapEntered=True, playerMoved=before != after,
                  aapcsPreserved=True, fatalErrorRecoveryVerified=True, guardCases=4, pitches=[720, 736],
                  runtimeBases=[hex(BASE), '0x66000000'], arenaUsed=p.call('gr3_status', 2),
                  ticks=p.call('gr3_status', 3), titleSha256=hashlib.sha256(title).hexdigest(),
                  gameplaySha256=hashlib.sha256(gameplay).hexdigest(), hardwareTested=False,
                  scope='ARM engine, WAD reads, menus, gameplay, input and blitter. No RTOS scheduler, LCD DMA, cache or physical buttons.')
    (out / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
