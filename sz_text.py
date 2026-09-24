#!/usr/bin/env python3
"""
sz_text.py - Extrae a CSV el texto de un GAME*.SZ de Policenauts (base EN con
             DATCH) y audita que la extraccion sea fiel.

Estructura usada (deducida y verificada sobre GAME18.SZ):
  * Todo el texto esta NEGADO byte a byte (b -> 256-b). El script trabaja
    siempre en la "vista negada", donde el ingles se lee normal.
  * El bytecode apunta a las direcciones ORIGINALES de las cadenas japonesas.
    En el EN, en cada una de esas direcciones hay (casi siempre) un ancla de
    3 bytes `91 hh ll` (vista negada). En bytes crudos, r1:r2 = (-hh):(-ll) es
    un entero de 16 bits con signo, big-endian:
                destino = P + 2 + desplazamiento
    y el destino es donde esta el texto EN real de esa cadena.
  * Una cadena termina en 00. 0A es salto de linea. Un `91 hh ll` que aparece
    a mitad de una cadena es el ancla de OTRA cadena (isla de 3 bytes) y se
    saltea al leer.
  * `80 23 80 57` es un codigo de 4 bytes (significado sin confirmar).
  * Encadenado `92`: un fragmento que termina en `92 00` continua en el proximo
    `92` hacia adelante (rellena huecos entre anclas). Deducido de 3 oraciones
    que se leen completas al unir los fragmentos; falta confirmarlo con el motor.

Cada FILA del CSV es una ENTRADA = una direccion original JP (un "ancla").

Uso:
  python sz_text.py dump "EN\\GAME18.SZ" "JP\\GAME18.SZ" -o GAME18.csv
  python sz_text.py tokens GAME18.csv        # inventario de codigos + estadisticas
  python sz_text.py smoke "EN\\GAME18.SZ" "JP\\GAME18.SZ" -o GAME18_smoke.SZ   # leet en su lugar
  python sz_text.py build "EN\\GAME18.SZ" "JP\\GAME18.SZ" GAME18.csv -o GAME18_new.SZ

build: reempaqueta TODO el texto (ingles o traduccion) en el espacio libre, recalcula
los desplazamientos de las anclas, relee el resultado con el mismo extractor y
solo escribe el archivo si la verificacion pasa. Rechaza caracteres no ASCII (la
fuente del juego no los tiene) y los bytes 00/91/92 en las traducciones.

Notacion de la columna de texto:
  \\n            salto de linea (0A)
  {80238057}    el codigo de 4 bytes de arriba
  {XX}          cualquier otro byte no imprimible; tambien "{" y "\\" literales
No borres ni muevas los {..}: son codigos del juego.

Solo stdlib.
"""
import argparse
import bisect
import csv
import os
import re
import sys

NEG = bytes((-i) & 0xFF for i in range(256))
PRINT = set(range(0x20, 0x7F))
PAG = bytes([0x80, 0x23, 0x80, 0x57])


# ------------------------------------------------------- texto <-> tokens

def to_tokens(b):
    out, i = [], 0
    while i < len(b):
        if b[i:i + 4] == PAG:
            out.append('{80238057}')
            i += 4
            continue
        c = b[i]
        if c == 0x0A:
            out.append('\\n')
        elif c in PRINT and c not in (0x7B, 0x5C):      # '{' y '\'
            out.append(chr(c))
        else:
            out.append('{%02X}' % c)
        i += 1
    return ''.join(out)


def from_tokens(s, ascii_only=False):
    out, i = bytearray(), 0
    while i < len(s):
        if s.startswith('\\n', i):
            out.append(0x0A)
            i += 2
        elif s[i] == '\\':
            raise ValueError("barra invertida suelta: usa \\n o {5C}")
        elif s[i] == '{':
            j = s.find('}', i)
            if j < 0:
                raise ValueError(f"'{{' sin cerrar cerca de {s[i:i + 8]!r}")
            try:
                bs = bytes.fromhex(s[i + 1:j])
            except ValueError:
                bs = b''
            if not bs:
                raise ValueError(f"token invalido {s[i:j + 1]!r}")
            out += bs
            i = j + 1
        elif s[i] == '\r':
            i += 1                       # CRLF de una celda con salto real: ignorar
        else:
            c = ord(s[i])
            if c > 0xFF:
                raise ValueError(f"caracter fuera de 1 byte: {s[i]!r} "
                                 f"(mapealo a un glifo libre de la fuente)")
            if ascii_only and not (0x20 <= c < 0x7F or c == 0x0A):
                raise ValueError(
                    f"caracter no ASCII {s[i]!r} (U+{c:04X}): la fuente del juego "
                    f"no lo tiene; hace falta mapear un glifo (fase de fuente) y "
                    f"escribirlo como {{XX}}")
            out.append(c)
            i += 1
    return bytes(out)


# ------------------------------------------------------- estructura

def redirect_target(en, a):
    """Si en[a] abre un ancla 91 valida devuelve el destino, si no None."""
    if a + 3 > len(en) or en[a] != 0x91:
        return None
    d = (((-en[a + 1]) & 0xFF) << 8) | ((-en[a + 2]) & 0xFF)
    if d >= 0x8000:
        d -= 0x10000
    t = a + 2 + d
    return t if 0 <= t < len(en) else None


CHAIN = 0x92


def read_string(en, x, window=2048):
    """Lee desde x hasta 00 salteando islas 91 hh ll y siguiendo el encadenado 92.

    Encadenado (DATCH "text-chaining", deducido; ver informe): un fragmento que
    termina en `92 00` sigue en el PROXIMO byte 92 hacia adelante, y el texto
    de esa continuacion va justo despues de ese 92. Sirve para llenar huecos
    sueltos entre anclas.

    Devuelve (bytes_de_texto, intervalos_usados, terminada, islas, saltos)."""
    out, i, islands, hops = bytearray(), x, 0, 0
    ivs, seg = [], x
    while i < len(en):
        b = en[i]
        if b == 0x00:
            ivs.append((seg, i + 1))
            return bytes(out), ivs, True, islands, hops
        if b == 0x91 and i + 3 <= len(en):
            islands += 1
            i += 3
            continue
        if b == CHAIN and i + 1 < len(en) and en[i + 1] == 0x00:
            j = en.find(bytes([CHAIN]), i + 2, i + 2 + window)
            if j >= 0:
                ivs.append((seg, i + 2))
                hops += 1
                seg, i = j, j + 1
                continue
        out.append(b)
        i += 1
    ivs.append((seg, i))
    return bytes(out), ivs, False, islands, hops


def jp_starts(jp, start):
    """Inicios de cadena JP: posiciones tras un 00 con contenido no nulo.
    Los ultimos 2 bytes son un trailer (E0 53 en la vista negada), no una cadena."""
    return [i for i in range(max(start, 1), len(jp) - 2)
            if jp[i - 1] == 0 and jp[i] != 0]


