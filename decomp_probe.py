#!/usr/bin/env python3
"""
decomp_probe.py - Prueba variantes comunes de LZSS sobre un archivo .SZ y
                   puntua cada resultado por cuanto se parece a texto real
                   (cobertura del esquema 0x80+ascii que ya conocemos).

No asume el algoritmo exacto: prueba una matriz de parametros razonables
(orden de bits de la bandera, ancho de offset/largo, MSB/LSB) y te dice cual
combinacion, si alguna, produce una salida plausible. Si NINGUNA da bien,
es evidencia de que no es LZSS clasico y hay que buscar otra familia
(LZ77 puro, RLE, Huffman, algo propietario).

Uso:
  python3 decomp_probe.py GAME18.SZ
  python3 decomp_probe.py GAME18.SZ --skip-header 4   # saltar firma+campo
  python3 decomp_probe.py GAME18.SZ --dump-best out.bin
"""
import argparse

PRINTABLE = set(range(0x20, 0x7F))


def ascii80_ratio(data, sample=1 << 18):
    d = data[:sample]
    if not d:
        return 0.0
    hits, i, n = 0, 0, len(d) - 1
    while i < n:
        if d[i] == 0x80 and d[i + 1] in PRINTABLE:
            hits += 2
            i += 2
        else:
            i += 1
    return 100.0 * hits / len(d)


def find_ascii80_runs(data, minlen_chars=6):
    """Encuentra rachas del patron 0x80+ascii y devuelve el texto de cada una."""
    runs = []
    i, n = 0, len(data) - 1
    start, chars = None, []
    while i < n:
        if data[i] == 0x80 and data[i + 1] in PRINTABLE:
            if start is None:
                start, chars = i, []
            chars.append(chr(data[i + 1]))
            i += 2
        else:
            if start is not None and len(chars) >= minlen_chars:
                runs.append(''.join(chars))
            start, chars = None, []
            i += 1
    if start is not None and len(chars) >= minlen_chars:
        runs.append(''.join(chars))
    return runs


def ascii80_diversity(data, sample=1 << 18):
    """Diversidad REAL de texto: de las rachas de largo razonable (>=6
    caracteres), cuantas tienen buena variedad interna de caracteres (no
    son solo un patron corto repetido como '@@@@@@@'). Un archivo lleno de
    bucles LZSS degenerados puede tener muchos caracteres distintos SUMADOS
    entre rachas distintas sin que ninguna racha individual sea texto real;
    por eso hay que mirar la diversidad DENTRO de cada racha, no el total
    del archivo."""
    d = data[:sample]
    runs = find_ascii80_runs(d)
    if not runs:
        return 0.0
    good_runs = 0
    for r in runs:
        distinct = len(set(r))
        # texto real de prosa satura entre 20 y 40 caracteres distintos
        # (letras + espacio + puntuacion) sin importar el largo de la racha;
        # un bucle degenerado se estanca en 1-4 sin importar cuanto crezca
        if distinct >= 12:
            good_runs += 1
    return good_runs  # cantidad de rachas que parecen texto real, no bucles


def printable_ratio(data, sample=1 << 18):
    d = data[:sample]
    if not d:
        return 0.0
    return 100.0 * sum(1 for b in d if b in PRINTABLE or b in (0, 0x0A, 0x0D)) / len(d)


def lzss_decode(data, flag_lsb_first, offset_bits, length_bits, min_match,
                 offset_from_end, max_out, swap_fields=False, invert_literal=False,
                 pair_big_endian=False):
    """LZSS generico parametrizable. Devuelve (salida, bytes_de_entrada_consumidos).
    swap_fields=True prueba el orden [largo][offset] en vez de [offset][largo]
    dentro del word empaquetado de 16 bits.
    invert_literal=True prueba la convencion bit=0 -> literal (algunas
    implementaciones usan 1=literal, otras 1=coincidencia).
    pair_big_endian=True prueba el par offset+largo en big-endian en vez de
    little-endian (nunca probado hasta ahora, dimension separada del orden
    de bits del BYTE DE FLAGS)."""
    out = bytearray()
    n = len(data)
    pos = 0
    pair_bytes = (offset_bits + length_bits + 7) // 8
    if pair_bytes != 2:
        return None, 0

    while pos < n and len(out) < max_out:
        if pos >= n:
            break
        flags = data[pos]
        pos += 1
        bit_iter = range(8) if flag_lsb_first else range(7, -1, -1)
        for bit_i in bit_iter:
            if pos >= n or len(out) >= max_out:
                break
            bit_val = (flags >> bit_i) & 1
            is_literal = (not bit_val) if invert_literal else bool(bit_val)
            if is_literal:
                out.append(data[pos])
                pos += 1
            else:
                if pos + 1 >= n:
                    return bytes(out), pos
                if pair_big_endian:
                    packed = (data[pos] << 8) | data[pos + 1]
                else:
                    packed = data[pos] | (data[pos + 1] << 8)
                pos += 2
                if swap_fields:
                    length = (packed & ((1 << length_bits) - 1)) + min_match
                    offset = packed >> length_bits
                else:
                    offset = packed & ((1 << offset_bits) - 1)
                    length = (packed >> offset_bits) + min_match
                if offset_from_end:
                    src = len(out) - offset - 1
                else:
                    src = offset
                if src < 0:
                    continue
                for k in range(length):
                    if src + k < 0 or src + k >= len(out):
                        break
                    out.append(out[src + k])
    return bytes(out), pos


