#!/usr/bin/env python3
"""
fontlab.py - Herramientas para la fuente latina de Policenauts (FONT.DPK).

FORMATO (descifrado por experimentos con mascaras de bits en el emulador):
  * .MDB/.RB: cabecera de 8 bytes (tamano de tabla y de datos, big-endian), tabla de
    entradas [ancho][offset de 24 bits] y la zona de datos de glifos.
  * La zona de datos es UN SOLO flujo de pixeles de 2 bits (4 por byte, bit mas alto
    primero). Valor 0 = transparente, 1..3 = tinta de menos a mas intensa.
  * Cada glifo es una "baldosa" de 12 filas x ANCHO pixeles, guardada por filas
    (ancho de fila = ancho del glifo). La baldosa del glifo i EMPIEZA 8 BYTES ANTES
    del inicio de su bloque de datos:  inicio_i = 4*offset_i - 32 (en pixeles).
    Por eso su mitad superior queda al final del bloque del glifo anterior.
  * Con 32 los glifos de todos los anchos (4..11) forman una sola pieza conectada.
  * El indice del glifo es (codigo ASCII - 0x20).

CUIDADO: los .RB/.MDB ingleses llevan al final codigo MIPS de JunkerHQ (DATCH, etc.).
Estas herramientas SOLO tocan la zona de datos de glifos, nunca ese codigo.

Comandos:
  sheet    DPK -o hoja.png [--file KPRFONT.RB]         hoja de contacto de los glifos
  glyph    DPK CARACTER [--file ...]                   un glifo en arte ASCII
  charstats CARPETA_CSV                                caracteres que usa el guion ingles
  accents  DPK -o NUEVO.DPK --slots '...'              crea a e i o u u: n ! ? en ranuras

Solo stdlib (+ Pillow para los PNG).
"""
import argparse
import csv
import glob
import json
import os
import struct
import sys
import tempfile
from collections import Counter
import importlib.util

D_SHIFT = 32                       # inicio de la baldosa = 4*offset - 32  (8 bytes antes)
ROWS = 12
EN_FILES = ('KPRFONT.RB', 'KPRFONT.MDB', 'KANJIFNT.RB')


