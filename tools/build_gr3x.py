# SPDX-License-Identifier: GPL-2.0-only
"""Build a position-independent ARM DOOM runtime; no firmware or camera writes."""
import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / 'src'
ENGINE_COMMIT = '355cfbd16fac119718879239336ee2ea408886bd'
ENGINE_SHA256 = 'ec16ce721436bf3fa4c338320afedd56a93c0b563a94ebf283718503c71ca780'


def relocate(raw, offsets, base):
    result = bytearray(raw)
    if len(offsets) != len(set(offsets)):
        raise ValueError('Duplicate relocation')
    for offset in offsets:
        if offset < 0 or offset % 4 or offset + 4 > len(raw):
            raise ValueError('Relocation outside image or unaligned')
        value = struct.unpack_from('<I', raw, offset)[0]
        if value >= len(raw):
            raise ValueError('Relocation target outside image')
        struct.pack_into('<I', result, offset, value + base)
    return bytes(result)


def validate_wad(raw):
    if len(raw) < 12 or raw[:4] != b'IWAD':
        raise ValueError('Expected a complete IWAD')
    count, directory = struct.unpack_from('<II', raw, 4)
    if not count or directory < 12 or directory + count * 16 > len(raw):
        raise ValueError('Invalid WAD directory')
    for i in range(count):
        start, size = struct.unpack_from('<II', raw, directory + i * 16)
        if start + size > len(raw):
            raise ValueError('Lump extends past WAD')