def try_all(data, max_out):
    results = []
    for flag_lsb in (True, False):
        for off_bits, len_bits in ((12, 4), (11, 5), (10, 6), (13, 3)):
            for min_match in (2, 3):
                for off_end in (True, False):
                    for swap in (False, True):
                        for invert in (False, True):
                            for pair_be in (False, True):
                                try:
                                    out, consumed = lzss_decode(
                                        data, flag_lsb, off_bits, len_bits,
                                        min_match, off_end, max_out, swap,
                                        invert, pair_be)
                                except Exception:
                                    out = None
                                if not out or len(out) < 32:
                                    continue
                                score_text = ascii80_ratio(out)
                                diversity = ascii80_diversity(out)
                                score_print = printable_ratio(out)
                                results.append({
                                    'flag_lsb': flag_lsb, 'off_bits': off_bits,
                                    'len_bits': len_bits, 'min_match': min_match,
                                    'off_end': off_end, 'swap': swap,
                                    'invert': invert, 'pair_be': pair_be,
                                    'out_len': len(out), 'consumed': consumed,
                                    'total_in': len(data),
                                    'ascii80': score_text, 'diversity': diversity,
                                    'printable': score_print,
                                })
    results.sort(key=lambda r: (-r['diversity'], -r['ascii80']))
    return results


