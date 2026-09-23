#!/usr/bin/env python3
"""
pntool.py - Toolkit de analisis y reinsercion de texto para Policenauts (PSX)
             sobre la base ya parcheada al ingles por JunkerHQ (hack DATCH).

Solo stdlib. Probado en Python 3.8+.

FILOSOFIA
---------
La estructura exacta del DATCH ("Double Addressing with Text-Chaining Hack")
nunca fue publicada como especificacion. Esta herramienta NO la asume: te da
comandos para deducirla de los binarios y despues VALIDARLA estadisticamente
antes de que escribas una sola linea de traduccion.

FLUJO
-----
  1. scan    -> encontrar bloques de texto (donde hay texto y con que encoding)
  2. gaps    -> ver que bytes interrumpen el texto (candidatos a marcador DATCH)
  3. probe   -> probar una hipotesis de formato y medir que tan bien resuelve
  4. dump    -> con el formato validado, exportar a CSV para traducir
  5. build   -> reinsertar el CSV traducido recalculando punteros

Ejemplo:
  python3 pntool.py scan  EN/SCRIPT.DAT
  python3 pntool.py gaps  EN/SCRIPT.DAT
  python3 pntool.py probe EN/SCRIPT.DAT --marker 0x1B --width 4 --endian little
  python3 pntool.py dump  EN/SCRIPT.DAT --marker 0x1B --width 4 -o script.csv
  python3 pntool.py build EN/SCRIPT.DAT script_es.csv -o ES/SCRIPT.DAT \
                          --marker 0x1B --width 4
"""

import argparse
import csv
import os
import re
import struct
import sys

try:
    from sz_text import load_charmap, apply_charmap, check_reserved, from_tokens
except ImportError:
    load_charmap = apply_charmap = check_reserved = from_tokens = None
from collections import Counter

PRINTABLE = set(range(0x20, 0x7F))

# Palabras funcionales/comunes en ingles: casi cualquier oracion real de
# guion (narracion, dialogo) va a contener alguna de estas, sin importar
# el largo total de la linea. Al reves, una racha de bytes al azar de un
# stream MDEC comprimido casi nunca va a deletrear una de estas por
# casualidad -- sirve como filtro que generaliza mejor que un --minlen fijo
# (que asume un piso de longitud que puede no aplicar a otros videos).
COMMON_WORDS = frozenset("""
the and was is of to in a that it for on with as this his her she he
they we you i from at by not are be have has had but or if will would
can could what when where who why how all one two three no yes ok us
our my your their them there here then than so been being do does did
was were am an into out up down over under about after before during
which while these those such very just only also more most some any
were an about no not all
""".split())


# ---------------------------------------------------------------------------
# Deteccion de texto
# ---------------------------------------------------------------------------

RAW_FLOOR = 3  # piso interno para rachas "crudas" antes de fusionar: evita
               # que datos binarios de alta entropia generen millones de
               # rachas de 1-2 caracteres por puro azar, sin perder
               # fragmentos cortos reales (el caso mas corto confirmado
               # hasta ahora, "there. ", mide 7 caracteres).


def find_runs_ascii_raw(data, floor):
    """Todas las rachas de ASCII imprimible de al menos `floor` caracteres,
    SIN aplicar el minlen final -- para poder fusionar antes de filtrar."""
    runs, start = [], None
    for i, b in enumerate(data):
        if b in PRINTABLE:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= floor:
                runs.append((start, i, data[start:i].decode('latin-1')))
            start = None
    if start is not None and len(data) - start >= floor:
        runs.append((start, len(data), data[start:].decode('latin-1')))
    return runs


def find_runs_ascii80_raw(data, floor):
    """Version cruda (sin minlen final) de las rachas 0x80<ascii>."""
    runs = []
    i, start, chars = 0, None, []
    n = len(data)
    while i < n - 1:
        if data[i] == 0x80 and data[i + 1] in PRINTABLE:
            if start is None:
                start, chars = i, []
            chars.append(chr(data[i + 1]))
            i += 2
            continue
        if start is not None:
            if len(chars) >= floor:
                runs.append((start, i, ''.join(chars)))
            start, chars = None, []
        i += 1
    if start is not None and len(chars) >= floor:
        runs.append((start, i, ''.join(chars)))
    return runs


def fusionar_rachas(data, runs, max_gap=2):
    """Fusiona rachas consecutivas separadas por un salto de linea valido:
    un solo 0x0A (el esquema simple, visto en PN_VOX1.PAC), o el marcador
    0x80 (el esquema de las FMV). En el esquema FMV el caracter visible
    (siempre '|' en lo que vimos hasta ahora) NO forma parte del hueco --
    como es imprimible, el propio detector de rachas ya lo cuenta como el
    PRIMER caracter de la racha siguiente. Por eso el hueco real es de un
    solo byte (el 0x80), no dos, y ese '|' se descarta al fusionar (el
    '\\n' ya representa el salto de linea, no hace falta conservarlo).

    Sin esto, una frase partida en dos lineas pierde el fragmento corto del
    final o principio si cada racha se evalua por separado contra minlen:
    "It's impossible to stay hidden" + "there. " nunca deberia perder el
    "there." solo por medir 7 caracteres -- es la MISMA oracion."""
    if not runs:
        return runs
    fusionadas = [runs[0]]
    for s, e, t in runs[1:]:
        ps, pe, pt = fusionadas[-1]
        gap = data[pe:s]
        if len(gap) <= max_gap and gap == b'\x0A':
            fusionadas[-1] = (ps, e, pt + '\n' + t)
        elif len(gap) <= max_gap and gap == b'\x80' and t[:1] == '|':
            fusionadas[-1] = (ps, e, pt + '\n' + t[1:])
        else:
            fusionadas.append((s, e, t))
    return fusionadas


def find_runs_ascii(data, minlen):
    """Rachas de ASCII imprimible de 1 byte por caracter. Primero junta
    rachas crudas, fusiona las separadas por un salto de linea valido, y
    recien ahi filtra por minlen -- asi una continuacion corta que sigue a
    una racha larga y valida no se pierde por medir poco por si sola."""
    floor = min(RAW_FLOOR, minlen)
    crudas = find_runs_ascii_raw(data, floor)
    fusionadas = fusionar_rachas(data, crudas)
    return [(s, e, t) for s, e, t in fusionadas if len(t) >= minlen]


def find_runs_ascii80(data, minlen):
    """Rachas de pares 0x80 <ascii> (el esquema que describe slowbeef),
    con la misma logica de fusion antes de filtrar que find_runs_ascii."""
    floor = min(RAW_FLOOR, minlen)
    crudas = find_runs_ascii80_raw(data, floor)
    fusionadas = fusionar_rachas(data, crudas)
    return [(s, e, t) for s, e, t in fusionadas if len(t) >= minlen]


ENCODINGS = {'ascii': find_runs_ascii, 'ascii80': find_runs_ascii80}


def autodetect(data, minlen):
    """Devuelve el encoding que cubre mas bytes del archivo."""
    best, best_cov = 'ascii', -1
    for name, fn in ENCODINGS.items():
        runs = fn(data, minlen)
        cov = sum(e - s for s, e, _ in runs)
        if cov > best_cov:
            best, best_cov = name, cov
    return best


def get_runs(data, encoding, minlen):
    if encoding == 'auto':
        encoding = autodetect(data, minlen)
    return encoding, ENCODINGS[encoding](data, minlen)


# ---------------------------------------------------------------------------
# Punteros
# ---------------------------------------------------------------------------

def read_ptr(data, off, width, endian):
    if off + width > len(data):
        return None
    return int.from_bytes(data[off:off + width], endian)


def write_ptr(value, width, endian):
    return value.to_bytes(width, endian)


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def cmd_scan(args):
    data = open(args.binfile, 'rb').read()
    enc, runs = get_runs(data, args.encoding, args.minlen)
    cov = sum(e - s for s, e, _ in runs)
    print(f"archivo   : {args.binfile}  ({len(data)} bytes)")
    print(f"encoding  : {enc}")
    print(f"rachas    : {len(runs)}   cobertura: {cov} bytes "
          f"({100.0 * cov / max(1, len(data)):.1f}%)")
    if runs:
        print(f"blob      : 0x{runs[0][0]:X} .. 0x{runs[-1][1]:X}")
    print()
    for s, e, t in runs[:args.limit]:
        show = t if len(t) <= 70 else t[:67] + '...'
        print(f"  0x{s:08X}  len={e - s:5d}  {show!r}")
    if len(runs) > args.limit:
        print(f"  ... ({len(runs) - args.limit} mas; usa --limit)")


def cmd_gaps(args):
    """Histograma de los bytes que interrumpen el texto.

    En un binario con DATCH, el marcador insertado deberia aparecer como un
    valor dominante justo antes de cada racha de texto reubicada.
    """
    data = open(args.binfile, 'rb').read()
    enc, runs = get_runs(data, args.encoding, args.minlen)
    if not runs:
        print("sin texto detectado")
        return

    lead, gapsize, gapblob = Counter(), Counter(), Counter()
    for idx in range(1, len(runs)):
        prev_end = runs[idx - 1][1]
        start = runs[idx][0]
        gap = data[prev_end:start]
        gapsize[len(gap)] += 1
        if gap:
            lead[gap[0]] += 1
            gapblob[gap.hex()] += 1

    print(f"encoding: {enc}   rachas: {len(runs)}\n")
    print("-- byte que abre el hueco (candidato a marcador DATCH) --")
    for b, c in lead.most_common(args.top):
        ch = chr(b) if b in PRINTABLE else '.'
        print(f"  0x{b:02X} '{ch}'   {c:6d}  ({100.0 * c / max(1, sum(lead.values())):.1f}%)")

    print("\n-- tamano del hueco en bytes --")
    for size, c in gapsize.most_common(args.top):
        print(f"  {size:4d} bytes   {c:6d}")

    print("\n-- secuencias completas de hueco mas repetidas --")
    for hx, c in gapblob.most_common(args.top):
        print(f"  {hx:<24} {c:6d}")

    print("\nLectura: si un byte domina la primera tabla y el tamano de hueco\n"
          "es constante (p.ej. 5 = 1 marcador + 4 de puntero), esa es tu\n"
          "hipotesis para --marker y --width. Validala con 'probe'.")


def cmd_probe(args):
    """Valida una hipotesis de formato DATCH.

    Hipotesis: en el hueco aparece <marker><puntero de N bytes>, y ese puntero
    apunta al inicio de otra racha de texto del mismo archivo. Si la hipotesis
    es correcta, un porcentaje alto de punteros va a caer exactamente sobre un
    inicio de racha conocido. Si es incorrecta, van a caer en cualquier lado.
    """
    data = open(args.binfile, 'rb').read()
    enc, runs = get_runs(data, args.encoding, args.minlen)
    starts = {s for s, _, _ in runs}
    base = args.base

    total = hits = near = oob = 0
    samples = []
    for i in range(1, len(runs)):
        prev_end, start = runs[i - 1][1], runs[i][0]
        gap = data[prev_end:start]
        pos = gap.find(bytes([args.marker]))
        if pos < 0:
            continue
        off = prev_end + pos + 1
        val = read_ptr(data, off, args.width, args.endian)
        if val is None:
            continue
        total += 1
        target = val - base
        if not (0 <= target < len(data)):
            oob += 1
        elif target in starts:
            hits += 1
            if len(samples) < args.limit:
                samples.append((off, val, target, True))
        else:
            if any(abs(target - s) <= 2 for s in starts):
                near += 1
            if len(samples) < args.limit:
                samples.append((off, val, target, False))

    print(f"encoding={enc}  marker=0x{args.marker:02X}  width={args.width}  "
          f"endian={args.endian}  base=0x{base:X}\n")
    if total == 0:
        print("El marcador no aparece en ningun hueco. Proba otro valor.")
        return
    pct = 100.0 * hits / total
    print(f"punteros evaluados : {total}")
    print(f"exactos            : {hits}  ({pct:.1f}%)")
    print(f"cerca (+-2 bytes)  : {near}")
    print(f"fuera de archivo   : {oob}\n")
    for off, val, tgt, ok in samples:
        flag = 'OK ' if ok else '   '
        print(f"  {flag} @0x{off:08X} -> 0x{val:08X} (abs 0x{tgt:08X})")
    print()
    if pct >= 80:
        print(">>> HIPOTESIS VALIDADA. Usa estos parametros en dump/build.")
    elif pct >= 20:
        print(">>> PARCIAL. Proba otro --base (a veces es la direccion de carga\n"
              "    en RAM, no 0) o cambia --endian / --width.")
    else:
        print(">>> RECHAZADA. Volve a 'gaps' y proba otro marcador.")


