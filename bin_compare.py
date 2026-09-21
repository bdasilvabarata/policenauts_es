#!/usr/bin/env python3
"""
bin_compare.py (v2) - Compara dos imagenes .bin de CD (2352 bytes/sector) y dice
                      DONDE difieren y A QUE ARCHIVO pertenece cada sector.

Mejoras sobre la v1:
  * Distingue Mode 2 Form 1 y Form 2 (bit 0x20 del submode). En Form 2 (audio
    XA, video STR) los DATOS llegan hasta el byte 2347 y solo los ultimos 4
    bytes son EDC; la v1 tomaba esa cola como "edc/ecc" y podia ocultar
    diferencias reales de audio.
  * Lee el directorio ISO9660 de la imagen A y atribuye cada sector distinto
    a su archivo.

Zonas:  sync | header | subheader | datos | edc | ecc
   Form 1: datos = 24-2071, edc = 2072-2075, ecc = 2076-2351
   Form 2: datos = 24-2347, edc = 2348-2351

Como leerlo:
  * diferencias SOLO en edc/ecc: irrelevantes para emuladores.
  * diferencias en 'datos' dentro de un archivo del juego: los archivos no son
    identicos byte a byte (mirar cuales).
  * diferencias en 'subheader': banderas de fin de archivo, canal XA, etc.
  * LBA < ~30: area del sistema y descriptor de volumen (fechas): normal.

Uso:
  python bin_compare.py "bin\\EN1.bin" "bin\\EN1_control.bin"

Solo stdlib.
"""
import argparse
import bisect
import os
import sys

SECTOR = 2352
CHUNK_SECTORS = 2048
ORDER = ['sync', 'header', 'subheader', 'datos', 'edc', 'ecc']


def regions(form2):
    if form2:
        return [('sync', 0, 12), ('header', 12, 16), ('subheader', 16, 24),
                ('datos', 24, 2348), ('edc', 2348, 2352)]
    return [('sync', 0, 12), ('header', 12, 16), ('subheader', 16, 24),
            ('datos', 24, 2072), ('edc', 2072, 2076), ('ecc', 2076, 2352)]


def is_form2(sec):
    return sec[15] == 2 and bool(sec[18] & 0x20)


# ---------------------------------------------------------------- ISO9660

def read_data(f, lba, n=1):
    out = bytearray()
    for i in range(n):
        f.seek((lba + i) * SECTOR + 24)
        out += f.read(2048)
    return bytes(out)


