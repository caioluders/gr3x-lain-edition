#!/usr/bin/env python3
# SPDX-License-Identifier: WTFPL
"""End-to-end emulation of the LAIN launcher on a BAKED firmware (Unicorn, no camera).

Usage:
    .venv/bin/python tests/lain_e2e.py out/fwdc243b-NAME.bin [screenshot dir]
Env: TICK_MS (timer period to simulate, default 29), LAIN_BUILD (build folder).

It decodes the firmware, runs the launcher code against mocks of the camera services it
calls (heap, timers, UI manager, filesystem, audio output driver) and checks:
  boot splash, menu lines, Game Boy launch + ROM picker + SD load, button semantics
  (2 = pushed, 1 = let go), held/tapped keys, sound chunks + real-time pacing, exit.
The mocks encode MY reading of the camera's interfaces; they cannot prove the camera
agrees -- only that the launcher does what was intended.
"""
import array, hashlib, json, os, struct, sys
from unicorn import *
from unicorn.arm_const import *

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.environ.get('LAIN_BUILD', os.path.join(REPO, 'build'))
sys.path.insert(0, os.path.join(REPO, 'tools'))
sys.path.insert(0, os.path.join(REPO, 'deps', 'gr-custom-tool'))
import make_test_rom
from gr4_editor.codec.container import inspect_container
from gr4_editor.codec.frames import decode_payload, section_rows

FW = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else None
TICK_MS = float(os.environ.get('TICK_MS', '29'))
CHUNK_MS = 60
PUSHED, LET_GO = 2, 1

raw = open(FW, 'rb').read()
info = inspect_container(raw)
decoded, _ = decode_payload(raw[128:info['payload_end']], info['decoded_size'])
row = {r['name']: r for r in section_rows(decoded)}['RTOS']
img = bytes(decoded[row['start'] + 16:row['end']])
ex = json.load(open(os.path.join(BUILD, 'camera.json')))['exports']

MAGIC = 0x4d4f4f44
C, ROOT, DESC, FB, MSG, PAY, RET = 0x60001000, 0x60003000, 0x60004000, 0x6a000000, 0x60005000, 0x60005100, 0x10008000
FSF, FS, FSVT, STUB = 0x60006000, 0x60006100, 0x60006200, 0x60008000
AF, SVC, DRV = 0x60009000, 0x60009100, 0x60009400
PENDING, BOOTED = 0x55804390, 0x55804394
S_ACTIVE, S_APP = 36, 76

rom = make_test_rom.build()
CARD = [('NOTES.TXT', 0x20, b'x'), ('SUBDIR', 0x10, b''), ('MYGAME.GB', 0x20, rom), ('readme.gb', 0x20, b'\0' * 0x8000)]
ROM_DIR = 'C:\\LAIN\\GAMEBOY\\ROM'


