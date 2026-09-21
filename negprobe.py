#!/usr/bin/env python3
"""
negprobe.py - Verifica si un .SZ de Policenauts NO esta comprimido sino
              codificado con "negacion de byte": b -> (256 - b) & 0xFF.

MOTIVO
------
slowbeef documenta (LP Policenauts, "ROMhacking Technicals: Part 2") que la
rutina de copia de texto del juego lee cada byte y ejecuta `subu r2, r0, r2`
(r2 = 0 - r2) antes de escribirlo al buffer de dibujo. Es decir, el texto en
memoria/disco esta "espejado": 'B' (0x42) se guarda como 0xBE, 'E' (0x45)
como 0xBB, 'Y' (0x59) como 0xA7. El 0x80 previo a cada caracter queda igual
(0 - 0x80 = 0x80).

Consecuencia: un detector que busque `0x80 + ASCII (0x20-0x7E)` NO ve nada
en el archivo crudo, porque lo que hay es `0x80 + (0x82..0xE0)`. Eso da
ascii80% ~ 0% aunque el archivo no este comprimido.

Este script prueba identidad vs negacion, con y sin prefijo 0x80, y ademas
da un mapa de entropia por sector para distinguir "comprimido de verdad"
(entropia ~7.9 bits/byte) de "bytecode + texto codificado" (mucho menor).

USO
---
  python3 negprobe.py GAME18.SZ
  python3 negprobe.py GAME18_JP.SZ --vs GAME18_EN.SZ     # compara cabeceras
  python3 negprobe.py GAME18.SZ --dump-neg GAME18.neg    # volcar negado, para
                                                         # usar con pntool.py scan
Solo stdlib.
"""
import argparse
import math
import sys
from collections import Counter

PRINTABLE = frozenset(range(0x20, 0x7F))
NEG_TABLE = bytes((-i) & 0xFF for i in range(256))


def neg(data):
    return data.translate(NEG_TABLE)


def entropy(data):
    if not data:
        return 0.0
    n = len(data)
    return -sum(v / n * math.log2(v / n) for v in Counter(data).values())


def runs_plain(data, minlen):
    """Rachas de ASCII imprimible de 1 byte por caracter."""
    runs, start = [], None
    for i, b in enumerate(data):
        if b in PRINTABLE:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= minlen:
                runs.append((start, i, data[start:i].decode('latin-1')))
            start = None
    if start is not None and len(data) - start >= minlen:
        runs.append((start, len(data), data[start:].decode('latin-1')))
    return runs


def runs_80(data, minlen):
    """Rachas de pares 0x80 <ascii>."""
    runs, chars, start, i, n = [], [], None, 0, len(data) - 1
    while i < n:
        if data[i] == 0x80 and data[i + 1] in PRINTABLE:
            if start is None:
                start, chars = i, []
            chars.append(chr(data[i + 1]))
            i += 2
            continue
        if start is not None and len(chars) >= minlen:
            runs.append((start, i, ''.join(chars)))
        start, chars = None, []
        i += 1
    if start is not None and len(chars) >= minlen:
        runs.append((start, i, ''.join(chars)))
    return runs


def summarize(runs, total):
    cov = sum(e - s for s, e, _ in runs)
    # Leccion del informe: mirar diversidad DENTRO de cada racha, no el total.
    diverse = sum(1 for _, _, t in runs if len(set(t)) >= 12)
    return {'runs': len(runs), 'cov': 100.0 * cov / max(1, total),
            'diverse': diverse}


def entropy_map(data, block=2048):
    ramp = " .:-=+*#%@"
    print(f"mapa de entropia por bloque de {block} bytes "
          f"(' '=baja ... '@'=~8 bits, tipico de dato comprimido):")
    line, per_line = '', 64
    for idx, off in enumerate(range(0, len(data), block)):
        h = entropy(data[off:off + block])
        line += ramp[min(9, int(h / 8 * 10))]
        if len(line) == per_line:
            print(f"  {idx - per_line + 1:4d} |{line}|")
            line = ''
    if line:
        print(f"  {idx - len(line) + 1:4d} |{line}|")


