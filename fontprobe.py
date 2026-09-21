#!/usr/bin/env python3
"""
fontprobe.py - Genera variantes de FONT.DPK para PROBAR en el emulador que
               archivo de fuente usa el juego para cada tipo de texto.

Formato de los archivos .MDB / .RB de la fuente (deducido, ver informe):
  bytes 0-3   tamano de la tabla en bytes (big-endian)          p.ej. 640
  bytes 4-7   tamano de la zona de datos de glifos (big-endian) p.ej. 3901
  desde 8     tabla de entradas de 4 bytes: [ancho][offset de 24 bits, big-endian]
              (las 95 primeras = ASCII 0x20-0x7E, con anchos proporcionales)
  despues     datos de glifos; luego (en KANJIFNT) los kanji, que no se tocan.

Modos:
  zero    pone en CERO los datos de los glifos de UN archivo (la tabla queda igual,
          asi el ancho de los caracteres se conserva y solo desaparece el dibujo).
          Si en el juego el texto de dialogo se ve en blanco, ese archivo es el
          que se usa para dialogos.

  randomize  pone BYTES ALEATORIOS (conocidos, con semilla fija) en los glifos de
          varias letras de los TRES archivos de fuente que usa la version inglesa
          (KPRFONT.MDB, KPRFONT.RB, KANJIFNT.RB), cada uno con datos distintos. Al ver
          en el juego como se dibujan esas letras se puede deducir como estan
          codificados los pixeles de los glifos proporcionales: con datos aleatorios,
          solo la disposicion correcta reproduce la imagen.

  assign     aisla las CAPAS: pone en cero el dibujo de TODAS las letras en los cuatro
          archivos y asigna cada letra de un texto a UN solo archivo con bytes
          aleatorios. Cada letra visible viene entonces de un origen conocido; una
          letra que queda en blanco indica que ese archivo no se usa para ese texto.

  masks      mapa de POSICIONES: para letras del MISMO ancho, unas letras encienden un
          byte de su dibujo si cierto bit de su numero de posicion vale 1 y otras
          encienden un solo campo de 2 bits de todos los bytes. Cada pixel encendido
          en pantalla dice a que byte y a que campo pertenece: se lee a simple vista y
          no depende de adivinar la disposicion.

Uso:
  python fontprobe.py zero "FONT.DPK" KPRFONT.MDB -o "FONT_sin_KPRMDB.DPK"
  python fontprobe.py masks "FONT_EN.DPK" -o "FONT_masks.DPK" --width 7
  python fontprobe.py randomize "FONT_EN.DPK" -o "FONT_probe.DPK"
  python fontprobe.py assign "FONT_EN.DPK" -o "FONT_menu.DPK" --text "New Game Continue Options" --files KPRFONT.RB KPRFONT.MDB

Recalcula el checksum del directorio (CRC-32/BZIP2). Solo stdlib.
"""
import argparse
import importlib.util
import os
import struct
import sys
import tempfile