class Camera:
    def __init__(self):
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        u.mem_map(0x53000000, 0x2000000); u.mem_write(0x53000000, img)
        for base, size in ((0x55800000, 0x100000), (0x60000000, 0x100000), (0x61000000, 0x1000000), (FB, 0x200000), (0x10000000, 0x20000)):
            u.mem_map(base, size)
        u.reg_write(UC_ARM_REG_C1_C0_2, u.reg_read(UC_ARM_REG_C1_C0_2) | (0xf << 20)); u.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        w = self.w
        w(0x5580438c, C); w(C, 0x53b2f9e8); w(C + 0x6a0, MAGIC); w(C + 0x15c, ROOT); w(ROOT + 0xec, DESC)
        d = bytearray(0x20); d[2] = 5; struct.pack_into('<I', d, 8, FB); struct.pack_into('<4H', d, 0x18, 736, 480, 720, 480)
        u.mem_write(DESC, bytes(d))
        w(FSF + 0x14, FS); w(FS, FSVT); w(AF + 4, SVC); w(SVC + 0xbc, DRV)
        for slot in (0x1c, 0x20, 0x24, 0x30, 0x58, 0x5c, 0x60): w(FSVT + slot, STUB + slot * 4)
        self.heap = 0x61000000; self.log = []; self.aud = []; self.played = []
        self.find = 0; self.file = None; self.pos = 0; self.screen = 0x08000000
        self.clock = 0.0; self.next_chunk = float(CHUNK_MS); self.empty_card = False
        for lo, hi in ((0x53714330, 0x53714334), (0x53714d60, 0x53714d64), (0x5371451c, 0x53714520), (0x5371457c, 0x53714580),
                       (0x5316f000, 0x53600000), (0x536a0000, 0x536c0000), (STUB, STUB + 0x1000), (RET, RET + 4)):
            u.hook_add(UC_HOOK_CODE, self.hook, begin=lo, end=hi)

    def w(self, a, v): self.u.mem_write(a, struct.pack('<I', v))
    def r(self, a): return struct.unpack('<I', self.u.mem_read(a, 4))[0]
    def cstr(self, a): return bytes(self.u.mem_read(a, 64)).split(b'\0')[0].decode()

    def entry(self, e, i):
        name, attr, data = CARD[i]
        self.u.mem_write(e + 0x18 + 4, name.encode().ljust(13, b'\0')); self.u.mem_write(e + 0x18 + 0x11, bytes([attr])); self.w(e + 0x18 + 0x18, len(data))

    def hook(self, u, a, size, user):
        G = u.reg_read
        r0, r1, r2, r3, sp = (G(x) for x in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP))
        def ret(v): u.reg_write(UC_ARM_REG_R0, v); u.reg_write(UC_ARM_REG_PC, G(UC_ARM_REG_LR))
        if a == RET: u.emu_stop()
        elif a == 0x53391b14: ret(0)                               # stock live-view draw
        elif a in (0x5320ad88, 0x5320aefc, 0x5320b01c): ret(0x60007000)
        elif a == 0x53228c4c: ret(self.screen)                     # current screen id
        elif a == 0x5322b468: self.log.append('arm'); ret(7)       # timer start
        elif a in (0x5322b1ec, 0x533ab98c): ret(0)
        elif a == 0x536af31c: p = self.heap; self.heap += (r0 + 0xfff) & ~0xfff; ret(p)
        elif a == 0x536af358: self.log.append('free'); ret(0)
        elif a == 0x532288b0: self.log.append(f'POST {r1:#x}'); ret(0)
        elif a == 0x535847d8: ret(FSF)
        elif a == 0x53594990: ret(r0)
        elif a == 0x53568e58: ret(AF)
        elif a == 0x53714330: self.aud.append(f'enable stereo={r1}'); ret(0)
        elif a == 0x53714d60:
            self.aud.append('start'); self.take_chunk(); u.mem_write(DRV + 0x66, bytes([1])); ret(0)
        elif a == 0x5371451c: self.aud.append('halt'); ret(0)
        elif a == 0x5371457c: self.aud.append('stop'); u.mem_write(DRV + 0x66, bytes([0])); ret(0)
        elif a == STUB + 0x58 * 4:
            directory = self.cstr(r1); self.log.append(f'findfirst {directory} {self.cstr(r2)}'); self.find = 0
            if self.empty_card and directory != 'C:\\': self.w(self.r(sp + 4), 0xc); self.find = 99; ret(0)
            else: self.entry(self.r(sp), 0); ret(1)
        elif a == STUB + 0x5c * 4:
            self.find += 1
            if self.find < len(CARD): self.entry(r1, self.find); ret(1)
            else: self.w(r2, 0xc); ret(0)
        elif a == STUB + 0x60 * 4: self.log.append('findclose'); ret(0)
        elif a == STUB + 0x1c * 4:
            path = self.cstr(r1); self.log.append(f'open {path} mode={r2}')
            match = [x for x in CARD if x[0] == path.split('\\')[-1]]
            if path.startswith(ROM_DIR + '\\') and match: self.file = match[0][2]; self.pos = 0; self.w(r3, 0x1234); ret(1)
            else: ret(0)
        elif a == STUB + 0x30 * 4: self.w(r2, len(self.file)); ret(1)
        elif a == STUB + 0x24 * 4:
            chunk = self.file[self.pos:self.pos + r3]; u.mem_write(r2, chunk); self.pos += len(chunk); ret(1 if len(chunk) == r3 else 0)
        elif a == STUB + 0x20 * 4: self.log.append(f'close {r1:#x}'); ret(0)

    def take_chunk(self):
        self.played.append(bytes(self.u.mem_read(self.r(DRV + 0x8c), self.r(DRV + 0x90)))); self.w(DRV + 0x8c, 0); self.w(DRV + 0x90, 0)

    def call(self, fn, a0=0, a1=0):
        u = self.u
        u.reg_write(UC_ARM_REG_SP, 0x1001f000); u.reg_write(UC_ARM_REG_LR, RET); u.reg_write(UC_ARM_REG_R0, a0); u.reg_write(UC_ARM_REG_R1, a1)
        u.emu_start(fn, 0); return u.reg_read(UC_ARM_REG_R0)

    def session(self): return self.r(C + 0x6a4)
    def draw(self): self.call(ex['camera_draw'], ROOT)

    def tick(self):
        self.call(ex['camera_tick'], self.session())
        if self.aud and self.aud[-1] in ('start',) or (self.u.mem_read(DRV + 0x66, 1)[0] == 1):
            self.clock += TICK_MS                       # mock speaker in real time
            while self.clock >= self.next_chunk:
                self.next_chunk += CHUNK_MS
                if self.u.mem_read(DRV + 0x66, 1)[0] != 1: break
                if self.r(DRV + 0x8c): self.take_chunk()
                else: self.aud.append('UNDERRUN')

    def button(self, switch, phase):
        self.u.mem_write(MSG, struct.pack('<HHI', switch, 0, PAY)); self.u.mem_write(PAY, bytes([phase]))
        return self.call(ex['camera_filter'], MSG)

    def tap(self, switch):
        self.button(switch, PUSHED); self.tick(); self.button(switch, LET_GO); self.tick()

    def menu_ok(self, line_id):
        u = self.u
        u.reg_write(UC_ARM_REG_SP, 0x1001f000); u.reg_write(UC_ARM_REG_LR, RET); u.reg_write(UC_ARM_REG_R1, line_id)
        u.emu_start(0x5316f3b8, 0); obj = u.reg_read(UC_ARM_REG_R0)
        return self.call(self.r(self.r(obj) + 0x24), obj, line_id)

    def shot(self, name=None):
        self.draw(); data = bytes(self.u.mem_read(FB, 736 * 480 * 4))
        if OUT and name:
            from PIL import Image
            Image.frombuffer('RGBA', (736, 480), data, 'raw', 'RGBA', 0, 1).crop((0, 0, 720, 480)).save(os.path.join(OUT, name + '.png'))
        return hashlib.sha256(data).hexdigest()[:10]