# ------------------------------------------------------- dump

def cmd_dump(args):
    en = open(args.en, 'rb').read().translate(NEG)
    jp = open(args.jp, 'rb').read().translate(NEG)
    args.start = resolve_start(args.start, en, jp)
    starts = [a for a in jp_starts(jp, args.start) if a < len(en)]

    rows, other, rt_bad, unterminated = [], [], [], []
    seen = {}
    for a in starts:
        t = redirect_target(en, a)
        if t is not None:
            kind = 'redirect'
        elif en[a] in PRINT:
            kind, t = 'inplace', a
        else:
            other.append(a)
            continue
        text, ivs, ok, islands, hops = read_string(en, t, args.window)
        end = max(b for _, b in ivs)
        if not ok:
            unterminated.append(a)
        tok = to_tokens(text)
        if from_tokens(tok) != text:
            rt_bad.append(a)
        notes = []
        if kind == 'inplace':
            notes.append('sin codigo 91: revisar')
        if t in seen:
            notes.append(f"mismo destino que #{seen[t]}")
        else:
            seen[t] = len(rows)
        if islands:
            notes.append(f"{islands} isla(s) 91 inline")
        if hops:
            notes.append(f"cadena 92: {hops + 1} fragmentos unidos")
        rows.append({'anchor': a, 'kind': kind, 'target': t, 'end': end,
                     'ivs': ivs, 'text': text, 'tok': tok,
                     'notes': '; '.join(notes)})

    with open(args.out, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['id', 'jp_addr', 'tipo', 'destino', 'bytes',
                    'original', 'traduccion', 'notas'])
        for i, r in enumerate(rows):
            w.writerow([i, f"0x{r['anchor']:X}", r['kind'], f"0x{r['target']:X}",
                        len(r['text']) + 1, r['tok'], '', r['notes']])

    n_red = sum(1 for r in rows if r['kind'] == 'redirect')
    dup = sum(1 for r in rows if 'mismo destino' in r['notes'])
    print(f"EN: {args.en}  ({len(en)} bytes)")
    print(f"JP: {args.jp}  ({len(jp)} bytes)")
    print(f"inicios de cadena JP considerados (desde 0x{args.start:X}): {len(starts)}")
    print(f"  con ancla 91           : {n_red}")
    print(f"  texto EN en su lugar   : {len(rows) - n_red}   (marcadas 'revisar')")
    print(f"  ni una cosa ni la otra : {len(other)}")
    print(f"destinos repetidos (dos anclas, misma cadena): {dup}")
    print(f"ida y vuelta texto<->tokens con diferencias   : {len(rt_bad)}")
    print(f"cadenas sin terminador 00                     : {len(unterminated)}")
    print(f"-> {len(rows)} filas en {args.out}\n")

    if other:
        print(f"anclas JP sin nada reconocible en el EN (primeras {args.limit}):")
        for a in other[:args.limit]:
            print(f"  0x{a:06X}  bytes EN: {en[a:a + 6].hex(' ')}")
        print()
    if not rows:
        return 0

    # ---- cobertura del bloque de texto
    lo = min(r['anchor'] for r in rows)
    hi = max(r['end'] for r in rows)
    owned = bytearray(hi - lo)
    for r in rows:
        for a0, b0 in r['ivs']:
            for k in range(a0, b0):
                if lo <= k < hi:
                    owned[k - lo] = 1
        if r['kind'] == 'redirect':
            for k in range(r['anchor'], r['anchor'] + 3):
                if lo <= k < hi:
                    owned[k - lo] = 1
    runs, i = [], 0
    while i < len(owned):
        if owned[i] == 0 and en[lo + i] != 0:
            j = i
            while j < len(owned) and owned[j] == 0 and en[lo + j] != 0:
                j += 1
            runs.append((lo + i, lo + j))
            i = j
        else:
            i += 1
    left = sum(e - s for s, e in runs)
    print(f"bloque de texto: 0x{lo:X} .. 0x{hi:X}  ({hi - lo} bytes)")
    print(f"  bytes distintos de 0 que ninguna entrada usa: {left} en {len(runs)} tramos")
    for s, e in runs[:args.limit]:
        print(f"    0x{s:06X} ({e - s:4d} B)  {to_tokens(en[s:e][:50])!r}\n"
              f"      hex: {en[s:s + 16].hex(' ')}"
              f"   JP ahi: {jp[s:s + 8].hex(' ') if s < len(jp) else '-'}")
    if len(runs) > args.limit:
        print(f"    ... ({len(runs) - args.limit} mas; usa --limit)")
    tail = hi
    while tail < len(en) and en[tail] == 0:
        tail += 1
    print(f"  despues del bloque: {tail - hi} bytes en cero (0x{hi:X}..0x{tail:X}), "
          f"luego {len(en) - tail} bytes: {en[tail:tail + 16].hex(' ')}")
    _, _, _, zeros_unowned, unk91 = audit_block(en, rows)
    print(f"  bytes en cero sin dueno dentro del bloque: {zeros_unowned}  "
          f"(build los reutiliza como espacio libre)")
    print(f"  bytes 0x91 que no son ancla conocida     : {len(unk91)}  "
          f"(deben ser 0: si no, build aborta)")
    for k in unk91[:args.limit]:
        ctx = en[max(0, k - 4):k + 9]
        tgt = redirect_target(en, k)
        es_inicio = k < len(jp) and jp[k - 1] == 0 and jp[k] != 0
        det = "destino fuera del archivo" if tgt is None else (
            f"destino 0x{tgt:X} {to_tokens(en[tgt:tgt + 24])!r}")
        jctx = jp[max(0, k - 4):k + 9].hex(' ') if k < len(jp) else '-'
        print(f"    0x91 en 0x{k:06X}  inicio JP: {'si' if es_inicio else 'no'}  "
              f"{det}\n      EN -4..+8: {ctx.hex(' ')}\n      JP -4..+8: {jctx}")
    if len(unk91) > args.limit:
        print(f"    ... ({len(unk91) - args.limit} mas; usa --limit)")
    ls = [line_stats(r['text']) for r in rows]
    print(f"  ingles original: hasta {max(n for n, _ in ls)} lineas por cadena, "
          f"linea mas larga {max(w for _, w in ls)} caracteres")
    return 0


def line_stats(b):
    """(lineas, ancho_maximo) de un texto en bytes; los codigos no cuentan."""
    t = b.replace(PAG, b'')
    parts = t.split(b'\n')
    return len(parts), max(len(x) for x in parts)


def load_charmap(path):
    """JSON {caracter: caracter_ASCII_de_la_ranura}, creado por fontlab.py accents."""
    import json
    if not path:
        return {}
    cm = json.load(open(path, encoding='utf-8'))
    for k, v in cm.items():
        if len(k) != 1 or len(v) != 1 or not 0x20 <= ord(v) < 0x7F:
            raise SystemExit(f"charmap invalido: {k!r} -> {v!r}")
    return cm