def walk_iso(f, total_sectors):
    """Devuelve lista ordenada [(lba_ini, lba_fin_incl, ruta)] o None."""
    try:
        pvd = read_data(f, 16)
        if pvd[1:6] != b'CD001':
            return None
        root = pvd[156:190]
        root_lba = int.from_bytes(root[2:6], 'little')
        root_size = int.from_bytes(root[10:14], 'little')
    except Exception:
        return None
    files, stack = [], [(root_lba, root_size, '')]
    seen = set()
    while stack:
        lba, size, path = stack.pop()
        if lba in seen or lba <= 0 or lba >= total_sectors:
            continue
        seen.add(lba)
        n = max(1, (size + 2047) // 2048)
        data = read_data(f, lba, n)
        pos = 0
        while pos < len(data):
            ln = data[pos]
            if ln == 0:
                pos = (pos // 2048 + 1) * 2048
                continue
            rec = data[pos:pos + ln]
            pos += ln
            if len(rec) < 34:
                continue
            ext = int.from_bytes(rec[2:6], 'little')
            sz = int.from_bytes(rec[10:14], 'little')
            flags = rec[25]
            nlen = rec[32]
            name = rec[33:33 + nlen]
            if name in (b'\x00', b'\x01'):
                continue
            nm = name.decode('latin-1').split(';')[0]
            full = f"{path}/{nm}"
            if flags & 2:
                stack.append((ext, sz, full))
            else:
                files.append((ext, ext + max(1, (sz + 2047) // 2048) - 1, full))
    files.sort()
    return files


def lookup(files, starts, lba):
    i = bisect.bisect_right(starts, lba) - 1
    if i >= 0 and files[i][0] <= lba <= files[i][1]:
        return files[i][2]
    return None


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('a', help='imagen original')
    ap.add_argument('b', help='imagen a comparar')
    ap.add_argument('--top', type=int, default=20, help='archivos/rangos a mostrar')
    args = ap.parse_args()

    sa, sb = os.path.getsize(args.a), os.path.getsize(args.b)
    print(f"A: {args.a}  {sa} bytes  ({sa / SECTOR:.2f} sectores)")
    print(f"B: {args.b}  {sb} bytes  ({sb / SECTOR:.2f} sectores)")
    if sa != sb:
        print("Los tamanos DIFIEREN: la disposicion de sectores no es la misma.")
    total = min(sa, sb) // SECTOR
    print()

    diffs = []          # (lba, form2_A, submode_cambia, zonas)
    done = 0
    with open(args.a, 'rb') as fa, open(args.b, 'rb') as fb:
        lba = 0
        while lba < total:
            n = min(CHUNK_SECTORS, total - lba)
            da, db = fa.read(n * SECTOR), fb.read(n * SECTOR)
            if da != db:
                for i in range(n):
                    o = i * SECTOR
                    ca, cb = da[o:o + SECTOR], db[o:o + SECTOR]
                    if ca == cb:
                        continue
                    f2 = is_form2(ca)
                    zonas = tuple(nm for nm, lo, hi in regions(f2)
                                  if ca[lo:hi] != cb[lo:hi])
                    diffs.append((lba + i, f2, is_form2(cb) != f2, zonas))
            lba += n
            pct = 100 * lba // total
            if pct >= done + 10:
                done = pct
                print(f"  ...{pct}%", file=sys.stderr)
        files = walk_iso(fa, total)

    print(f"sectores comparados: {total}")
    print(f"sectores distintos : {len(diffs)}")
    if not diffs:
        print("\nLas imagenes son identicas sector por sector.")
        return 0

    only_ecc = [d for d in diffs if set(d[3]) <= {'edc', 'ecc'}]
    real = [d for d in diffs if not set(d[3]) <= {'edc', 'ecc'}]
    f2n = sum(1 for d in diffs if d[1])
    print(f"  de los cuales Form 2 (audio/video): {f2n}, Form 1: {len(diffs) - f2n}")
    print(f"  SOLO edc/ecc (irrelevante en emulador): {len(only_ecc)}")
    print(f"  con cambios reales (subheader/datos/header): {len(real)}")
    changed_form = sum(1 for d in diffs if d[2])
    if changed_form:
        print(f"  ATENCION: {changed_form} sectores cambiaron de Form 1 a Form 2 o viceversa")
    cat = {}
    for d in diffs:
        for z in d[3]:
            cat[z] = cat.get(z, 0) + 1
    print("por zona: " + ", ".join(f"{z}={cat[z]}" for z in ORDER if z in cat))

    # ---- atribucion a archivos
    print()
    if files is None:
        print("(no pude leer el directorio ISO9660; sin atribucion a archivos)")
    else:
        starts = [f[0] for f in files]
        per = {}
        for lba, f2, _, zonas in real:
            key = lookup(files, starts, lba) or '(sistema / directorios / huecos)'
            e = per.setdefault(key, {'n': 0, 'z': {}, 'lbas': []})
            e['n'] += 1
            for z in zonas:
                if z not in ('edc', 'ecc'):
                    e['z'][z] = e['z'].get(z, 0) + 1
            if len(e['lbas']) < 3:
                e['lbas'].append(lba)
        ecc_per = {}
        for lba, f2, _, zonas in only_ecc:
            key = lookup(files, starts, lba) or '(sistema / directorios / huecos)'
            ecc_per[key] = ecc_per.get(key, 0) + 1
        print(f"CAMBIOS REALES por archivo (top {args.top}):")
        print(f"  {'sectores':>8}  {'archivo':<34} zonas / primeros LBA")
        for key, e in sorted(per.items(), key=lambda kv: -kv[1]['n'])[:args.top]:
            zs = ', '.join(f"{z}={n}" for z, n in e['z'].items())
            print(f"  {e['n']:>8}  {key:<34} {zs}  LBA {e['lbas']}")
        if len(per) > args.top:
            print(f"  ... ({len(per) - args.top} archivos mas)")
        if ecc_per:
            print(f"\nsectores con SOLO edc/ecc distinto, por archivo (top {args.top}):")
            for key, n in sorted(ecc_per.items(), key=lambda kv: -kv[1])[:args.top]:
                print(f"  {n:>8}  {key}")

    late_real = [d for d in real if d[0] > 40]
    print(f"\nprimer sector distinto: LBA {diffs[0][0]}")
    print(f"cambios reales despues del LBA 40: {len(late_real)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())