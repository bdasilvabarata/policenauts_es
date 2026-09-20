#!/usr/bin/env python3
"""
comparar_arboles.py - Compara dos carpetas extraidas con dumpsxiso
                       (p.ej. extracted_jp1 vs extracted_en1) y prioriza
                       que archivos mirar primero.

Uso:
  python3 comparar_arboles.py extracted_jp1 extracted_en1
  python3 comparar_arboles.py extracted_jp1 extracted_en1 --solo-texto
"""
import argparse
import hashlib
import os
import sys

PRINTABLE = set(range(0x20, 0x7F))


def md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def walk_rel(root):
    out = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace('\\', '/')
            out[rel] = full
    return out


def ascii80_coverage(path, sample=1 << 20):
    """% de bytes que parecen texto en el esquema 0x80+ascii, sobre una muestra."""
    with open(path, 'rb') as f:
        data = f.read(sample)
    if not data:
        return 0.0
    hits, i, n = 0, 0, len(data) - 1
    while i < n:
        if data[i] == 0x80 and data[i + 1] in PRINTABLE:
            hits += 2
            i += 2
        else:
            i += 1
    return 100.0 * hits / len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('jp_dir')
    ap.add_argument('en_dir')
    ap.add_argument('--solo-texto', action='store_true',
                    help='mostrar solo archivos distintos con indicio de texto')
    ap.add_argument('--min-texto', type=float, default=2.0,
                    help='umbral %% de cobertura ascii80 para considerarlo candidato')
    args = ap.parse_args()

    jp = walk_rel(args.jp_dir)
    en = walk_rel(args.en_dir)

    only_jp = sorted(set(jp) - set(en))
    only_en = sorted(set(en) - set(jp))
    common = sorted(set(jp) & set(en))

    same_size_diff = []
    diff_size = []
    identical = 0

    for rel in common:
        pj, pe = jp[rel], en[rel]
        sj, se = os.path.getsize(pj), os.path.getsize(pe)
        if sj != se:
            diff_size.append((rel, sj, se))
            continue
        if md5(pj) == md5(pe):
            identical += 1
            continue
        same_size_diff.append((rel, sj))

    print(f"JP: {len(jp)} archivos   EN: {len(en)} archivos")
    print(f"solo en JP: {len(only_jp)}   solo en EN: {len(only_en)}")
    print(f"identicos: {identical}   mismo tamano pero distintos: {len(same_size_diff)}"
          f"   tamano distinto: {len(diff_size)}\n")

    if only_jp:
        print("-- SOLO EN JP (raro; revisar por que no esta en EN) --")
        for r in only_jp[:20]:
            print(f"  {r}")
        print()

    if only_en:
        print("-- SOLO EN EN (archivos nuevos agregados por JunkerHQ) --")
        for r in only_en[:20]:
            print(f"  {r}")
        print()

    def coverage_line(rel, size_j, size_e=None):
        cov = ascii80_coverage(en[rel])
        if args.solo_texto and cov < args.min_texto:
            return None
        tag = "TEXTO" if cov >= args.min_texto else "     "
        if size_e is None:
            return f"  [{tag}] {rel:<50} {size_j:>10}      (mismo tamano)  ascii80~{cov:5.1f}%"
        return f"  [{tag}] {rel:<50} {size_j:>10} -> {size_e:>10}  ascii80~{cov:5.1f}%"

    print("-- MISMO TAMANO, CONTENIDO DISTINTO (candidatos mas probables) --")
    rows = [r for rel, sj in same_size_diff
            if (r := coverage_line(rel, sj)) is not None]
    rows.sort(key=lambda s: -float(s.split('~')[1].rstrip('%')))
    for r in rows[:60]:
        print(r)
    if len(rows) > 60:
        print(f"  ... ({len(rows) - 60} mas)")
    print()

    print("-- TAMANO DISTINTO (JunkerHQ expandio el archivo; usual en el mas grande) --")
    rows2 = [r for rel, sj, se in diff_size
             if (r := coverage_line(rel, sj, se)) is not None]
    rows2.sort(key=lambda s: -float(s.split('~')[1].rstrip('%')))
    for r in rows2[:60]:
        print(r)
    if len(rows2) > 60:
        print(f"  ... ({len(rows2) - 60} mas)")

    print("\nSugerencia: arranca por los de arriba de cada lista (mayor %% ascii80).")
    print("Sobre esos, corre pntool.py scan / gaps / probe para confirmar el formato.")


if __name__ == '__main__':
    main()