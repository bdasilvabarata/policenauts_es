#!/usr/bin/env python3
"""
dpk_patch.py - Inspecciona un contenedor FRID (.DPK de Policenauts) y reemplaza
               un subarchivo del MISMO tamano (para probar en el emulador).

Formato (ver informe): firma "FRID"; directorio desde 0x20 con registros de 24
bytes: nombre(12) + offset(4, LE) + tamano(4, LE) + checksum(4).

  python dpk_patch.py info    GAME1.DPK
  python dpk_patch.py replace GAME1.DPK GAME18.SZ NUEVO.SZ -o GAME1_test.DPK
  python dpk_patch.py replace-dir GAME1.DPK CARPETA -o GAME1_test.DPK   # varios a la vez
  python dpk_patch.py break-ck GAME1.DPK -o GAME1_rotos.DPK   # prueba: checksums invertidos
  python dpk_patch.py repack GAME1.DPK CARPETA -o GAME1_nuevo.DPK   # tamanos NUEVOS permitidos

Checksum del directorio: CRC-32/BZIP2 (poly 04C11DB7, init FFFFFFFF, sin reflejar,
xorout FFFFFFFF) sobre los datos exactos, guardado en little-endian. El juego lo
VERIFICA al cargar (con los checksums rotos da 'Read Error 4097').
'info' lista las entradas y verifica cada checksum.
'replace' solo acepta un archivo del mismo tamano (asi no cambia ningun offset)
y recalcula el checksum de esa entrada.
Si el tamano es distinto, avisa: hace falta reempaquetar el contenedor.

Solo stdlib.
"""
import argparse
import os
import sys
import zlib

REC = 24


def parse(data):
    if data[:4] != b'FRID':
        raise SystemExit("no empieza con la firma 'FRID'")
    out, pos = [], 0x20
    while pos + REC <= len(data):
        raw = data[pos:pos + 12]
        if not raw[:1].isalnum():
            break
        name = raw.rstrip(b'\x00')
        if not name or not all(32 <= b < 127 for b in name):
            break
        off = int.from_bytes(data[pos + 12:pos + 16], 'little')
        size = int.from_bytes(data[pos + 16:pos + 20], 'little')
        if off == 0 or off + size > len(data) or off < pos:
            break
        out.append({'name': name.decode('ascii'), 'off': off, 'size': size,
                    'ck': data[pos + 20:pos + 24], 'rec': pos})
        pos += REC
    return out


# --- Checksum del directorio: IDENTIFICADO con ckfind.py sobre GAME1.DPK (28/28 entradas)
#     CRC-32/BZIP2: poly 04C11DB7, init FFFFFFFF, sin reflejar, xorout FFFFFFFF,
#     calculado sobre los datos EXACTOS de la entrada y guardado en little-endian.
_M = 0xFFFFFFFF


def _mk_table():
    tab = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) & _M if c & 0x80000000 else (c << 1) & _M
        tab.append(c)
    return tab


_TAB = _mk_table()


def crc32_bzip2(data):
    r = _M
    tab = _TAB
    for b in data:
        r = ((r << 8) & _M) ^ tab[((r >> 24) ^ b) & 0xFF]
    return r ^ _M


assert crc32_bzip2(b'123456789') == 0xFC891918     # valor de referencia del catalogo CRC


def ck_bytes(blob):
    return crc32_bzip2(blob).to_bytes(4, 'little')


def verify_all(data, es):
    """Cuantas entradas tienen el checksum correcto segun el algoritmo identificado."""
    return sum(1 for e in es if ck_bytes(data[e['off']:e['off'] + e['size']]) == e['ck'])


ALGOS = {
    'crc32':   lambda b: zlib.crc32(b) & 0xFFFFFFFF,
    'crc32~':  lambda b: (~zlib.crc32(b)) & 0xFFFFFFFF,
    'adler32': lambda b: zlib.adler32(b) & 0xFFFFFFFF,
    'sum8':    lambda b: sum(b) & 0xFFFFFFFF,
    'sum16le': lambda b: sum(int.from_bytes(b[i:i + 2], 'little')
                             for i in range(0, len(b) - 1, 2)) & 0xFFFFFFFF,
    'sum32le': lambda b: sum(int.from_bytes(b[i:i + 4], 'little')
                             for i in range(0, len(b) - 3, 4)) & 0xFFFFFFFF,
}


def find_algo(data, entries):
    """Devuelve (nombre, endian, aciertos) del algoritmo que mas entradas acierta."""
    best = (None, None, 0)
    for name, fn in ALGOS.items():
        for endian in ('little', 'big'):
            hits = 0
            for e in entries:
                blob = data[e['off']:e['off'] + e['size']]
                if fn(blob).to_bytes(4, endian) == e['ck']:
                    hits += 1
            if hits > best[2]:
                best = (name, endian, hits)
    return best


