#!/usr/bin/env python3
"""
redir91.py - Prueba la hipotesis: `91 hh ll` es un SALTO RELATIVO.

En los bytes CRUDOS del .SZ (antes de negar), el codigo es `6F r1 r2`, donde
r1:r2 es un entero de 16 bits CON SIGNO, big-endian, y

        destino = P + 2 + desplazamiento        (P = posicion del codigo)

Es decir, el codigo vive en la DIRECCION ORIGINAL de una cadena japonesa
(las direcciones que el bytecode ya trae) y redirige al lugar real donde quedo
el texto ingles de esa cadena (antes o despues, dentro de +-32 KB).

Chequeos que hace:
  1. el destino cae dentro del archivo
  2. el destino es un INICIO DE CADENA (el byte anterior es 00)
  3. muestra el texto que hay en el destino para inspeccion visual
  4. con --jp: que fraccion de las posiciones de codigo coincide con inicios
     de cadena del archivo japones (el bytecode conserva esas direcciones)

Uso:
  python redir91.py "bin\\extraido_game1_en\\GAME18.SZ" --jp "bin\\extraido_game1_jp\\GAME18.SZ"

Solo stdlib.
"""
import argparse
import random
import sys

NEG = bytes((-i) & 0xFF for i in range(256))
PRINT = set(range(0x20, 0x7F))
TEXT_NEIGHBOR = PRINT | {0x00, 0x0A, 0x80}


def find_codes(buf, start):
    """Codigos `91 hh ll` en la vista negada (mismo filtro que codes91.py)."""
    n, codes, i = len(buf), [], start
    while i < n - 3:
        if buf[i] == 0x91 and buf[i - 1] in TEXT_NEIGHBOR \
                and (buf[i + 3] in TEXT_NEIGHBOR or buf[i + 3] == 0x91):
            r1, r2 = (-buf[i + 1]) & 0xFF, (-buf[i + 2]) & 0xFF
            disp = (r1 << 8) | r2
            if disp >= 0x8000:
                disp -= 0x10000
            codes.append((i, disp, i + 2 + disp))
            i += 3
        else:
            i += 1
    return codes


def preview(buf, pos, limit=56):
    out, i = [], pos
    while i < len(buf) and len(out) < limit:
        b = buf[i]
        if b == 0x00:
            out.append('<END>')
            break
        if b == 0x91 and i + 2 < len(buf):
            out.append('|')
            i += 3
            continue
        out.append(chr(b) if b in PRINT else ('/' if b == 0x0A else '~'))
        i += 1
    return ''.join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('infile')
    ap.add_argument('--jp', default=None, help='el mismo .SZ de la version JP')
    ap.add_argument('--start', type=lambda x: int(x, 0), default=0x2F40)
    ap.add_argument('--samples', type=int, default=6)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--exceptions', action='store_true',
                    help='listar TODOS los destinos que no son inicio de cadena, '
                         'con los 6 bytes previos (vista negada)')
    args = ap.parse_args()

    buf = open(args.infile, 'rb').read().translate(NEG)
    n = len(buf)
    codes = find_codes(buf, args.start)
    print(f"{args.infile}: {n} bytes; codigos 91: {len(codes)}\n")
    if not codes:
        return 0

    in_range = [c for c in codes if 0 <= c[2] < n]
    at_start = [c for c in in_range if c[2] == 0 or buf[c[2] - 1] == 0x00]
    fwd = sum(1 for c in codes if c[1] > 0)
    bwd = sum(1 for c in codes if c[1] < 0)
    print(f"destino dentro del archivo        : {len(in_range)}/{len(codes)} "
          f"({100.0 * len(in_range) / len(codes):.1f}%)")
    print(f"destino = inicio de cadena (00 antes): {len(at_start)}/{len(codes)} "
          f"({100.0 * len(at_start) / len(codes):.1f}%)")
    print(f"saltos hacia adelante / atras      : {fwd} / {bwd}")
    print(f"desplazamiento min / max           : "
          f"{min(c[1] for c in codes):+d} / {max(c[1] for c in codes):+d}")
    dsts = [c[2] for c in in_range]
    print(f"destinos en [0x{min(dsts):X} .. 0x{max(dsts):X}]\n")

    if args.jp:
        jp = open(args.jp, 'rb').read().translate(NEG)
        jp_starts = {i for i in range(args.start, len(jp)) if jp[i - 1] == 0x00}
        hit = sum(1 for c in codes if c[0] in jp_starts)
        print(f"posiciones de codigo que son inicio de cadena JP: {hit}/{len(codes)} "
              f"({100.0 * hit / len(codes):.1f}%)")
        code_pos = {c[0] for c in codes}
        jp_list = sorted(jp_starts)
        nocode = [a for a in jp_list if a not in code_pos and a < n]
        plain = [a for a in nocode if buf[a] in PRINT]
        print(f"inicios de cadena JP (posiciones tras un 00): {len(jp_list)}")
        print(f"  con codigo 91 en el EN : {len(jp_list) - len(nocode)}")
        print(f"  SIN codigo 91 en el EN : {len(nocode)}   "
              f"(de esas, EN[a] es caracter imprimible: {len(plain)})")
        if nocode:
            print("  muestra de las que no tienen codigo:  posicion  byte previo  texto EN ahi")
            rnd0 = random.Random(args.seed)
            show = nocode[:4] + (rnd0.sample(nocode[4:], min(4, len(nocode) - 4))
                                 if len(nocode) > 4 else [])
            for a in show:
                print(f"    0x{a:06X}   {buf[a - 1]:02X}   {preview(buf, a)!r}")
        print()

    if args.exceptions:
        exc = [c for c in codes
               if not (0 <= c[2] < n and (c[2] == 0 or buf[c[2] - 1] == 0))]
        print(f"destinos que NO son inicio de cadena: {len(exc)}")
        print("  codigo      desplaz.  destino    previos(6)         texto en el destino")
        for p, d, x in exc:
            prev = ' '.join(f'{b:02X}' for b in buf[max(0, x - 6):x])
            print(f"  0x{p:06X}  {d:+7d}  0x{x:06X}  {prev:<17}  {preview(buf, x)!r}")
        print()

    rnd = random.Random(args.seed)
    picks = codes[:args.samples]
    if len(codes) > 3 * args.samples:
        picks += rnd.sample(codes[args.samples:-args.samples], args.samples)
        picks += codes[-args.samples:]
    print("  codigo      desplaz.  destino    texto en el destino ('|' = codigo inline)")
    for p, d, x in picks:
        txt = preview(buf, x) if 0 <= x < n else '(fuera del archivo)'
        ok = '' if (0 <= x < n and (x == 0 or buf[x - 1] == 0)) else '  <-- no es inicio'
        print(f"  0x{p:06X}  {d:+7d}  0x{x:06X}  {txt!r}{ok}")
    return 0


if __name__ == '__main__':
    sys.exit(main())