def reserved_chars(cm):
    """Caracteres ASCII que ahora dibujan un acento: no se pueden escribir a mano."""
    return {v for v in cm.values() if v not in '\\{'} if cm else set()


def check_reserved(s, cm):
    bad = sorted({c for c in s if c in reserved_chars(cm)})
    if bad:
        raise ValueError("los caracteres " + ' '.join(repr(c) for c in bad) +
                         " estan reservados (dibujan acentos: " +
                         ', '.join(f"{c}={[k for k, v in cm.items() if v == c][0]}" for c in bad) +
                         "); no los escribas a mano")


def apply_charmap(s, cm):
    """Reemplaza cada caracter acentuado por el de su ranura. Las ranuras que coinciden con
    caracteres especiales del CSV (\\, { y }) se escriben como token hexadecimal."""
    if not cm:
        return s
    out = []
    for c in s:
        v = cm.get(c)
        if v is None:
            out.append(c)
        elif v in '\\{}':
            out.append('{%02X}' % ord(v))
        else:
            out.append(v)
    return ''.join(out)


# ------------------------------------------------------- analisis y auditoria

def view(path):
    return open(path, 'rb').read().translate(NEG)


def analyze(en, jp, start, window=2048):
    """Filas (una por inicio de cadena JP) del EN en vista negada."""
    starts = [a for a in jp_starts(jp, start) if a < len(en)]
    rows, other = [], []
    for a in starts:
        t = redirect_target(en, a)
        if t is not None:
            kind = 'redirect'
        elif en[a] in PRINT:
            kind, t = 'inplace', a
        else:
            other.append(a)
            continue
        text, ivs, ok, islands, hops = read_string(en, t, window)
        rows.append({'anchor': a, 'kind': kind, 'target': t, 'text': text,
                     'ivs': ivs, 'terminated': ok, 'islands': islands, 'hops': hops})
    return rows, other


def audit_block(en, rows):
    """Cobertura del bloque: lo, hi, tramos no cero sin dueno, ceros sin dueno,
    y bytes 0x91 que no pertenecen a ninguna ancla conocida."""
    lo = min(r['anchor'] for r in rows)
    hi = max(b for r in rows for _, b in r['ivs'])
    owned = bytearray(hi - lo)
    code_bytes = set()
    for r in rows:
        for a0, b0 in r['ivs']:
            for k in range(a0, b0):
                if lo <= k < hi:
                    owned[k - lo] = 1
        if r['kind'] == 'redirect':
            for k in range(r['anchor'], r['anchor'] + 3):
                code_bytes.add(k)
                if lo <= k < hi:
                    owned[k - lo] = 1
    runs, zeros, i = [], 0, 0
    while i < len(owned):
        if owned[i] == 0 and en[lo + i] != 0:
            j = i
            while j < len(owned) and owned[j] == 0 and en[lo + j] != 0:
                j += 1
            runs.append((lo + i, lo + j))
            i = j
        else:
            if owned[i] == 0:
                zeros += 1
            i += 1
    unknown91 = [k for k in range(lo, hi) if en[k] == 0x91 and k not in code_bytes]
    return lo, hi, runs, zeros, unknown91


# ------------------------------------------------------- build