def cmd_dump(args):
    data = open(args.binfile, 'rb').read()
    enc, runs = get_runs(data, args.encoding, args.minlen)
    with open(args.out, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['id', 'anchor_off', 'text_off', 'orig_len',
                    'original', 'traduccion', 'notas'])
        for i, (s, e, t) in enumerate(runs):
            anchor = ''
            if i > 0:
                prev_end = runs[i - 1][1]
                gap = data[prev_end:s]
                pos = gap.find(bytes([args.marker]))
                if pos >= 0:
                    anchor = f"0x{prev_end + pos:X}"
            w.writerow([i, anchor, f"0x{s:X}", e - s, t, '', ''])
    print(f"encoding detectado: {enc}")
    print(f"{len(runs)} lineas -> {args.out}")
    print("Traduci en la columna 'traduccion'. Las vacias quedan en ingles.")


def encode_text(text, encoding):
    out = bytearray()
    for ch in text:
        c = ord(ch)
        if c > 0xFF:
            raise ValueError(f"caracter fuera de rango de 1 byte: {ch!r} "
                             f"(mapealo a un glifo libre de la fuente)")
        if encoding == 'ascii80':
            out += bytes([0x80, c])
        else:
            out.append(c)
    return bytes(out)


def cmd_build(args):
    data = bytearray(open(args.binfile, 'rb').read())
    enc, runs = get_runs(bytes(data), args.encoding, args.minlen)

    rows = list(csv.DictReader(open(args.csv, encoding='utf-8')))
    if len(rows) != len(runs):
        print(f"AVISO: el CSV tiene {len(rows)} filas y el binario "
              f"{len(runs)} rachas. Usa el CSV generado por 'dump' sobre "
              f"ESTE mismo binario.", file=sys.stderr)

    blob_start = runs[0][0]
    blob_end = runs[-1][1]
    anchors = {}
    for i in range(1, len(runs)):
        gap = data[runs[i - 1][1]:runs[i][0]]
        pos = gap.find(bytes([args.marker]))
        if pos >= 0:
            anchors[i] = runs[i - 1][1] + pos

    # Zona libre: todo el blob salvo los anchors (que NO se pueden mover,
    # porque el bytecode original apunta ahi).
    reserved = set()
    for off in anchors.values():
        for k in range(1 + args.width):
            reserved.add(off + k)

    free = [i for i in range(blob_start, blob_end) if i not in reserved]
    if args.expand_at is not None:
        a, n = args.expand_at, args.expand_size
        if a + n > len(data):
            print(f"ERROR: la zona de expansion 0x{a:X}+{n} excede el archivo")
            return 1
        free += [i for i in range(a, a + n) if i not in reserved]
    free = sorted(set(free))

    # Empaquetar textos nuevos en la zona libre.
    new_text = {}
    for i, (s, e, orig) in enumerate(runs):
        tr = rows[i].get('traduccion', '').strip() if i < len(rows) else ''
        new_text[i] = encode_text(tr if tr else orig, enc)

    term = b'' if args.terminator is None else bytes([args.terminator])
    total_needed = sum(len(v) + len(term) for v in new_text.values())
    if total_needed > len(free):
        print(f"ERROR: necesitas {total_needed} bytes y hay {len(free)} libres.")
        print(f"Faltan {total_needed - len(free)} bytes. Opciones:")
        print("  - acortar traducciones")
        print("  - ampliar el blob en el .xml de mkpsxiso (mueve LBAs: retesteo)")
        return 1

    # Limpiar todo el espacio libre para no dejar colas del texto viejo.
    for off in free:
        data[off] = args.fill

    # Tramos contiguos de espacio libre.
    slots = []   # [addr, size]
    if free:
        run_start, prev = free[0], free[0]
        for off in free[1:]:
            if off != prev + 1:
                slots.append([run_start, prev - run_start + 1])
                run_start = off
            prev = off
        slots.append([run_start, prev - run_start + 1])

    # Best-fit: las lineas mas largas primero, al tramo mas chico que las
    # aguante. Una cadena tiene que ser contigua, asi que esto importa.
    placed = {}
    order = sorted(new_text, key=lambda i: -len(new_text[i]))
    for i in order:
        blob = new_text[i] + term
        cand = [s for s in slots if s[1] >= len(blob)]
        if not cand:
            biggest = max((s[1] for s in slots), default=0)
            print(f"ERROR: la linea {i} necesita {len(blob)} bytes contiguos "
                  f"y el mayor tramo libre es de {biggest}.")
            print(f"  texto: {new_text[i][:60]!r}")
            print("  Acortala, o reubicala a una zona de expansion "
                  "(--expand-at/--expand-size).")
            return 1
        slot = min(cand, key=lambda s: s[1])
        placed[i] = slot[0]
        data[slot[0]:slot[0] + len(blob)] = blob
        slot[0] += len(blob)
        slot[1] -= len(blob)

    # Reescribir los punteros de los anchors.
    rewritten = 0
    for i, off in anchors.items():
        if i not in placed:
            continue
        val = placed[i] + args.base
        data[off] = args.marker
        data[off + 1:off + 1 + args.width] = write_ptr(val, args.width, args.endian)
        rewritten += 1

    if len(data) != os.path.getsize(args.binfile):
        print("ERROR: cambio el tamano del archivo", file=sys.stderr)
        return 1

    open(args.out, 'wb').write(bytes(data))
    print(f"escrito {args.out}")
    print(f"  lineas reubicadas : {len(placed)}")
    print(f"  punteros reescritos: {rewritten}")
    print(f"  bytes usados      : {total_needed}/{len(free)}")
    print("  tamano IDENTICO al original (no hace falta relayout de LBAs)")
    return 0


def encontrar_regiones_diff(a, b, mingap):
    """Encuentra las regiones donde dos buffers difieren, agrupando
    diferencias separadas por menos de `mingap` bytes de coincidencia."""
    n = min(len(a), len(b))
    regions, start = [], None
    for i in range(n):
        if a[i] != b[i]:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= mingap:
                regions.append((start, i))
                start = None
            elif start is not None and i - start < mingap:
                pass
    if start is not None:
        regions.append((start, n))
    return regions


def cmd_diff(args):
    a = open(args.file_a, 'rb').read()
    b = open(args.file_b, 'rb').read()
    print(f"A: {args.file_a}  {len(a)} bytes")
    print(f"B: {args.file_b}  {len(b)} bytes\n")
    regions = encontrar_regiones_diff(a, b, args.mingap)
    print(f"regiones distintas: {len(regions)}")
    for s, e in regions[:args.limit]:
        print(f"  0x{s:08X} .. 0x{e:08X}  ({e - s} bytes)")
    if len(regions) > args.limit:
        print(f"  ... ({len(regions) - args.limit} mas)")


def cmd_diffcheck(args):
    """Cruza las regiones distintas entre dos binarios (JP vs EN) contra un
    CSV de movscan/textscan, para saber cuantas caen DENTRO de un
    subtitulo ya conocido (offset/offset_fin) y cuantas quedan afuera --
    sin explicacion todavia, y candidatas a investigar aparte."""
    a = open(args.file_a, 'rb').read()
    b = open(args.file_b, 'rb').read()
    print(f"A: {args.file_a}  {len(a)} bytes")
    print(f"B: {args.file_b}  {len(b)} bytes")
    if len(a) != len(b):
        print("ADVERTENCIA: los archivos NO tienen el mismo tamano -- el diff "
              "posicional deja de ser confiable despues del primer desfasaje.")

    regions = encontrar_regiones_diff(a, b, args.mingap)
    print(f"regiones distintas: {len(regions)}\n")

    subs = []
    for r in csv.DictReader(open(args.csv, encoding='utf-8')):
        try:
            off = int(r['offset'], 0)
            fin = int(r['offset_fin'], 0)
        except (KeyError, ValueError):
            continue
        subs.append((off, fin))
    subs.sort()
    print(f"subtitulos conocidos en el CSV: {len(subs)}\n")

    def solapa_con_subtitulo(s, e):
        # busqueda lineal simple; el CSV de un .MOV no es tan grande como
        # para necesitar algo mas fino
        for off, fin in subs:
            if off < e and fin > s:
                return True
        return False

    dentro = [(s, e) for s, e in regions if solapa_con_subtitulo(s, e)]
    fuera = [(s, e) for s, e in regions if not solapa_con_subtitulo(s, e)]

    print(f"dentro de un subtitulo conocido : {len(dentro)}  "
          f"({sum(e - s for s, e in dentro)} bytes)")
    print(f"SIN explicar (fuera de todos)   : {len(fuera)}  "
          f"({sum(e - s for s, e in fuera)} bytes)\n")

    print(f"-- regiones sin explicar (primeras {args.limit}) --")
    for s, e in fuera[:args.limit]:
        print(f"  0x{s:08X} .. 0x{e:08X}  ({e - s} bytes)")
    if len(fuera) > args.limit:
        print(f"  ... ({len(fuera) - args.limit} mas; usa --limit)")

    if args.out:
        with open(args.out, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['offset', 'offset_fin', 'largo'])
            for s, e in fuera:
                w.writerow([f"0x{s:X}", f"0x{e:X}", e - s])
        print(f"\nregiones sin explicar exportadas a {args.out}")
    return 0


# ---------------------------------------------------------------------------

def cmd_header(args):
    """Vuelca los primeros N bytes en hex+ascii, y si --detect-offsets esta
    activo, prueba leer los primeros words como una posible tabla de offsets
    de un formato contenedor (heuristica simple)."""
    data = open(args.binfile, 'rb').read()
    n = min(args.bytes, len(data))
    print(f"{args.binfile}  ({len(data)} bytes totales, mostrando {n})\n")
    for off in range(0, n, 16):
        chunk = data[off:off + 16]
        hexs = ' '.join(f'{b:02X}' for b in chunk)
        asci = ''.join(chr(b) if b in PRINTABLE else '.' for b in chunk)
        print(f"  {off:08X}  {hexs:<48}  {asci}")

    if args.detect_offsets:
        print("\n-- heuristica: primeros words de 4 bytes como posible tabla --")
        w = args.width
        count = min(args.entries, n // w)
        vals = []
        for i in range(count):
            v = int.from_bytes(data[i * w:i * w + w], args.endian)
            vals.append(v)
        ascending = all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))
        in_range = sum(1 for v in vals if 0 < v < len(data))
        print(f"  primeros {count} valores de {w} bytes ({args.endian}):")
        for i, v in enumerate(vals[:20]):
            flag = "OK" if 0 < v < len(data) else "??"
            print(f"    [{i:3d}] 0x{v:08X}  ({v:>10})  {flag}")
        print(f"\n  monotono creciente : {ascending}")
        print(f"  dentro del archivo : {in_range}/{count}")
        if ascending and in_range >= count * 0.8:
            print("  >>> Se PARECE a una tabla de offsets/tamanos real.")
        else:
            print("  >>> No parece tabla de offsets con estos parametros; "
                  "proba otro --width o --endian, o count.")



def parse_frid_dir(data, record_size=24, name_len=12):
    """Parsea el directorio FRID: registros de nombre+offset+tamano+checksum
    empezando en 0x20, hasta que un registro no parezca valido."""
    if data[:4] != b'FRID':
        return None
    entries = []
    pos = 0x20
    n = len(data)
    while pos + record_size <= n:
        name_raw = data[pos:pos + name_len]
        if not name_raw[0:1].isalnum():
            break  # ya no es un nombre de archivo: se acabo el directorio
        try:
            name = name_raw.rstrip(b'\x00').decode('ascii')
        except UnicodeDecodeError:
            break
        if not name or not all(32 <= b < 127 for b in name_raw if b != 0):
            break
        off = int.from_bytes(data[pos + name_len:pos + name_len + 4], 'little')
        size = int.from_bytes(data[pos + name_len + 4:pos + name_len + 8], 'little')
        checksum = data[pos + name_len + 8:pos + name_len + 12]
        if off == 0 or off + size > n or off < pos:
            break  # coordenadas invalidas: dejamos de estar en el directorio
        entries.append({'name': name, 'offset': off, 'size': size,
                        'checksum': checksum.hex()})
        pos += record_size
    return entries


