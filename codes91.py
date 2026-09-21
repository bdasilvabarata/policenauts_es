#!/usr/bin/env python3
"""
codes91.py - Lista los codigos de 3 bytes `91 hh ll` (hh:ll = entero de 16 bits
             big-endian) que aparecen DENTRO del texto de un .SZ, en la vista
             NEGADA (la misma que usa hexat.py / pntool.py sobre el .neg).

Contexto (observado en GAME18.SZ EN): el texto se corta a mitad de palabra por
estos codigos ("This is your stand" 91 01 14 "ard type of..."), y el texto
sigue inmediatamente despues del codigo. Sus valores cambian de forma
sistematica a lo largo del archivo (0x00FF..0x012A al principio, valores
"negativos" como 0xFCBF a mitad, 0xE4EE cerca del final), lo que sugiere un
desplazamiento acumulado entre direcciones originales (JP) y reales (EN).

Este script te deja verlo entero y contrastarlo con el crecimiento del archivo:

  python codes91.py "bin\\extraido_game1_en\\GAME18.SZ" --growth 9920

Solo stdlib.
"""
import argparse
import sys

NEG = bytes((-i) & 0xFF for i in range(256))
PRINT = set(range(0x20, 0x7F))
TEXT_NEIGHBOR = PRINT | {0x00, 0x0A, 0x80}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('infile')
    ap.add_argument('--start', type=lambda x: int(x, 0), default=0x2F40,
                    help='donde empieza el texto (antes hay bytecode; def 0x2F40)')
    ap.add_argument('--growth', type=int, default=None,
                    help='diferencia de tamano EN-JP del archivo, para comparar')
    ap.add_argument('--csv', default=None, help='volcar todos los codigos a CSV')
    ap.add_argument('--show', type=int, default=12,
                    help='cuantos codigos mostrar al principio y al final')
    args = ap.parse_args()

    buf = open(args.infile, 'rb').read().translate(NEG)
    n = len(buf)
    codes, i = [], args.start
    while i < n - 3:
        if buf[i] == 0x91 and buf[i - 1] in TEXT_NEIGHBOR \
                and (buf[i + 3] in TEXT_NEIGHBOR or buf[i + 3] == 0x91):
            v = (buf[i + 1] << 8) | buf[i + 2]
            sv = v - 0x10000 if v >= 0x8000 else v
            codes.append((i, v, sv))
            i += 3
        else:
            i += 1

    print(f"{args.infile}: {n} bytes; texto desde 0x{args.start:X}")
    print(f"codigos 91 hh ll encontrados: {len(codes)}\n")
    if not codes:
        return 0

    def row(c):
        p, v, sv = c
        return f"  0x{p:06X}   0x{v:04X}   {sv:+7d}"

    print("  posicion   valor    con signo")
    head = codes[:args.show]
    tail = codes[-args.show:] if len(codes) > args.show * 2 else codes[args.show:]
    for c in head:
        print(row(c))
    if len(codes) > 2 * args.show:
        print("  ...")
    for c in tail:
        print(row(c))

    svs = [c[2] for c in codes]
    print(f"\nprimer valor: {svs[0]:+d}   ultimo valor: {svs[-1]:+d}   "
          f"minimo: {min(svs):+d}   maximo: {max(svs):+d}")
    ups = sum(1 for a, b in zip(svs, svs[1:]) if b > a)
    downs = sum(1 for a, b in zip(svs, svs[1:]) if b < a)
    print(f"pasos hacia arriba: {ups}   hacia abajo: {downs}")
    if args.growth is not None:
        print(f"\ncrecimiento EN-JP del archivo: {args.growth:+d}   "
              f"(-crecimiento = {-args.growth:+d})")
        print(f"ultimo valor - (-crecimiento) = {svs[-1] + args.growth:+d}")
        print("Si la hipotesis 'desplazamiento acumulado' es correcta, el ultimo\n"
              "valor deberia quedar cerca de -crecimiento.")

    if args.csv:
        with open(args.csv, 'w', encoding='utf-8') as fh:
            fh.write("pos,valor,con_signo\n")
            for p, v, sv in codes:
                fh.write(f"0x{p:X},0x{v:04X},{sv}\n")
        print(f"\nvolcado a {args.csv}")
    return 0


if __name__ == '__main__':
    sys.exit(main())