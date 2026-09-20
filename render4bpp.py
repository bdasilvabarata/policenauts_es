#!/usr/bin/env python3
"""
render4bpp.py - Interpreta un archivo como: 32 bytes de paleta (16 colores
                 BGR555 tipo TIM de PSX) + datos de 4 bits por pixel, y
                 lo renderiza en PNG para inspeccion visual.

Requiere: pip install pillow --break-system-packages

Uso:
  python3 render4bpp.py NAMEID1.DAT --out salida.png --width 64
  # si no sabes el ancho, --grid genera una hoja de contacto con varios anchos
  python3 render4bpp.py NAMEID1.DAT --grid --out grilla.png
"""
import argparse
from PIL import Image


def bgr555_to_rgb(v):
    r = (v & 0x1F) << 3
    g = ((v >> 5) & 0x1F) << 3
    b = ((v >> 10) & 0x1F) << 3
    return (r, g, b)


def load_palette(data):
    pal = []
    for i in range(16):
        v = int.from_bytes(data[i*2:i*2+2], 'little')
        pal.append(bgr555_to_rgb(v))
    return pal


def decode(data, width, header_size, nibble_order='high_first', gray=False):
    palette_raw = data[:header_size]
    pixel_data = data[header_size:]
    pal = None if gray else load_palette(palette_raw)

    nibbles = []
    for b in pixel_data:
        hi, lo = (b >> 4) & 0xF, b & 0xF
        if nibble_order == 'high_first':
            nibbles += [hi, lo]
        else:
            nibbles += [lo, hi]

    h = max(1, len(nibbles) // width)
    mode = 'L' if gray else 'RGB'
    img = Image.new(mode, (width, h))
    px = img.load()
    for i, n in enumerate(nibbles[:width * h]):
        x, y = i % width, i // width
        px[x, y] = (n * 17) if gray else pal[n]
    return img


def get_nibbles(data, header_size, nibble_order):
    pixel_data = data[header_size:]
    nibbles = []
    for b in pixel_data:
        hi, lo = (b >> 4) & 0xF, b & 0xF
        if nibble_order == 'high_first':
            nibbles += [hi, lo]
        else:
            nibbles += [lo, hi]
    return nibbles


def row_uniformity(nibbles, width, row_idx):
    """Que tan 'pareja' (un solo valor domina) es una fila. 1.0 = perfecta."""
    row = nibbles[row_idx*width:(row_idx+1)*width]
    if not row:
        return 0.0
    from collections import Counter
    c = Counter(row)
    return c.most_common(1)[0][1] / len(row)


def autocorrelation(signal, lag):
    n = len(signal) - lag
    if n < 5:
        return 0.0
    mean = sum(signal) / len(signal)
    num = sum((signal[i] - mean) * (signal[i + lag] - mean) for i in range(n))
    den = sum((s - mean) ** 2 for s in signal)
    return num / den if den else 0.0


def score_width(nibbles, width, min_rows=10):
    """Puntua un ancho candidato buscando el periodo (alto de caja) que
    maximiza la autocorrelacion de la señal de uniformidad por fila.
    Mas robusto que buscar un 'gap' fijo entre filas-borde: no depende de
    un umbral binario ni de que el borde superior/inferior este siempre
    separado por el mismo numero de filas."""
    n_rows = len(nibbles) // width
    if n_rows < min_rows:
        return 0.0, None, []
    unif = [row_uniformity(nibbles, width, r) for r in range(n_rows)]
    best_lag, best_corr = None, -1.0
    for lag in range(6, max(7, n_rows // 2)):
        c = autocorrelation(unif, lag)
        if c > best_corr:
            best_corr, best_lag = c, lag
    return best_corr, best_lag, unif


def cmd_auto_width(data, header_size, nibble_order, w_min, w_max):
    print("[experimental: esta heuristica da pistas, no certezas. "
          "La confirmacion real es mirar la imagen.]\n")
    nibbles = get_nibbles(data, header_size, nibble_order)
    print(f"probando anchos {w_min}..{w_max} sobre {len(nibbles)} nibbles\n")
    results = []
    for w in range(w_min, w_max + 1):
        score, lag, unif = score_width(nibbles, w)
        if score > 0.15:
            results.append((score, w, lag))
    results.sort(key=lambda r: -r[0])
    if not results:
        print("Ninguno de los anchos probados muestra un patron periodico")
        print("claro. Puede que no haya un patron de caja regular, o que")
        print("--header-size / --order este mal.")
        return None
    print(f"{'ancho':>6} {'alto caja':>10} {'score':>8}")
    for score, w, lag in results[:10]:
        print(f"{w:>6} {lag:>10} {score:>8.3f}")
    best_w = results[0][1]
    print(f"\n>>> Mejor candidato: ancho = {best_w}, alto de caja ~ {results[0][2]}")
    print("    Confirmalo visualmente con --width ese valor (sin --grid).")
    return best_w


def cmd_find_boundary(data, header_size, nibble_order, width):
    nibbles = get_nibbles(data, header_size, nibble_order)
    n_rows = len(nibbles) // width
    unif = [row_uniformity(nibbles, width, r) for r in range(n_rows)]
    border_rows = [i for i, u in enumerate(unif) if u >= 0.85]
    if len(border_rows) < 4:
        print("Muy pocas filas-borde detectadas; no puedo estimar el limite.")
        return
    from collections import Counter
    gaps = [border_rows[i+1] - border_rows[i] for i in range(len(border_rows)-1)]
    period, _ = Counter(gaps).most_common(1)[0]
    # avanzar mientras el patron de borde se sostenga cada 'period' filas
    last_good = border_rows[0]
    i = 0
    while i < len(border_rows) - 1:
        if border_rows[i+1] - border_rows[i] == period:
            last_good = border_rows[i+1]
            i += 1
        else:
            break
    boundary_row = last_good + 1
    boundary_nibble = boundary_row * width
    boundary_byte = header_size + boundary_nibble // 2
    n_boxes = (boundary_row) // period if period else 0
    print(f"periodo de caja detectado: {period} filas")
    print(f"patron de borde se mantiene hasta la fila {last_good} "
          f"(~{n_boxes} cajas completas)")
    print(f"limite estimado raster/no-raster: byte 0x{boundary_byte:X} "
          f"({boundary_byte} decimal)")
    print(f"\nDespues de ese offset, lo que sigue del archivo probablemente")
    print(f"NO es bitmap con este ancho: es otra estructura (tabla, indice,")
    print(f"u otro bloque con dimensiones distintas). Investigalo aparte.")


def cmd_detect_height(data, header_size, nibble_order, width, max_period):
    nibbles = get_nibbles(data, header_size, nibble_order)
    n_rows = len(nibbles) // width
    unif = [row_uniformity(nibbles, width, r) for r in range(n_rows)]
    print(f"ancho fijo = {width}, {n_rows} filas totales\n")
    print(f"{'periodo':>8} {'autocorr':>10}")
    scored = []
    for lag in range(3, min(max_period, n_rows // 3)):
        c = autocorrelation(unif, lag)
        scored.append((c, lag))
    scored.sort(key=lambda x: -x[0])
    for c, lag in scored[:10]:
        print(f"{lag:>8} {c:>10.3f}")
    best_period = scored[0][1]
    n_entries = n_rows // best_period
    print(f"\n>>> periodo mas probable: {best_period} filas")
    print(f"    {n_rows} filas / {best_period} = {n_entries} entradas "
          f"({n_rows % best_period} filas sobrantes)")
    return best_period


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('infile')
    ap.add_argument('--out', default='salida.png')
    ap.add_argument('--width', type=int, default=32)
    ap.add_argument('--header-size', type=int, default=32,
                    help='bytes de paleta al inicio (0 si no hay paleta)')
    ap.add_argument('--order', default='high_first',
                    choices=['high_first', 'low_first'])
    ap.add_argument('--gray', action='store_true',
                    help='ignorar paleta, mapear nibble directo a gris')
    ap.add_argument('--scale', type=int, default=8)
    ap.add_argument('--max-rows', type=int, default=None,
                    help='limitar a N filas (imagen chica, para que no se '
                         'degrade al subirla al chat)')
    ap.add_argument('--start-row', type=int, default=0,
                    help='arrancar a mostrar desde esta fila (para ver '
                         'tramos intermedios/finales del archivo)')
    ap.add_argument('--grid', action='store_true',
                    help='generar una hoja con varios anchos candidatos')
    ap.add_argument('--auto-width', action='store_true',
                    help='detectar el ancho buscando filas de borde periodicas')
    ap.add_argument('--width-range', default='8-160',
                    help='rango a probar con --auto-width, formato MIN-MAX')
    ap.add_argument('--find-boundary', action='store_true',
                    help='con --width fijo, encontrar donde termina el raster')
    ap.add_argument('--detect-height', action='store_true',
                    help='con --width fijo (ya confirmado), detectar la '
                         'altura de caja / periodo de entrada')
    ap.add_argument('--max-period', type=int, default=60)
    args = ap.parse_args()

    data = open(args.infile, 'rb').read()
    print(f"{args.infile}: {len(data)} bytes")

    if args.auto_width:
        w_min, w_max = (int(x) for x in args.width_range.split('-'))
        best = cmd_auto_width(data, args.header_size, args.order, w_min, w_max)
        if best and not args.grid:
            return
        if best:
            args.width = best

    if args.find_boundary:
        cmd_find_boundary(data, args.header_size, args.order, args.width)
        return

    if args.detect_height:
        cmd_detect_height(data, args.header_size, args.order, args.width,
                          args.max_period)
        return


    if args.grid:
        widths = [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 128]
        imgs = []
        for w in widths:
            for order in ['high_first', 'low_first']:
                im = decode(data, w, args.header_size, order, args.gray)
                im = im.resize((w * 3, im.height * 3), Image.NEAREST)
                imgs.append((f"w={w} {order}", im))
        pad = 20
        total_h = sum(im.height + pad for _, im in imgs)
        max_w = max(im.width for _, im in imgs) + pad
        sheet = Image.new('RGB', (max_w, total_h), (40, 40, 40))
        y = 0
        for label, im in imgs:
            sheet.paste(im.convert('RGB'), (5, y))
            y += im.height + pad
        sheet.save(args.out)
        print(f"grilla con {len(imgs)} variantes -> {args.out}")
        print("abrila y buscá cual ancho muestra formas reconocibles "
              "(letras, sprites); ese es tu --width real.")
    else:
        img = decode(data, args.width, args.header_size, args.order, args.gray)
        total_rows = img.height
        top = min(args.start_row, total_rows)
        bottom = total_rows if args.max_rows is None else min(total_rows, top + args.max_rows)
        img = img.crop((0, top, img.width, bottom))
        img = img.resize((img.width * args.scale, img.height * args.scale),
                         Image.NEAREST)
        img.save(args.out)
        print(f"filas {top}-{bottom} de {total_rows} totales")
        print(f"{img.width}x{img.height} (escalado) -> {args.out}")


if __name__ == '__main__':
    main()