def cmd_dpk_list(args):
    data = open(args.binfile, 'rb').read()
    entries = parse_frid_dir(data)
    if entries is None:
        print("No empieza con firma 'FRID'. Este parser es especifico a ese "
              "formato; si el archivo usa otra firma, avisa cual es.")
        return 1
    print(f"{args.binfile}  ({len(data)} bytes)  {len(entries)} entradas\n")
    print(f"{'#':>4}  {'nombre':<16} {'offset':>10} {'tamano':>10} "
          f"{'align2048':>9}  checksum")
    for i, e in enumerate(entries):
        align = 'si' if e['offset'] % 2048 == 0 else 'NO'
        print(f"{i:>4}  {e['name']:<16} 0x{e['offset']:08X} "
              f"{e['size']:>10} {align:>9}  {e['checksum']}")
    total = sum(e['size'] for e in entries)
    print(f"\nsuma de tamanos de subarchivos: {total} "
          f"({100.0*total/len(data):.1f}% del contenedor)")
    return 0


def cmd_dpk_extract(args):
    data = open(args.binfile, 'rb').read()
    entries = parse_frid_dir(data)
    if entries is None:
        print("No empieza con firma 'FRID'.")
        return 1
    os.makedirs(args.outdir, exist_ok=True)
    print(f"extrayendo {len(entries)} subarchivos a {args.outdir}/\n")
    for e in entries:
        blob = data[e['offset']:e['offset'] + e['size']]
        outpath = os.path.join(args.outdir, e['name'])
        open(outpath, 'wb').write(blob)
        cov = ascii80_ratio(blob)
        flag = "TEXTO?" if cov >= 2.0 else ""
        print(f"  {e['name']:<16} {e['size']:>10} bytes  "
              f"ascii80~{cov:5.1f}%  {flag}")
    return 0


def ascii80_ratio(data, sample=1 << 20):
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



def cmd_dpk_diff(args):
    da = open(args.file_a, 'rb').read()
    db = open(args.file_b, 'rb').read()
    ea = parse_frid_dir(da)
    eb = parse_frid_dir(db)
    if ea is None or eb is None:
        print("Alguno de los dos no empieza con 'FRID'.")
        return 1
    by_name_b = {e['name']: e for e in eb}
    print(f"{'nombre':<16} {'tam A':>10} {'tam B':>10} {'diff':>8}  estado")
    rows = []
    for e in ea:
        nb = by_name_b.get(e['name'])
        if nb is None:
            rows.append((e['name'], e['size'], None, None, 'SOLO EN A'))
            continue
        diff = nb['size'] - e['size']
        estado = 'identico' if e['checksum'] == nb['checksum'] else 'distinto'
        rows.append((e['name'], e['size'], nb['size'], diff, estado))
    solo_b = [n for n in by_name_b if n not in {e['name'] for e in ea}]

    rows.sort(key=lambda r: -(r[3] or 0))
    for name, sa, sb, diff, estado in rows:
        sb_s = str(sb) if sb is not None else '-'
        diff_s = f"{diff:+d}" if diff is not None else '-'
        print(f"{name:<16} {sa:>10} {sb_s:>10} {diff_s:>8}  {estado}")
    for n in solo_b:
        print(f"{n:<16} {'-':>10} {by_name_b[n]['size']:>10} {'-':>8}  SOLO EN B")
    return 0



def encode_variants(text):
    """Devuelve la frase codificada en las dos variantes que nos importan:
    ASCII plano (1 byte/caracter) y ASCII con 0x80 antepuesto a cada byte
    (el esquema que documento slowbeef para las FMV de Policenauts).
    """
    plano = text.encode('latin-1', errors='ignore')
    con80 = bytearray()
    for b in plano:
        con80.append(0x80)
        con80.append(b)
    return {'ascii': bytes(plano), 'ascii80': bytes(con80)}


def hex_context(data, pos, length, radius=24):
    ini = max(0, pos - radius)
    fin = min(len(data), pos + length + radius)
    return data[ini:fin].hex(' ')


def iter_files(target):
    if os.path.isdir(target):
        for dirpath, _, filenames in os.walk(target):
            for name in filenames:
                yield os.path.join(dirpath, name)
    else:
        yield target


def cmd_grep(args):
    """Busca frases literales (subtitulos, dialogo conocido, etc.) en un
    archivo o en una carpeta entera, en las dos variantes de encoding.

    Pensado para localizar texto SIN pasar por decomp_probe.py: si el texto
    de subtitulos de video esta sin comprimir (como reporta slowbeef para
    las FMV), esto lo deberia encontrar directo.
    """
    strings = list(args.strings or [])
    if args.strings_file:
        with open(args.strings_file, encoding='utf-8') as fh:
            strings += [line.rstrip('\n') for line in fh if line.strip()]
    strings = [s for s in strings if s]
    if not strings:
        print("No se paso ninguna frase (--strings o --strings-file).")
        return 1

    total = 0
    archivos = 0
    for path in iter_files(args.target):
        try:
            data = open(path, 'rb').read()
        except OSError:
            continue
        archivos += 1
        for cand in strings:
            for enc_name, patron in encode_variants(cand).items():
                if not patron:
                    continue
                idx = data.find(patron)
                while idx != -1:
                    total += 1
                    print(f"[{enc_name}] {path}")
                    print(f"  frase : {cand!r}")
                    print(f"  offset: 0x{idx:X} ({idx})")
                    print(f"  hexctx: {hex_context(data, idx, len(patron))}")
                    print()
                    idx = data.find(patron, idx + 1)

    print(f"archivos recorridos : {archivos}")
    print(f"coincidencias total : {total}")
    if total == 0:
        print("\nSin resultados. Proba con fragmentos mas cortos (sin\n"
              "puntuacion/apostrofos) o con un --minlen mas bajo en 'scan'\n"
              "para ver si el texto aparece con otro encoding.")
    return 0


def cmd_around(args):
    """Inspecciona la estructura de 'chunks' de tamano fijo alrededor de un
    offset dado. Pensado para mapear contenedores tipo STR/XA (PSX) donde
    video, audio y (sospechamos) subtitulos se intercalan en bloques de
    tamano constante (2048 es el mas comun para sectores Mode2/Form1).

    Idea: si hay un campo de 'tipo de chunk' en el header de cada bloque,
    va a verse distinto en el chunk que contiene texto vs. los de video/audio
    de alrededor. Compara las columnas a simple vista.
    """
    data = open(args.binfile, 'rb').read()
    size = args.chunk
    off = args.offset
    total_chunks = len(data) // size
    idx = off // size

    print(f"archivo      : {args.binfile}  ({len(data)} bytes)")
    print(f"chunk size   : {size}  ({total_chunks} chunks totales)")
    print(f"offset 0x{off:X} -> chunk #{idx}  "
          f"(0x{idx * size:08X}..0x{(idx + 1) * size:08X})  "
          f"pos_en_chunk=0x{off % size:X}\n")

    lo = max(0, idx - args.context)
    hi = min(total_chunks, idx + args.context + 1)
    for c in range(lo, hi):
        base = c * size
        header = data[base:base + args.header]
        hexs = ' '.join(f'{b:02x}' for b in header)
        asci = ''.join(chr(b) if b in PRINTABLE else '.' for b in header)
        marker = '  <== contiene el offset buscado' if c == idx else ''
        print(f"  chunk #{c:6d}  @0x{base:08X}  {hexs}  |{asci}|{marker}")


def cmd_hexdump(args):
    """Hexdump clasico (16 bytes por fila) de una ventana arbitraria alrededor
    de un offset. A diferencia de 'around', no asume ningun tamano de chunk:
    sirve para comparar el header que antecede a varias ocurrencias de texto
    y buscar un formato de paquete (tamano/flags/terminador) reconocible."""
    data = open(args.binfile, 'rb').read()
    off = args.offset
    lo = max(0, off - args.before)
    hi = min(len(data), off + args.after)

    print(f"archivo: {args.binfile}  ({len(data)} bytes)")
    print(f"offset objetivo: 0x{off:X}  ventana: 0x{lo:X} .. 0x{hi:X}\n")

    base = lo - (lo % 16)
    for row_start in range(base, hi, 16):
        chunk = data[row_start:row_start + 16]
        cells = []
        for i, b in enumerate(chunk):
            addr = row_start + i
            s = f'{b:02x}'
            if addr == off:
                s = f'[{s}]'
            elif lo <= addr < off + (args.marklen or 0):
                s = f'{s}'
            cells.append(s)
        hexs = ' '.join(cells)
        asci = ''.join(
            (chr(b) if b in PRINTABLE else '.') for b in chunk)
        print(f"  {row_start:08X}  {hexs:<64}  |{asci}|")


def compile_hex_pattern(hexpat):
    """Compila un patron hex a una regex sobre bytes. Soporta '??' como
    comodin de UN byte cualquiera en esa posicion (ej. '0038????0100' matcha
    00 38 <cualquiera> <cualquiera> 01 00 -- util porque descubrimos que el
    campo de 'tipo' del header de chunk varia segun sea video/audio/otro,
    y un patron 100% fijo se pierde los chunks de tipo distinto a 0001).
    Devuelve (regex_compilada, largo_en_bytes_del_patron)."""
    hexpat = hexpat.strip()
    if len(hexpat) % 2 != 0:
        raise ValueError(f"patron hex de largo impar: {hexpat!r}")
    partes = []
    for i in range(0, len(hexpat), 2):
        par = hexpat[i:i + 2]
        if par == '??':
            partes.append(b'.')
        else:
            partes.append(re.escape(bytes.fromhex(par)))
    return re.compile(b''.join(partes), re.DOTALL), len(hexpat) // 2


def find_pattern_offsets(data, hexpat):
    """Todas las ocurrencias (offsets, ordenados) de un patron hex con
    wildcards opcionales. Devuelve (offsets, largo_del_patron_en_bytes)."""
    regex, plen = compile_hex_pattern(hexpat)
    return [m.start() for m in regex.finditer(data)], plen


def cmd_findhex(args):
    """Busca un patron de bytes crudo (hex, sin espacios) en un archivo o
    carpeta, y para cada match muestra los bytes siguientes interpretados
    como posibles campos de 2 y 4 bytes little-endian. Util para mapear
    headers de chunk (p.ej. la firma 0x3800 de los sectores STR de PSX).
    Soporta '??' como comodin de un byte (ver compile_hex_pattern).

    Con --before-offset, en vez de listar TODAS las ocurrencias, busca
    solo la ULTIMA que aparece antes de ese offset -- para encontrar el
    header del chunk que contiene un texto ya localizado."""
    if not args.pattern.strip():
        print("Patron vacio.")
        return 1

    total = 0
    archivos = 0
    for path in iter_files(args.target):
        try:
            data = open(path, 'rb').read()
        except OSError:
            continue
        archivos += 1

        offsets, plen = find_pattern_offsets(data, args.pattern)
        if args.before_offset is not None:
            candidatos = [o for o in offsets if o < args.before_offset]
            hits = [candidatos[-1]] if candidatos else []
        else:
            hits = offsets

        for idx in hits:
            total += 1
            tail = data[idx:idx + args.after]
            hexs = ' '.join(f'{b:02x}' for b in tail)
            print(f"{path}")
            if args.before_offset is not None:
                dist = args.before_offset - idx
                print(f"  offset: 0x{idx:X} ({idx})   "
                      f"(a {dist} bytes antes de la referencia 0x{args.before_offset:X})")
            else:
                print(f"  offset: 0x{idx:X} ({idx})")
            print(f"  bytes  : {hexs}")
            p = idx + plen
            if p + 8 <= len(data):
                f_u16_a = int.from_bytes(data[p:p+2], 'little')
                f_u16_b = int.from_bytes(data[p+2:p+4], 'little')
                f_u32   = int.from_bytes(data[p+4:p+8], 'little')
                print(f"  campos tras el patron: u16={f_u16_a} u16={f_u16_b} "
                      f"u32={f_u32} (0x{f_u32:X})")
            print()

    print(f"archivos recorridos : {archivos}")
    print(f"coincidencias total : {total}")
    return 0