def header_compare(path_a, path_b):
    a, b = open(path_a, 'rb').read(), open(path_b, 'rb').read()
    delta = len(b) - len(a)
    print(f"A: {path_a}  {len(a)} bytes")
    print(f"B: {path_b}  {len(b)} bytes   (B - A = {delta:+d})\n")
    for name, d in (('A', a), ('B', b)):
        print(f"  {name} primeros 16: {' '.join(f'{x:02X}' for x in d[:16])}")
    print()
    if delta <= 0:
        print("B no es mas grande que A; comparo igual, pero el criterio de "
              "'campo == delta' pierde sentido.")
    cands = {}
    for div in (1, 2, 4, 8, 2048):
        if delta > 0 and delta % div == 0:
            cands[delta // div] = f"delta/{div}"
    hits = []
    limit = min(64, len(a), len(b))
    for width in (2, 4):
        for endian in ('little', 'big'):
            for off in range(0, limit - width + 1):
                va = int.from_bytes(a[off:off + width], endian)
                vb = int.from_bytes(b[off:off + width], endian)
                if va == vb:
                    continue
                d = vb - va
                if d in cands:
                    hits.append(f"  off {off:2d} w={width} {endian:6s}: "
                                f"{va} -> {vb}  (diferencia = {cands[d]})")
                if vb == len(b) and va == len(a):
                    hits.append(f"  off {off:2d} w={width} {endian:6s}: "
                                f"el campo es el TAMANO DEL ARCHIVO")
    if hits:
        print("campos de cabecera que cambian en sintonia con el tamano:")
        print('\n'.join(hits))
        print("\n>>> Si aparece uno, la cabecera lleva tamanos/conteos y el "
              "archivo es casi seguro NO comprimido (o al menos, con "
              "tamano descomprimido explicito).")
    else:
        print("ningun campo de los primeros 64 bytes acompana el cambio de "
              "tamano (con las divisiones probadas).")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('infile')
    ap.add_argument('--vs', default=None,
                    help='otro archivo (p.ej. la version EN) para comparar cabeceras')
    ap.add_argument('--minlen80', type=int, default=6,
                    help='largo minimo de racha 0x80+ascii, en caracteres')
    ap.add_argument('--minlen', type=int, default=8,
                    help='largo minimo de racha de ASCII plano (mas alto, porque el '
                         'azar produce mas rachas cortas de imprimibles)')
    ap.add_argument('--show', type=int, default=8,
                    help='cuantas rachas de muestra mostrar de la mejor variante')
    ap.add_argument('--dump-neg', default=None,
                    help='escribir el archivo completo negado (para pntool.py scan)')
    args = ap.parse_args()

    data = open(args.infile, 'rb').read()
    total = len(data)
    print(f"{args.infile}: {total} bytes")
    h = entropy(data)
    print(f"entropia global: {h:.3f} bits/byte "
          f"(dato comprimido/cifrado ~7.8-8.0; bytecode+texto suele ser < 7)\n")

    print("bytes mas frecuentes (en texto ingles negado dominan 0x80 y 0xE0=espacio):")
    for b, c in Counter(data).most_common(8):
        print(f"  0x{b:02X}  {c:7d}  ({100.0 * c / total:.1f}%)")
    print()

    variants = {}
    for label, buf in (('identidad', data), ('negado', neg(data))):
        variants[(label, '0x80+ascii')] = (buf, runs_80(buf, args.minlen80))
        variants[(label, 'ascii plano')] = (buf, runs_plain(buf, args.minlen))

    print(f"{'transformacion':<12} {'esquema':<12} {'rachas':>7} {'cobertura':>10} "
          f"{'rachas diversas':>16}")
    scored = []
    for (label, scheme), (buf, runs) in variants.items():
        s = summarize(runs, total)
        print(f"{label:<12} {scheme:<12} {s['runs']:>7} {s['cov']:>9.2f}% "
              f"{s['diverse']:>16}")
        scored.append((s['diverse'], s['cov'], label, scheme))
    scored.sort(reverse=True)
    best = scored[0]
    print()

    entropy_map(data)
    print()

    if best[0] >= 1 and best[1] >= 3.0:
        _, _, label, scheme = best
        print(f">>> HALLAZGO: '{label}' + '{scheme}' produce texto con rachas "
              f"diversas y {best[1]:.1f}% de cobertura.")
        if label == 'negado':
            print("    El archivo NO necesita descompresion: el texto esta "
                  "negado byte a byte.")
            print("    Confirmalo mirando las muestras de abajo (deberia ser "
                  "ingles legible / kanji-codes, no basura).")
        buf, runs = variants[(label, scheme)]
        print()
        for s, e, t in runs[:args.show]:
            show = t if len(t) <= 70 else t[:67] + '...'
            print(f"  0x{s:08X}  len={e - s:5d}  {show!r}")
        if len(runs) > args.show:
            print(f"  ... ({len(runs) - args.show} mas)")
    elif h > 7.6:
        print(">>> Ninguna variante da texto Y la entropia es alta: ahi si "
              "apunta a compresion (o cifrado) real. Mira el mapa de "
              "entropia: quiza solo parte del archivo esta comprimida.")
    else:
        print(">>> Entropia baja pero ninguna variante da texto legible: no "
              "parece comprimido, pero la codificacion es otra (otra "
              "constante de negacion, XOR, tabla propia...). Mira el "
              "histograma de bytes y proba con --vs sobre JP/EN.")

    if args.dump_neg:
        open(args.dump_neg, 'wb').write(neg(data))
        print(f"\narchivo negado volcado a {args.dump_neg}")
        print("  siguiente paso: python3 pntool.py scan "
              f"{args.dump_neg}   (autodetecta 0x80+ascii o ascii plano)")

    if args.vs:
        print("\n" + "=" * 60 + "\n")
        header_compare(args.infile, args.vs)
    return 0


if __name__ == '__main__':
    sys.exit(main())