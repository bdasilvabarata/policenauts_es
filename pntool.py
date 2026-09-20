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
import sys
from collections import Counter

PRINTABLE = set(range(0x20, 0x7F))


# ---------------------------------------------------------------------------
# Deteccion de texto
# ---------------------------------------------------------------------------

def find_runs_ascii(data, minlen):
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


def find_runs_ascii80(data, minlen):
    """Rachas de pares 0x80 <ascii> (el esquema que describe slowbeef)."""
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
            if len(chars) >= minlen:
                runs.append((start, i, ''.join(chars)))
            start, chars = None, []
        i += 1
    if start is not None and len(chars) >= minlen:
        runs.append((start, i, ''.join(chars)))
    return runs


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


def cmd_diff(args):
    a = open(args.file_a, 'rb').read()
    b = open(args.file_b, 'rb').read()
    print(f"A: {args.file_a}  {len(a)} bytes")
    print(f"B: {args.file_b}  {len(b)} bytes\n")
    n = min(len(a), len(b))
    regions, start = [], None
    for i in range(n):
        if a[i] != b[i]:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= args.mingap:
                regions.append((start, i))
                start = None
            elif start is not None and i - start < args.mingap:
                pass
    if start is not None:
        regions.append((start, n))
    print(f"regiones distintas: {len(regions)}")
    for s, e in regions[:args.limit]:
        print(f"  0x{s:08X} .. 0x{e:08X}  ({e - s} bytes)")
    if len(regions) > args.limit:
        print(f"  ... ({len(regions) - args.limit} mas)")


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

    sp = sub.add_parser('diff', help='comparar dos binarios (JP vs EN)')
    sp.add_argument('file_a')
    sp.add_argument('file_b')
    sp.add_argument('--mingap', type=int, default=16)
    sp.add_argument('--limit', type=int, default=60)
    sp.set_defaults(func=cmd_diff)

    args = p.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == '__main__':
    main()