def cmd_chunkmap(args):
    """Encuentra TODAS las ocurrencias del patron de header en un archivo,
    calcula el 'tamano' de cada chunk como la distancia al siguiente header,
    arma un histograma de tamanos (para ver el tamano 'normal' de chunk de
    video) y, si se pasan --marks, indica en que chunk cae cada offset
    conocido (p.ej. los offsets de texto ya localizados con 'grep').

    Hipotesis a validar: los chunks que contienen texto deberian ser
    outliers de tamano (mas grandes que el resto, por el texto+padding).
    """
    data = open(args.binfile, 'rb').read()
    offsets, _plen = find_pattern_offsets(data, args.pattern)

    if len(offsets) < 2:
        print(f"Solo {len(offsets)} ocurrencia(s) del patron; no alcanza "
              "para calcular tamanos de chunk.")
        return 1

    sizes = [offsets[i + 1] - offsets[i] for i in range(len(offsets) - 1)]

    print(f"archivo          : {args.binfile}  ({len(data)} bytes)")
    print(f"patron           : {args.pattern}")
    print(f"chunks detectados: {len(offsets)} (el ultimo no tiene tamano, "
          f"no hay header siguiente)\n")

    hist = Counter(sizes)
    print("-- tamanos de chunk mas frecuentes --")
    for size, count in hist.most_common(args.top):
        print(f"  {size:8d} bytes   x{count}")

    print(f"\n  min={min(sizes)}  max={max(sizes)}  "
          f"promedio={sum(sizes)/len(sizes):.1f}")

    marks = []
    if args.marks:
        marks = [int(x, 0) for x in args.marks.split(',')]

    if marks:
        moda_size, moda_count = hist.most_common(1)[0]
        print(f"\n(tamano mas frecuente / moda: {moda_size} bytes, x{moda_count})")
        print("-- offsets marcados y el chunk que los contiene --")
        for m in marks:
            found = False
            for i in range(len(offsets) - 1):
                if offsets[i] <= m < offsets[i + 1]:
                    size = sizes[i]
                    pos_in_chunk = m - offsets[i]
                    flag = "tamano = moda" if size == moda_size else \
                        f">>> OUTLIER <<< (moda={moda_size}, diff={size - moda_size:+d})"
                    print(f"  0x{m:X}  -> chunk #{i}  @0x{offsets[i]:X}  "
                          f"tamano={size}  pos_en_chunk={pos_in_chunk}  {flag}")
                    found = True
                    break
            if not found:
                print(f"  0x{m:X}  -> no cae dentro de ningun chunk mapeado "
                      "(esta antes del primer header o despues del ultimo)")
    return 0


def clasificar_hallazgos(hallazgos, noise_threshold, single_token_minlen=10):
    """Separa una lista de hallazgos (abs_off, encoding, texto, ...resto) en
    con_palabra / sin_palabra / probable_ruido, usando las mismas heuristicas
    que movscan (ruido repetitivo tipo SCIPPDTS + palabra inglesa comun
    reconocible) mas una nueva: un token largo SIN espacios internos y SIN
    ninguna palabra comun reconocible -- el dialogo real de Policenauts
    virtualmente siempre tiene un espacio (frase de varias palabras) o, si
    es una sola palabra corta, coincide con una palabra comun. Una racha
    larga pegada sin espacios y sin match es casi siempre audio (ADPCM)
    que cayo por casualidad en rango imprimible, no texto real.
    No asume nada sobre de donde salieron los hallazgos, asi que sirve
    tanto para el escaneo por chunk (.MOV) como para un escaneo lineal sin
    estructura de chunk (PN_VOX1.PAC u otro archivo)."""
    def base_de(t):
        caps = re.findall(r'[A-Z]{5,}', t)
        if caps:
            longest = max(caps, key=len)
            return longest[:8]
        return t.rstrip('0123456789 !"\'#$%&()*+,-./:;<=>?@[\\]^_`{|}~')

    conteo_base = Counter(base_de(h[2]) for h in hallazgos)

    def tiene_palabra(t):
        palabras = re.findall(r"[A-Za-z']{2,}", t)
        return any(p.lower() in COMMON_WORDS for p in palabras)

    def es_token_unico_sospechoso(t):
        limpio = t.strip()
        return (' ' not in limpio and len(limpio) >= single_token_minlen
                and not tiene_palabra(t))

    def es_ruido(t):
        b = base_de(t)
        if len(b) >= 3 and conteo_base[b] >= noise_threshold:
            return True
        return es_token_unico_sospechoso(t)

    probable_texto = [h for h in hallazgos if not es_ruido(h[2])]
    probable_ruido = [h for h in hallazgos if es_ruido(h[2])]
    con_palabra = [h for h in probable_texto if tiene_palabra(h[2])]
    sin_palabra = [h for h in probable_texto if not tiene_palabra(h[2])]
    patrones_ruido = sorted({base_de(h[2]) for h in probable_ruido})
    return con_palabra, sin_palabra, probable_ruido, patrones_ruido, es_ruido, tiene_palabra


def analizar_mov(binfile, pattern_hex, tail, minlen, forbidden_str, noise_threshold,
                  single_token_minlen=10):
    """Logica central de extraccion de subtitulos de un .MOV/STR. Devuelve un
    dict con todo lo necesario para imprimir un reporte o escribir un CSV,
    reutilizable tanto para un solo archivo (movscan) como para un barrido
    de carpeta completa (movscan-all)."""
    data = open(binfile, 'rb').read()
    offsets, _plen = find_pattern_offsets(data, pattern_hex)

    if len(offsets) < 2:
        return {'ok': False, 'motivo': f"solo {len(offsets)} ocurrencia(s) "
                "del patron; nada para escanear", 'binfile': binfile}

    forbidden = set(forbidden_str) if forbidden_str else set()
    hallazgos = []
    descartados_prohibido = 0
    for i in range(len(offsets) - 1):
        chunk_start, chunk_end = offsets[i], offsets[i + 1]
        tail_start = max(chunk_start, chunk_end - tail)
        ventana = data[tail_start:chunk_end]

        for name, fn in ENCODINGS.items():
            for s, e, t in fn(ventana, minlen):
                if forbidden and any(ch in forbidden for ch in t):
                    descartados_prohibido += 1
                    continue
                abs_off = tail_start + s
                abs_fin = tail_start + e
                hallazgos.append((abs_off, abs_fin, name, t, i, chunk_start))

    hallazgos.sort()

    # El offset_fin crudo (donde termina el texto detectado) subestima el
    # espacio disponible -- pero asumir que TODO el tramo hasta el proximo
    # landmark es relleno libre resulto ser PELIGROSO: encontramos casos
    # con bytes no-cero sin identificar ahi (ej. "60 13" en el caso de
    # "The year was 2010.") que resultaron ser datos reales de video --
    # pisarlos corrompio el frame (macrobloques en cuadricula, clasico de
    # un bitstream MDEC desincronizado). La correccion: solo extender
    # mientras el archivo ORIGINAL tenga bytes 0x00 reales y consecutivos,
    # nunca mas alla del proximo hallazgo/chunk como limite duro. Un byte
    # 0x00 verificado es evidencia real de relleno; asumir por ausencia de
    # otra explicacion no lo es.
    for k in range(len(hallazgos)):
        abs_off, abs_fin_texto, name, t, ci, cs = hallazgos[k]
        if k + 1 < len(hallazgos) and hallazgos[k + 1][4] == ci:
            limite_duro = hallazgos[k + 1][0]
        else:
            limite_duro = offsets[ci + 1]
        p = abs_fin_texto
        while p < limite_duro and data[p] == 0x00:
            p += 1
        hallazgos[k] = (abs_off, p, name, t, ci, cs)

    # clasificar_hallazgos espera el texto en la posicion [2] de la tupla
    vista_clasificar = [(off, name, t, fin, ci, cs)
                        for off, fin, name, t, ci, cs in hallazgos]
    con_palabra, sin_palabra, probable_ruido, patrones_ruido, es_ruido, tiene_palabra = \
        clasificar_hallazgos(vista_clasificar, noise_threshold, single_token_minlen)

    return {
        'ok': True,
        'binfile': binfile,
        'chunks': len(offsets),
        'hallazgos': hallazgos,
        'descartados_prohibido': descartados_prohibido,
        'con_palabra': con_palabra,
        'sin_palabra': sin_palabra,
        'probable_ruido': probable_ruido,
        'patrones_ruido': patrones_ruido,
        'es_ruido': es_ruido,
        'tiene_palabra': tiene_palabra,
    }


def escribir_csv_subtitulos(path_csv, r):
    """Escribe el CSV de subtitulos a partir del resultado de analizar_mov()."""
    with open(path_csv, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['offset', 'offset_fin', 'largo_original', 'encoding', 'chunk',
                   'texto', 'ruido_probable', 'palabra_reconocible', 'traduccion'])
        for abs_off, abs_fin, enc, texto, chunk_idx, chunk_start in r['hallazgos']:
            texto_csv = texto.replace('\n', '\\n')
            w.writerow([f"0x{abs_off:X}", f"0x{abs_fin:X}", abs_fin - abs_off,
                       enc, chunk_idx, texto_csv,
                       'si' if r['es_ruido'](texto) else '',
                       'si' if r['tiene_palabra'](texto) else '', ''])


def cmd_movscan(args):
    """Extraccion automatica de subtitulos de un .MOV/STR: encuentra todos
    los headers de chunk, y para cada chunk revisa si sus ultimos N bytes
    (la 'cola', justo antes del chunk siguiente) contienen una racha de
    ASCII imprimible. No necesita conocer las frases de antemano.

    Filosofia: el resto de un chunk de video es MDEC comprimido (entropia
    altisima); una racha larga de ASCII legible ahi no puede ser azar.
    """
    print(f"archivo   : {args.binfile}")
    forbidden = set(args.forbidden) if args.forbidden else set()

    r = analizar_mov(args.binfile, args.pattern, args.tail, args.minlen,
                     args.forbidden, args.noise_threshold, args.single_token_minlen)
    if not r['ok']:
        print(r['motivo'])
        return 1

    print(f"({os.path.getsize(args.binfile)} bytes)")
    print(f"chunks    : {r['chunks']}")
    print(f"cola      : ultimos {args.tail} bytes de cada chunk")
    print(f"minlen    : {args.minlen} caracteres")
    if forbidden:
        print(f"prohibidos: {''.join(sorted(forbidden))!r} "
              f"(se descarta cualquier racha que contenga alguno)")
    print()
    if forbidden:
        print(f"(descartadas {r['descartados_prohibido']} racha(s) por contener "
              f"caracteres prohibidos)\n")

    con_palabra, sin_palabra, probable_ruido = \
        r['con_palabra'], r['sin_palabra'], r['probable_ruido']

    print(f"-- {len(r['hallazgos'])} racha(s) totales: "
          f"{len(con_palabra)} con palabra reconocible, "
          f"{len(sin_palabra)} sin palabra reconocible (revisar a mano), "
          f"{len(probable_ruido)} probable ruido repetitivo --\n")

    print(f"-- con palabra reconocible ({len(con_palabra)}) --")
    for abs_off, enc, texto, abs_fin, chunk_idx, chunk_start in con_palabra:
        show = texto.replace('\n', '\\n')
        show = show if len(show) <= 90 else show[:87] + '...'
        print(f"  0x{abs_off:08X}  chunk #{chunk_idx:<4} [{enc:7}]  {show!r}")

    if sin_palabra:
        print(f"\n-- sin palabra reconocible ({len(sin_palabra)}) "
              f"-- pueden ser texto corto real o ruido residual, revisar --")
        for abs_off, enc, texto, abs_fin, chunk_idx, chunk_start in sin_palabra:
            show = texto.replace('\n', '\\n')
            show = show if len(show) <= 90 else show[:87] + '...'
            print(f"  0x{abs_off:08X}  chunk #{chunk_idx:<4} [{enc:7}]  {show!r}")

    if probable_ruido:
        print(f"\n(omitidos {len(probable_ruido)} hallazgos de ruido repetitivo "
              f"-- patrones base: {r['patrones_ruido'][:10]}"
              f"{' ...' if len(r['patrones_ruido']) > 10 else ''})")

    if args.out:
        escribir_csv_subtitulos(args.out, r)
        print(f"\nexportado a {args.out}  (columnas 'ruido_probable' y "
              f"'palabra_reconocible' te ayudan a priorizar/filtrar antes de traducir)")
    return 0


