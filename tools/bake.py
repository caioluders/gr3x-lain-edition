#!/usr/bin/env python3
# SPDX-License-Identifier: WTFPL
"""Bake the LAIN launcher blob (DOOM, Game Boy, splash) into GR IIIx 1.60 fwdc243b.bin.

GR IIIx 1.60 and GR III 2.10 are byte-identical across the whole DOOM integration
surface (all 7 patch sites verified), so the GR III port applies here with the camera
blob relocated to the GR IIIx RTOS-end base (0x540b55c0). This:
  1. verifies each patch site's expected instruction word,
  2. applies the 7 patches (2 data + 5 hook branches) from camera.json,
  3. appends camera.bin (DOOM runtime + WAD + launcher) at the RTOS payload end,
  4. updates the RTOS length, fixes the decoded checksum, re-encodes reusing frames,
  5. round-trips and asserts the RTOS stays below 0x55000000 (native-data boundary).
Run with --help for the arguments.
"""
import argparse, hashlib, json, os, struct, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser(description="Bake the LAIN launcher blob into a stock GR IIIx 1.60 firmware")
ap.add_argument("--firmware", type=Path, default=REPO / "input" / "fwdc243b.bin", help="official fwdc243b.bin (1.60)")
ap.add_argument("--build", type=Path, default=REPO / "build", help="folder with camera.bin + camera.json")
ap.add_argument("--codec", type=Path, default=REPO / "deps" / "gr-custom-tool", help="gr-custom-tool checkout")
ap.add_argument("--out", type=Path, required=True, help="patched firmware to write")
ARGS = ap.parse_args()
sys.path.insert(0, str(ARGS.codec))
from gr4_editor.codec.container import inspect_container, sum32
from gr4_editor.codec.frames import decode_payload, section_rows, framed_container
from gr4_editor.firmware import _repack_frames

ORIG = str(ARGS.firmware)
RUNTIME_DIR = str(ARGS.build)
OUT = str(ARGS.out)
STOCK_SHA256 = "708e79f3b7b96a7e1581b4acf4c412233050a1832718ef7f06c8397db1b431c7"   # GR IIIx 1.60
RTOS_BASE = 0x53000000
NATIVE_DATA = 0x55000000