def cmd_info(args):
    data = open(args.dpk, 'rb').read()
    es = parse(data)
    print(f"{args.dpk}: {len(data)} bytes, {len(es)} entradas\n")
    print(f"{'#':>3} {'nombre':<14}{'offset':>10}{'tamano':>10}  {'checksum':<9} {'CRC-32/BZIP2':<12}")
    ok = 0
    for i, e in enumerate(es):
        good = ck_bytes(data[e['off']:e['off'] + e['size']]) == e['ck']
        ok += good
        print(f"{i:>3} {e['name']:<14}0x{e['off']:08X}{e['size']:>10}  {e['ck'].hex()}  "
              f"{'OK' if good else 'NO COINCIDE'}")
    print()
    print(f"Verificacion con CRC-32/BZIP2 (little-endian, datos exactos): {ok}/{len(es)} entradas")
    if ok == len(es):
        print(">>> El algoritmo esta confirmado: 'replace' y 'replace-dir' recalculan los checksums.")
    elif ok:
        print("Coincide solo en una parte; 'replace' NO recalcula los checksums. "
              "Corre ckfind.py para investigar.")
    else:
        print("No coincide con ninguna entrada (otro tipo de contenedor?).")
    return 0


def cmd_replace(args):
    data = bytearray(open(args.dpk, 'rb').read())
    es = parse(bytes(data))
    e = next((x for x in es if x['name'].upper() == args.name.upper()), None)
    if e is None:
        print(f"no hay una entrada llamada {args.name!r}")
        return 1
    new = open(args.newfile, 'rb').read()
    if len(new) != e['size']:
        print(f"ERROR: {args.newfile} mide {len(new)} bytes y la entrada {e['name']} "
              f"mide {e['size']}.\nEste comando solo hace reemplazos del mismo tamano "
              f"(no mueve offsets). Para tamano distinto hace falta reempaquetar.")
        return 1
    orig = bytes(data)
    data[e['off']:e['off'] + e['size']] = new
    if verify_all(orig, es) == len(es):
        ck = ck_bytes(new)
        data[e['rec'] + 20:e['rec'] + 24] = ck
        print(f"checksum recalculado (CRC-32/BZIP2): {e['ck'].hex()} -> {ck.hex()}")
    else:
        print("AVISO: el contenedor original no verifica con CRC-32/BZIP2; checksum sin tocar")
    open(args.out, 'wb').write(bytes(data))
    print(f"{e['name']} reemplazado en 0x{e['off']:X} ({e['size']} bytes) -> {args.out}")
    return 0


def cmd_replace_dir(args):
    """Reemplaza, en un solo paso, todos los subarchivos de una carpeta que tengan
    el mismo nombre y el mismo tamano que una entrada del contenedor."""
    data = bytearray(open(args.dpk, 'rb').read())
    es = parse(bytes(data))
    algo_ok = verify_all(bytes(data), es) == len(es)
    done, skipped = [], []
    for e in es:
        p = os.path.join(args.dir, e['name'])
        if not os.path.isfile(p):
            continue
        new = open(p, 'rb').read()
        if len(new) != e['size']:
            skipped.append((e['name'], f"mide {len(new)}, la entrada {e['size']}"))
            continue
        if new == bytes(data[e['off']:e['off'] + e['size']]):
            continue
        data[e['off']:e['off'] + e['size']] = new
        if algo_ok:
            data[e['rec'] + 20:e['rec'] + 24] = ck_bytes(new)
        done.append(e['name'])
    open(args.out, 'wb').write(bytes(data))
    print(f"reemplazados {len(done)}: {', '.join(done) if done else '-'}")
    for nm, why in skipped:
        print(f"  NO reemplazado {nm}: {why}")
    if algo_ok:
        print("checksums recalculados (CRC-32/BZIP2) para las entradas reemplazadas")
    else:
        print("AVISO: el contenedor original no verifica con CRC-32/BZIP2; checksums sin tocar")
    print("verificacion del resultado: "
          f"{verify_all(bytes(data), parse(bytes(data)))}/{len(es)} entradas con checksum correcto")
    print(f"-> {args.out}")
    return 0


def cmd_break_ck(args):
    """Invierte los 4 bytes de checksum del directorio (sin tocar NINGUN dato).
    Sirve para saber si el juego verifica esos checksums al cargar."""
    data = bytearray(open(args.dpk, 'rb').read())
    es = parse(bytes(data))
    only = {n.upper() for n in args.only} if args.only else None
    done = []
    for e in es:
        if only is not None and e['name'].upper() not in only:
            continue
        for i in range(4):
            data[e['rec'] + 20 + i] ^= 0xFF
        done.append(e['name'])
    open(args.out, 'wb').write(bytes(data))
    print(f"checksum invertido en {len(done)} entrada(s): {', '.join(done)}")
    print("datos de los subarchivos: SIN TOCAR")
    print(f"-> {args.out}")
    return 0