def cmd_movscanall(args):
    """Corre analizar_mov() sobre todos los .MOV/.STR de una carpeta y
    escribe un CSV por archivo en la carpeta de salida (la crea si hace
    falta). Al final imprime una tabla resumen con el conteo por video."""
    src = args.srcdir
    out_dir = args.outdir
    os.makedirs(out_dir, exist_ok=True)

    exts = tuple(e.lower() for e in args.ext.split(','))
    archivos = sorted(
        f for f in os.listdir(src)
        if f.lower().endswith(exts) and os.path.isfile(os.path.join(src, f))
    )
    if not archivos:
        print(f"No se encontraron archivos con extension {exts} en {src}")
        return 1

    print(f"carpeta origen : {src}")
    print(f"carpeta salida : {out_dir}")
    print(f"videos a procesar: {len(archivos)}\n")

    resumen = []
    for nombre in archivos:
        binfile = os.path.join(src, nombre)
        base, _ = os.path.splitext(nombre)
        csv_path = os.path.join(out_dir, f"{base}.csv")

        r = analizar_mov(binfile, args.pattern, args.tail, args.minlen,
                         args.forbidden, args.noise_threshold, args.single_token_minlen)
        if not r['ok']:
            print(f"  {nombre:20s}  SALTEADO ({r['motivo']})")
            resumen.append((nombre, None, None, None))
            continue

        escribir_csv_subtitulos(csv_path, r)
        n_pal = len(r['con_palabra'])
        n_sin = len(r['sin_palabra'])
        n_ruido = len(r['probable_ruido'])
        print(f"  {nombre:20s}  con_palabra={n_pal:<4} sin_palabra={n_sin:<4} "
              f"ruido={n_ruido:<5}  -> {csv_path}")
        resumen.append((nombre, n_pal, n_sin, n_ruido))

    total_pal = sum(r[1] for r in resumen if r[1] is not None)
    total_sin = sum(r[2] for r in resumen if r[2] is not None)
    total_ruido = sum(r[3] for r in resumen if r[3] is not None)
    procesados = sum(1 for r in resumen if r[1] is not None)
    print(f"\n-- resumen: {procesados}/{len(archivos)} videos procesados --")
    print(f"   total con palabra reconocible : {total_pal}")
    print(f"   total sin palabra reconocible : {total_sin}  (revisar a mano)")
    print(f"   total ruido repetitivo omitido: {total_ruido}")
    print(f"\nCSVs en: {out_dir}")
    return 0


def leer_registro_vox(data, off, max_len, min_text):
    """Intenta leer un registro tipo PN_VOX1.PAC en off (header de 16 bytes:
    total_len/campo_b/campo_c/reservado, todos u32 LE, seguido de texto
    ASCII terminado en 0x00 y padding). Devuelve dict o None si no valida."""
    if off < 0 or off + 16 > len(data):
        return None
    total_len, campo_b, campo_c, reservado = struct.unpack_from('<IIII', data, off)
    if total_len < 17 or total_len > max_len:
        return None
    if off + total_len > len(data):
        return None
    cuerpo = data[off + 16: off + total_len]
    term = cuerpo.find(b'\x00')
    if term < min_text:
        return None
    texto_bytes = cuerpo[:term]
    if any(b not in PRINTABLE and b not in (0x0A,) for b in texto_bytes):
        return None
    texto = texto_bytes.decode('latin-1').replace('\n', '\\n')
    return {'offset': off, 'total_len': total_len, 'campo_b': campo_b,
            'campo_c': campo_c, 'reservado': reservado, 'texto': texto}


def cmd_voxwalk(args):
    """Camina una cadena de registros de tamano variable (header de 16
    bytes: total_len/campo_b/campo_c/reservado, todos u32 LE, seguido de
    texto ASCII terminado en 0x00 y padding hasta completar total_len).

    A diferencia de movscan, esto NO escanea ASCII a ciegas: usa el campo
    de tamano de cada registro para saltar directo al siguiente, validando
    en cada paso que el resultado siga teniendo forma de registro real.
    Arranca desde un offset ancla ya confirmado (con 'grep', por ejemplo)
    y camina hacia adelante y, opcionalmente, intenta resincronizar hacia
    atras para encontrar el comienzo real de la cadena.

    NOTA: esto solo encuentra la cadena CONTIGUA que contiene el ancla --
    si el archivo tiene una cadena separada por escena (con audio real
    entre medio), usa 'voxscan' para encontrar TODAS las cadenas del
    archivo de una sola pasada.
    """
    data = open(args.binfile, 'rb').read()
    anchor = args.anchor

    def leer_registro(off):
        return leer_registro_vox(data, off, args.max_len, args.min_text)

    # Caminar hacia adelante desde el ancla
    registros = []
    off = anchor
    r = leer_registro(off)
    while r is not None:
        registros.append(r)
        off = r['offset'] + r['total_len']
        r = leer_registro(off)
    fin_valido = off

    # Intentar resincronizar hacia atras: probar cada offset en una ventana
    # antes del ancla y ver si caminando hacia adelante desde ahi se llega
    # exactamente al ancla (confirma que es un comienzo de registro real).
    inicio_real = anchor
    intentos_atras = 0
    if args.walk_back:
        ventana = data[max(0, anchor - args.walk_back):anchor]
        base = max(0, anchor - args.walk_back)
        candidatos_validos = []
        for i in range(len(ventana)):
            cand_off = base + i
            r0 = leer_registro(cand_off)
            if r0 is None:
                continue
            intentos_atras += 1
            # caminar desde el candidato y ver si llega EXACTO al ancla
            o = cand_off
            rr = r0
            while rr is not None and o < anchor:
                o = rr['offset'] + rr['total_len']
                if o == anchor:
                    candidatos_validos.append(cand_off)
                    break
                rr = leer_registro(o)
        if candidatos_validos:
            inicio_real = min(candidatos_validos)
            # re-caminar completo desde el verdadero inicio
            registros = []
            off = inicio_real
            r = leer_registro(off)
            while r is not None:
                registros.append(r)
                off = r['offset'] + r['total_len']
                r = leer_registro(off)
            fin_valido = off

    print(f"archivo        : {args.binfile}")
    print(f"ancla           : 0x{anchor:X}")
    if args.walk_back:
        print(f"inicio real     : 0x{inicio_real:X}  "
              f"({'resincronizado' if inicio_real != anchor else 'ancla = inicio'}, "
              f"{intentos_atras} candidatos probados en la ventana hacia atras)")
    print(f"fin de la cadena: 0x{fin_valido:X}  (primer registro invalido)")
    print(f"registros validos: {len(registros)}\n")

    for r in registros[:args.limit]:
        show = r['texto'] if len(r['texto']) <= 90 else r['texto'][:87] + '...'
        print(f"  0x{r['offset']:08X}  len={r['total_len']:<4} "
              f"b={r['campo_b']:<6} c={r['campo_c']:<6}  {show!r}")
    if len(registros) > args.limit:
        print(f"  ... ({len(registros) - args.limit} mas; usa --limit)")

    if args.out:
        with open(args.out, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['offset', 'total_len', 'campo_b', 'campo_c', 'texto', 'traduccion'])
            for r in registros:
                w.writerow([f"0x{r['offset']:X}", r['total_len'], r['campo_b'],
                           r['campo_c'], r['texto'], ''])
        print(f"\nexportado a {args.out}")
    return 0


def cmd_restore(args):
    """Busca cada '<nombre>.orig' suelto en --origdir y lo copia de vuelta
    a su lugar dentro de --tree, encontrando el destino por nombre de
    archivo (sin necesidad de que vos sepas/escribas la ruta exacta).

    Pensado para el paso 'antes de re-escanear, volver todo a pristino'
    que hace falta cada vez que se corrige la herramienta y ya habia
    builds de prueba escritas encima del arbol de extraccion."""
    import shutil
    origs = sorted(f for f in os.listdir(args.origdir) if f.lower().endswith('.orig'))
    if not origs:
        print(f"no hay archivos .orig en {args.origdir}")
        return 1

    print(f"origdir: {args.origdir}   tree: {args.tree}\n")
    restaurados = saltados = 0
    for nombre_orig in origs:
        nombre_real = nombre_orig[:-len('.orig')]
        candidatos = []
        for dirpath, _dirnames, filenames in os.walk(args.tree):
            for f in filenames:
                if f.lower() == nombre_real.lower():
                    candidatos.append(os.path.join(dirpath, f))

        origpath = os.path.join(args.origdir, nombre_orig)
        if len(candidatos) == 0:
            print(f"  {nombre_orig:<30}  SALTEADO -- no encontre {nombre_real!r} en {args.tree}")
            saltados += 1
            continue
        if len(candidatos) > 1:
            print(f"  {nombre_orig:<30}  SALTEADO -- {nombre_real!r} aparece "
                  f"{len(candidatos)} veces en {args.tree}, ambiguo:")
            for c in candidatos:
                print(f"      {c}")
            saltados += 1
            continue

        destino = candidatos[0]
        if args.dry_run:
            print(f"  {nombre_orig:<30}  -> {destino}   (dry-run, no se copio)")
        else:
            shutil.copyfile(origpath, destino)
            print(f"  {nombre_orig:<30}  -> {destino}   restaurado")
        restaurados += 1

    print(f"\n{restaurados} restaurado(s), {saltados} salteado(s)"
          f"{'  (dry-run: nada se toco de verdad)' if args.dry_run else ''}")
    return 0