def cmd_build(args):
    en = view(args.en)
    jp = view(args.jp)
    args.start = resolve_start(args.start, en, jp)
    base_size = len(en)
    rows, other = analyze(en, jp, args.start, args.window)
    if not rows:
        print("no se encontraron cadenas")
        return 1
    if en[-2:] != jp[-2:] and not args.force:
        print(f"ABORTO: el trailer del EN ({en[-2:].hex(' ')}) no coincide con el del JP "
              f"({jp[-2:].hex(' ')}); build asume que son iguales (usa --force)")
        return 1
    by_addr = {r['anchor']: r for r in rows}
    errors, warns = [], []
    cmap = load_charmap(getattr(args, 'charmap', None))

    # ---- CSV
    tr = {}
    for c in csv.DictReader(open(args.csv, encoding='utf-8')):
        try:
            a = int(c['jp_addr'], 16)
        except (KeyError, ValueError):
            errors.append(f"fila con jp_addr invalido: {c.get('id')!r}")
            continue
        if a not in by_addr:
            errors.append(f"id {c.get('id')}: jp_addr {c['jp_addr']} no existe en este "
                          f"binario (CSV de otro archivo?)")
            continue
        tr[a] = c

    new, changed = {}, set()
    autocompletados = []
    for r in rows:
        a = r['anchor']
        c = tr.get(a)
        if c is not None and c.get('original', '') != to_tokens(r['text']):
            warns.append(f"id {c.get('id')} ({c['jp_addr']}): la columna 'original' no "
                         f"coincide con el binario actual")
        cell = (c.get('traduccion') or '') if c is not None else ''
        # Auto-completar el terminador de control final (ej. '{0D}') si la
        # traduccion no lo trae -- casi siempre es un olvido, no una
        # decision deliberada, y a esta escala (cientos de filas) confiar
        # en que nadie se olvide nunca no es realista. Solo actua sobre el
        # ORIGINAL de esta fila puntual, nunca inventa un terminador que
        # esa fila no tenia.
        if cell.strip() and c is not None:
            term = re.search(r'(\{[0-9A-Fa-f]+\})+$', c.get('original', ''))
            term = term.group(0) if term else ''
            if term and not cell.endswith(term):
                cell = cell + term
                autocompletados.append((c.get('id'), c.get('jp_addr'), term))
        try:
            check_reserved(cell, cmap)
        except ValueError as e:
            errors.append(f"id {c.get('id') if c else '?'} ({c['jp_addr'] if c else ''}): {e}")
            continue
        cell = apply_charmap(cell, cmap)
        if cell.strip():
            try:
                b = from_tokens(cell, ascii_only=True)
            except ValueError as e:
                errors.append(f"id {c.get('id')} ({c['jp_addr']}): {e}")
                continue
            bad = sorted({x for x in b if x in (0x00, 0x91, 0x92)})
            if bad:
                errors.append(f"id {c.get('id')} ({c['jp_addr']}): bytes reservados "
                              f"{[hex(x) for x in bad]} (00 termina la cadena; 91/92 son "
                              f"codigos del DATCH)")
                continue
            new[a] = b
            changed.add(a)
        else:
            new[a] = r['text']
    if autocompletados:
        print(f"(se auto-completo el terminador final en {len(autocompletados)} fila(s) "
              f"donde faltaba -- copiado del propio 'original' de cada una)")
        for i, addr, term in autocompletados[:10]:
            print(f"  id={i} ({addr}): agregado {term!r}")
        if len(autocompletados) > 10:
            print(f"  ... ({len(autocompletados) - 10} mas)")
        print()
    # ---- avisos de tamano de caja (no bloquean)
    orig_stats = [line_stats(r['text']) for r in rows]
    lim_w = args.max_line or max(w for _, w in orig_stats)
    lim_l = args.max_lines or max(n for n, _ in orig_stats)
    box_warn = []
    for r in rows:
        a = r['anchor']
        if a in changed:
            nl, mw = line_stats(new[a])
            if mw > lim_w or nl > lim_l:
                box_warn.append((tr[a].get('id'), tr[a]['jp_addr'], nl, mw))
    if warns and not args.force:
        for w in warns[:10]:
            errors.append("(usa --force para ignorar) " + w)
    if errors:
        print(f"ERRORES ({len(errors)}):")
        for e in errors[:30]:
            print("  " + e)
        return 1

    # ---- auditoria previa
    lo, hi_old, runs_unowned, _, unknown91 = audit_block(en, rows)
    # bytes que YA eran huerfanos (no-cero, sin dueno) antes de tocar nada --
    # la comparacion de seguridad real es POR BYTE contra este set, no por
    # tramo completo: si el reflow limpia un vecino que antes tambien era
    # huerfano, el tramo se puede partir en pedazos mas chicos sin que eso
    # sea un problema nuevo -- lo que importa es si CADA byte puntual ya
    # era huerfano antes, no como el reporte agrupa los tramos.
    was_orphan_before = set()
    for s0, e0 in runs_unowned:
        was_orphan_before.update(range(s0, e0))

    body = bytearray(en[:-2])
    trailer = bytes(en[-2:])
    nb = len(body)
    fixed, free = bytearray(nb), bytearray(nb)
    anchors, relocate, inplace_writes = {}, [], []
    for r in rows:
        a = r['anchor']
        if r['kind'] == 'redirect':
            anchors[a] = r
            relocate.append(r)
            for a0, b0 in r['ivs']:
                for k in range(a0, min(b0, nb)):
                    free[k] = 1
        else:
            if len(r['ivs']) != 1:
                if a in changed:
                    print(f"ERROR: la cadena en su lugar 0x{a:X} tiene encadenado 92; "
                          f"no se puede traducir automaticamente")
                    return 1
            a0, b0 = r['ivs'][0]
            if a not in changed:
                for k in range(a0, b0):
                    fixed[k] = 1
            else:
                data = new[a] + b'\x00'
                if len(data) <= b0 - a0:
                    inplace_writes.append((a0, data.ljust(b0 - a0, b'\x00')))
                    for k in range(a0, b0):
                        fixed[k] = 1
                elif b0 - a0 >= 3:
                    r['kind'] = 'redirect'
                    anchors[a] = r
                    relocate.append(r)
                    for k in range(a0, b0):
                        free[k] = 1
                else:
                    print(f"ERROR: la cadena en su lugar 0x{a:X} ({b0 - a0} bytes) no "
                          f"alcanza para traducirla ni para ponerle un ancla")
                    return 1
    for a in anchors:
        for k in range(a, a + 3):
            fixed[k] = 1
    # 0x91 que no son ancla conocida: se dejan EXACTAMENTE donde estan (3 bytes) y se
    # fija tambien la cadena a la que apuntan, para que el puntero siga diciendo lo mismo
    pinned = set()
    keep_unowned = set()             # bytes no cero sin dueno que se conservan a proposito
    for s0, e0 in runs_unowned:
        keep_unowned.update(range(s0, e0))
    for k in unknown91:
        if k + 3 > nb:
            continue
        pinned.add(k)
        for x in range(k, k + 3):
            fixed[x] = 1
            keep_unowned.add(x)          # la ancla suelta en si es huerfana por diseno
        t = redirect_target(en, k)
        if t is not None and t < nb:
            _, ivs2, _, _, _ = read_string(en, t, args.window)
            for a0, b0 in ivs2:
                for x in range(a0, min(b0, nb)):
                    fixed[x] = 1
                    keep_unowned.add(x)
    owned = bytearray(nb)
    for r in rows:
        for a0, b0 in r['ivs']:
            for k in range(a0, min(b0, nb)):
                owned[k] = 1
    for a in anchors:
        for k in range(a, a + 3):
            owned[k] = 1
    unowned_nonzero = 0
    for k in range(lo, nb):
        if not owned[k]:
            if en[k] == 0:
                free[k] = 1
            else:
                fixed[k] = 1
                unowned_nonzero += 1
    pool = [k for k in range(nb) if free[k] and not fixed[k]]
    pool_set = set(pool)
    for k in pool:
        body[k] = 0

    # ---- bloques: tramos libres unidos solo si el hueco son anclas completas
    runs = []
    for k in pool:
        if runs and runs[-1][1] == k:
            runs[-1][1] = k + 1
        else:
            runs.append([k, k + 1])
    blocks = []
    for i, (s0, e0) in enumerate(runs):
        skippable = False
        if blocks:
            g0, g1 = runs[i - 1][1], s0
            skippable = (g1 - g0) % 3 == 0 and all(k in anchors or k in pinned
                                                   for k in range(g0, g1, 3))
        if skippable:
            blocks[-1].extend(range(s0, e0))
        else:
            blocks.append(list(range(s0, e0)))
    if not blocks or blocks[-1][-1] != nb - 1:
        blocks.append([])
    last = len(blocks) - 1

    # ---- empaquetado secuencial en orden de ancla
    ptr = [0] * len(blocks)
    placed, bi = {}, 0
    for r in relocate:
        a = r['anchor']
        data = new[a] + b'\x00'
        while True:
            avail = len(blocks[bi]) - ptr[bi]
            if len(data) <= avail:
                break
            if bi == last:
                need = len(data) - avail
                start_ext = len(body)
                body.extend(bytes(need))
                blocks[bi].extend(range(start_ext, start_ext + need))
                break
            bi += 1
        pos, p = blocks[bi], ptr[bi]
        for i, byte in enumerate(data):
            body[pos[p + i]] = byte
        placed[a] = pos[p]
        ptr[bi] = p + len(data)
    for a0, data in inplace_writes:
        body[a0:a0 + len(data)] = data

    # ---- anclas
    maxd = 0
    for a in anchors:
        d = placed[a] - (a + 2)
        if not -32768 <= d <= 32767:
            errors.append(f"ancla 0x{a:X}: desplazamiento {d} fuera de +-32 KB")
            continue
        maxd = max(maxd, abs(d))
        raw = d & 0xFFFF
        body[a] = 0x91
        body[a + 1] = (-(raw >> 8)) & 0xFF
        body[a + 2] = (-(raw & 0xFF)) & 0xFF
    if errors:
        print("ERRORES:")
        for e in errors[:20]:
            print("  " + e)
        return 1

    new_view = bytes(body) + trailer
    growth = len(new_view) - base_size
    out = bytearray(new_view.translate(NEG))

    # ---- cabecera: el campo BE16 de los bytes 2-3 vale (tamano - 12103) en JP y EN
    header_msg = "sin cambios (el tamano no cambio)"
    if growth != 0 and not args.no_header_fix:
        old_raw = open(args.en, 'rb').read()
        field = int.from_bytes(old_raw[2:4], 'big')
        tail_len = len(old_raw) - field
        newf = len(out) - tail_len
        if 0 <= newf < 0x10000:
            out[2:4] = newf.to_bytes(2, 'big')
            header_msg = (f"campo 0x{field:04X} -> 0x{newf:04X} (suponiendo campo = "
                          f"tamano - {tail_len}, como en JP y EN)")
        else:
            header_msg = "NO actualizado (fuera de rango)"
    elif growth != 0:
        header_msg = "NO actualizado (--no-header-fix)"

    # ---- verificacion: releer el resultado con el mismo extractor
    problems = []
    rows2, other2 = analyze(new_view, jp, args.start, args.window)
    m2 = {r['anchor']: r for r in rows2}
    if len(rows2) != len(rows):
        problems.append(f"filas: antes {len(rows)}, despues {len(rows2)}")
    bad_txt = 0
    for r in rows:
        r2 = m2.get(r['anchor'])
        if r2 is None:
            problems.append(f"falta la fila 0x{r['anchor']:X}")
        elif r2['text'] != new[r['anchor']] or r2['hops']:
            bad_txt += 1
            if bad_txt <= 5:
                problems.append(f"texto distinto en 0x{r['anchor']:X}: "
                                f"{to_tokens(r2['text'])[:50]!r}")
    if bad_txt > 5:
        problems.append(f"... y {bad_txt - 5} filas mas con texto distinto")
    if rows2:
        _, _, runs2, _, unk2 = audit_block(new_view, rows2)
        bad_runs = [r for r in runs2
                   if not all(x in keep_unowned or x in was_orphan_before
                              for x in range(r[0], r[1]))]
        if bad_runs:
            problems.append(f"{len(bad_runs)} tramos no cero sin dueno en el resultado "
                            f"(primero en 0x{bad_runs[0][0]:X})")
        if set(unk2) != set(unknown91):
            problems.append(f"los 0x91 desconocidos cambiaron: antes {len(unknown91)}, "
                            f"despues {len(unk2)}")
    ancbytes = {k for a in anchors for k in (a, a + 1, a + 2)}
    ipbytes = {k for a0, d in inplace_writes for k in range(a0, a0 + len(d))}
    diffs = [k for k in range(nb)
             if k not in pool_set and k not in ancbytes and k not in ipbytes
             and new_view[k] != en[k]]
    if diffs:
        problems.append(f"{len(diffs)} bytes fuera del pool cambiaron (p.ej. 0x{diffs[0]:X})")
    if new_view[-2:] != en[-2:]:
        problems.append("el trailer final cambio")

    used = sum(len(new[r['anchor']]) + 1 for r in relocate)
    free_left = sum(len(b) - p for b, p in zip(blocks, ptr))
    old_text = sum(len(r['text']) + 1 for r in rows)
    new_text = sum(len(new[r['anchor']]) + 1 for r in rows)
    print(f"filas: {len(rows)}   traducidas/cambiadas: {len(changed)}   "
          f"reubicadas: {len(relocate)}")
    print(f"bytes de texto: {old_text} -> {new_text}   "
          f"(pool: {len(pool)} B en {sum(1 for b in blocks if b)} bloques)")
    print(f"desplazamiento maximo usado: {maxd} (limite 32767)")
    print(f"tamano: {base_size} -> {len(new_view)}  ({growth:+d} bytes)")
    print(f"cabecera: {header_msg}")
    print(f"espacio libre que quedo sin usar en los bloques: {free_left} bytes")
    if unowned_nonzero:
        print(f"AVISO: {unowned_nonzero} bytes no cero sin dueno se dejaron intactos")
    if pinned:
        print(f"AVISO: {len(pinned)} codigo(s) 0x91 que no son ancla conocida se dejaron en su "
              f"lugar, junto con la cadena a la que apuntan")
    if box_warn:
        print(f"\nAVISO de caja: {len(box_warn)} cadena(s) traducida(s) superan el maximo "
              f"del ingles original ({lim_w} caracteres por linea, {lim_l} lineas); "
              f"podrian no entrar en el cuadro de dialogo:")
        for i, ad, nl, mw in box_warn[:10]:
            print(f"  id {i} ({ad}): {nl} lineas, la mas larga {mw} caracteres")
        if len(box_warn) > 10:
            print(f"  ... y {len(box_warn) - 10} mas")
    if problems:
        print(f"\nVERIFICACION: FALLO ({len(problems)})")
        for pr in problems:
            print("  - " + pr)
        if not args.force:
            diag_path = args.out + '.SINVERIFICAR'
            open(diag_path, 'wb').write(bytes(out))
            print(f"no se escribio {args.out} (usa --force para escribirlo igual)")
            print(f"\nvolcado SIN VERIFICAR para diagnostico (NO USAR como parche): {diag_path}")
            print(f"  inspecciona ahi con hexdump/findhex el primer offset del problema, "
                  f"y compara contra {args.en}")
            return 1
    else:
        print("\nVERIFICACION: OK (se releyo el resultado: mismas filas, mismo texto, "
              "sin tramos huerfanos, anclas validas, bytes fuera del pool intactos)")
    open(args.out, 'wb').write(bytes(out))
    print(f"-> {args.out}")
    if growth != 0:
        print("\nOJO: el tamano cambio. Mete el archivo en GAME1.DPK con "
              "'dpk_patch.py repack' (reescribe offsets, tamanos y CRC) y rearma la ISO.")
    return 0