def load_dp():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location('dpk_patch', os.path.join(here, 'dpk_patch.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Font:
    """Fuente .MDB/.RB en memoria: tabla + flujo de pixeles de 2 bits."""

    def __init__(self, blob):
        self.blob = bytearray(blob)
        self.ts, self.ds = struct.unpack_from('>II', self.blob, 0)
        self.n = self.ts // 4
        self.ent = [(self.blob[8 + 4 * i],
                     int.from_bytes(self.blob[8 + 4 * i + 1:8 + 4 * i + 4], 'big'))
                    for i in range(self.n)]
        self.orig_ent = list(self.ent)
        self.base = 8 + self.ts
        area = self.blob[self.base:self.base + self.ds]
        self.G = []
        for x in area:
            self.G += [(x >> 6) & 3, (x >> 4) & 3, (x >> 2) & 3, x & 3]

    def width(self, i):
        return self.ent[i][0]

    def start(self, i):
        return 4 * self.ent[i][1] - D_SHIFT

    def tile(self, i):
        w = self.width(i)
        s = self.start(i)
        return [[self.G[s + r * w + c] if 0 <= s + r * w + c < len(self.G) else 0
                 for c in range(w)] for r in range(ROWS)]

    def set_tile(self, i, rows):
        w = self.width(i)
        s = self.start(i)
        if s < 0 or s + ROWS * w > len(self.G):
            raise ValueError(f"la baldosa {i} sale de la zona de datos")
        for r in range(ROWS):
            for c in range(w):
                self.G[s + r * w + c] = rows[r][c] if c < len(rows[r]) else 0

    def rebuild(self, new_w):
        """Cambia el ANCHO de ciertos glifos (dict indice -> ancho) y reconstruye tabla y flujo.
        Solo debe usarse con ranuras sin uso. Reescribe las baldosas de TODOS los glifos en sus
        nuevas posiciones; el total de datos no puede crecer (la zona tiene tamano fijo)."""
        real = [i for i in range(self.n) if self.ent[i][0] > 0]
        tiles = {i: self.tile(i) for i in real}
        W = {i: new_w.get(i, self.ent[i][0]) for i in real}
        used_old = sum(3 * self.ent[i][0] for i in real)
        used_new = sum(3 * W[i] for i in real)
        if used_new > used_old:
            raise ValueError(f"los nuevos anchos ocupan {used_new} bytes y solo hay {used_old}")
        ent = list(self.ent)
        off = 0
        for i in real:
            ent[i] = (W[i], off)
            off += 3 * W[i]
        for k in range(0, max(0, 4 * used_old - D_SHIFT)):
            self.G[k] = 0
        self.ent = ent
        for i in real:
            w = W[i]
            s0 = 4 * ent[i][1] - D_SHIFT
            for r in range(ROWS):
                row = tiles[i][r]
                for c in range(w):
                    idx = s0 + r * w + c
                    if 0 <= idx < len(self.G):
                        self.G[idx] = row[c] if c < len(row) else 0

    def to_blob(self):
        out = bytearray(self.blob)
        for i, (w, off) in enumerate(self.ent):
            out[8 + 4 * i] = w
            out[8 + 4 * i + 1:8 + 4 * i + 4] = off.to_bytes(3, 'big')
        for k in range(0, min(len(self.G), 4 * self.ds), 4):
            g = self.G[k:k + 4]
            out[self.base + k // 4] = (g[0] << 6) | (g[1] << 4) | (g[2] << 2) | g[3]
        return bytes(out)


def get_font(dp, path, name):
    data = open(path, 'rb').read()
    es = dp.parse(data)
    e = next((x for x in es if x['name'].upper() == name.upper()), None)
    if e is None:
        raise SystemExit(f"no hay {name} en {path}")
    return data, Font(data[e['off']:e['off'] + e['size']])


def art(rows, chars=' .+#'):
    return [''.join(chars[p] for p in r) for r in rows]


# ------------------------------------------------------------------ sheet / glyph

def cmd_sheet(dp, a):
    from PIL import Image
    data, f = get_font(dp, a.dpk, a.file)
    pal = [(0, 0, 0), (80, 80, 90), (170, 170, 190), (255, 255, 255)]
    Z, cols, cw = 6, 16, 14
    rows = (f.n + cols - 1) // cols
    img = Image.new('RGB', (cols * cw * Z, rows * cw * Z), (30, 30, 60))
    for i in range(f.n):
        t = f.tile(i)
        ox, oy = (i % cols) * cw * Z, (i // cols) * cw * Z
        for r in range(ROWS):
            for c in range(len(t[r])):
                for yy in range(Z):
                    for xx in range(Z):
                        img.putpixel((ox + (c + 1) * Z + xx, oy + (r + 1) * Z + yy), pal[t[r][c]])
    img.save(a.out)
    print(f"{f.n} glifos de {a.file} -> {a.out}")
    return 0


def cmd_glyph(dp, a):
    data, f = get_font(dp, a.dpk, a.file)
    i = ord(a.char) - 0x20
    print(f"'{a.char}' idx {i}, ancho {f.width(i)}")
    for r, line in enumerate(art(f.tile(i))):
        print(f"  {r:2d} |{line}|")
    return 0


# ------------------------------------------------------------------ charstats

def cmd_charstats(dp, a):
    cnt = Counter()
    for p in glob.glob(os.path.join(a.csvdir, '*.csv')):
        for r in csv.DictReader(open(p, encoding='utf-8')):
            t = r.get('original', '')
            t = t.replace('\\n', ' ')
            i = 0
            while i < len(t):
                if t[i] == '{':
                    j = t.find('}', i)
                    i = j + 1 if j > 0 else i + 1
                    continue
                cnt[t[i]] += 1
                i += 1
    used = {c for c in cnt}
    print(f"caracteres distintos usados en el guion: {len(used)}; total {sum(cnt.values())}\n")
    print("USADOS (mas frecuentes):")
    print('  ' + '  '.join(f"{c}:{n}" for c, n in cnt.most_common(40)))
    if a.all:
        rare = [(c, n) for c, n in cnt.most_common() if n < 100 or not c.isalnum()]
        print("\nRAROS o simbolos (todos, con su cantidad):")
        print('  ' + '  '.join(f"{c!r}:{n}" for c, n in sorted(rare, key=lambda x: x[1])))
    unused = [chr(c) for c in range(0x21, 0x7F) if chr(c) not in used]
    print(f"\nASCII imprimibles que el guion NUNCA usa ({len(unused)}): {''.join(unused)}")
    if a.font:
        data, f = get_font(dp, a.font, a.file)
        print("\nranuras libres y su ancho (candidatas para acentos):")
        print('  ' + '  '.join(f"'{c}' w={f.width(ord(c) - 0x20)}" for c in unused))
    return 0


# ------------------------------------------------------------------ acentos

ACUTE = ["0023", "0230", "2300"]                      # 4x3
DIAER = ["33033", "33033"]                             # 5x2
TILDE = ["02320", "20023", "00000"][:2] + ["00000"]    # se ajusta abajo
TILDE = ["023000", "200230"]                           # 6x2 (onda)


def overlay(rows, ov, r0, c0):
    """Pega ov (lista de cadenas de digitos) en rows tomando el MAXIMO por pixel."""
    for dr, line in enumerate(ov):
        for dc, ch in enumerate(line):
            v = int(ch)
            r, c = r0 + dr, c0 + dc
            if 0 <= r < ROWS and 0 <= c < len(rows[r]) and v > rows[r][c]:
                rows[r][c] = v
    return rows


def ink_rows(rows):
    ys = [r for r in range(ROWS) if any(rows[r])]
    return (ys[0], ys[-1]) if ys else (0, 0)


def pad(rows, w):
    return [list(r) + [0] * (w - len(r)) if len(r) < w else list(r)[:w] for r in rows]


def rot180(rows):
    top, bot = ink_rows(rows)
    body = [r[::-1] for r in rows[top:bot + 1]][::-1]
    return body


def flipv(rows):
    top, bot = ink_rows(rows)
    return [r[:] for r in rows[top:bot + 1]][::-1]


def place_bottom(body, w, bottom_row):
    out = [[0] * w for _ in range(ROWS)]
    r0 = bottom_row - len(body) + 1
    for k, row in enumerate(body):
        for c in range(min(w, len(row))):
            out[r0 + k][c] = row[c]
    return out


def compose(f, base_char, kind, slot_w):
    """Devuelve las 12 filas del glifo acentuado, del ancho de la ranura."""
    bi = ord(base_char) - 0x20
    rows = [list(r) for r in f.tile(bi)]
    w = f.width(bi)
    if kind in ('acute', 'diaer', 'tilde'):
        top, _ = ink_rows(rows)
        ov = {'acute': ACUTE, 'diaer': DIAER, 'tilde': TILDE}[kind]
        ovw = len(ov[0])
        # las letras minusculas empiezan en la fila 'top'; el acento va 1 fila por encima
        r0 = max(0, top - len(ov) - 1)
        c0 = max(0, (w - ovw) // 2 + (1 if kind == 'acute' else 0))
        rows = overlay(rows, ov, r0, c0)
        return pad(rows, slot_w)
    if kind == 'flipv':            # ¡  : el '!' invertido, con el punto arriba y el palo hacia abajo
        body = flipv(rows)
        return pad(place_bottom(body, w, 10), slot_w)
    if kind == 'rot180':           # ¿  : el '?' girado 180 grados
        body = rot180(rows)
        return pad(place_bottom(body, w, 10), slot_w)
    raise ValueError(kind)


TARGETS = [  # (caracter, base, tipo), de MAYOR a MENOR prioridad
    ('á', 'a', 'acute'), ('é', 'e', 'acute'), ('í', 'i', 'acute'),
    ('ó', 'o', 'acute'), ('ú', 'u', 'acute'), ('ñ', 'n', 'tilde'),
    ('¿', '?', 'rot180'), ('¡', '!', 'flipv'), ('ü', 'u', 'diaer'),
]


def _try_plan(f, slots, targets):
    free = sorted(set(slots), key=lambda c: (f.width(ord(c) - 0x20), c))
    need = sorted(targets, key=lambda t: -f.width(ord(t[1]) - 0x20))
    plan, used = {}, set()
    for tgt in need:
        bw = f.width(ord(tgt[1]) - 0x20)
        cand = [c for c in free if c not in used and f.width(ord(c) - 0x20) >= bw]
        if not cand:
            return None
        s = cand[0]
        used.add(s)
        plan[tgt[0]] = s
    return plan


def plan_fit(f, slots, skip=''):
    """Con reconstruccion de tabla cualquier ranura libre sirve para cualquier acento: se le da
    el ancho que necesite. Prioridad: ranuras de ancho parecido (menos cambios en la tabla)."""
    targets = [t for t in TARGETS if t[0] not in skip]
    free = sorted(set(slots))
    if len(free) < len(targets):
        targets = targets[:len(free)]
    plan, used = {}, set()
    for tgt in targets:
        need = f.width(ord(tgt[1]) - 0x20)
        cand = sorted((c for c in free if c not in used),
                      key=lambda c: (abs(f.width(ord(c) - 0x20) - need), c))
        plan[tgt[0]] = cand[0]
        used.add(cand[0])
    dropped = [t[0] for t in TARGETS if t[0] not in skip and t[0] not in plan]
    return plan, dropped


def plan_slots(f, slots, skip=''):
    """Reparte las ranuras libres (al objetivo mas ancho, la ranura mas angosta que le sirva).
    Si no alcanzan, descarta los objetivos de menor prioridad (el ultimo de TARGETS) y avisa."""
    targets = [t for t in TARGETS if t[0] not in skip]
    dropped = []
    while targets:
        plan = _try_plan(f, slots, targets)
        if plan is not None:
            return plan, dropped
        dropped.append(targets.pop()[0])
    return None, dropped


def cmd_accents(dp, a):
    data = open(a.dpk, 'rb').read()
    es = {e['name']: e for e in dp.parse(data)}
    fonts = {}
    for nm in EN_FILES:
        e = es[nm]
        fonts[nm] = Font(data[e['off']:e['off'] + e['size']])
    ref = fonts['KPRFONT.RB']
    if a.keep_widths:
        plan, dropped = plan_slots(ref, a.slots, a.skip or '')
    else:
        plan, dropped = plan_fit(ref, a.slots, a.skip or '')
    if not plan:
        print("no hay ranuras libres suficientes; pasa mas con --slots")
        return 1
    if dropped:
        print(f"AVISO: no hay ranuras libres para: {' '.join(dropped)}  (quedan SIN glifo)")
    tgt = {t[0]: t for t in TARGETS}
    need_w = {ch: ref.width(ord(tgt[ch][1]) - 0x20) for ch in plan}
    new_w = {ord(slot) - 0x20: need_w[ch] for ch, slot in plan.items()} if not a.keep_widths else {}
    print("ranuras usadas (el guion en espanol escribira ESE caracter ASCII para cada acento):")
    charmap = {}
    for ch, slot in plan.items():
        charmap[ch] = slot
        oldw = ref.width(ord(slot) - 0x20)
        neww = new_w.get(ord(slot) - 0x20, oldw)
        print(f"  {ch}  ->  '{slot}'  (ancho de la ranura: {oldw}" + (f" -> {neww})" if neww != oldw else ")"))
    new_tiles = {}
    for ch, slot in plan.items():
        _, base, kind = tgt[ch]
        si = ord(slot) - 0x20
        new_tiles[si] = compose(ref, base, kind, new_w.get(si, ref.width(si)))
    with tempfile.TemporaryDirectory() as td:
        for nm, f in fonts.items():
            before = {i: f.tile(i) for i in range(f.n) if f.width(i) > 0}
            hdr_before = bytes(f.blob[:8])
            f.rebuild(new_w)
            for si, rows in new_tiles.items():
                f.set_tile(si, rows)
            for i, t in before.items():
                if i not in new_tiles and f.tile(i) != t:
                    print(f"ERROR: cambio la baldosa {i} en {nm}")
                    return 1
            blob = f.to_blob()
            if blob[:8] != hdr_before or len(blob) != len(f.blob):
                print("ERROR: cambio la cabecera o el tamano")
                return 1
            e = es[nm]
            orig = data[e['off']:e['off'] + e['size']]
            cola = 8 + f.ts + f.ds
            if blob[cola:] != orig[cola:]:
                print(f"ERROR: cambio el codigo/kanji de la cola en {nm}")
                return 1
            open(os.path.join(td, nm), 'wb').write(blob)
        ns = argparse.Namespace(dpk=a.dpk, dir=td, out=a.out, force=False)
        rc = dp.cmd_repack(ns)
        if rc:
            return rc
    if a.preview:
        from PIL import Image
        pal = [(0, 0, 0), (80, 80, 90), (170, 170, 190), (255, 255, 255)]
        Z = 10
        f0 = fonts['KPRFONT.RB']
        items = [(ch, ord(slot) - 0x20) for ch, slot in plan.items()]
        img = Image.new('RGB', (len(items) * 14 * Z, 2 * 14 * Z + 10), (30, 30, 60))
        for k, (ch, si) in enumerate(items):
            w = f0.width(si)
            base_rows = ref.tile(ord(tgt[ch][1]) - 0x20) if False else None
            for row, rows in ((0, f0.tile(si)),):
                for r in range(ROWS):
                    for c in range(min(w, len(rows[r]))):
                        for yy in range(Z):
                            for xx in range(Z):
                                img.putpixel((k * 14 * Z + (c + 1) * Z + xx,
                                              row * (14 * Z + 5) + (r + 1) * Z + yy), pal[rows[r][c]])
        # segunda fila: la letra base tomada de la fuente ORIGINAL
        o = Font(data[es['KPRFONT.RB']['off']:es['KPRFONT.RB']['off'] + es['KPRFONT.RB']['size']])
        for k, (ch, si) in enumerate(items):
            rows = o.tile(ord(tgt[ch][1]) - 0x20)
            for r in range(ROWS):
                for c in range(len(rows[r])):
                    for yy in range(Z):
                        for xx in range(Z):
                            img.putpixel((k * 14 * Z + (c + 1) * Z + xx,
                                          (14 * Z + 5) + (r + 1) * Z + yy), pal[rows[r][c]])
        img.save(a.preview)
        print(f"vista previa (arriba: nuevo glifo, abajo: letra base) -> {a.preview}")
    json.dump(charmap, open(a.charmap, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"mapa de caracteres -> {a.charmap}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sp = sub.add_parser('sheet')
    sp.add_argument('dpk')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--file', default='KPRFONT.RB')
    sp = sub.add_parser('glyph')
    sp.add_argument('dpk')
    sp.add_argument('char')
    sp.add_argument('--file', default='KPRFONT.RB')
    sp = sub.add_parser('charstats')
    sp.add_argument('csvdir')
    sp.add_argument('--font', default=None, help='FONT.DPK para mostrar el ancho de cada ranura')
    sp.add_argument('--all', action='store_true', help='listar tambien todos los caracteres raros')
    sp.add_argument('--file', default='KPRFONT.RB')
    sp = sub.add_parser('accents')
    sp.add_argument('dpk')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--slots', required=True, help="caracteres ASCII libres, ej: '~^|{}[]@'")
    sp.add_argument('--preview', default=None)
    sp.add_argument('--charmap', default='charmap.json')
    sp.add_argument('--skip', default='', help="acentos a NO crear, ej: 'ü'")
    sp.add_argument('--keep-widths', action='store_true',
                    help='NO reconstruir la tabla: solo usa ranuras que ya tengan ancho suficiente')
    a = ap.parse_args()
    dp = load_dp()
    fn = {'sheet': cmd_sheet, 'glyph': cmd_glyph, 'charstats': cmd_charstats,
          'accents': cmd_accents}[a.cmd]
    sys.exit(fn(dp, a) or 0)


if __name__ == '__main__':
    main()