def cmd_repack(args):
    """Reempaqueta el contenedor: cada subarchivo de la carpeta REEMPLAZA a la entrada
    del mismo nombre (puede tener CUALQUIER tamano); el resto se copia igual.
    Reescribe offsets (alineados), tamanos y checksums. La cabecera (32 bytes: firma,
    cantidad de entradas, alineado 0x800...) no lleva tamano total: se conserva."""
    data = open(args.dpk, 'rb').read()
    es = parse(data)
    if not es:
        print("no pude leer el directorio")
        return 1
    if verify_all(data, es) != len(es) and not args.force:
        print("El contenedor de partida no verifica con CRC-32/BZIP2 (usa --force).")
        return 1
    align = int.from_bytes(data[8:12], 'little') or 2048
    first = min(e['off'] for e in es)
    count = int.from_bytes(data[12:16], 'little')
    if count != len(es):
        print(f"AVISO: la cabecera dice {count} entradas y el directorio tiene {len(es)}")
    # el original debe tener el layout canonico: secuencial y alineado
    cur, canon = first, True
    for e in sorted(es, key=lambda x: x['off']):
        if e['off'] != cur:
            canon = False
        cur = e['off'] + e['size']
        cur += (-cur) % align
    if not canon:
        print("AVISO: el layout original no es secuencial/alineado; el resultado lo normaliza")
    hdr = bytearray(data[:first])
    body = bytearray()
    cur = first
    replaced, newlayout = [], []
    for e in es:
        p = os.path.join(args.dir, e['name']) if args.dir else None
        if p and os.path.isfile(p):
            blob = open(p, 'rb').read()
            replaced.append((e['name'], e['size'], len(blob)))
        else:
            blob = data[e['off']:e['off'] + e['size']]
        newlayout.append((e, cur, blob))
        pad = (-len(blob)) % align
        body += blob + bytes(pad)
        cur += len(blob) + pad
    for e, o, blob in newlayout:
        r = e['rec']
        hdr[r + 12:r + 16] = o.to_bytes(4, 'little')
        hdr[r + 16:r + 20] = len(blob).to_bytes(4, 'little')
        hdr[r + 20:r + 24] = ck_bytes(blob)
    out = bytes(hdr) + bytes(body)
    # verificacion: releer
    es2 = parse(out)
    problems = []
    if len(es2) != len(es):
        problems.append(f"entradas: {len(es)} -> {len(es2)}")
    for (e, o, blob), e2 in zip(newlayout, es2):
        if e2['name'] != e['name'] or e2['off'] != o or e2['size'] != len(blob) \
                or out[o:o + len(blob)] != blob:
            problems.append(f"{e['name']}: no se relee igual")
    if verify_all(out, es2) != len(es2):
        problems.append("algun checksum no cierra")
    for nm, a, b in replaced:
        print(f"  {nm:<14} {a:>9} -> {b:>9}  ({b - a:+d})")
    print(f"reemplazados: {len(replaced)}")
    print(f"tamano del contenedor: {len(data)} -> {len(out)}  ({len(out) - len(data):+d} bytes, "
          f"{(len(out) - len(data)) // 2048:+d} sectores)")
    if not replaced:
        same = out == data
        nd = sum(1 for x, y in zip(out, data) if x != y) if len(out) == len(data) else -1
        print(f"sin reemplazos: identico al original: {'si' if same else 'no'}"
              + ("" if same else f" ({nd} bytes distintos, deberian estar solo en el relleno)"))
    if problems:
        print("VERIFICACION: FALLO")
        for pr in problems:
            print("  - " + pr)
        return 1
    print(f"VERIFICACION: OK ({len(es2)} entradas releidas con datos y checksum correctos)")
    open(args.out, 'wb').write(out)
    print(f"-> {args.out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sp = sub.add_parser('info')
    sp.add_argument('dpk')
    sp.set_defaults(func=cmd_info)
    sp = sub.add_parser('replace')
    sp.add_argument('dpk')
    sp.add_argument('name', help='nombre del subarchivo, ej GAME18.SZ')
    sp.add_argument('newfile')
    sp.add_argument('-o', '--out', required=True)
    sp.set_defaults(func=cmd_replace)
    sp = sub.add_parser('replace-dir', help='reemplazar varios subarchivos desde una carpeta')
    sp.add_argument('dpk')
    sp.add_argument('dir')
    sp.add_argument('-o', '--out', required=True)
    sp.set_defaults(func=cmd_replace_dir)
    sp = sub.add_parser('repack', help='reempaquetar con subarchivos de CUALQUIER tamano')
    sp.add_argument('dpk')
    sp.add_argument('dir', nargs='?', default=None, help='carpeta con los subarchivos nuevos')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--force', action='store_true')
    sp.set_defaults(func=cmd_repack)
    sp = sub.add_parser('break-ck', help='invertir los checksums del directorio (prueba diagnostica)')
    sp.add_argument('dpk')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--only', nargs='*', default=None, help='solo estas entradas, ej: GAME00.SZ')
    sp.set_defaults(func=cmd_break_ck)
    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == '__main__':
    main()