LEET = {ord('a'): ord('4'), ord('e'): ord('3'), ord('i'): ord('1'),
        ord('o'): ord('0'), ord('A'): ord('4'), ord('E'): ord('3'),
        ord('I'): ord('1'), ord('O'): ord('0')}


def looks_like_sentence(text):
    """Frase de dialogo: tiene espacios, largo >= 8 y al menos 4 minusculas.
    Descarta identificadores y datos (ACT1, c19, warning.pak, BCP...)."""
    return (b' ' in text and len(text) >= 8
            and sum(1 for c in text if 0x61 <= c <= 0x7A) >= 4)


def leet_bytes(en, rows, mode='sentences'):
    """Cambia a leet el texto de las filas con ancla, en su lugar. -> (buf, cadenas, cambios)"""
    skip = set()
    for r in rows:
        if r['kind'] == 'redirect':
            skip.update(range(r['anchor'], r['anchor'] + 3))
    buf, n, strs = bytearray(en), 0, 0
    for r in rows:
        if r['kind'] != 'redirect':      # las cadenas en su lugar pueden ser datos (warning.pak)
            continue
        if mode == 'sentences' and not looks_like_sentence(r['text']):
            continue
        strs += 1
        for a0, b0 in r['ivs']:
            i = a0
            while i < b0:
                if i in skip:
                    i += 1
                elif buf[i] == 0x91 and i + 3 <= len(buf):
                    i += 3                       # isla (ancla conocida o no): no tocar
                elif buf[i:i + 4] == PAG:
                    i += 4
                else:
                    if buf[i] in LEET:
                        buf[i] = LEET[buf[i]]
                        n += 1
                    i += 1
    return buf, strs, n


