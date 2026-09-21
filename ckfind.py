#!/usr/bin/env python3
"""
ckfind.py - Busca el algoritmo del campo "checksum" de 4 bytes del directorio de
            un contenedor FRID (.DPK de Policenauts).

Confirmado por prueba en emulador: el juego VERIFICA ese campo al cargar (con los
checksums invertidos y los datos intactos da "Read Error 4097").

Hace dos cosas:
  1. BATERIA: prueba ~70 algoritmos (familias CRC-32 con distintos polinomios,
     inicio y xor final; sumas y XOR de bytes/palabras de 16 y 32 bits en little y
     big endian; Adler/Fletcher; variantes con el tamano) sobre varias EXTENSIONES
     de datos (exacta, hasta el proximo sector, primeros/ultimos bytes...), y
     cuenta en cuantas entradas coincide el valor con el campo.
  2. DIFERENCIAL (si das dos contenedores, p.ej. JP y EN): para las entradas que
     tienen el mismo tamano y contenido distinto, comprueba si la DIFERENCIA de
     checksums se explica por la diferencia de datos. Es independiente de valores
     iniciales y funciona aunque la extension sea desconocida: distingue sumas
     (aditivas) de XOR/CRC (lineales) y dice el polinomio/palabra.

Uso:
  python ckfind.py "bin\\GAME1.DPK.orig"
  python ckfind.py "bin\\GAME1.DPK.orig" --jp "bin\\extracted_jp1\\NAUTS\\GAME1.DPK"

Solo stdlib.
"""
import argparse
import array
import sys
import zlib
from functools import reduce
from operator import xor

M = 0xFFFFFFFF
REC = 24


# ------------------------------------------------------------ contenedor

def parse(data):
    if data[:4] != b'FRID':
        raise SystemExit("no empieza con 'FRID'")
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
                    'ck': data[pos + 20:pos + 24]})
        pos += REC
    return out


def load(path):
    data = open(path, 'rb').read()
    es = parse(data)
    offs = sorted(e['off'] for e in es)
    for e in es:
        nxt = min((o for o in offs if o > e['off']), default=len(data))
        ex = data[e['off']:e['off'] + e['size']]
        e['ext'] = {
            'exacta': ex,
            'hasta_sector': data[e['off']:nxt],
            'primeros_2048': ex[:2048],
            'primeros_64': ex[:64],
            'ultimos_2048': ex[-2048:],
            'sin_4_primeros': ex[4:],
            'sin_32_primeros': ex[32:],
        }
        e['le'] = int.from_bytes(e['ck'], 'little')
        e['be'] = int.from_bytes(e['ck'], 'big')
    return es


# ------------------------------------------------------------ CRC generico

def reflect(v, bits):
    r = 0
    for i in range(bits):
        if v >> i & 1:
            r |= 1 << (bits - 1 - i)
    return r


REF8 = bytes(reflect(i, 8) for i in range(256))
_tables = {}


def crc_table(poly):
    if poly not in _tables:
        t = []
        for i in range(256):
            c = i << 24
            for _ in range(8):
                c = ((c << 1) ^ poly) & M if c & 0x80000000 else (c << 1) & M
            t.append(c)
        _tables[poly] = t
    return _tables[poly]


def crc_generic(data, poly, init, refl, xorout):
    """Modelo Rocksoft con refin == refout == refl."""
    t = crc_table(poly)
    if refl:
        data = data.translate(REF8)
    r = init
    for b in data:
        r = ((r << 8) & M) ^ t[((r >> 24) ^ b) & 0xFF]
    if refl:
        r = reflect(r, 32)
    return r ^ xorout


POLYS = {0x04C11DB7: 'CRC-32/IEEE', 0x1EDC6F41: 'CRC-32C', 0xA833982B: 'CRC-32D',
         0x814141AB: 'CRC-32Q', 0x000000AF: 'XFER', 0xF4ACFB13: 'AUTOSAR',
         0x741B8CD7: 'CRC-32K', 0x8001801B: 'sony?'}


# ------------------------------------------------------------ algoritmos simples

def _words(b, n, big):
    pad = (-len(b)) % n
    if pad:
        b = b + bytes(pad)
    a = array.array('H' if n == 2 else 'I')
    a.frombytes(b)
    if big:
        a.byteswap()
    return a


