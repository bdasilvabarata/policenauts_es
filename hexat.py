#!/usr/bin/env python3
"""
hexat.py - Visor hex con la MISMA vista que 'pntool.py scan/gaps' sobre el
           archivo .neg: por defecto muestra los bytes NEGADOS (b -> 256-b),
           asi el ingles sale legible en la columna ASCII y los valores
           coinciden con los de la salida de 'gaps'.

Uso:
  python hexat.py GAME18.SZ 0x2F40 192            # 192 bytes desde 0x2F40
  python hexat.py GAME18.SZ 0x2F40 192 --raw      # bytes crudos, sin negar
  python hexat.py GAME18.SZ --find "80 23 80"     # buscar patron (en vista negada)
  python hexat.py GAME18.SZ --find "91 f6" --max 5 --ctx 24 --after 40

Nota: sobre el archivo .SZ crudo o sobre el .neg da lo mismo si NO pasas
--raw (el script niega por su cuenta). Pasale siempre el .SZ original.
Solo stdlib.
"""
import argparse
import sys

NEG = bytes((-i) & 0xFF for i in range(256))


def show(buf, start, length):
    start = max(0, start)
    end = min(len(buf), start + length)
    for off in range(start & ~0xF, end, 16):
        cells, asc = [], ''
        for i in range(16):
            pos = off + i
            if pos < start or pos >= end:
                cells.append('  ')
                asc += ' '
            else:
                b = buf[pos]
                cells.append(f'{b:02X}')
                asc += chr(b) if 0x20 <= b < 0x7F else '.'
        print(f"{off:08X}  {' '.join(cells[:8])}  {' '.join(cells[8:])}  |{asc}|")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('infile')
    ap.add_argument('offset', nargs='?', type=lambda x: int(x, 0), default=None)
    ap.add_argument('length', nargs='?', type=lambda x: int(x, 0), default=128)
    ap.add_argument('--raw', action='store_true',
                    help='mostrar bytes crudos en vez de negados')
    ap.add_argument('--find', default=None,
                    help='patron hex a buscar, en la vista elegida (negada por defecto)')
    ap.add_argument('--max', type=int, default=6, help='maximo de coincidencias a mostrar')
    ap.add_argument('--ctx', type=int, default=16, help='bytes de contexto antes')
    ap.add_argument('--after', type=int, default=32, help='bytes de contexto despues')
    args = ap.parse_args()

    data = open(args.infile, 'rb').read()
    buf = data if args.raw else data.translate(NEG)
    view = 'CRUDA' if args.raw else 'NEGADA'
    print(f"{args.infile}: {len(data)} bytes   vista {view}\n")

    if args.find:
        try:
            pat = bytes.fromhex(args.find.replace(' ', ''))
        except ValueError:
            print("patron hex invalido (ej: \"91 f6\")")
            return 1
        matches, pos = [], 0
        while True:
            m = buf.find(pat, pos)
            if m < 0:
                break
            matches.append(m)
            pos = m + 1
        print(f"patron {pat.hex(' ')}: {len(matches)} coincidencias "
              f"(mostrando {min(len(matches), args.max)})\n")
        for m in matches[:args.max]:
            print(f"-- coincidencia en 0x{m:X}")
            show(buf, m - args.ctx, args.ctx + len(pat) + args.after)
            print()
        return 0

    if args.offset is None:
        print("falta OFFSET (o usa --find)")
        return 1
    show(buf, args.offset, args.length)
    return 0


if __name__ == '__main__':
    sys.exit(main())