def cmd_smoke(args):
    """Prueba de humo: cambia el texto EN EN SU LUGAR (sin mover nada) a "leet"
    (a->4 e->3 i->1 o->0). Mismo tamano, sin reempaquetar: sirve para comprobar la
    ruta .SZ -> .DPK -> ISO -> juego sin depender de build."""
    en = view(args.en)
    jp = view(args.jp)
    args.start = resolve_start(args.start, en, jp)
    rows, _ = analyze(en, jp, args.start, args.window)
    buf, strs, n = leet_bytes(en, rows, args.mode)
    open(args.out, 'wb').write(bytes(buf).translate(NEG))
    print(f"{strs} cadenas tocadas, {n} caracteres cambiados, tamano igual "
          f"({len(buf)} bytes) -> {args.out}")
    print("En el juego, los dialogos de esta escena deberian verse en 'leet': "
          "Th4t w4s T4rg3t M0d3's...")
    return 0


def plausible_text(en, t, limit=400):
    """Cadena que arranca en t: termina en 00 y es casi toda ASCII imprimible."""
    i, n_txt, n_ok = t, 0, 0
    while i < len(en) and i < t + limit:
        b = en[i]
        if b == 0:
            return n_txt >= 2 and n_ok >= 0.9 * n_txt
        if b == 0x91:
            i += 3
            continue
        n_txt += 1
        if 0x20 <= b < 0x7F or b == 0x0A:
            n_ok += 1
        i += 1
    return False


def auto_start(en, jp):
    """Busca donde empieza el bloque de texto: el primer inicio de cadena JP que
    tenga en el EN un ancla 91 valida hacia texto plausible, con al menos 20
    anclas mas dentro de los 3000 bytes siguientes (descarta falsos positivos
    aislados en el bytecode). Devuelve None si no hay texto EN redirigido."""
    n = min(len(en), len(jp))
    cands = []
    for i in range(0x40, n - 3):
        if jp[i - 1] == 0 and jp[i] != 0 and en[i] == 0x91:
            t = redirect_target(en, i)
            if t is not None and plausible_text(en, t):
                cands.append(i)
    for k, a in enumerate(cands):
        j = bisect.bisect_right(cands, a + 3000)
        if j - k >= 20:
            return a
    return None


def start_arg(x):
    return 'auto' if x == 'auto' else int(x, 0)


def resolve_start(st, en, jp):
    if st != 'auto':
        return st
    a = auto_start(en, jp)
    if a is None:
        raise SystemExit("no pude detectar el inicio del bloque de texto (--start auto); "
                         "pasa --start 0x....")
    print(f"inicio del bloque de texto detectado: 0x{a:X}")
    return a


def cmd_smoke_all(args):
    """Aplica el leet a TODOS los GAME*.SZ de dos carpetas (EN y JP) que pasen
    las verificaciones; los demas se saltean y se explica por que."""
    os.makedirs(args.outdir, exist_ok=True)
    names = sorted(f for f in os.listdir(args.endir) if f.upper().endswith('.SZ'))
    ok = 0
    print(f"{'archivo':<12} {'resultado':<10} detalle")
    for nm in names:
        pe, pj = os.path.join(args.endir, nm), os.path.join(args.jpdir, nm)
        if nm.upper() in {x.upper() for x in args.skip}:
            print(f"{nm:<12} {'saltado':<10} excluido con --skip")
            continue
        if not os.path.isfile(pj):
            print(f"{nm:<12} {'saltado':<10} no hay version JP para comparar")
            continue
        en, jp = view(pe), view(pj)
        if en == jp:
            print(f"{nm:<12} {'saltado':<10} identico en JP y EN (sin texto traducido)")
            continue
        st = auto_start(en, jp)
        if st is None:
            print(f"{nm:<12} {'saltado':<10} no se reconocio un bloque de texto EN con anclas")
            continue
        rows, _ = analyze(en, jp, st, args.window)
        if len(rows) < 20:
            print(f"{nm:<12} {'saltado':<10} pocas filas ({len(rows)})")
            continue
        _, _, runs, _, unk = audit_block(en, rows)
        unterminated = sum(1 for r in rows if not r['terminated'])
        runs_bytes = sum(e - s0 for s0, e in runs)
        if unterminated or runs_bytes > 300 or len(unk) > 40:
            print(f"{nm:<12} {'saltado':<10} la auditoria no cierra (sin dueno={len(runs)} tramos/"
                  f"{runs_bytes} B, 0x91 sueltos={len(unk)}, sin terminar={unterminated})")
            continue
        buf, strs, n = leet_bytes(en, rows, args.mode)
        open(os.path.join(args.outdir, nm), 'wb').write(bytes(buf).translate(NEG))
        ok += 1
        aviso = ''
        if runs or unk:
            aviso = f"   [aviso: {len(runs)} tramos sin dueno ({runs_bytes} B), {len(unk)} 0x91 sueltos; se dejaron intactos]"
        print(f"{nm:<12} {'LEET':<10} bloque desde 0x{st:X}: {strs} cadenas, {n} caracteres{aviso}")
    print(f"\n{ok} archivo(s) escritos en {args.outdir}")
    return 0


def cmd_fill_test(args):
    """Rellena la columna 'traduccion' de un CSV con el mismo texto ALARGADO (repite
    palabras del propio texto hasta el factor pedido). Solo para PROBAR el
    reempaquetado con textos mas largos; no es una traduccion."""
    rows = list(csv.DictReader(open(args.csvin, encoding='utf-8')))
    n = 0
    for r in rows:
        if r.get('tipo') != 'redirect':
            continue
        orig = r['original']
        words = [w for w in orig.replace('\\n', ' ').split(' ')
                 if w and '{' not in w and '\\' not in w]
        target = int(len(orig) * args.factor)
        extra, i = '', 0
        while words and len(orig) + len(extra) < target:
            extra += ' ' + words[i % len(words)]
            i += 1
        r['traduccion'] = orig + extra
        n += 1
    fn = list(rows[0].keys())
    with open(args.csvout, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=fn)
        w.writeheader()
        w.writerows(rows)
    print(f"{n} filas alargadas (factor {args.factor}) -> {args.csvout}")
    return 0