ok = True
def check(name, condition):
    global ok
    ok &= bool(condition); print('OK  ' if condition else 'FAIL', name)


# ---- 1. boot splash
cam = Camera()
cam.shot('boot_splash'); s = cam.session()
check('splash starts on the first live-view draw after power-on', s and cam.r(s + S_APP) == 2 and cam.r(BOOTED) == MAGIC)
n = 0
while cam.session() and n < 200: cam.tick(); n += 1
check('splash ends by itself after ~2.5 s and frees', 85 <= n <= 95 and 'free' in cam.log)
cam.log.clear(); cam.draw(); check('splash not shown again', cam.session() == 0 and not cam.log)
cam.w(BOOTED, 0); cam.draw(); cam.button(8, PUSHED); cam.tick(); cam.tick()
check('pushing a game button dismisses the splash', cam.session() == 0)

# ---- 2. menu lines
cam.log.clear(); result = cam.menu_ok(0x020001a0)
check('DOOM line: flags app 0, posts exit-to-shooting, returns 0', cam.r(PENDING) == MAGIC and cam.log == ['POST 0x86'] and result == 0)
cam.log.clear(); cam.menu_ok(0x020001a1)
check('GAME BOY line: flags app 1, posts exit-to-shooting', cam.r(PENDING) == MAGIC + 1 and cam.log == ['POST 0x86'])

# ---- 3. Game Boy launch, picker, SD load
cam.log.clear(); cam.draw(); s = cam.session()
check('Game Boy session created and armed', s and cam.r(s + S_APP) == 1 and cam.r(s + S_ACTIVE) == 1 and 'arm' in cam.log)
check('ROM folder listed', f'findfirst {ROM_DIR} *.*' in cam.log and 'findclose' in cam.log)
picker = cam.shot('picker'); cam.log.clear()
# OK is still held from the menu: its let-go arrives now and must not act
cam.button(8, LET_GO); cam.tick(); check('let-go of a button pushed before launch is ignored', not [l for l in cam.log if l.startswith('open')])
cam.tap(10); cam.tap(8)
check('bad ROM (second entry) opened then refused', f'open {ROM_DIR}\\readme.gb mode=1' in cam.log and any(l.startswith('close') for l in cam.log))
cam.log.clear(); cam.tap(9); cam.tap(8)
files = [l for l in cam.log if l != 'arm']
check('first ROM opened, read, closed', files == [f'open {ROM_DIR}\\MYGAME.GB mode=1', 'close 0x1234'])
for _ in range(30): cam.tick()
game = cam.shot('game'); check('game picture replaces the picker', game != picker)