def sum8(b):   return sum(b) & M
def sum16(b, big): return sum(_words(b, 2, big)) & M
def sum32(b, big): return sum(_words(b, 4, big)) & M
def xor8(b):
    x = reduce(xor, b, 0)
    return x * 0x01010101
def xor16(b, big):
    x = reduce(xor, _words(b, 2, big), 0)
    return x | (x << 16)
def xor32(b, big):  return reduce(xor, _words(b, 4, big), 0)
def rol32sum(b, big):
    c = 0
    for w in _words(b, 4, big):
        c = (((c << 1) | (c >> 31)) & M) ^ w
    return c
def fletcher32(b):
    s1 = s2 = 0
    for w in _words(b, 2, False):
        s1 = (s1 + w) % 65535
        s2 = (s2 + s1) % 65535
    return (s2 << 16) | s1


BASE = {
    'sum8': lambda b: sum8(b),
    'sum16le': lambda b: sum16(b, False), 'sum16be': lambda b: sum16(b, True),
    'sum32le': lambda b: sum32(b, False), 'sum32be': lambda b: sum32(b, True),
    'xor8x4': lambda b: xor8(b),
    'xor16le': lambda b: xor16(b, False), 'xor16be': lambda b: xor16(b, True),
    'xor32le': lambda b: xor32(b, False), 'xor32be': lambda b: xor32(b, True),
    'rol32xor_le': lambda b: rol32sum(b, False), 'rol32xor_be': lambda b: rol32sum(b, True),
    'adler32': lambda b: zlib.adler32(b) & M,
    'fletcher32': lambda b: fletcher32(b),
}


def transforms(x, size):
    """Variantes tipicas del valor calculado."""
    yield '', x & M
    yield '~', (~x) & M
    yield 'neg', (-x) & M
    yield '+size', (x + size) & M
    yield '^size', (x ^ size) & M
    yield '-size', (x - size) & M


def matches(e, x):
    return x == e['le'] or x == e['be']


# ------------------------------------------------------------ bateria

def battery(entries, top):
    probes = sorted(entries, key=lambda e: e['size'])[:3]
    hits = []
    n = len(entries)

    def score(ext, fn):
        best = {}
        for e in entries:
            blob = e['ext'][ext]
            x = fn(blob)
            for tname, v in transforms(x, e['size']):
                if v == e['le']:
                    best[(tname, 'LE')] = best.get((tname, 'LE'), 0) + 1
                if v == e['be']:
                    best[(tname, 'BE')] = best.get((tname, 'BE'), 0) + 1
        return best

    # algoritmos simples: todas las extensiones
    for ext in probes[0]['ext']:
        for name, fn in BASE.items():
            for tname, endian in ((t, en) for t in ('', '~', 'neg', '+size', '^size', '-size')
                                  for en in ('LE', 'BE')):
                pass
            best = score(ext, fn)
            for (tname, endian), c in best.items():
                hits.append((c, n, ext, name + (' ' + tname if tname else ''), endian))

    # CRC: filtro previo con las 3 entradas mas chicas
    combos = [(p, i, r, xo) for p in POLYS for i in (0, M) for r in (True, False)
              for xo in (0, M)]
    for ext in probes[0]['ext']:
        for (p, i, r, xo) in combos:
            fn = lambda b, p=p, i=i, r=r, xo=xo: crc_generic(b, p, i, r, xo)
            ok = True
            for e in probes:
                v = fn(e['ext'][ext])
                if not matches(e, v) and not any(matches(e, t) for _, t in
                                                 transforms(v, e['size'])):
                    ok = False
                    break
            if not ok:
                continue
            best = score(ext, fn)
            nm = (f"CRC32 poly={p:08X}({POLYS[p]}) init={i:08X} "
                  f"{'refl' if r else 'directo'} xorout={xo:08X}")
            for (tname, endian), c in best.items():
                hits.append((c, n, ext, nm + (' ' + tname if tname else ''), endian))

    hits.sort(key=lambda h: -h[0])
    return hits


# ------------------------------------------------------------ diferencial