def main():
    manifest = json.load(open(f"{RUNTIME_DIR}/camera.json"))
    camera = open(f"{RUNTIME_DIR}/camera.bin", "rb").read()
    assert hashlib.sha256(camera).hexdigest() == manifest["sha256"], "camera.bin hash mismatch"
    assert len(camera) == manifest["bytes"]
    base = manifest["address"]
    print(f"[bake] camera base {base:#x}, {len(camera)} bytes, sha {manifest['sha256'][:16]}")

    raw = open(ORIG, "rb").read()
    assert hashlib.sha256(raw).hexdigest() == STOCK_SHA256, "input is not the official GR IIIx 1.60 fwdc243b.bin"
    info = inspect_container(raw)
    decoded, _ = decode_payload(raw[128:info["payload_end"]], info["decoded_size"])
    decoded = bytearray(decoded)
    assert sum32(decoded) == 0
    original_decoded = bytes(decoded)

    rtos = {r["name"]: r for r in section_rows(bytes(decoded))}["RTOS"]
    hdr = rtos["start"]; payload_off = hdr + 16; payload_len = rtos["end"] - payload_off
    assert payload_len == 17520064, f"unexpected RTOS size {payload_len}"
    assert base == RTOS_BASE + payload_len, f"camera base {base:#x} != RTOS end {RTOS_BASE+payload_len:#x}"

    def off(va): return payload_off + (va - RTOS_BASE)

    # 0) Convert an expendable Setup item into a "LAIN" SUBMENU opener (GRMod's proven method:
    #    copy the descriptor, set the item's DRAW (stride 8) -> a submenu-line draw and ID
    #    (stride 4) -> a submenu id, repoint the menu-table image; add a SubMenuController
    #    record + hook SetCurrentMenu). Count stays 30 -> no menu-structure crash.
    RAM, RAM_IMG, RAM_LEN = 0x55000000, 0x5407b540, 0x3a080
    def toff(va):
        if RAM <= va < RAM + RAM_LEN: va = RAM_IMG + va - RAM
        return payload_off + (va - RTOS_BASE)
    def u32at(va): return struct.unpack_from("<I", decoded, toff(va))[0]
    def movw(r, imm): return 0xe3000000 | ((imm & 0xf000) << 4) | (r << 12) | (imm & 0xfff)
    def movt(r, imm): return 0xe3400000 | ((imm & 0xf000) << 4) | (r << 12) | (imm & 0xfff)
    def relabel(tid, s):
        for li in range(21):
            row = u32at(0x55002bac + li * 4); desc = u32at(row + tid * 4); strva = u32at(desc + 4)
            cap = max(1, (struct.unpack_from("<I", original_decoded, toff(desc))[0] & 0xff) - 1)
            t = s[:cap]
            decoded[toff(strva): toff(strva) + 2 * (len(t) + 1)] = (t + "\0").encode("utf-16-le")
            decoded[toff(desc)] = len(t) + 1
    # constants (GR IIIx 1.60, from GRMod shutdown.ts/datestamp.ts)
    SETUP = 0x53b36a54; SETUP_ENTRY_IMAGE = 0x5407d408; DESC_SZ = 0x384
    D_DRAW, D_ID, D_COUNT = 0x8c, 0x1bc, 0x8b
    SUB_TABLE = 0x53b3fb10; SUB_RECORD = 0x64; SUB_RECORDS = 17; D_RANGE_RECORD = 6
    SUB_SITES = [(0x531c5d18, 0x531c5d20, 3), (0x531c5d80, 0x531c5d88, 2), (0x531c60e8, 0x531c60f4, 8),
                 (0x531c6318, 0x531c631c, 5), (0x531c63e4, 0x531c63f0, 3)]
    SUB_CONSTRUCTOR_START = 0x531c66ac
    LAIN_ID = 0x020001a0; LAIN_SUBMENU_ID = 0x020001a3
    K = int(os.environ.get("LAIN_ITEM", "24"))           # Setup item to repurpose (24 = Certification Marks)
    K_TEXT = int(os.environ.get("LAIN_TEXT", "655"))     # that item's freed text (-> "DOOM" line)
    lain_draw = manifest["exports"]["camera_lain_draw"]
    assert decoded[toff(SETUP + D_COUNT)] == 30, "Setup item count != 30"

    # texts: two NEW ids appended to the text table (GRMod's method), so no stock text is
    # relabelled: T_LAIN = setup line + submenu title, T_DOOM = submenu line.
    TEXT_ROOTS, TEXT_COUNT, LANGS = 0x55002bac, 786, 21
    TEXT_BOUNDS = [0x533966d4, 0x533970ec]                                   # movw r3,#785
    TEXT_ROOT_SITES = [(0x533966e8, 0x533966f0), (0x533970fc, 0x53397100)]   # movw/movt r3,#roots
    NEW_TEXTS = ("LAIN", "DOOM", "GAME BOY")
    T_LAIN, T_DOOM, T_GB = TEXT_COUNT, TEXT_COUNT + 1, TEXT_COUNT + 2
    GB_ID = 0x020001a1

    # appended blobs: [camera.bin][descriptor copy][submenu table copy]
    camera_blob = camera + b"\x00" * (-len(camera) % 16)
    desc_va = base + len(camera_blob)
    dcopy = bytearray(decoded[toff(SETUP):toff(SETUP) + DESC_SZ])
    old_id = struct.unpack_from("<I", dcopy, D_ID + K * 4)[0]
    struct.pack_into("<I", dcopy, D_DRAW + K * 8, lain_draw)        # item K draw -> submenu-line draw
    struct.pack_into("<I", dcopy, D_ID + K * 4, LAIN_SUBMENU_ID)    # item K id -> LAIN submenu
    dcopy = bytes(dcopy) + b"\x00" * (-DESC_SZ % 16)
    sub_va = desc_va + len(dcopy)
    subtbl = bytearray(decoded[toff(SUB_TABLE):toff(SUB_TABLE) + SUB_RECORDS * SUB_RECORD])
    rec = bytearray(decoded[toff(SUB_TABLE + D_RANGE_RECORD * SUB_RECORD):
                            toff(SUB_TABLE + D_RANGE_RECORD * SUB_RECORD) + SUB_RECORD])  # D-Range template
    struct.pack_into("<H", rec, 2, T_LAIN)         # title "LAIN"
    struct.pack_into("<H", rec, 6, 2)              # line count = 2
    struct.pack_into("<H", rec, 8, T_DOOM)         # line0 text "DOOM"
    struct.pack_into("<H", rec, 0xa, T_GB)         # line1 text "GAME BOY"
    struct.pack_into("<I", rec, 0x14, LAIN_ID)     # line0 OK id -> camera_factory -> lain_object
    struct.pack_into("<I", rec, 0x18, GB_ID)       # line1 OK id -> camera_factory -> lain_object_gb
    assert struct.unpack_from("<II", rec, 0x1c) == (0x10000000, 0x10000000) and struct.unpack_from("<II", rec, 0x28) == (540, 540)
    for o in (0x3c, 0x40, 0x44, 0x48):             # value-draw {fn,param} x2: none (as record 16)
        struct.pack_into("<I", rec, o, 0)
    subtbl += rec
    subtbl = bytes(subtbl) + b"\x00" * (-len(subtbl) % 16)

    # extended text table: [strings+records][21 rows of (786+2) record ptrs][21 row ptrs]
    text_va = sub_va + len(subtbl)
    tblob = bytearray()
    recs = []
    for name in NEW_TEXTS:
        str_va = text_va + len(tblob); tblob += (name + "\0").encode("utf-16-le"); tblob += b"\x00" * (-len(tblob) % 4)
        recs.append(text_va + len(tblob)); tblob += struct.pack("<BBBBI", len(name) + 1, 1, 0, 0, str_va)
    row_vas = []
    for l in range(LANGS):
        row = u32at(TEXT_ROOTS + l * 4)
        row_vas.append(text_va + len(tblob))
        tblob += decoded[toff(row): toff(row) + TEXT_COUNT * 4] + struct.pack(f"<{len(recs)}I", *recs)
    roots_va = text_va + len(tblob)
    tblob += struct.pack(f"<{LANGS}I", *row_vas)
    tblob = bytes(tblob) + b"\x00" * (-len(tblob) % 16)
    for at in TEXT_BOUNDS:
        assert u32at(at) == movw(3, TEXT_COUNT - 1), f"text bound {at:#x}"
        struct.pack_into("<I", decoded, toff(at), movw(3, TEXT_COUNT + len(NEW_TEXTS) - 1))
    for low, high in TEXT_ROOT_SITES:
        assert u32at(low) == movw(3, TEXT_ROOTS & 0xffff) and u32at(high) == movt(3, TEXT_ROOTS >> 16), f"text root {low:#x}"
        struct.pack_into("<I", decoded, toff(low), movw(3, roots_va & 0xffff))
        struct.pack_into("<I", decoded, toff(high), movt(3, roots_va >> 16))
    print(f"[bake] text table extended: LAIN={T_LAIN}, DOOM={T_DOOM}, GAME BOY={T_GB}, roots @ {roots_va:#x}")

    # repoint the menu-table image entry -> descriptor copy
    assert u32at(SETUP_ENTRY_IMAGE) == SETUP, f"Setup menu entry != {SETUP:#x}"
    struct.pack_into("<I", decoded, toff(SETUP_ENTRY_IMAGE), desc_va)
    # repoint the 5 submenu-table sites -> submenu table copy
    for low, high, r in SUB_SITES:
        assert u32at(low) == movw(r, SUB_TABLE & 0xffff), f"sub site {low:#x}"
        struct.pack_into("<I", decoded, toff(low), movw(r, sub_va & 0xffff))
        struct.pack_into("<I", decoded, toff(high), movt(r, sub_va >> 16))
    # bump the submenu constructor's start count 17 -> 18 (mov r2,#17 -> #18)
    assert u32at(SUB_CONSTRUCTOR_START) == 0xe3a02011, "sub constructor start"
    struct.pack_into("<I", decoded, toff(SUB_CONSTRUCTOR_START), 0xe3a02012)
    print(f'[bake] LAIN submenu: item {K} (id {old_id:#x}) -> submenu {LAIN_SUBMENU_ID:#x}; desc copy @ {desc_va:#x}, '
          f'sub table @ {sub_va:#x}, record 17 = "LAIN"->"DOOM"({LAIN_ID:#x})')

    # 1) verify + 2) apply the 7 patches
    for p in manifest["patches"]:
        o = off(p["address"])
        got = struct.unpack_from("<I", decoded, o)[0]
        assert got == p["expected"], f"patch site {p['address']:#x}: got {got:#010x} exp {p['expected']:#010x}"
        struct.pack_into("<I", decoded, o, p["replacement"])
        print(f"[bake] patch {p['address']:#x} {got:#010x} -> {p['replacement']:#010x}  ({p['reason']})")

    # 3) append [camera.bin][descriptor copy][submenu table copy] at the RTOS payload end
    insert_at = rtos["end"]
    assert insert_at == off(base)
    blob = camera_blob + dcopy + subtbl + tblob
    assert base + len(camera_blob) == desc_va and desc_va + len(dcopy) == sub_va
    decoded[insert_at:insert_at] = blob
    new_len = payload_len + len(blob)
    struct.pack_into("<I", decoded, hdr + 12, new_len)
    new_end_va = RTOS_BASE + new_len
    print(f"[bake] RTOS {payload_len} -> {new_len} (+{len(blob)}); new RTOS end VA {new_end_va:#x}")
    assert new_end_va <= NATIVE_DATA, f"RTOS would overlap native data at {NATIVE_DATA:#x}"
    print(f"[bake] headroom below 0x55000000: {NATIVE_DATA - new_end_va} bytes")

    # 4) fix checksum
    struct.pack_into("<I", decoded, len(decoded) - 4, 0)
    fix = (-sum32(decoded)) & 0xffffffff
    struct.pack_into("<I", decoded, len(decoded) - 4, fix)
    assert sum32(decoded) == 0

    # re-encode reusing original frames
    payload, rep = _repack_frames(raw, original_decoded, bytes(decoded), True)
    out = framed_container(raw, payload, bytes(decoded))
    print(f"[bake] repack reused {rep['reused_frames']} frames, +{rep['generated_literal_frames']} literal")
    open(OUT, "wb").write(out)
    print(f"[bake] wrote {OUT} ({len(out)} bytes, +{len(out)-len(raw)} vs orig) sha={hashlib.sha256(out).hexdigest()[:16]}")

    # 5) round-trip
    info2 = inspect_container(out)
    dec2, _ = decode_payload(out[128:info2["payload_end"]], info2["decoded_size"])
    assert sum32(dec2) == 0
    rows2 = {r["name"]: r for r in section_rows(dec2)}
    assert [r["name"] for r in section_rows(dec2)] == [r["name"] for r in section_rows(original_decoded)], "section set changed"
    r2 = rows2["RTOS"]; po2 = r2["start"] + 16
    # verify patches + camera head survived
    for p in manifest["patches"]:
        o = po2 + (p["address"] - RTOS_BASE)
        assert struct.unpack_from("<I", dec2, o)[0] == p["replacement"]
    camoff = po2 + payload_len
    assert dec2[camoff:camoff+len(camera)] == camera, "camera blob mismatch after round-trip"
    print(f"[bake] round-trip OK: patches + {len(camera)}-byte camera blob verified; sections intact")

if __name__ == "__main__":
    main()