def main():
    from elftools.elf.elffile import ELFFile
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--engine', type=Path, required=True, help='Pinned PureDOOM.h')
    ap.add_argument('--wad', type=Path, required=True, help='User-supplied DOOM shareware IWAD')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--gb-core', type=Path, required=True, help='Peanut-GB peanut_gb.h')
    ap.add_argument('--splash', type=Path, required=True, help='Boot splash picture (any size; fitted to 720x480)')
    ap.add_argument('--gb-rom', type=Path, help='Game Boy ROM to embed (default: built-in test ROM)')
    a = ap.parse_args()
    engine = a.engine.read_bytes()
    if hashlib.sha256(engine).hexdigest() != ENGINE_SHA256:
        raise ValueError('PureDOOM.h does not match the pinned source')
    wad = a.wad.read_bytes(); validate_wad(wad)
    out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    # Compile the exact checked header bytes. Quoted includes otherwise search
    # beside the adapter first and can select an unchecked, same-named header.
    adapter_path = out / 'gr3_doom.c'
    adapter_path.write_bytes((SRC / 'gr3_doom.c').read_bytes())
    (out / 'PureDOOM.h').write_bytes(engine)
    script = out / 'runtime.ld'
    script.write_text('SECTIONS { . = 0; .text : { *(.text*) } .rodata : { *(.rodata*) } '
                      '.data : { *(.data*) } .dynamic : { *(.dynamic) } .got : { *(.got*) } '
                      '.rel.dyn : { *(.rel.dyn) } .bss : { *(.bss*) *(COMMON) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }\n', encoding='ascii')
    flags = ['-target', 'arm-freestanding-eabi', '-mcpu=cortex_a9', '-marm', '-mfloat-abi=soft',
             '-Os', '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fPIC',
             '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-nostdlib', f'-ffile-prefix-map={out}=.',
             '-Wno-incompatible-pointer-types', '-Wno-pointer-sign', '-Wno-parentheses',
             '-Wno-deprecated-non-prototype',
             '-Wl,-T,' + str(script), '-Wl,--entry=gr3_init']
    elf_path = out / 'doom.elf'
    object_path = out / 'doom.o'
    compile_flags = [f for f in flags if not f.startswith('-Wl,')]
    subprocess.run([sys.executable, '-m', 'ziglang', 'cc', *compile_flags, '-I', str(out),
                    '-c', str(adapter_path), '-o', str(object_path)], check=True)
    link_flags = ['-target', 'arm-freestanding-eabi', '-mcpu=cortex_a9', '-marm', '-mfloat-abi=soft',
                  '-nostdlib', '-fPIC', '-Wl,--entry=gr3_init']
    subprocess.run([sys.executable, '-m', 'ziglang', 'cc', *link_flags, '-Wl,-T,' + str(script),
                    str(object_path), '-o', str(elf_path)], check=True)
    with elf_path.open('rb') as stream:
        elf = ELFFile(stream)
        if elf['e_machine'] != 'EM_ARM': raise ValueError('Expected ARM ELF')
        sections = [s for s in elf.iter_sections() if s['sh_flags'] & 2 and s['sh_size']]
        memory_size = max(s['sh_addr'] + s['sh_size'] for s in sections)
        image = bytearray((memory_size + 15) & ~15)
        for s in sections:
            if s['sh_type'] != 'SHT_NOBITS': image[s['sh_addr']:s['sh_addr'] + s['sh_size']] = s.data()
        syms = list(elf.get_section_by_name('.symtab').iter_symbols())
        undefined = [s.name for s in syms if s.name and s['st_shndx'] == 'SHN_UNDEF']
        if undefined: raise ValueError('Unresolved symbols: ' + str(undefined))
        exports = {s.name: s['st_value'] for s in syms if s.name.startswith('gr3_') and s['st_info']['type'] == 'STT_FUNC'}
    # Zig 0.13 does not expose LLD's --emit-relocs. Link the same PIC program at
    # two bases instead. Every changed word must be an in-image absolute pointer
    # plus exactly the base delta; reject split instruction/address relocations.
    second_base = 0x10000000
    second_script = out / 'runtime-second.ld'
    second_script.write_text(script.read_text(encoding='ascii').replace('. = 0;', '. = 0x10000000;'), encoding='ascii')
    second_elf = out / 'doom-second.elf'
    subprocess.run([sys.executable, '-m', 'ziglang', 'cc', *link_flags, '-Wl,-T,' + str(second_script),
                    str(object_path), '-o', str(second_elf)], check=True)
    shifted = bytearray(len(image))
    with second_elf.open('rb') as stream:
        elf = ELFFile(stream)
        for s in elf.iter_sections():
            if s['sh_flags'] & 2 and s['sh_size']:
                start = s['sh_addr'] - second_base
                if start < 0 or start + s['sh_size'] > len(image): raise ValueError('Second link layout changed')
                if s['sh_type'] != 'SHT_NOBITS': shifted[start:start + s['sh_size']] = s.data()
    offsets = []
    for off in range(0, len(image), 4):
        old, new = struct.unpack_from('<I', image, off)[0], struct.unpack_from('<I', shifted, off)[0]
        if old != new:
            if old >= len(image) or new != old + second_base:
                raise ValueError(f'Non-pointer relocation at {off:#x}: {old:#x} -> {new:#x}')
            offsets.append(off)
    if not offsets or relocate(image, offsets, second_base) != shifted: raise ValueError('Relocation reconstruction failed')
    relocate(image, offsets, 0x68000000)  # Validate all runtime fixups before writing.
    (out / 'runtime.bin').write_bytes(image)
    (out / 'relocations.bin').write_bytes(b''.join(struct.pack('<I', x) for x in offsets))
    (out / 'doom1.wad').write_bytes(wad)
    report = dict(schema=1, engineCommit=ENGINE_COMMIT, engineSha256=ENGINE_SHA256,
                  adapterSha256=hashlib.sha256((SRC / 'gr3_doom.c').read_bytes()).hexdigest(),
                  imageBytes=len(image), imageSha256=hashlib.sha256(image).hexdigest(),
                  wadBytes=len(wad), wadSha256=hashlib.sha256(wad).hexdigest(),
                  relocations=offsets, exports=exports, arenaBytes=8 * 1024 * 1024,
                  hardwareTested=False, compilerFlags=flags)
    (out / 'runtime.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    gb = build_gb(out, a.gb_core, a.gb_rom, link_flags, script, second_script)
    splash = build_splash(out, a.splash, link_flags, script, second_script)
    build_camera(out, report, gb, splash)
    print(json.dumps({k: report[k] for k in ('imageBytes', 'wadBytes', 'imageSha256', 'hardwareTested')}))


def build_image(work, source, stem, label, flags, link_flags, script, second_script, required):
    """Compile one app adapter into a position-independent image + its pointer fixups."""
    from elftools.elf.elffile import ELFFile
    obj = work / (stem + '.o')
    subprocess.run([sys.executable, '-m', 'ziglang', 'cc', *flags, '-I', str(work), '-c', str(work / source), '-o', str(obj)], check=True)
    first, second = work / (stem + '.elf'), work / (stem + '-second.elf')
    subprocess.run([sys.executable, '-m', 'ziglang', 'cc', *link_flags, '-Wl,-T,' + str(script), str(obj), '-o', str(first)], check=True)
    subprocess.run([sys.executable, '-m', 'ziglang', 'cc', *link_flags, '-Wl,-T,' + str(second_script), str(obj), '-o', str(second)], check=True)
    second_base = 0x10000000
    with first.open('rb') as stream:
        elf = ELFFile(stream)
        sections = [s for s in elf.iter_sections() if s['sh_flags'] & 2 and s['sh_size']]
        image = bytearray((max(s['sh_addr'] + s['sh_size'] for s in sections) + 15) & ~15)
        for s in sections:
            if s['sh_type'] != 'SHT_NOBITS': image[s['sh_addr']:s['sh_addr'] + s['sh_size']] = s.data()
        syms = list(elf.get_section_by_name('.symtab').iter_symbols())
        undefined = [s.name for s in syms if s.name and s['st_shndx'] == 'SHN_UNDEF']
        if undefined: raise ValueError(label + ' runtime unresolved symbols: ' + str(undefined))
        exports = {s.name: s['st_value'] for s in syms if s.name.startswith('gr3_') and s['st_info']['type'] == 'STT_FUNC'}
    shifted = bytearray(len(image))
    with second.open('rb') as stream:
        for s in ELFFile(stream).iter_sections():
            if s['sh_flags'] & 2 and s['sh_size']:
                start = s['sh_addr'] - second_base
                if start < 0 or start + s['sh_size'] > len(image): raise ValueError(label + ' second link layout changed')
                if s['sh_type'] != 'SHT_NOBITS': shifted[start:start + s['sh_size']] = s.data()
    offsets = []
    for off in range(0, len(image), 4):
        old, new = struct.unpack_from('<I', image, off)[0], struct.unpack_from('<I', shifted, off)[0]
        if old != new:
            if old >= len(image) or new != old + second_base: raise ValueError(f'{label} non-pointer relocation at {off:#x}')
            offsets.append(off)
    if relocate(image, offsets, second_base) != shifted: raise ValueError(label + ' relocation reconstruction failed')
    for name in required:
        if name not in exports: raise ValueError(label + ' runtime lacks ' + name)
    return image, offsets, exports


def build_splash(out, picture_path, link_flags, script, second_script):
    """Boot splash: the picture on the left, LAIN EDITION on the right, 720x480, 256 colours."""
    from PIL import Image, ImageDraw, ImageFont
    art = Image.open(picture_path).convert('RGBA')
    canvas = Image.new('RGBA', (720, 480), (0, 0, 0, 255))
    height = 460; width = round(art.width * height / art.height)
    art = art.resize((width, height), Image.LANCZOS)
    canvas.alpha_composite(art, (24, 10))
    font = ImageFont.load_default()
    def block(text, scale, colour):                       # tiny bitmap text, enlarged without smoothing
        box = font.getbbox(text)
        small = Image.new('L', (box[2] + 2, box[3] + 2), 0)
        ImageDraw.Draw(small).text((1, 1), text, fill=255, font=font)
        small = small.crop(small.getbbox()).point(lambda v: 255 if v > 110 else 0)
        big = small.resize((small.width * scale, small.height * scale), Image.NEAREST)
        tile = Image.new('RGBA', big.size, colour); tile.putalpha(big); return tile
    centre = (24 + width + 720) // 2
    top, bottom = block('LAIN', 9, (232, 226, 222, 255)), block('EDITION', 5, (150, 132, 128, 255))
    y = (480 - top.height - 22 - bottom.height) // 2
    canvas.alpha_composite(top, (centre - top.width // 2, y))
    canvas.alpha_composite(bottom, (centre - bottom.width // 2, y + top.height + 22))
    canvas.convert('RGB').save(out / 'splash-preview.png')
    indexed = canvas.convert('RGB').quantize(256, dither=Image.Dither.NONE)
    palette = indexed.getpalette()[:768] + [0] * 768
    data = b''.join(bytes([palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2], 255]) for i in range(256)) + indexed.tobytes()
    if len(data) != 1024 + 720 * 480: raise ValueError('Splash picture size')
    work = out / 'splash'; work.mkdir(exist_ok=True)
    (work / 'gr3_splash.c').write_bytes((SRC / 'gr3_splash.c').read_bytes())
    flags = ['-target', 'arm-freestanding-eabi', '-mcpu=cortex_a9', '-marm', '-mfloat-abi=soft',
             '-O2', '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fPIC',
             '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-nostdlib', f'-ffile-prefix-map={out}=.', '-Wall', '-Wextra', '-Werror']
    image, offsets, exports = build_image(work, 'gr3_splash.c', 'splash', 'Splash', flags, link_flags, script, second_script,
                                          ('gr3_init', 'gr3_tick', 'gr3_key', 'gr3_release_keys', 'gr3_blit'))
    (out / 'splash-runtime.bin').write_bytes(image)
    (out / 'splash-relocations.bin').write_bytes(b''.join(struct.pack('<I', x) for x in offsets) or bytes(4))
    (out / 'splash-picture.bin').write_bytes(data)
    return dict(imageBytes=len(image), relocations=offsets, exports=exports, pictureBytes=len(data),
                sha256=hashlib.sha256(bytes(image) + data).hexdigest())


def build_gb(out, core, rom_path, link_flags, script, second_script):
    """Build the Game Boy app as a second position-independent runtime image (same ABI)."""
    from elftools.elf.elffile import ELFFile
    import make_test_rom
    rom = rom_path.read_bytes() if rom_path else make_test_rom.build()
    if len(rom) < 0x150 or len(rom) > 8 * 1024 * 1024: raise ValueError('Game Boy ROM size')
    check = 0
    for i in range(0x134, 0x14d): check = (check - rom[i] - 1) & 255
    if check != rom[0x14d]: raise ValueError('Game Boy ROM header checksum mismatch')
    if rom[0x143] == 0xc0: raise ValueError('Game Boy Color-only ROM: the core is DMG only')
    work = out / 'gb'; work.mkdir(exist_ok=True)
    (work / 'gr3_gb.c').write_bytes((SRC / 'gr3_gb.c').read_bytes())
    (work / 'peanut_gb.h').write_bytes(core.read_bytes())
    for name in ('minigb_apu.c', 'minigb_apu.h'): (work / name).write_bytes((core.parent / name).read_bytes())
    for name in ('stdlib.h', 'string.h', 'time.h'): (work / name).write_bytes((SRC / 'gb_shim' / name).read_bytes())
    flags = ['-target', 'arm-freestanding-eabi', '-mcpu=cortex_a9', '-marm', '-mfloat-abi=soft',
             '-O2', '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fPIC',
             '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-nostdlib', f'-ffile-prefix-map={out}=.', '-Wno-unused-function']
    image, offsets, exports = build_image(work, 'gr3_gb.c', 'gb', 'Game Boy', flags, link_flags, script, second_script,
                                          ('gr3_init', 'gr3_tick', 'gr3_key', 'gr3_release_keys', 'gr3_blit', 'gr3_audio', 'gr3_pace'))
    (out / 'gb-runtime.bin').write_bytes(image)
    (out / 'gb-relocations.bin').write_bytes(b''.join(struct.pack('<I', x) for x in offsets))
    (out / 'gb-rom.bin').write_bytes(rom)
    report = dict(imageBytes=len(image), romBytes=len(rom), relocations=offsets, exports=exports,
                  imageSha256=hashlib.sha256(image).hexdigest(), romSha256=hashlib.sha256(rom).hexdigest(),
                  romTitle=rom[0x134:0x144].split(b'\0')[0].decode('ascii', 'replace'), builtInTestRom=rom_path is None)
    (out / 'gb-runtime.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('imageBytes', 'romBytes', 'romTitle', 'builtInTestRom')}))
    return report


def build_camera(out, report, gb, splash):
    from elftools.elf.elffile import ELFFile
    header = [f'#define DOOM_IMAGE_BYTES {report["imageBytes"]}u',
              f'#define DOOM_WAD_BYTES {report["wadBytes"]}u',
              f'#define DOOM_FIXUP_COUNT {len(report["relocations"])}u']
    header += [f'#define OFFSET_{name} {value}u' for name, value in report['exports'].items()]
    header += [f'#define GB_IMAGE_BYTES {gb["imageBytes"]}u', f'#define GB_ROM_BYTES {gb["romBytes"]}u',
               f'#define GB_FIXUP_COUNT {len(gb["relocations"])}u']
    header += [f'#define GBOFF_{name} {value}u' for name, value in gb['exports'].items()]
    header += [f'#define SPLASH_IMAGE_BYTES {splash["imageBytes"]}u', f'#define SPLASH_PICTURE_BYTES {splash["pictureBytes"]}u',
               f'#define SPLASH_FIXUP_COUNT {len(splash["relocations"])}u']
    header += [f'#define SPOFF_{name} {value}u' for name, value in splash['exports'].items()]
    (out / 'runtime-offsets.h').write_text('\n'.join(header) + '\n', encoding='ascii')
    # Zig's assembler cache does not track .incbin dependencies. Bind their
    # content hashes into the assembly source to invalidate stale asset objects.
    assets = ['/* embedded content: ' + report['imageSha256'] + ' ' + report['wadSha256'] +
              ' ' + hashlib.sha256((out / 'relocations.bin').read_bytes()).hexdigest() +
              ' ' + gb['imageSha256'] + ' ' + gb['romSha256'] + ' ' + splash['sha256'] + ' */']
    for symbol, file in [('doom_image', 'runtime.bin'), ('doom_fixups', 'relocations.bin'), ('doom_wad', 'doom1.wad'),
                         ('gb_image', 'gb-runtime.bin'), ('gb_fixups', 'gb-relocations.bin'), ('gb_rom', 'gb-rom.bin'),
                         ('splash_image', 'splash-runtime.bin'), ('splash_fixups', 'splash-relocations.bin'), ('splash_picture', 'splash-picture.bin')]:
        assets += ['.section .rodata', '.balign 16', f'.global {symbol}', f'{symbol}:', f'.incbin "{(out / file).as_posix()}"']
    asset_source = out / 'assets.S'
    asset_source.write_text('\n'.join(assets) + '\n', encoding='ascii')
    address = 0x540b55c0
    script = out / 'camera.ld'
    script.write_text(f'SECTIONS {{ . = {address:#x}; .text : {{ *(.text*) *(.rodata*) }} '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }\n', encoding='ascii')
    flags = ['-target', 'arm-freestanding-eabi', '-mcpu=cortex_a9', '-marm', '-mfloat-abi=soft',
             '-Os', '-g', '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fno-pic',
             '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-nostdlib', f'-ffile-prefix-map={out}=.', '-Wall', '-Wextra', '-Werror']
    elf_path = out / 'camera.elf'
    exports_required = ['camera_keys', 'camera_factory', 'camera_sub_setcurrent', 'camera_lain_draw', 'camera_try_open', 'camera_construct', 'camera_destroy',
                        'camera_cleanup', 'camera_draw', 'camera_filter', 'camera_event', 'camera_tick', 'camera_call']
    keep = ['-Wl,-u,' + name for name in exports_required]
    subprocess.run([sys.executable, '-m', 'ziglang', 'cc', *flags, '-I', str(out), str(SRC / 'camera.c'),
                    str(asset_source), *keep, '-Wl,-T,' + str(script), '-Wl,--entry=camera_open', '-o', str(elf_path)], check=True)
    with elf_path.open('rb') as stream:
        elf = ELFFile(stream)
        for s in elf.iter_sections():
            if s.name in ('.data', '.bss', '.got') and s['sh_size']: raise ValueError('Launcher has mutable static storage')
        symbols = list(elf.get_section_by_name('.symtab').iter_symbols())
        if any(s.name and s['st_shndx'] == 'SHN_UNDEF' for s in symbols): raise ValueError('Launcher unresolved symbols')
        exports = {s.name: s['st_value'] for s in symbols if s.name.startswith('camera_') and s['st_info']['type'] == 'STT_FUNC'}
        asset_addresses = {s.name: s['st_value'] for s in symbols if s.name in ('doom_image', 'doom_fixups', 'doom_wad', 'gb_image', 'gb_fixups', 'gb_rom', 'splash_image', 'splash_fixups', 'splash_picture')}
        code = elf.get_section_by_name('.text').data()
    code += bytes(-len(code) % 4)
    embedded_assets = {}
    for symbol, file in [('doom_image', 'runtime.bin'), ('doom_fixups', 'relocations.bin'), ('doom_wad', 'doom1.wad'),
                         ('gb_image', 'gb-runtime.bin'), ('gb_fixups', 'gb-relocations.bin'), ('gb_rom', 'gb-rom.bin'),
                         ('splash_image', 'splash-runtime.bin'), ('splash_fixups', 'splash-relocations.bin'), ('splash_picture', 'splash-picture.bin')]:
        expected = (out / file).read_bytes()
        start = asset_addresses[symbol] - address
        if code[start:start + len(expected)] != expected: raise ValueError('Stale or incorrect embedded asset: ' + symbol)
        embedded_assets[symbol] = dict(address=asset_addresses[symbol], bytes=len(expected), sha256=hashlib.sha256(expected).hexdigest())
    (out / 'camera.bin').write_bytes(code)
    def branch(at, target, link=False):
        delta = target - at - 8
        if delta % 4 or not -0x2000000 <= delta < 0x2000000: raise ValueError('ARM branch range')
        return (0xeb000000 if link else 0xea000000) | ((delta >> 2) & 0xffffff)
    patches = [dict(address=0x53000460, expected=0x55804374, replacement=0x558043c0, reason='Clear reserved owner word before native heap'),
               dict(address=0x5316fa94, expected=0xe300069c, replacement=0xe30006c0, reason='Extend live-view controller for owned DOOM state')]
    for at, expected, symbol, link in [(0x5316faa0, 0xeb000dd7, 'camera_construct', True),
                                       (0x53170b50, 0xe1a0c00d, 'camera_destroy', False),
                                       (0x5316d194, 0xeb08925e, 'camera_draw', True),
                                       (0x532231e4, 0xe1d130b0, 'camera_event', False),
                                       (0x5316f3b8, 0xe300313b, 'camera_factory', False),
                                       (0x531c5950, 0xe28134fe, 'camera_sub_setcurrent', False),
                                       (0x53221a4c, 0xeb001c7e, 'camera_keys', True)]:
        patches.append(dict(address=at, expected=expected, replacement=branch(at, exports[symbol], link), reason=symbol))
    manifest = dict(schema=1, address=address, bytes=len(code), sha256=hashlib.sha256(code).hexdigest(),
                    sourceSha256=hashlib.sha256((SRC / 'camera.c').read_bytes()).hexdigest(),
                    runtimeSha256=report['imageSha256'], wadSha256=report['wadSha256'],
                    exports=exports, assets=embedded_assets, patches=patches, hardwareTested=False)
    (out / 'camera.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