def differential(en, jp):
    pairs = []
    jm = {e['name']: e for e in jp}
    for e in en:
        j = jm.get(e['name'])
        if j is None or j['size'] != e['size']:
            continue
        a, b = e['ext']['exacta'], j['ext']['exacta']
        if a != b:
            pairs.append((e, j, a, b))
    ident = sum(1 for e in en if e['name'] in jm and jm[e['name']]['size'] == e['size']
                and e['ext']['exacta'] == jm[e['name']]['ext']['exacta'])
    same_ck = sum(1 for e in en if e['name'] in jm and jm[e['name']]['size'] == e['size']
                  and e['ext']['exacta'] == jm[e['name']]['ext']['exacta']
                  and e['ck'] == jm[e['name']]['ck'])
    print(f"entradas de igual tamano: con datos distintos = {len(pairs)}; "
          f"identicas = {ident} (con el mismo checksum: {same_ck})")
    stale = sum(1 for e, j, a, b in pairs if e['ck'] == j['ck'])
    print(f"de las {len(pairs)} con datos distintos, tienen el MISMO checksum en JP y EN: {stale}")
    if stale == len(pairs) and pairs:
        print("  >>> el checksum NO cambia aunque cambie el contenido: no depende del contenido de esas "
              "entradas (o el parche EN no lo actualizo).")
    if not pairs:
        return
    res = []

    def tally(name, fn):
        for endian in ('le', 'be'):
            add = sub = xr = 0
            for e, j, a, b in pairs:
                x, y = fn(a), fn(b)
                if (x - y) & M == (e[endian] - j[endian]) & M:
                    add += 1
                if (y - x) & M == (e[endian] - j[endian]) & M:
                    sub += 1
                if x ^ y == e[endian] ^ j[endian]:
                    xr += 1
            res.append((add, f"aditivo {name} vs {endian.upper()}"))
            res.append((sub, f"aditivo-invertido {name} vs {endian.upper()}"))
            res.append((xr, f"XOR {name} vs {endian.upper()}"))

    for name, fn in BASE.items():
        tally(name, fn)
    # CRC lineal: crc0(a ^ b) == ck_a ^ ck_b
    for p in POLYS:
        for r in (True, False):
            for endian in ('le', 'be'):
                c = 0
                for e, j, a, b in pairs:
                    d = bytes(x ^ y for x, y in zip(a, b))
                    if crc_generic(d, p, 0, r, 0) == (e[endian] ^ j[endian]):
                        c += 1
                res.append((c, f"CRC lineal poly={p:08X}({POLYS[p]}) "
                               f"{'refl' if r else 'directo'} vs {endian.upper()}"))
    res.sort(key=lambda r: -r[0])
    print("\nprueba diferencial (cuantas de las entradas cambiadas cuadran):")
    for c, nm in res[:10]:
        print(f"  {c:>3}/{len(pairs)}  {nm}")
    if res[0][0] >= max(2, int(0.9 * len(pairs))):
        print(f"\n>>> DIFERENCIAL: {res[0][1]} explica {res[0][0]}/{len(pairs)} entradas.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dpk', help='contenedor FRID (EN)')
    ap.add_argument('--jp', default=None, help='el mismo contenedor de la version JP')
    ap.add_argument('--top', type=int, default=12)
    args = ap.parse_args()

    # autocomprobacion del motor CRC contra zlib
    t = b'123456789'
    assert crc_generic(t, 0x04C11DB7, M, True, M) == zlib.crc32(t) & M == 0xCBF43926, "CRC interno"
    assert crc_generic(t, 0x04C11DB7, M, False, M) == 0xFC891918, "CRC interno (bzip2)"

    en = load(args.dpk)
    print(f"{args.dpk}: {len(en)} entradas\n")
    jp = None
    if args.jp:
        jp = load(args.jp)
        print(f"{args.jp}: {len(jp)} entradas\n")
        print("=== DIFERENCIAL JP vs EN ===")
        differential(en, jp)
        print()

    print("=== BATERIA sobre las entradas del primer contenedor ===")
    hits = battery(en, args.top)
    print(f"{'aciertos':>9}  {'extension':<16} {'algoritmo':<62} campo")
    for c, n, ext, nm, endian in hits[:args.top]:
        print(f"{c:>4}/{n:<4}  {ext:<16} {nm:<62} {endian}")
    if not hits or hits[0][0] < max(2, len(en) // 2):
        print("\nNingun algoritmo probado llega a la mitad de las entradas. "
              "Hace falta mirar el codigo del juego (SLPS_002.15).")
    else:
        c, n, ext, nm, endian = hits[0]
        print(f"\n>>> IDENTIFICADO: {nm}  (extension {ext}, campo {endian}-endian) "
              f"coincide en {c}/{n} entradas.")
    return 0


if __name__ == '__main__':
    sys.exit(main())