def cmd_single(args, data):
    off_bits, len_bits = args.bits.split('+')
    off_bits, len_bits = int(off_bits), int(len_bits)
    out, consumed = lzss_decode(data, args.flag == 'lsb', off_bits, len_bits,
                                args.min_match, args.off_end, args.max_out,
                                args.swap, args.invert, args.pair_be)
    total = len(data)
    print(f"parametros: flag={args.flag} off={off_bits} len={len_bits} "
          f"min={args.min_match} off_end={args.off_end} swap={args.swap} "
          f"invert={args.invert} pair_be={args.pair_be}\n")
    print(f"entrada consumida : {consumed} / {total} bytes "
          f"({100.0*consumed/total:.1f}%)")
    print(f"salida producida   : {len(out)} bytes "
          f"{'<-- TOCO EL TOPE --max-out, subilo' if len(out) >= args.max_out else '(termino sola, no toco el limite)'}")
    print(f"ascii80%           : {ascii80_ratio(out):.2f}%")
    print(f"diversidad         : {ascii80_diversity(out)} caracteres distintos "
          f"{'(sospechoso, 0 = probable patron degenerado, sin rachas de texto real)' if ascii80_diversity(out) < 1 else ''}")
    print(f"printable%         : {printable_ratio(out):.2f}%")
    if consumed < total:
        faltante = total - consumed
        print(f"\nOJO: quedaron {faltante} bytes de entrada SIN CONSUMIR.")
        print("Si el formato fuera correcto, deberia consumir el 100% del")
        print("stream (o casi). Que se corte antes es señal de que, en algun")
        print("punto intermedio, la decodificacion se desincronizo aunque el")
        print("principio pintara bien.")
    if args.dump_best:
        open(args.dump_best, 'wb').write(out)
        print(f"\nvolcado a {args.dump_best}")



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('infile')
    ap.add_argument('--skip-header', type=int, default=4,
                    help='bytes de firma/campo a saltar antes del stream comprimido')
    ap.add_argument('--max-out', type=int, default=1 << 17,
                    help='limite de bytes de salida por intento (seguridad)')
    ap.add_argument('--top', type=int, default=10)
    ap.add_argument('--dump-best', default=None,
                    help='si se da, escribe la mejor decodificacion a este archivo')
    ap.add_argument('--single', action='store_true',
                    help='correr UNA configuracion especifica (no el barrido) '
                         'con --max-out grande, para confirmar si termina sola')
    ap.add_argument('--flag', choices=['lsb', 'msb'], default='lsb')
    ap.add_argument('--bits', default='12+4', help='formato OFFSET+LARGO, ej 12+4')
    ap.add_argument('--min-match', type=int, default=2)
    ap.add_argument('--off-end', action='store_true')
    ap.add_argument('--swap', action='store_true',
                    help='probar el orden [largo][offset] en vez de [offset][largo]')
    ap.add_argument('--invert', action='store_true',
                    help='probar bit=0 -> literal (convencion invertida)')
    ap.add_argument('--pair-be', action='store_true',
                    help='probar el par offset+largo en big-endian')
    args = ap.parse_args()

    raw = open(args.infile, 'rb').read()
    data = raw[args.skip_header:]

    if args.single:
        print(f"{args.infile}: {len(raw)} bytes totales, stream desde "
              f"offset {args.skip_header} ({len(data)} bytes)\n")
        cmd_single(args, data)
        return 0

    print(f"{args.infile}: {len(raw)} bytes totales, probando desde "
          f"offset {args.skip_header} ({len(data)} bytes de stream)\n")

    results = try_all(data, args.max_out)
    if not results:
        print("Ninguna variante de LZSS produjo salida utilizable.")
        print("Esto es evidencia (no prueba) de que el formato no es un LZSS")
        print("clasico de 16 bits. Proximo paso: mirar si hay un arbol Huffman")
        print("(cabecera con tabla de frecuencias) o RLE simple.")
        return 1

    print(f"{'flag':>6} {'off':>4} {'len':>4} {'min':>4} {'offend':>7} "
          f"{'swap':>5} {'inv':>4} {'pbe':>4} {'salida':>8} {'divers':>7} {'ascii80%':>9} {'imprim%':>9}")
    for r in results[:args.top]:
        print(f"{'lsb' if r['flag_lsb'] else 'msb':>6} {r['off_bits']:>4} "
              f"{r['len_bits']:>4} {r['min_match']:>4} "
              f"{'si' if r['off_end'] else 'no':>7} "
              f"{'si' if r['swap'] else 'no':>5} "
              f"{'si' if r['invert'] else 'no':>4} "
              f"{'si' if r['pair_be'] else 'no':>4} {r['out_len']:>8} "
              f"{r['diversity']:>7} {r['ascii80']:>8.2f}% {r['printable']:>8.2f}%")

    best = results[0]
    print()
    if best['diversity'] >= 1 and best['ascii80'] > 3.0:
        print(">>> HAY UNA VARIANTE PROMETEDORA (diversidad alta = no es un")
        print("    patron degenerado tipo '@@@@@'). Revisa el dump con")
        print("    --dump-best y 'pntool.py scan' sobre el resultado.")
    elif best['diversity'] < 1:
        print(">>> Sospechoso: la mejor variante tiene POCA diversidad de")
        print("    caracteres (probablemente un patron repetido tipo '@@@@',")
        print("    no texto real). Es casi seguro un falso positivo aunque")
        print("    consuma bien el stream. Segui probando otras filas de")
        print("    la tabla, o valores de --skip-header.")
    elif best['printable'] > 60.0:
        print(">>> Ninguna parece texto en el esquema 0x80+ascii, pero alguna")
        print("    da alto % de bytes imprimibles simples. Puede ser un ")
        print("    encoding distinto (ascii plano, sin 0x80). Revisa a mano.")
    else:
        print(">>> Ninguna variante parece texto reconocible. Probablemente")
        print("    NO es LZSS clasico de 16 bits, o el --skip-header esta mal.")
        print("    Proba otros valores de --skip-header (0, 2, 6, 8).")

    if args.dump_best:
        out, _ = lzss_decode(data, best['flag_lsb'], best['off_bits'],
                          best['len_bits'], best['min_match'],
                          best['off_end'], args.max_out, best['swap'],
                          best['invert'], best['pair_be'])
        open(args.dump_best, 'wb').write(out)
        print(f"\nmejor resultado volcado a {args.dump_best}")
    return 0


if __name__ == '__main__':
    main()