def load_dpk_patch():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location('dpk_patch', os.path.join(here, 'dpk_patch.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PROBE_FILES = ('KPRFONT.MDB', 'KPRFONT.RB', 'KANJIFNT.RB')
# letras de la linea "This is a replica of the badge I had / in LAPD."; se dejan
# sin tocar a, e, i, s (y el resto) para poder ubicar el texto en la captura
PROBE_LETTERS = 'ADLPTIbdghprcoflt'


def probe_bytes(seed, fname, letter, n):
    import random
    rnd = random.Random(f"{seed}:{fname}:{letter}")
    return bytes(rnd.randrange(256) for _ in range(n))


def cmd_randomize(dp, args):
    data = open(args.dpk, 'rb').read()
    es = dp.parse(data)
    ents = {x['name']: x for x in es}
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for nm in PROBE_FILES:
            e = ents.get(nm)
            if e is None:
                print(f"falta {nm} en el contenedor")
                return 1
            blob = bytearray(data[e['off']:e['off'] + e['size']])
            ts, ds = struct.unpack_from('>II', blob, 0)
            n_ent = ts // 4
            done = []
            for ch in args.letters:
                i = ord(ch) - 0x20
                if not 0 <= i < n_ent:
                    continue
                w = blob[8 + 4 * i]
                off = int.from_bytes(blob[8 + 4 * i + 1:8 + 4 * i + 4], 'big')
                a = 8 + ts + off
                blob[a:a + 3 * w] = probe_bytes(args.seed, nm, ch, 3 * w)
                done.append(f"{ch}(w={w})")
            print(f"{nm}: {len(done)} letras con bytes aleatorios: {' '.join(done)}")
            open(os.path.join(td, nm), 'wb').write(bytes(blob))
        ns = argparse.Namespace(dpk=args.dpk, dir=td, out=args.out, force=False)
        return dp.cmd_repack(ns)


ALL_FILES = ('KPRFONT.RB', 'KPRFONT.MDB', 'KANJIFNT.RB', 'KANJIFNT.MDB')
DEFAULT_TEXT = "This is my baby, a Beretta 92F. I've used and since I was a kid."


def assign_plan(text, files=None):
    """letra -> archivo (reparto fijo y reproducible entre los archivos elegidos)."""
    files = tuple(files) if files else ALL_FILES
    letters = sorted({c for c in text if c != ' '})
    return {c: files[i % len(files)] for i, c in enumerate(letters)}


def cmd_assign(dp, args):
    data = open(args.dpk, 'rb').read()
    es = dp.parse(data)
    ents = {x['name']: x for x in es}
    plan = assign_plan(args.text, args.files)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for nm in ALL_FILES:
            e = ents.get(nm)
            if e is None:
                print(f"falta {nm}")
                return 1
            blob = bytearray(data[e['off']:e['off'] + e['size']])
            ts, ds = struct.unpack_from('>II', blob, 0)
            blob[8 + ts:8 + ts + ds] = bytes(ds)            # todas las letras en cero
            mine = []
            for ch, f in plan.items():
                if f != nm:
                    continue
                i = ord(ch) - 0x20
                if not 0 <= i < ts // 4:
                    continue
                w = blob[8 + 4 * i]
                off = int.from_bytes(blob[8 + 4 * i + 1:8 + 4 * i + 4], 'big')
                blob[8 + ts + off:8 + ts + off + 3 * w] = probe_bytes(args.seed, nm, ch, 3 * w)
                mine.append(f"{ch}(w={w})")
            print(f"{nm:<13}: {' '.join(mine)}")
            open(os.path.join(td, nm), 'wb').write(bytes(blob))
        ns = argparse.Namespace(dpk=args.dpk, dir=td, out=args.out, force=False)
        return dp.cmd_repack(ns)


MASK_TEXT = "This is my baby, a Beretta 92F. I've used and since I was a kid."
MASK_FILES = ('KPRFONT.RB', 'KPRFONT.MDB', 'KANJIFNT.RB')


def mask_plan(text, width, table):
    """Reparte las letras de ancho `width` del texto en roles: bit0..bitB-1 y campo0..3."""
    n = 3 * width
    nbits = max(1, (n - 1).bit_length())
    letters = []
    for c in text:
        if c != ' ' and c not in letters and table[ord(c) - 0x20] == width:
            letters.append(c)
    need = nbits + 4
    if len(letters) < need:
        return None, nbits, letters
    roles = {}
    for j in range(nbits):
        roles[letters[j]] = ('bit', j)
    for f in range(4):
        roles[letters[nbits + f]] = ('campo', f)
    return roles, nbits, letters


def mask_bytes(role, n):
    kind, j = role
    if kind == 'bit':
        return bytes(0xFF if (k >> j) & 1 else 0x00 for k in range(n))
    return bytes([(0xC0, 0x30, 0x0C, 0x03)[j]] * n)


def cmd_masks(dp, args):
    data = open(args.dpk, 'rb').read()
    es = dp.parse(data)
    ents = {x['name']: x for x in es}
    rb = data[ents['KPRFONT.RB']['off']:ents['KPRFONT.RB']['off'] + ents['KPRFONT.RB']['size']]
    ts0, _ = struct.unpack_from('>II', rb, 0)
    table = [rb[8 + 4 * i] for i in range(ts0 // 4)]
    roles, nbits, letters = mask_plan(args.text, args.width, table)
    if roles is None:
        print(f"el texto solo tiene {len(letters)} letras de ancho {args.width} y hacen falta "
              f"{nbits + 4} (bits + 4 campos): {' '.join(letters)}")
        return 1
    print(f"ancho {args.width}: {3 * args.width} bytes por glifo, {nbits} letras de bit y 4 de campo")
    for c, r in roles.items():
        print(f"  '{c}' -> {r[0]} {r[1]}")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for nm in ALL_FILES:
            e = ents[nm]
            blob = bytearray(data[e['off']:e['off'] + e['size']])
            ts, ds = struct.unpack_from('>II', blob, 0)
            blob[8 + ts:8 + ts + ds] = bytes(ds)                   # todo en cero
            if nm in MASK_FILES:
                for c, r in roles.items():
                    i = ord(c) - 0x20
                    w = blob[8 + 4 * i]
                    off = int.from_bytes(blob[8 + 4 * i + 1:8 + 4 * i + 4], 'big')
                    blob[8 + ts + off:8 + ts + off + 3 * w] = mask_bytes(r, 3 * w)
            open(os.path.join(td, nm), 'wb').write(bytes(blob))
        ns = argparse.Namespace(dpk=args.dpk, dir=td, out=args.out, force=False)
        return dp.cmd_repack(ns)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sp = sub.add_parser('zero', help='poner en cero los glifos de un archivo de fuente')
    sp.add_argument('dpk')
    sp.add_argument('name', help='KANJIFNT.MDB | KANJIFNT.RB | KPRFONT.MDB | KPRFONT.RB')
    sp.add_argument('-o', '--out', required=True)
    sp = sub.add_parser('randomize', help='bytes aleatorios conocidos en varias letras')
    sp.add_argument('dpk')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--seed', default='1234')
    sp.add_argument('--letters', default=PROBE_LETTERS)
    sp = sub.add_parser('assign', help='aislar capas: cada letra en UN solo archivo, el resto en cero')
    sp.add_argument('dpk')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--seed', default='1234')
    sp.add_argument('--text', default=DEFAULT_TEXT)
    sp.add_argument('--files', nargs='*', default=None,
                    help='repartir las letras solo entre estos archivos (los cuatro se ponen '
                         'en cero igual). Ej: --files KPRFONT.RB KPRFONT.MDB')
    sp = sub.add_parser('masks', help='mapa de posiciones con mascaras de bits')
    sp.add_argument('dpk')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--width', type=int, default=7)
    sp.add_argument('--text', default=MASK_TEXT)
    args = ap.parse_args()

    dp = load_dpk_patch()
    if args.cmd == 'masks':
        return cmd_masks(dp, args)
    if args.cmd == 'assign':
        return cmd_assign(dp, args)
    if args.cmd == 'randomize':
        return cmd_randomize(dp, args)
    data = open(args.dpk, 'rb').read()
    es = dp.parse(data)
    e = next((x for x in es if x['name'].upper() == args.name.upper()), None)
    if e is None:
        print(f"no hay una entrada llamada {args.name!r}; hay: {', '.join(x['name'] for x in es)}")
        return 1
    blob = bytearray(data[e['off']:e['off'] + e['size']])
    ts, ds = struct.unpack_from('>II', blob, 0)
    a, b = 8 + ts, 8 + ts + ds
    if not (0 < ts < 4096 and 0 < ds < len(blob) and b <= len(blob)):
        print(f"cabecera inesperada (tabla={ts}, datos={ds}); no toco nada")
        return 1
    nz = sum(1 for x in blob[a:b] if x)
    blob[a:b] = bytes(b - a)
    print(f"{e['name']}: tabla={ts} B ({ts // 4} entradas), datos de glifos {ds} B "
          f"(0x{a:X}..0x{b:X}); {nz} bytes no nulos puestos en cero")
    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, e['name']), 'wb').write(bytes(blob))
        ns = argparse.Namespace(dpk=args.dpk, dir=td, out=args.out, force=False)
        return dp.cmd_repack(ns)


if __name__ == '__main__':
    sys.exit(main())