# ---- 4. buttons: 2 = pushed, 1 = let go; held keys stay held, released keys release
cam.button(12, PUSHED)
for _ in range(8): cam.tick()
moved = cam.shot(); check('holding right scrolls while held', moved != game)
cam.button(12, LET_GO); cam.tick(); cam.tick(); rest = cam.shot()
for _ in range(6): cam.tick()
check('letting go stops it (no stuck key)', cam.shot() == rest)
cam.button(11, LET_GO); cam.tick(); cam.tick()
for _ in range(4): cam.tick()
check('a stray let-go does not push the key', cam.shot() == rest)
cam.button(12, PUSHED); cam.button(12, LET_GO); cam.tick(); a = cam.shot(); cam.tick(); cam.tick(); b = cam.shot()
for _ in range(4): cam.tick()
check('a tap shorter than one tick still registers once, then releases', a != rest and cam.shot() == b)
check('game buttons are swallowed, others pass to the camera', cam.button(9, PUSHED) == 1 and cam.button(9, LET_GO) == 1 and cam.button(40, PUSHED) == 0)

# ---- 5. sound: hold A (the test ROM plays ~440 Hz), speaker mock consumes in real time
check('sound output started in the stock call order', cam.aud[:2] == ['enable stereo=1', 'start'])
cam.played.clear(); cam.button(8, PUSHED)
for _ in range(100): cam.tick()
cam.button(8, LET_GO); cam.tick()
left = array.array('h', b''.join(cam.played))[0::2]
sounding = [i for i, x in enumerate(left) if x]; left = left[sounding[0]:sounding[-1] + 1]; seconds = len(left) / 48000
crossings = sum(1 for i in range(1, len(left)) if left[i - 1] <= 0 < left[i])
gap = longest = 0
for x in left:
    gap = gap + 1 if x == 0 else 0; longest = max(longest, gap)
held = 100 * TICK_MS / 1000
print(f'    tick {TICK_MS} ms: tone {seconds:.2f} s of {held:.2f} s held, ~{crossings / seconds:.0f} Hz, longest gap {longest / 48:.1f} ms, chunks {sorted(set(len(p) for p in cam.played))}')
check('tone is ~440 Hz in 60 ms chunks', 400 < crossings / seconds < 480 and set(len(p) for p in cam.played) == {11520})
check('no underrun, no restart, no gaps', cam.aud.count('start') == 1 and 'UNDERRUN' not in cam.aud and longest < 96)
check('game runs in real time at this tick rate', abs(seconds - held) < 0.3)

# ---- 6. exit
cam.log.clear(); cam.screen = 0x02000001; cam.tick()
check('leaving the shooting screen stops sound and frees the session', cam.session() == 0 and 'free' in cam.log and cam.aud[-2:] == ['halt', 'stop'])

# ---- 7. DOOM session: launch, a held key is released on let-go, MENU exits
cam.screen = 0x08000000; cam.w(PENDING, MAGIC); cam.draw(); s = cam.session()
check('DOOM session created', s and cam.r(s + S_APP) == 0 and cam.r(s + S_ACTIVE) == 1)
for _ in range(3): cam.tick()
cam.button(9, PUSHED); cam.tick(); check('pushed key is applied', cam.r(s + 68) == 1 << 9)
cam.button(9, LET_GO); cam.tick(); check('let-go clears it', cam.r(s + 68) == 0)
# DOOM sound: reach the title screen, push OK (opens the menu with a sound), listen
for _ in range(45): cam.tick()
cam.played.clear(); cam.button(8, PUSHED); cam.tick(); cam.button(8, LET_GO)
for _ in range(25): cam.tick()
samples = array.array('h', b''.join(cam.played)); peak = max((abs(x) for x in samples), default=0)
print(f'    DOOM: {len(cam.played)} chunks, peak {peak}')
check('DOOM menu sound reaches the speaker at an audible level', peak > 3000)
cam.log.clear(); cam.button(5, PUSHED); check('MENU exits DOOM and frees', cam.session() == 0 and 'free' in cam.log)

# ---- 8. empty ROM folder: diagnostics screen instead of a list
cam2 = Camera(); cam2.empty_card = True; cam2.w(BOOTED, MAGIC); cam2.w(PENDING, MAGIC + 1); cam2.draw()
cam2.shot('picker_empty'); check('empty folder: picker still opens', cam2.session() != 0)

print('ALL OK' if ok else 'FAILURES')
sys.exit(0 if ok else 1)