def cmd_synctrad(args):
    """Copia traducciones ya cargadas de un CSV FUENTE a un CSV DESTINO
    cuando el texto en ingles coincide (normalizado: sin espacios en las
    puntas, sin puntuacion final, sin importar mayus/minus). Pensado para
    archivos que resultaron tener el mismo bloque de strings duplicado
    fisicamente en otro archivo (ej. MENU.BIN e ITP.BIN comparten un
    cluster de mensajes de tarjeta de memoria byte a byte identico) --
    asi no hay que traducir la misma frase dos veces a mano.

    IMPORTANTE: el margen disponible (offset_fin - offset) de una entrada
    NO tiene por que ser igual en los dos archivos, aunque el contenido en
    ingles sea identico -- depende de que otra cadena viene despues en
    cada uno. Por eso esto SIEMPRE valida, fila por fila del destino, que
    la traduccion candidata entre en SU propio margen (con el mismo
    encoder que usa textbuild, respetando --charmap si se pasa) antes de
    copiarla. Si no entra, la fila queda vacia (se conserva el ingles al
    hacer build) en vez de copiar algo que despues textbuild rechazaria
    -- o peor, que alguna otra ruta corte a la fuerza.

    Nunca pisa una traduccion que el destino ya tenga cargada, salvo que
    pases --force."""
    def norm(t):
        return t.strip().rstrip('.?!,:').strip().lower()

    cm = load_charmap(args.charmap) if args.charmap and load_charmap else {}

    def entra(texto_ingles_original, trad, disponible):
        """Misma logica que textbuild: coemitting bytes con charmap+tokens,
        +1 de terminador, contra el espacio disponible de ESTA fila."""
        try:
            mapeado = apply_charmap(trad, cm) if cm else trad
            check_reserved(trad, cm)
            payload = from_tokens(mapeado, ascii_only=True)
        except (ValueError, TypeError):
            return False, 0
        return (len(payload) + 1) <= disponible, len(payload) + 1

    fuente = list(csv.DictReader(open(args.fuente, encoding='utf-8')))
    destino = list(csv.DictReader(open(args.destino, encoding='utf-8')))

    fuente_por_norm = {}
    for r in fuente:
        trad = (r.get('traduccion') or '').strip()
        if not trad:
            continue
        n = norm(r.get('texto') or '')
        if not n:
            continue
        fuente_por_norm.setdefault(n, []).append(trad)

    # si un mismo texto normalizado tiene traducciones distintas en la
    # fuente, no adivinamos cual -- se reporta como ambiguo y no se copia
    ambiguos = {n for n, ts in fuente_por_norm.items() if len(set(ts)) > 1}

    copiadas = pisadas = sin_match = ambiguo_saltado = ya_tenia = no_entra = 0
    detalle_no_entra = []
    for r in destino:
        n = norm(r.get('texto') or '')
        ya_tiene = (r.get('traduccion') or '').strip()
        if n in ambiguos:
            if not ya_tiene:
                ambiguo_saltado += 1
            continue
        candidatos = fuente_por_norm.get(n)
        if not candidatos:
            if not ya_tiene:
                sin_match += 1
            continue
        if ya_tiene and not args.force:
            ya_tenia += 1
            continue

        try:
            disponible = int(r['offset_fin'], 0) - int(r['offset'], 0)
        except (KeyError, ValueError):
            disponible = 0
        ok, necesario = entra(r.get('texto') or '', candidatos[0], disponible)
        if not ok:
            no_entra += 1
            detalle_no_entra.append((r.get('offset', '?'), necesario, disponible, candidatos[0]))
            continue

        if ya_tiene and args.force:
            pisadas += 1
        else:
            copiadas += 1
        r['traduccion'] = candidatos[0]

    print(f"fuente : {args.fuente}  ({len(fuente)} filas, "
          f"{sum(1 for r in fuente if (r.get('traduccion') or '').strip())} traducidas)")
    print(f"destino: {args.destino}  ({len(destino)} filas)\n")
    print(f"copiadas nuevas       : {copiadas}")
    if args.force:
        print(f"pisadas (--force)     : {pisadas}")
    print(f"ya tenian traduccion  : {ya_tenia}  (no tocadas; usa --force para pisarlas)")
    print(f"sin match en la fuente: {sin_match}")
    print(f"NO ENTRAN en el destino: {no_entra}  (quedaron vacias -- se conserva el ingles)")
    if detalle_no_entra:
        print("\n-- filas que no entraron (offset destino, necesario/disponible, traduccion) --")
        for off, nec, disp, trad in detalle_no_entra[:args.limit]:
            show = trad if len(trad) <= 50 else trad[:47] + '...'
            print(f"  {off}  {nec}B/{disp}B  {show!r}")
        if len(detalle_no_entra) > args.limit:
            print(f"  ... ({len(detalle_no_entra) - args.limit} mas; usa --limit)")
    if ambiguo_saltado:
        print(f"\nambiguas (mismo texto, distinta traduccion en la fuente): {ambiguo_saltado} "
              f"-- resolvelas a mano")

    out_path = args.out or args.destino
    fieldnames = list(destino[0].keys()) if destino else []
    with open(out_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(destino)
    print(f"\nescrito: {out_path}")
    return 0


def cmd_textset(args):
    """Escribe una traduccion en UNA fila de un CSV de movscan/textscan,
    identificada por su 'offset' (columna clave de estos CSV, ya que no
    tienen un 'id' numerico como los de sz_text.py). Equivalente de
    'sz_text.py set' para este formato de CSV. Si se pasa --charmap,
    valida que el texto entre en la fuente antes de guardarlo (no escribe
    bytes en el binario, eso lo hace 'textbuild')."""
    rows = list(csv.DictReader(open(args.csvfile, encoding='utf-8')))
    if not rows:
        print("CSV vacio")
        return 1
    key = args.offset.strip()
    if not key.lower().startswith('0x'):
        key = '0x' + key
    hit = None
    for r in rows:
        if (r.get('offset') or '').strip().lower() == key.lower():
            hit = r
            break
    if hit is None:
        print(f"no encontre una fila con offset {key} en {args.csvfile} "
              "(copiala tal cual de la columna 'offset' del CSV)")
        return 1

    if args.charmap:
        if load_charmap is None:
            print("ERROR: no encontre sz_text.py en esta carpeta (hace falta para --charmap).")
            return 1
        try:
            cm = load_charmap(args.charmap)
            check_reserved(args.texto, cm)
            from_tokens(apply_charmap(args.texto, cm), ascii_only=True)
        except ValueError as e:
            print(f"texto invalido: {e}")
            return 1

    hit['traduccion'] = args.texto
    fn = list(rows[0].keys())
    with open(args.csvfile, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=fn)
        w.writeheader()
        w.writerows(rows)
    print(f"offset {key}: {hit.get('texto', '')[:60]!r}\n"
          f"  -> traduccion: {args.texto!r}")
    return 0


def cmd_margen(args):
    """Analiza, por cada fila de un CSV de movscan/textscan, cuanto margen
    real hay para que la traduccion sea mas larga que el ingles original:
    margen_bytes = (offset_fin - offset) - (len(texto) + 1 terminador)
    margen_pct   = margen_bytes / len(texto) * 100

    Sirve para saber CUANTAS lineas ya soportan un crecimiento del X% con
    el espacio que ya existe (sin tocar nada del video/audio), antes de
    asumir que hay que abreviar en todos lados."""
    rows = list(csv.DictReader(open(args.csv, encoding='utf-8')))
    datos = []
    for r in rows:
        try:
            off = int(r['offset'], 0)
            fin = int(r['offset_fin'], 0)
        except (KeyError, ValueError):
            continue
        texto = (r.get('texto') or '').replace('\\n', '\n')
        if not texto:
            continue
        disponible = fin - off
        usado = len(texto) + 1  # +terminador
        margen_bytes = disponible - usado
        margen_pct = (margen_bytes / len(texto) * 100) if len(texto) else 0
        datos.append((off, texto, len(texto), disponible, margen_bytes, margen_pct))

    if not datos:
        print("no encontre filas con offset/offset_fin/texto en este CSV")
        return 1

    umbral = args.umbral
    cumplen = [d for d in datos if d[5] >= umbral]
    no_cumplen = [d for d in datos if d[5] < umbral]

    print(f"lineas analizadas: {len(datos)}")
    print(f"cumplen >= {umbral}% de margen : {len(cumplen)} "
          f"({100*len(cumplen)/len(datos):.1f}%)")
    print(f"NO llegan a {umbral}% de margen : {len(no_cumplen)} "
          f"({100*len(no_cumplen)/len(datos):.1f}%)\n")

    no_cumplen.sort(key=lambda d: d[5])  # peor margen primero
    print(f"-- las {min(args.limit, len(no_cumplen))} lineas con MENOS margen --")
    for off, texto, largo, disp, mb, mp in no_cumplen[:args.limit]:
        show = texto if len(texto) <= 50 else texto[:47] + '...'
        signo = '' if mb >= 0 else '¡NEGATIVO! '
        print(f"  0x{off:08X}  margen={mp:+6.1f}% ({mb:+4d}B)  {signo}{show!r}")

    if args.out:
        with open(args.out, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['offset', 'texto', 'largo_en', 'disponible',
                       'margen_bytes', 'margen_pct'])
            for off, texto, largo, disp, mb, mp in sorted(datos, key=lambda d: d[5]):
                w.writerow([f"0x{off:X}", texto, largo, disp, mb, f"{mp:.1f}"])
        print(f"\nexportado a {args.out} (ordenado de menor a mayor margen)")
    return 0


def cmd_textbuild(args):
    """Reinserta las traducciones de un CSV generado por movscan o textscan
    (comparten las columnas offset/offset_fin/encoding/texto/traduccion)
    de vuelta en el binario original.

    Politica de seguridad -- SIN mecanismo de reubicacion (a diferencia de
    los GAME*.SZ, que usan el truco DATCH de punteros + espacio libre):
      - Si la traduccion entra en el espacio original (offset_fin-offset,
        dejando lugar para el terminador 0x00), se escribe y se rellena
        el resto con 0x00.
      - Si NO entra, esa fila se rechaza (no se escribe nada ahi) y se
        reporta como error -- nunca se pisa el chunk/registro siguiente.
      - El '\\n' del CSV se reinserta siempre como un 0x0A simple (no el
        esquema 0x80+'|' que tambien vimos en algunos .MOV) -- probar en
        emulador si el motor lo requiere distinto para video.

    Verifica leyendo el archivo de salida y comparando byte a byte contra
    lo que se penso escribir, antes de darlo por bueno."""
    data = bytearray(open(args.binfile, 'rb').read())
    rows = list(csv.DictReader(open(args.csv, encoding='utf-8')))
    if not rows:
        print("CSV vacio.")
        return 1

    if args.charmap and load_charmap is None:
        print("ERROR: no encontre sz_text.py en esta carpeta (hace falta para --charmap).")
        return 1
    cm = load_charmap(args.charmap) if args.charmap and load_charmap else {}
    escrituras = []   # (offset, fin, payload) para detectar solapes y verificar
    resultados = []   # (fila, estado, detalle)

    for idx, r in enumerate(rows):
        trad = (r.get('traduccion') or '').strip()
        if not trad:
            continue

        off_s, fin_s = r.get('offset', ''), r.get('offset_fin', '')
        if not off_s or not fin_s:
            resultados.append((idx, 'ERROR', 'falta offset/offset_fin en esta fila '
                               '(este CSV no es de movscan/textscan?)'))
            continue
        try:
            off, fin = int(off_s, 0), int(fin_s, 0)
        except ValueError:
            resultados.append((idx, 'ERROR', f'offset invalido: {off_s!r}/{fin_s!r}'))
            continue
        if fin <= off or fin > len(data):
            resultados.append((idx, 'ERROR', f'rango invalido 0x{off:X}..0x{fin:X}'))
            continue

        enc = (r.get('encoding') or 'ascii').strip() or 'ascii'
        try:
            mapeado = apply_charmap(trad, cm) if cm else trad
            check_reserved(trad, cm)
            payload_plano = from_tokens(mapeado, ascii_only=True)
        except ValueError as e:
            resultados.append((idx, 'ERROR', str(e)))
            continue
        if args.newline_byte != 0x0A:
            payload_plano = payload_plano.replace(b'\x0A', bytes([args.newline_byte]))
        if enc == 'ascii80':
            payload = bytearray()
            for b in payload_plano:
                payload += bytes([0x80, b])
            payload = bytes(payload)
        else:
            payload = payload_plano

        disponible = fin - off
        necesario = len(payload) + 1  # +terminador 0x00
        if necesario > disponible:
            resultados.append((idx, 'ERROR',
                f'traduccion ({necesario}B con terminador) no entra en el espacio '
                f'original ({disponible}B); sobran {necesario - disponible}B. '
                f'Acortala. texto: {trad[:60]!r}'))
            continue

        escrituras.append((off, fin, payload))
        resultados.append((idx, 'OK', f'0x{off:X}..0x{fin:X}  {len(payload)}/{disponible}B'))

    # Detectar solapes entre escrituras antes de tocar nada.
    escrituras.sort()
    for k in range(1, len(escrituras)):
        if escrituras[k][0] < escrituras[k - 1][1]:
            print(f"ERROR: las filas con offset 0x{escrituras[k-1][0]:X} y "
                  f"0x{escrituras[k][0]:X} se solapan -- no se escribio nada.")
            return 1

    for off, fin, payload in escrituras:
        bloque = payload + b'\x00' * (fin - off - len(payload))
        data[off:fin] = bloque

    ok = sum(1 for _, s, _ in resultados if s == 'OK')
    err = sum(1 for _, s, _ in resultados if s == 'ERROR')
    print(f"filas con traduccion : {len(resultados)}   OK: {ok}   ERROR: {err}\n")
    for idx, s, det in resultados:
        if s == 'ERROR' or args.verbose:
            print(f"  fila {idx:<5} [{s}]  {det}")

    if err and not args.force:
        print(f"\n{err} fila(s) con error -- no se escribio el archivo de salida.\n"
              "Corregi esas traducciones o pasa --force para escribir igual "
              "las que sí entraron (las que fallaron quedan en ingles).")
        return 1

    if not args.out:
        print("\n(no se paso -o, no se escribio ningun archivo; esto fue solo "
              "una validacion en seco)")
        return 0

    open(args.out, 'wb').write(bytes(data))

    # Verificacion: releer el archivo de salida y comparar byte a byte.
    releido = open(args.out, 'rb').read()
    fallos_verif = 0
    for off, fin, payload in escrituras:
        if releido[off:off + len(payload)] != payload:
            fallos_verif += 1
            print(f"  VERIFICACION FALLO en 0x{off:X}: lo escrito no coincide "
                  "con lo que se penso escribir")
    if fallos_verif:
        print(f"\n{fallos_verif} fallo(s) de verificacion -- revisar antes de usar "
              f"{args.out}")
        return 1

    print(f"\nVERIFICACION: OK ({len(escrituras)} escrituras releidas y confirmadas)")
    print(f"escrito: {args.out}")
    return 0


def cmd_textscan(args):
    """Escaneo de texto SIN asumir ninguna estructura de chunk/header --
    a diferencia de movscan (que corta el archivo en chunks por un patron
    de bytes) o voxwalk/voxscan (que asumen un formato de registro con
    campo de tamano), esto solo busca rachas ASCII/ascii80 en todo el
    archivo de punta a punta y les aplica la misma clasificacion validada
    (caracteres prohibidos, ruido repetitivo, palabra reconocible).

    Util cuando la hipotesis de registro/chunk no vale para toda una zona
    del archivo (p.ej. el bloque de audio real de PN_VOX1.PAC, donde el
    texto esta pegado en algun punto de cada muestra de voz sin un header
    fijo detectable)."""
    data = open(args.binfile, 'rb').read()
    forbidden = set(args.forbidden) if args.forbidden else set()

    print(f"archivo   : {args.binfile}  ({len(data)} bytes)")
    print(f"minlen    : {args.minlen} caracteres")
    if forbidden:
        print(f"prohibidos: {''.join(sorted(forbidden))!r}")
    print()

    hallazgos = []
    descartados_prohibido = 0
    for name, fn in ENCODINGS.items():
        for s, e, t in fn(data, args.minlen):
            if forbidden and any(ch in forbidden for ch in t):
                descartados_prohibido += 1
                continue
            hallazgos.append((s, e, name, t))
    hallazgos.sort()
    if forbidden:
        print(f"(descartadas {descartados_prohibido} racha(s) por caracteres prohibidos)\n")

    # Extender offset_fin solo con bytes 0x00 verificados en el original,
    # acotado por el proximo hallazgo (nunca invade otra cadena) -- misma
    # tecnica de seguridad que aplicamos a movscan despues de que la
    # version anterior (que asumia todo el tramo libre) corrompiera un
    # video. Sin esto, el margen para crecer queda en CERO en archivos
    # de slots ajustados como MENU.BIN, aunque haya padding real.
    for k in range(len(hallazgos)):
        s, e_texto, name, t = hallazgos[k]
        limite_duro = hallazgos[k + 1][0] if k + 1 < len(hallazgos) else len(data)
        p = e_texto
        while p < limite_duro and data[p] == 0x00:
            p += 1
        hallazgos[k] = (s, p, name, t)

    # clasificar_hallazgos espera texto en la posicion [2] -- (s, enc, texto, ...);
    # acomodamos una vista temporal solo para clasificar sin duplicar la logica
    vista_clasificar = [(s, name, t, e) for s, e, name, t in hallazgos]
    con_palabra, sin_palabra, probable_ruido, patrones_ruido, es_ruido, tiene_palabra = \
        clasificar_hallazgos(vista_clasificar, args.noise_threshold, args.single_token_minlen)

    print(f"-- {len(hallazgos)} racha(s) totales: "
          f"{len(con_palabra)} con palabra reconocible, "
          f"{len(sin_palabra)} sin palabra reconocible (revisar a mano), "
          f"{len(probable_ruido)} probable ruido repetitivo --\n")

    print(f"-- con palabra reconocible ({len(con_palabra)}) --")
    for abs_off, enc, texto, _e in con_palabra[:args.limit]:
        show = texto.replace('\n', '\\n')
        show = show if len(show) <= 90 else show[:87] + '...'
        print(f"  0x{abs_off:08X}  [{enc:7}]  {show!r}")
    if len(con_palabra) > args.limit:
        print(f"  ... ({len(con_palabra) - args.limit} mas; usa --limit)")

    if sin_palabra:
        print(f"\n-- sin palabra reconocible ({len(sin_palabra)}) -- revisar a mano --")
        for abs_off, enc, texto, _e in sin_palabra[:args.limit]:
            show = texto.replace('\n', '\\n')
            show = show if len(show) <= 90 else show[:87] + '...'
            print(f"  0x{abs_off:08X}  [{enc:7}]  {show!r}")
        if len(sin_palabra) > args.limit:
            print(f"  ... ({len(sin_palabra) - args.limit} mas; usa --limit)")

    if probable_ruido:
        print(f"\n(omitidos {len(probable_ruido)} hallazgos de ruido repetitivo "
              f"-- patrones base: {patrones_ruido[:10]}"
              f"{' ...' if len(patrones_ruido) > 10 else ''})")

    if args.out:
        with open(args.out, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['offset', 'offset_fin', 'largo_original', 'encoding', 'texto',
                       'ruido_probable', 'palabra_reconocible', 'traduccion'])
            for s, e, enc, texto in hallazgos:
                texto_csv = texto.replace('\n', '\\n')
                w.writerow([f"0x{s:X}", f"0x{e:X}", e - s, enc, texto_csv,
                           'si' if es_ruido(texto) else '',
                           'si' if tiene_palabra(texto) else '', ''])
        print(f"\nexportado a {args.out}")
    return 0


def cmd_voxscan(args):
    """Recorre el archivo ENTERO buscando todas las cadenas de registros
    validas, sin necesitar un ancla. Cada escena probablemente tiene su
    propia cadena de texto, separada de las demas por audio real (que no
    tiene forma de header valido) -- esto encuentra todas, no solo una.

    Estrategia: probar cada posicion alineada a 4 bytes como posible inicio
    de cadena; si camina hacia adelante y da al menos --min-chain registros
    validos consecutivos, se acepta como cadena real y se salta hasta su
    final antes de seguir buscando (evita volver a escanear lo ya cubierto
    y evita reportar sub-cadenas que empiezan en medio de otra ya valida).
    """
    data = open(args.binfile, 'rb').read()
    n = len(data)
    step = args.step

    cadenas = []
    pos = 0
    total_registros = 0
    while pos + 16 <= n:
        r = leer_registro_vox(data, pos, args.max_len, args.min_text)
        if r is None:
            pos += step
            continue

        registros = [r]
        off = r['offset'] + r['total_len']
        rr = leer_registro_vox(data, off, args.max_len, args.min_text)
        while rr is not None:
            registros.append(rr)
            off = rr['offset'] + rr['total_len']
            rr = leer_registro_vox(data, off, args.max_len, args.min_text)

        if len(registros) >= args.min_chain:
            cadenas.append({'inicio': pos, 'fin': off, 'registros': registros})
            total_registros += len(registros)
            pos = off  # saltar toda la cadena ya cubierta
        else:
            pos += step

    print(f"archivo    : {args.binfile}  ({n} bytes)")
    print(f"cadenas encontradas : {len(cadenas)}")
    print(f"registros totales   : {total_registros}\n")

    for i, c in enumerate(cadenas):
        primero = c['registros'][0]['texto']
        show = primero if len(primero) <= 50 else primero[:47] + '...'
        print(f"  cadena #{i:<3}  0x{c['inicio']:08X} .. 0x{c['fin']:08X}  "
              f"({len(c['registros']):3d} registros)  primer texto: {show!r}")

    if args.out:
        with open(args.out, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['cadena', 'offset', 'total_len', 'campo_b', 'campo_c',
                       'texto', 'traduccion'])
            for i, c in enumerate(cadenas):
                for r in c['registros']:
                    w.writerow([i, f"0x{r['offset']:X}", r['total_len'],
                               r['campo_b'], r['campo_c'], r['texto'], ''])
        print(f"\nexportado a {args.out}  ({total_registros} registros, "
              f"{len(cadenas)} cadenas/escenas)")
    return 0


def main():
    p = argparse.ArgumentParser(
        description="Toolkit de texto para Policenauts PSX (base EN / DATCH)")
    sub = p.add_subparsers(dest='cmd', required=True)

    def common(sp):
        sp.add_argument('--encoding', default='auto',
                        choices=['auto', 'ascii', 'ascii80'])
        sp.add_argument('--minlen', type=int, default=4,
                        help='largo minimo de racha en caracteres')

    sp = sub.add_parser('scan', help='listar bloques de texto')
    sp.add_argument('binfile')
    sp.add_argument('--limit', type=int, default=40)
    common(sp)
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser('gaps', help='histograma de bytes entre textos')
    sp.add_argument('binfile')
    sp.add_argument('--top', type=int, default=12)
    common(sp)
    sp.set_defaults(func=cmd_gaps)

    sp = sub.add_parser('probe', help='validar hipotesis de formato DATCH')
    sp.add_argument('binfile')
    sp.add_argument('--marker', type=lambda x: int(x, 0), required=True)
    sp.add_argument('--width', type=int, default=4, choices=[2, 3, 4])
    sp.add_argument('--endian', default='little', choices=['little', 'big'])
    sp.add_argument('--base', type=lambda x: int(x, 0), default=0,
                    help='direccion base a restar (0 si son offsets de archivo)')
    sp.add_argument('--limit', type=int, default=15)
    common(sp)
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser('dump', help='exportar textos a CSV')
    sp.add_argument('binfile')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--marker', type=lambda x: int(x, 0), default=0x00)
    common(sp)
    sp.set_defaults(func=cmd_dump)

    sp = sub.add_parser('build', help='reinsertar CSV traducido')
    sp.add_argument('binfile')
    sp.add_argument('csv')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--marker', type=lambda x: int(x, 0), required=True)
    sp.add_argument('--width', type=int, default=4, choices=[2, 3, 4])
    sp.add_argument('--endian', default='little', choices=['little', 'big'])
    sp.add_argument('--base', type=lambda x: int(x, 0), default=0)
    sp.add_argument('--fill', type=lambda x: int(x, 0), default=0x00,
                    help='byte de relleno para el espacio libre (def 0x00)')
    sp.add_argument('--expand-at', type=lambda x: int(x, 0), default=None,
                    help='offset de una zona muerta del archivo reutilizable '
                         'como espacio extra de texto (verificala antes!)')
    sp.add_argument('--expand-size', type=int, default=0,
                    help='tamano en bytes de la zona de expansion')
    sp.add_argument('--terminator', type=lambda x: int(x, 0), default=None,
                    help='byte terminador de cadena, si el formato lo usa '
                         '(por defecto ninguno: el marcador ya delimita)')
    common(sp)
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser('dpk-list', help='listar directorio de un contenedor FRID (.DPK)')
    sp.add_argument('binfile')
    sp.set_defaults(func=cmd_dpk_list)

    sp = sub.add_parser('dpk-extract', help='extraer subarchivos de un contenedor FRID (.DPK)')
    sp.add_argument('binfile')
    sp.add_argument('-o', '--outdir', required=True)
    sp.set_defaults(func=cmd_dpk_extract)

    sp = sub.add_parser('dpk-diff', help='comparar directorios FRID de JP vs EN')
    sp.add_argument('file_a')
    sp.add_argument('file_b')
    sp.set_defaults(func=cmd_dpk_diff)

    sp = sub.add_parser('header', help='inspeccionar cabecera cruda / buscar tabla de offsets')
    sp.add_argument('binfile')
    sp.add_argument('--bytes', type=int, default=256, help='cuantos bytes volcar')
    sp.add_argument('--detect-offsets', action='store_true')
    sp.add_argument('--width', type=int, default=4, choices=[2, 4])
    sp.add_argument('--endian', default='little', choices=['little', 'big'])
    sp.add_argument('--entries', type=int, default=32)
    sp.set_defaults(func=cmd_header)

    sp = sub.add_parser('grep', help='buscar frases literales (ascii/ascii80) '
                                      'en un archivo o carpeta entera')
    sp.add_argument('target', help='archivo o carpeta (ej. bin\\ para recorrer todo)')
    sp.add_argument('--strings', nargs='+', help='una o mas frases a buscar')
    sp.add_argument('--strings-file', help='.txt con una frase por linea')
    sp.set_defaults(func=cmd_grep)

    sp = sub.add_parser('around', help='ver chunks/sectores alrededor de un offset '
                                        '(mapear estructura de un .MOV/STR)')
    sp.add_argument('binfile')
    sp.add_argument('--offset', type=lambda x: int(x, 0), required=True)
    sp.add_argument('--chunk', type=int, default=2048, help='tamano de chunk/sector')
    sp.add_argument('--context', type=int, default=3, help='chunks antes/despues a mostrar')
    sp.add_argument('--header', type=int, default=32, help='bytes de header a volcar por chunk')
    sp.set_defaults(func=cmd_around)

    sp = sub.add_parser('hexdump', help='volcar bytes alrededor de un offset arbitrario '
                                         '(sin atar a limites de chunk/sector)')
    sp.add_argument('binfile')
    sp.add_argument('--offset', type=lambda x: int(x, 0), required=True)
    sp.add_argument('--before', type=int, default=64)
    sp.add_argument('--after', type=int, default=64)
    sp.add_argument('--marklen', type=int, default=0,
                    help='opcional: cuantos bytes desde --offset resaltar entre []')
    sp.set_defaults(func=cmd_hexdump)

    sp = sub.add_parser('findhex', help='buscar un patron de bytes crudo (hex) '
                                         'y ver campos u16/u32 posteriores')
    sp.add_argument('target', help='archivo o carpeta')
    sp.add_argument('--pattern', required=True,
                    help='hex sin espacios, ej. 0038????0100')
    sp.add_argument('--after', type=int, default=24)
    sp.add_argument('--before-offset', type=lambda x: int(x, 0), default=None,
                    help='si se pasa, busca solo la ULTIMA ocurrencia antes '
                         'de este offset (para hallar el header de un chunk)')
    sp.set_defaults(func=cmd_findhex)

    sp = sub.add_parser('chunkmap', help='mapear todos los chunks de un archivo '
                                          'por un patron de header y detectar outliers de tamano')
    sp.add_argument('binfile')
    sp.add_argument('--pattern', default='0038????0100',
                    help='hex sin espacios del header a buscar')
    sp.add_argument('--marks', default=None,
                    help='offsets conocidos separados por coma (ej. 0x1806C,0x986AC) '
                         'para ver en que chunk caen')
    sp.add_argument('--top', type=int, default=10)
    sp.set_defaults(func=cmd_chunkmap)

    sp = sub.add_parser('movscan', help='extraer automaticamente subtitulos de un .MOV '
                                         'escaneando ASCII en la cola de cada chunk')
    sp.add_argument('binfile')
    sp.add_argument('--pattern', default='0038????0100')
    sp.add_argument('--tail', type=int, default=250,
                    help='cuantos bytes del final de cada chunk revisar')
    sp.add_argument('--minlen', type=int, default=8,
                    help='largo minimo de racha ASCII para considerarla texto')
    sp.add_argument('-o', '--out', default=None, help='opcional: exportar a CSV')
    sp.add_argument('--noise-threshold', type=int, default=3,
                    help='si una racha (sin contar digitos/puntuacion finales) se '
                         'repite esta cantidad de veces o mas, se marca como ruido')
    sp.add_argument('--single-token-minlen', type=int, default=10,
                    help='largo minimo de un token SIN espacios y SIN palabra '
                         'reconocible para tratarlo como ruido (default 10)')
    sp.add_argument('--forbidden', default='$*;<=>[\\]{@~^_`#',
                    help='caracteres que descartan una racha si aparecen (default: '
                         'los que el guion nunca usa; "|" se deja afuera porque '
                         'aparece como marcador de salto de linea en texto real). '
                         'Pasa "" para desactivar.')
    sp.set_defaults(func=cmd_movscan)

    sp = sub.add_parser('movscan-all', help='correr movscan sobre TODOS los .MOV de una '
                                             'carpeta y guardar un CSV por video')
    sp.add_argument('srcdir', help='carpeta con los .MOV (ej. bin\\extracted_en1\\NAUTS\\MOVIE)')
    sp.add_argument('-o', '--outdir', default=os.path.join('por_traducir', 'subtitulos_cinematicas'),
                    help='carpeta de salida para los CSV (default: '
                         'por_traducir\\subtitulos_cinematicas en la raiz del repo)')
    sp.add_argument('--ext', default='.mov,.str',
                    help='extensiones a procesar, separadas por coma (default: .mov,.str)')
    sp.add_argument('--pattern', default='0038????0100')
    sp.add_argument('--tail', type=int, default=250)
    sp.add_argument('--minlen', type=int, default=8)
    sp.add_argument('--noise-threshold', type=int, default=3)
    sp.add_argument('--single-token-minlen', type=int, default=10)
    sp.add_argument('--forbidden', default='$*;<=>[\\]{@~^_`#')
    sp.set_defaults(func=cmd_movscanall)

    sp = sub.add_parser('restore', help='restaurar todos los .orig sueltos de una '
                                         'carpeta a su lugar dentro del arbol, por nombre')
    sp.add_argument('origdir', help='carpeta con los .orig (ej. bin)')
    sp.add_argument('tree', help='raiz del arbol donde buscar el destino '
                                  '(ej. bin\\extracted_en1)')
    sp.add_argument('--dry-run', action='store_true',
                    help='mostrar que se restauraria, sin copiar nada de verdad')
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser('synctrad', help='copiar traducciones de un CSV a otro cuando el '
                                          'texto en ingles coincide (evita traducir '
                                          'el mismo bloque duplicado dos veces)')
    sp.add_argument('fuente', help='CSV con traducciones ya cargadas')
    sp.add_argument('destino', help='CSV a completar')
    sp.add_argument('-o', '--out', default=None,
                    help='archivo de salida (default: sobrescribe destino)')
    sp.add_argument('--force', action='store_true',
                    help='pisar traducciones que el destino ya tenga cargadas')
    sp.add_argument('--charmap', default=None,
                    help='JSON de fontlab.py accents, para medir el largo real '
                         '(acentos, tokens {XX}) al chequear si entra')
    sp.add_argument('--limit', type=int, default=30)
    sp.set_defaults(func=cmd_synctrad)

    sp = sub.add_parser('textset', help='escribir una traduccion en una fila de un CSV '
                                         'de movscan/textscan, por su offset')
    sp.add_argument('csvfile')
    sp.add_argument('offset', help='columna offset de la fila (ej. 0x14654)')
    sp.add_argument('texto', help='traduccion a escribir (usa \\n para salto de linea)')
    sp.add_argument('--charmap', default=None,
                    help='JSON de fontlab.py accents: valida que el texto entre en la '
                         'fuente antes de guardarlo (no escribe bytes, solo valida)')
    sp.set_defaults(func=cmd_textset)

    sp = sub.add_parser('margen', help='analizar cuanto margen real hay en cada fila '
                                        'de un CSV para que la traduccion crezca')
    sp.add_argument('csv')
    sp.add_argument('--umbral', type=float, default=15.0,
                    help='%% de crecimiento que se considera suficiente (default 15)')
    sp.add_argument('--limit', type=int, default=30)
    sp.add_argument('-o', '--out', default=None,
                    help='opcional: exportar todo ordenado de menor a mayor margen')
    sp.set_defaults(func=cmd_margen)

    sp = sub.add_parser('textbuild', help='reinsertar traducciones de un CSV (formato '
                                           'compartido movscan/textscan) en el binario')
    sp.add_argument('binfile', help='el .MOV o PN_VOX1.PAC ORIGINAL (sin traducir)')
    sp.add_argument('csv', help='CSV con la columna traduccion completada')
    sp.add_argument('-o', '--out', default=None,
                    help='archivo de salida; si se omite, solo valida en seco')
    sp.add_argument('--newline-byte', type=lambda x: int(x, 0), default=0x0A,
                    help='byte a usar para cada \\n del CSV (default 0x0A)')
    sp.add_argument('--force', action='store_true',
                    help='escribir igual las filas que SI entraron aunque otras hayan '
                         'fallado (las fallidas quedan en ingles)')
    sp.add_argument('--charmap', default=None,
                    help='JSON de fontlab.py accents: convierte a e i o u n ! ? '
                         '(y lo que hayas mapeado) a sus ranuras en la fuente')
    sp.add_argument('--verbose', action='store_true', help='mostrar tambien las filas OK')
    sp.set_defaults(func=cmd_textbuild)

    sp = sub.add_parser('textscan', help='escanear TODO un archivo buscando ASCII/ascii80 '
                                          'sin asumir estructura de chunk/registro')
    sp.add_argument('binfile')
    sp.add_argument('--minlen', type=int, default=10)
    sp.add_argument('--forbidden', default='$*;<=>[\\]{@~^_`#')
    sp.add_argument('--noise-threshold', type=int, default=3)
    sp.add_argument('--single-token-minlen', type=int, default=10,
                    help='largo minimo de un token SIN espacios y SIN palabra '
                         'reconocible para tratarlo como ruido (default 10)')
    sp.add_argument('--limit', type=int, default=200)
    sp.add_argument('-o', '--out', default=None, help='opcional: exportar a CSV')
    sp.set_defaults(func=cmd_textscan)

    sp = sub.add_parser('voxwalk', help='caminar una cadena de registros tamano-variable '
                                         '(tipo PN_VOX1.PAC) partiendo de un offset ancla')
    sp.add_argument('binfile')
    sp.add_argument('--anchor', type=lambda x: int(x, 0), required=True,
                    help='offset donde arranca un header de registro confirmado '
                         '(ej. con grep + hexdump)')
    sp.add_argument('--max-len', type=int, default=4096,
                    help='total_len maximo aceptado como valido (default 4096)')
    sp.add_argument('--min-text', type=int, default=2,
                    help='largo minimo del texto para considerar el registro valido')
    sp.add_argument('--walk-back', type=int, default=0,
                    help='bytes hacia atras del ancla a probar para resincronizar '
                         'el comienzo real de la cadena (0 = no intentar)')
    sp.add_argument('--limit', type=int, default=100)
    sp.add_argument('-o', '--out', default=None, help='opcional: exportar a CSV')
    sp.set_defaults(func=cmd_voxwalk)

    sp = sub.add_parser('voxscan', help='recorrer el archivo ENTERO buscando todas las '
                                         'cadenas de registros validas (una por escena)')
    sp.add_argument('binfile')
    sp.add_argument('--max-len', type=int, default=4096)
    sp.add_argument('--min-text', type=int, default=2)
    sp.add_argument('--min-chain', type=int, default=3,
                    help='minimo de registros consecutivos validos para aceptar '
                         'una cadena como real (evita falsos positivos, default 3)')
    sp.add_argument('--step', type=int, default=4,
                    help='paso entre posiciones candidatas a probar (default 4, '
                         'asumiendo alineacion de 4 bytes de los headers)')
    sp.add_argument('-o', '--out', default=None, help='opcional: exportar a CSV')
    sp.set_defaults(func=cmd_voxscan)

    sp = sub.add_parser('diff', help='comparar dos binarios (JP vs EN)')
    sp.add_argument('file_a')
    sp.add_argument('file_b')
    sp.add_argument('--mingap', type=int, default=16)
    sp.add_argument('--limit', type=int, default=60)
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser('diffcheck', help='cruzar un diff JP/EN contra un CSV de '
                                           'movscan/textscan: que diferencias son '
                                           'subtitulos conocidos y cuales no')
    sp.add_argument('file_a', help='JP (u original)')
    sp.add_argument('file_b', help='EN (o parcheado)')
    sp.add_argument('csv', help='CSV de movscan/textscan del archivo B')
    sp.add_argument('--mingap', type=int, default=16)
    sp.add_argument('--limit', type=int, default=60)
    sp.add_argument('-o', '--out', default=None,
                    help='opcional: exportar las regiones SIN explicar a un CSV')
    sp.set_defaults(func=cmd_diffcheck)

    args = p.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == '__main__':
    main()