def cmd_find(args):
    """Busca un texto en TODOS los .SZ de las carpetas EN/JP e indica en cual esta."""
    needle = args.text.lower()
    names = sorted(f for f in os.listdir(args.endir) if f.upper().endswith('.SZ'))
    total = 0
    for nm in names:
        pe, pj = os.path.join(args.endir, nm), os.path.join(args.jpdir, nm)
        if not os.path.isfile(pj):
            continue
        en, jp = view(pe), view(pj)
        if en == jp:
            continue
        st = auto_start(en, jp)
        if st is None:
            continue
        rows, _ = analyze(en, jp, st, 2048)
        hits = [(i, r) for i, r in enumerate(rows)
                if needle in to_tokens(r['text']).lower().replace('\\n', ' ')]
        for i, r in hits[:args.limit]:
            tok = to_tokens(r['text'])
            k = max(0, tok.lower().replace('\\n', ' ').find(needle) - 20)
            print(f"{nm}  fila {i:>4}  jp_addr 0x{r['anchor']:X}  ...{tok[k:k + 90]}...")
        total += len(hits)
        if len(hits) > args.limit:
            print(f"{nm}  ... y {len(hits) - args.limit} coincidencias mas")
    print(f"\n{total} coincidencia(s)")
    return 0


def _quiet(fn, args):
    """Ejecuta un comando capturando lo que imprime; devuelve (codigo, texto)."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = fn(args)
        except SystemExit as e:
            rc = 1
            print(e)
    return rc or 0, buf.getvalue()


def cmd_dump_all(args):
    """dump de TODOS los scripts: un CSV por archivo en la carpeta de salida."""
    import argparse as ap_
    import re
    os.makedirs(args.outdir, exist_ok=True)
    names = sorted(f for f in os.listdir(args.endir) if f.upper().endswith('.SZ'))
    print(f"{'archivo':<11} {'estado':<9} {'filas':>6} {'ancla':>6} {'en su lugar':>11}  auditoria")
    n_ok = 0
    for nm in names:
        pe, pj = os.path.join(args.endir, nm), os.path.join(args.jpdir, nm)
        if not os.path.isfile(pj):
            print(f"{nm:<11} {'saltado':<9} no hay version JP")
            continue
        en, jp = view(pe), view(pj)
        if en == jp:
            print(f"{nm:<11} {'saltado':<9} identico en JP y EN")
            continue
        if auto_start(en, jp) is None:
            print(f"{nm:<11} {'saltado':<9} sin bloque de texto EN reconocible")
            continue
        out = os.path.join(args.outdir, os.path.splitext(nm)[0] + '.csv')
        ns = ap_.Namespace(en=pe, jp=pj, out=out, start='auto', limit=3, window=2048)
        rc, txt = _quiet(cmd_dump, ns)
        if rc:
            print(f"{nm:<11} {'ERROR':<9} {txt.strip()[:80]}")
            continue
        g = lambda pat: (re.search(pat, txt) or [None, '?'])[1]
        filas = g(r'-> (\d+) filas')
        anc = g(r'con ancla 91\s*:\s*(\d+)')
        inp = g(r'texto EN en su lugar\s*:\s*(\d+)')
        sd = g(r'que ninguna entrada usa: (\d+)')
        u91 = g(r'no son ancla conocida\s*:\s*(\d+)')
        rt = g(r'con diferencias\s*:\s*(\d+)')
        flag = '' if (sd in ('0', '?') and u91 in ('0', '?')) else '  <- revisar'
        print(f"{nm:<11} {'OK':<9} {filas:>6} {anc:>6} {inp:>11}  sin dueno={sd} B, 0x91 sueltos={u91}, "
              f"ida-vuelta dif={rt}{flag}")
        n_ok += 1
    print(f"\n{n_ok} CSV en {args.outdir}")
    return 0


def cmd_build_all(args):
    """build de TODOS los scripts que tengan traducciones en su CSV."""
    import argparse as ap_
    import csv as csv_
    import re
    os.makedirs(args.outdir, exist_ok=True)
    names = sorted(f for f in os.listdir(args.endir) if f.upper().endswith('.SZ'))
    print(f"{'archivo':<11} {'estado':<9} {'cambiadas':>9} {'tamano EN':>10} {'tamano nuevo':>12}  verificacion")
    fails, written = 0, 0
    for nm in names:
        base = os.path.splitext(nm)[0]
        cpath = os.path.join(args.csvdir, base + '.csv')
        if not os.path.isfile(cpath):
            continue
        pe, pj = os.path.join(args.endir, nm), os.path.join(args.jpdir, nm)
        rows = list(csv_.DictReader(open(cpath, encoding='utf-8')))
        ntr = sum(1 for r in rows if (r.get('traduccion') or '').strip())
        if ntr == 0 and not args.all:
            continue
        out = os.path.join(args.outdir, nm)
        ns = ap_.Namespace(en=pe, jp=pj, csv=cpath, out=out, start='auto', window=2048,
                           force=args.force, no_header_fix=False, max_line=args.max_line,
                           max_lines=args.max_lines, charmap=getattr(args, 'charmap', None))
        rc, txt = _quiet(cmd_build, ns)
        sz = re.search(r'tamano: (\d+) -> (\d+)', txt)
        ver = 'OK' if 'VERIFICACION: OK' in txt else 'FALLO'
        if rc or ver != 'OK':
            fails += 1
            print(f"{nm:<11} {'ERROR':<9} {ntr:>9} {'-':>10} {'-':>12}  {ver}")
            for ln in txt.strip().splitlines()[:12]:
                print("    " + ln)
            continue
        written += 1
        a, b = (sz.group(1), sz.group(2)) if sz else ('?', '?')
        warn = ' + avisos de caja' if 'AVISO de caja' in txt else ''
        print(f"{nm:<11} {'OK':<9} {ntr:>9} {a:>10} {b:>12}  OK{warn}")
    print(f"\n{written} script(s) escritos en {args.outdir}; {fails} con error")
    if written:
        print("Siguiente paso:  python dpk_patch.py repack <GAME1.DPK.orig> "
              f"\"{args.outdir}\" -o <GAME1_nuevo.DPK>")
    return 1 if fails else 0


def cmd_set(args):
    """Escribe una traduccion en UNA fila de un CSV (por id o por direccion jp_addr)."""
    rows = list(csv.DictReader(open(args.csvfile, encoding='utf-8')))
    if not rows:
        print("CSV vacio")
        return 1
    key = args.key.strip()
    hit = None
    for r in rows:
        if r['id'] == key or (key.lower().startswith('0x') and r['jp_addr'].lower() == key.lower()):
            hit = r
            break
    if hit is None:
        print(f"no encontre la fila {key!r} (usa el id o la direccion jp_addr, ej 0x7469)")
        return 1
    try:
        _cm = load_charmap(getattr(args, 'charmap', None))
        check_reserved(args.text, _cm)
        from_tokens(apply_charmap(args.text, _cm), ascii_only=True)
    except ValueError as e:
        print(f"texto invalido: {e}")
        return 1
    hit['traduccion'] = args.text
    fn = list(rows[0].keys())
    with open(args.csvfile, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=fn)
        w.writeheader()
        w.writerows(rows)
    print(f"fila id={hit['id']} ({hit['jp_addr']}): {hit['original'][:60]!r}\n  -> traduccion: {args.text!r}")
    return 0


def cmd_tokens(args):
    """Inventario de codigos ({..}, \\n) dentro de las cadenas + estadisticas."""
    import re
    from collections import Counter
    rows = list(csv.DictReader(open(args.csvfile, encoding='utf-8')))
    if not rows:
        print("CSV vacio")
        return 1
    pat = re.compile(r'\{[0-9A-Fa-f]+\}|\\n')
    cnt, ejemplo, filas = Counter(), {}, Counter()
    for r in rows:
        seen = set()
        for m in pat.finditer(r['original']):
            t = m.group(0)
            cnt[t] += 1
            ejemplo.setdefault(t, (r['id'], r['original']))
            seen.add(t)
        for t in seen:
            filas[t] += 1
    sizes = [int(r['bytes']) for r in rows]
    print(f"{args.csvfile}: {len(rows)} filas")
    print(f"bytes de texto (con terminador): total {sum(sizes)}   "
          f"promedio {sum(sizes) / len(sizes):.1f}   maximo {max(sizes)}")
    trad = [r for r in rows if r.get('traduccion', '').strip()]
    if trad:
        print(f"filas ya traducidas: {len(trad)}")
    print(f"\ncodigos distintos: {len(cnt)}")
    print(f"{'codigo':<14}{'usos':>7}{'filas':>7}  ejemplo (id: texto)")
    for t, c in cnt.most_common(args.top):
        i, txt = ejemplo[t]
        k = txt.find(t)
        a = max(0, k - 25)
        frag = txt[a:k + len(t) + 25]
        print(f"{t:<14}{c:>7}{filas[t]:>7}  {i}: {frag!r}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sp = sub.add_parser('dump', help='exportar el texto a CSV y auditar')
    sp.add_argument('en', help='GAME*.SZ de la version EN')
    sp.add_argument('jp', help='el mismo .SZ de la version JP')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--start', type=start_arg, default='auto',
                    help="donde empieza el bloque de texto: 'auto' (def) o una direccion")
    sp.add_argument('--limit', type=int, default=10)
    sp.add_argument('--window', type=int, default=2048,
                    help='hasta cuantos bytes hacia adelante buscar la continuacion '
                         'de una cadena 92 (def 2048)')
    sp.set_defaults(func=cmd_dump)
    sp = sub.add_parser('build', help='reinsertar el CSV (con traducciones) y verificar')
    sp.add_argument('en', help='GAME*.SZ de la version EN (base)')
    sp.add_argument('jp', help='el mismo .SZ de la version JP')
    sp.add_argument('csv', help='CSV generado por dump, con la columna traduccion')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--start', type=start_arg, default='auto')
    sp.add_argument('--window', type=int, default=2048)
    sp.add_argument('--force', action='store_true')
    sp.add_argument('--no-header-fix', action='store_true',
                    help='no tocar el campo de tamano de la cabecera si el archivo crece')
    sp.add_argument('--charmap', default=None,
                    help='JSON de fontlab.py accents: convierte a e i o u n ! ? en sus ranuras')
    sp.add_argument('--max-line', type=int, default=0,
                    help='ancho maximo de linea para avisar (def: el maximo del ingles)')
    sp.add_argument('--max-lines', type=int, default=0,
                    help='maximo de lineas por cadena para avisar (def: el del ingles)')
    sp.set_defaults(func=cmd_build)
    sp = sub.add_parser('smoke', help='prueba de humo: texto en leet, en su lugar, mismo tamano')
    sp.add_argument('en')
    sp.add_argument('jp')
    sp.add_argument('-o', '--out', required=True)
    sp.add_argument('--start', type=start_arg, default='auto')
    sp.add_argument('--window', type=int, default=2048)
    sp.add_argument('--mode', choices=['sentences', 'all'], default='sentences',
                    help="'sentences' (def): solo cadenas que parecen frases; 'all': todas")
    sp.set_defaults(func=cmd_smoke)
    sp = sub.add_parser('smoke-all', help='leet en su lugar en TODOS los .SZ de dos carpetas')
    sp.add_argument('endir', help='carpeta con los .SZ de la version EN')
    sp.add_argument('jpdir', help='carpeta con los .SZ de la version JP')
    sp.add_argument('-o', '--outdir', required=True)
    sp.add_argument('--window', type=int, default=2048)
    sp.add_argument('--mode', choices=['sentences', 'all'], default='sentences',
                    help="'sentences' (def): solo cadenas que parecen frases; 'all': todas")
    sp.add_argument('--skip', nargs='*', default=[],
                    help='nombres de archivo a excluir, ej: --skip GAME00.SZ')
    sp.set_defaults(func=cmd_smoke_all)
    sp = sub.add_parser('fill-test', help='alargar el texto de un CSV, solo para probar reempaquetado')
    sp.add_argument('csvin')
    sp.add_argument('csvout')
    sp.add_argument('--factor', type=float, default=1.4)
    sp.set_defaults(func=cmd_fill_test)
    sp = sub.add_parser('find', help='buscar un texto en todos los .SZ de dos carpetas')
    sp.add_argument('text')
    sp.add_argument('endir')
    sp.add_argument('jpdir')
    sp.add_argument('--limit', type=int, default=5)
    sp.set_defaults(func=cmd_find)
    sp = sub.add_parser('dump-all', help='dump de todos los scripts de dos carpetas (un CSV por archivo)')
    sp.add_argument('endir')
    sp.add_argument('jpdir')
    sp.add_argument('-o', '--outdir', required=True)
    sp.set_defaults(func=cmd_dump_all)
    sp = sub.add_parser('build-all', help='reinsertar todos los CSV que tengan traducciones')
    sp.add_argument('endir')
    sp.add_argument('jpdir')
    sp.add_argument('csvdir')
    sp.add_argument('-o', '--outdir', required=True)
    sp.add_argument('--all', action='store_true', help='reconstruir tambien los CSV sin traducciones')
    sp.add_argument('--charmap', default=None,
                    help='JSON de fontlab.py accents: convierte a e i o u n ! ? en sus ranuras')
    sp.add_argument('--force', action='store_true')
    sp.add_argument('--max-line', type=int, default=0)
    sp.add_argument('--max-lines', type=int, default=0)
    sp.set_defaults(func=cmd_build_all)
    sp = sub.add_parser('set', help='escribir una traduccion en una fila de un CSV')
    sp.add_argument('csvfile')
    sp.add_argument('key', help='id de la fila o direccion jp_addr (ej 0x7469)')
    sp.add_argument('text')
    sp.add_argument('--charmap', default=None, help='JSON de fontlab.py accents')
    sp.set_defaults(func=cmd_set)
    sp = sub.add_parser('tokens', help='inventario de codigos y estadisticas de un CSV')
    sp.add_argument('csvfile')
    sp.add_argument('--top', type=int, default=40)
    sp.set_defaults(func=cmd_tokens)
    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == '__main__':
    main()