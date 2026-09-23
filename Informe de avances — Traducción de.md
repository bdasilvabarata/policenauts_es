# Informe de avances — Traducción de Policenauts (PSX) al español

*Última actualización: 21/09/2026*

**Objetivo del proyecto:** crear una traducción al español de Policenauts, jugable en PS1 original (modeada), partiendo del hack en inglés de JunkerHQ/slowbeef y reutilizando su infraestructura de punteros en vez de re-hacer el romhacking desde cero sobre el japonés.

---

## 0. Resumen ejecutivo

**Hito alcanzado:** la cadena técnica completa está resuelta y validada en emulador. Se puede tomar el texto de cualquier script, traducirlo con acentos y ñ, reinsertarlo (aunque sea más largo que el original), reempaquetar el contenedor con el checksum correcto, reconstruir la imagen del disco y verlo funcionando en pantalla. Lo que falta es, sobre todo, **traducir** y cubrir los textos que no viven en los scripts.

| Área | Estado |
|---|---|
| Extracción de los discos (JP1/JP2/EN1/EN2) | ✅ Hecho |
| Contenedor `GAME1.DPK` (formato FRID): lectura, checksum, reempaquetado | ✅ Hecho y validado en emulador |
| Formato de los scripts `GAME*.SZ` (codificación + DATCH) | ✅ Descifrado (el diagnóstico anterior de "compresión" era erróneo, ver §5) |
| Extractor/reinsertador de texto con verificación automática | ✅ 26 de 27 scripts del disco 1 |
| Reinserción con crecimiento arbitrario del texto | ✅ Validado en emulador |
| Fuente bitmap: formato descifrado | ✅ Hecho |
| Acentos y ñ (`á é í ó ú ü ñ ¿ ¡`) | ✅ Validado en emulador, con anchos correctos |
| **Traducción del guion** | ⏳ No iniciada (solo textos de prueba) |
| Cajas de nombre (`NAMEID1.DAT`) | ⏳ Formato resuelto; falta redibujar los 33 bitmaps |
| Menús, voces sincronizadas, subtítulos de video | ⏳ Sin investigar |
| Disco 2 (`GAME2.DPK`) | ⏳ Sin tocar |
| Distribución (parche xdelta3), prueba en consola real | ⏳ Pendiente |

---

## 1. Contexto y decisión estratégica

Existe un proyecto de traducción español en curso (TraduSquare / "Traducciones del Tío Víctor", con SkyBladeCloud, GameZelda, FacundoARG, IlDucci y otros), pero está muy poco activo desde hace años. Se decidió arrancar un proyecto independiente en vez de sumarse.

**Decisión clave de arquitectura:** en vez de partir del disco japonés y re-resolver el problema de punteros en el bytecode compilado (que JunkerHQ ya resolvió con su hack DATCH), se parte del **disco en inglés ya parcheado**, heredando esa infraestructura.

### Qué es DATCH (Double Addressing Text-Chaining Hack)
Documentado por slowbeef en los updates "Tales of ROMhacking" de su LP en lparchive.org. El problema original: el japonés guarda texto y punteros dentro de un script compilado tipo bytecode, donde un mismo valor numérico puede ser un puntero de texto o un opcode cualquiera — no se puede armar una tabla de punteros por búsqueda simple. La solución de JunkerHQ: dejar los punteros originales intactos, e insertar punteros *nuevos* embebidos en el propio flujo de texto que redirigen a donde quedó reubicado el texto en inglés (más largo), parcheando también la rutina de lectura de texto en ASM para reconocer el marcador.

**Consecuencia práctica:** comparar JP vs EN muestra el formato original + DATCH ya aplicado, no el formato original puro. El mecanismo concreto quedó descifrado (ver §5.3).

---

## 2. Entorno y toolchain

| Herramienta | Uso |
|---|---|
| `dumpsxiso` / `mkpsxiso` (Lameguy64) | Desarmar y rearmar la imagen ISO respetando XA/Mode2. Instalados en `C:\Users\bixen\tools\mkpsxiso\` (agregar al PATH en cada sesión: `$env:Path += ";C:\Users\bixen\tools\mkpsxiso"`) |
| Python 3 (stdlib; Pillow solo para vistas previas) | Todas las herramientas de análisis propias (ver §9) |
| DuckStation | Prueba en emulador; sirvió además como instrumento de ingeniería inversa (capturas tras alterar datos a propósito) |
| ImHex / HxD | Edición hexadecimal manual |
| Ghidra + PSX Loader / PCSX-Redux | Reservados para cuando haga falta tocar la rutina ASM de lectura de texto |
| ODE (xStation/MODE/PSIO) recomendado | Para iterar en consola real sin quemar CDs |
| xdelta3 | Distribución final como parche, nunca como imagen |
| jPSXdec | Para cuando se aborde el subtitulado de video STR/XA |

**Estructura del repositorio** (`C:\Users\bixen\Policenauts_es\`; scripts `.py` en la raíz, todo el juego en `bin\`):

| Carpeta / archivo | Contenido |
|---|---|
| `bin\extraido_game1_en\` / `bin\extraido_game1_jp\` | Scripts `.SZ` extraídos del DPK (EN / JP) |
| `bin\extracted_en1\NAUTS\` | Árbol del disco EN1 para `mkpsxiso` |
| `bin\csv\` | Un CSV por script (generado por `dump-all`); aquí se traduce |
| `bin\sz_out\` | Scripts reempaquetados (salida de `build-all`) |
| `bin\EN1.bin` / `bin\EN1.cue` | Imagen original (nunca se escribe) |
| `bin\en1.xml` | XML de reconstrucción para `mkpsxiso` |
| `bin\GAME1.DPK.orig` | Backup del contenedor original |
| `bin\FONT_EN.DPK` | Fuente inglesa original (backup) |
| `bin\charmap.json` | Mapa acento → ranura ASCII (generado por `fontlab.py accents`) |

---

## 3. Estructura del disco

Extracción con `dumpsxiso` de JP1/JP2/EN1/EN2. Comparación de árboles completos (`comparar_arboles.py`) sobre `NAUTS/`:

- **45 archivos** en total, 14 idénticos, 24 con mismo tamaño pero contenido distinto, 7 con tamaño distinto.
- La mayoría de los cambios de tamaño son **múltiplos exactos de 2048 bytes** (un sector PSX) — señal de repadding a nivel contenedor, no necesariamente texto reescrito.
- Los `.MOV` (video STR) tienen `ascii80%` bajo esperado (no son texto).
- `PN_VOX1.PAC` creció ~1.2 MB en sectores exactos; sospechoso de ser reordenamiento, no texto, aunque puede contener las voces sincronizadas con subtítulos (ver §12).
- `SLPS_002.15` es solo un cargador; el motor está en `KERNEL.SC` / `BIN.DPK`.

**Reconstrucción de la imagen:** `mkpsxiso` a partir de `en1.xml` genera una imagen que arranca y corre en DuckStation (intro y juego). `bin_compare.py` permite comparar dos imágenes sector por sector si hace falta auditar diferencias.

---

## 4. El contenedor `GAME1.DPK` — formato FRID, resuelto

```
Cabecera (32 bytes):
  "FRID" | 00 00 00 E0 | alineación 0x800 | cantidad de entradas |
  0x800 | 0x18 (tamaño de registro) | ceros      (sin tamaño total)

Directorio, registros de 24 bytes desde 0x20:
  12 bytes: nombre del subarchivo (ASCII, relleno con \0)
   4 bytes: offset (LE)
   4 bytes: tamaño (LE)
   4 bytes: checksum (LE)
```

**Contenido:** 27 subarchivos `GAME00.SZ` a `GAME1A.SZ` (uno por escena/capítulo, alineados a `0x800`) + `NAMEID1.DAT` = 28 entradas.

### Checksum — resuelto
Es **CRC-32/BZIP2** (polinomio `04C11DB7`, init `FFFFFFFF`, sin reflejar, xorout `FFFFFFFF`), calculado sobre los datos exactos de cada entrada y guardado en little-endian. Se identificó con `ckfind.py`. **El juego lo verifica al cargar:** un checksum erróneo produce "Read Error 4097" (se provocó a propósito con `dpk_patch.py break-ck` para confirmarlo).

### Reempaquetado
`dpk_patch.py repack` recalcula offsets (alineados a `0x800`), tamaños y checksums, permitiendo que cualquier subarchivo crezca. Validado en emulador.

### Priorización por crecimiento (JP→EN)
`GAME18.SZ` es el que más creció (+9 920 bytes, +23 %). `GAME07`, `GAME0A`, `GAME11`, `GAME12`, `GAME14`, `GAME16` casi no cambiaron. `GAME17.SZ` es byte a byte idéntico entre JP y EN (se omite).

---

## 5. Scripts `GAME*.SZ` — resuelto (reemplaza al antiguo §6 "compresión sin resolver")

### 5.1 Corrección del diagnóstico anterior
El informe previo daba por hecho que los `.SZ` estaban **comprimidos** con un algoritmo propietario (`ascii80%` ≈ 0 %). Se probaron ~128 variantes de LZSS sin éxito. **La hipótesis era errónea:** no hay compresión. Los bytes están **negados**: `b → (256 − b) & 0xFF`. El inglés es ASCII plano negado, por lo que ningún detector de ASCII lo veía. `negprobe.py` (que compara entropía y patrones al negar) lo confirmó de inmediato. Por eso `decomp_probe.py` quedó obsoleto.

### 5.2 Codificación
- Todo el archivo está negado; al des-negar, el texto EN se lee como ASCII.
- **Trailer:** los últimos 2 bytes del `.SZ` son constantes por archivo (p. ej. `E0 53`), idénticos en JP y EN. No hay tamaño total en la cabecera del DPK; el campo de cabecera del propio `.SZ` (bytes 2–3, big-endian) se comporta como *tamaño del archivo − constante* (en GAME18: tamaño − 12 103). Es una hipótesis por archivo; la prueba de crecimiento en emulador funcionó.

### 5.3 Mecanismo DATCH, descifrado
- El bytecode sigue apuntando a las **direcciones originales del japonés**. En el EN, en cada una de esas direcciones hay un **ancla** `91 hh ll` (bytes ya des-negados): un salto relativo de 16 bits con signo; `destino = P + 2 + desplazamiento`. Ahí vive el texto en inglés real.
- Una **cadena** termina en `00`; `0A` es salto de línea.
- **Islas:** un `91 hh ll` en medio de una cadena es el ancla de *otra* cadena; se saltea.
- **Encadenado `92`:** un fragmento que termina en `92 00` continúa en el siguiente `92` hacia adelante.
- **Código `{80238057}`:** 4 bytes de función desconocida; se conserva como token.
- Al reinsertar, se conservan sin moverlos los `0x91` que no se pudieron clasificar.

### 5.4 Ubicación del texto (ejemplo GAME18)
Bloque de texto en `0x2F44–0xC3A0` (37 980 bytes); tras él hay 2 669 bytes de ceros = espacio libre. El reinsertador reubica las cadenas dentro del bloque y regenera las anclas (rango de salto ±32 KB).

### 5.5 Censo de scripts (disco 1)

| Script | Filas | Con ancla | En su lugar | Estado |
|--------|-------|-----------|-------------|--------|
| GAME00 | 335 | 321 | 14 | revisar: 138 B sin dueño, 27 × `0x91`; mezcla diálogo con tablas de datos (`ACT1`, `c19`, …) |
| GAME01 | 961 | 958 | 3 | OK |
| GAME02 | 580 | 580 | 0 | OK |
| GAME03 | 497 | 495 | 2 | revisar (5 B, 1 × `0x91`) |
| GAME04 | 894 | 891 | 3 | revisar (6 B, 1 × `0x91`) |
| GAME05–GAME0B | varios | OK | — | OK (GAME08: 1 269 filas, 183 en su lugar, revisar 2 × `0x91`) |
| GAME0C–GAME15 | varios | OK | — | OK |
| GAME16 | 131 | 127 | 4 | revisar (1 B) |
| GAME17 | — | — | — | saltado: idéntico JP/EN |
| GAME18 | 783 | 770 | 13 | OK |
| GAME19, GAME1A | varios | OK | — | OK |

Total: **26 CSV** en `bin\csv\`. "En su lugar" = cadenas sin ancla que están en la dirección original.

### 5.6 Formato del CSV
Columnas: `id`, `jp_addr`, `tipo`, `destino`, `bytes`, `original`, `traduccion`, `notas`. Sintaxis dentro del texto: `\n` = salto de línea (`0A`); `{XX}` = byte hexadecimal; `{80238057}` = código de función. Si `traduccion` está vacía, se conserva el original.

---

## 6. Fuente bitmap y acentos — resuelto

### 6.1 Formato
`FONT.DPK` contiene 5 archivos: `KPRFONT.RB`, `KPRFONT.MDB`, `KANJIFNT.RB`, `KANJIFNT.MDB`, `RUBI.DAT`. Los tres primeros ingleses comparten la tabla ASCII y llevan al final código MIPS de JunkerHQ (cadenas `NEW LZO`, `DATCH`, etc.), que **no se toca**. `KANJIFNT.MDB` y `RUBI.DAT` se dejan intactos.

Descifrado por experimentos en emulador (poner a cero, aleatorizar, asignar máscaras de bits y mirar la captura):
- Zona de glifos = flujo continuo de píxeles a **2 bpp** (MSB primero; 0 = transparente, 1–3 = tinta).
- Cada glifo es una **baldosa de 12 filas × ancho**, que **empieza 8 bytes antes** de su offset en la tabla (`inicio = 4·offset − 32` en píxeles). La mitad superior de cada letra queda al final del bloque del glifo anterior.
- **Tabla de entradas** (4 bytes): `[ancho][offset BE de 3 bytes]`; índice = código ASCII − `0x20`. Hay 98 entradas: las 96 primeras son glifos (`0x20`–`0x7F`), las dos últimas están vacías (ancho 0). Los glifos ocupan 1 911 de los 2 303 bytes de la zona de datos.
- Comprobación de integridad: leer las 95 baldosas y volver a escribirlas reproduce el archivo **byte a byte**.

### 6.2 Acentos: ranuras y reconstrucción de la tabla
El guion inglés usa 84 caracteres distintos (626 649 en total) y **nunca** usa `$ * ; < = > [ \ ] { |`. Esas 11 ranuras ASCII se reutilizan para los acentos, que se componen sobre la letra base.

Primera versión: se usaron solo las ranuras que ya tenían ancho suficiente; `ñ` y `ú` (letras de 7 px) quedaron en ranuras de 8, dejando un hueco de 2 px a su derecha (las minúsculas normales dejan 0–1), y la `ü` no cabía. **Corrección:** como estas ranuras no se usan, se puede cambiar su ancho; `fontlab.py` reconstruye la tabla y reescribe todas las baldosas en sus nuevas posiciones (el total no puede crecer; se mantiene el tamaño de la zona de datos).

**Asignación definitiva:**

| Acento | Ranura | Ancho | | Acento | Ranura | Ancho |
|---|---|---|---|---|---|---|
| `á` | `*` | 7 | | `ñ` | `=` | 7 (era 8) |
| `é` | `<` | 7 | | `¿` | `\` | 8 |
| `í` | `;` | 3 (era 4) | | `¡` | `[` | 5 |
| `ó` | `>` | 7 | | `ü` | `]` | 7 (era 5) |
| `ú` | `$` | 7 (era 8) | | *(libres)* | `{` `\|` | — |

**Verificación (script independiente):** en los tres archivos ingleses, 0 glifos originales alterados, 0 anchos originales cambiados, cabecera y cola (código de JunkerHQ) idénticas; `KANJIFNT.MDB` y `RUBI.DAT` sin cambios; tamaño del contenedor igual. Los márgenes de los acentos coinciden con los de su letra base (p. ej. `ñ` = `n`, `ú` = `u`).

**Limitaciones conocidas:** no hay mayúsculas acentuadas (`Á É Í Ó Ú`, solo hay una fila libre sobre la altura de las mayúsculas) → se escriben sin tilde.

### 6.3 Protecciones en las herramientas
- `sz_text.py` (`set` y `build`) **rechaza** los caracteres que ahora dibujan acentos (`* < ; > $ = [ ]`): si alguien escribe un `*` a mano saldría como `é`. El mensaje indica qué acento dibuja cada uno.
- `\` y `{` (ranuras de `¿` y del token) se escriben internamente como `{5C}` / `{7B}`; se comprobó que `¿nada?` **no** se interpreta como salto de línea.
- Se acepta `á é í ó ú ü ñ ¡ ¿` con `--charmap bin\charmap.json`. En el SZ resultante las ranuras se ven así: `así` → `as;`, `Pingüino` → `Ping]ino` (es lo esperado).

---

## 7. `NAMEID1.DAT` — resuelto: es gráfico, no texto

**Conclusión confirmada visualmente:** bitmap de 4 bits por píxel (nibble) con paleta de color al inicio.

- **Cabecera:** 32 bytes = paleta de 16 colores BGR555 (formato TIM estándar de PSX)
- **Caja de cada entrada:** 40×15 píxeles ("Ed", "Jonathan", "Man", "Woman", "Lorraine", "Officer" con borde)
- **Total:** 33 entradas exactas (33×300+32 = 9 932 bytes, coincide con el tamaño del archivo)
- Sprite sheet de nombres/roles. La paleta JP es un gradiente suave (antialiasing de kanji); la EN es casi plana.

**Implicancia:** no se toca con herramientas de texto; es trabajo de edición gráfica: redibujar los 33 bitmaps en español respetando paleta y recuadro. Se ve en pantalla como el rótulo "Jonathan" junto al diálogo.

**Lección de proceso:** la detección automática de ancho/alto por heurística fue frágil y se descartó; funcionó generar recortes con dimensiones exactas conocidas (`--start-row`/`--max-rows`).

---

## 8. Pipeline de trabajo (procedimiento actual)

**Preparación única:**
```powershell
$env:Path += ";C:\Users\bixen\tools\mkpsxiso"
python sz_text.py dump-all "bin\extraido_game1_en" "bin\extraido_game1_jp" -o "bin\csv"
python fontlab.py accents "bin\FONT_EN.DPK" -o "bin\FONT_acc.DPK" --slots '$*;<=>[\]{|' --preview "bin\acentos.png" --charmap "bin\charmap.json"
```

**Traducir:** completar la columna `traduccion` de cada CSV (a mano o con `sz_text.py set`):
```powershell
python sz_text.py set "bin\csv\GAME01.csv" 0x7469 "¿Cómo está el niño?\n¡Sí, vamos!" --charmap "bin\charmap.json"
```

**Reconstruir, empaquetar y probar:**
```powershell
python sz_text.py build-all "bin\extraido_game1_en" "bin\extraido_game1_jp" "bin\csv" -o "bin\sz_out" --charmap "bin\charmap.json"
python dpk_patch.py repack "bin\GAME1.DPK.orig" "bin\sz_out" -o "bin\GAME1_nuevo.DPK"
Copy-Item "bin\GAME1_nuevo.DPK" "bin\extracted_en1\NAUTS\GAME1.DPK" -Force
Copy-Item "bin\FONT_acc.DPK" "bin\extracted_en1\NAUTS\FONT.DPK" -Force
mkpsxiso -o "bin\EN1_test.bin" -c "bin\EN1_test.cue" "bin\en1.xml"
```
`build` verifica automáticamente el resultado (relee el script: mismas filas, mismo texto, sin tramos huérfanos, anclas válidas, bytes fuera del pool intactos).

**Restaurar el original:** copiar de vuelta `GAME1.DPK.orig` y `FONT_EN.DPK` al árbol `extracted_en1`.

---

## 9. Herramientas propias (raíz del repo)

| Archivo | Función |
|---|---|
| **`sz_text.py`** | **Principal.** `dump`/`dump-all` (extraer a CSV), `build`/`build-all` (reinsertar con verificación), `smoke`/`smoke-all` (ida y vuelta), `fill-test`, `find`, `set`, `tokens`. Acepta `--charmap` |
| **`dpk_patch.py`** | Contenedor FRID: `info`, `replace`, `replace-dir`, `repack` (recalcula todo), `break-ck` (rompe un checksum a propósito) |
| **`fontlab.py`** | Fuente: `sheet` (hoja de glifos), `glyph`, `charstats` (uso de caracteres y ranuras libres; `--all` lista los raros), `accents` (genera acentos, reconstruye anchos; `--slots`, `--preview`, `--charmap`, `--skip`, `--keep-widths`) |
| `fontprobe.py` | Experimentos de fuente en emulador: `zero`, `randomize`, `assign`, `masks` |
| `negprobe.py` / `hexat.py` | Detección de codificación negada y entropía / visor hexadecimal con vista negada |
| `codes91.py` / `redir91.py` | Análisis de anclas DATCH (superados por `sz_text.py`) |
| `ckfind.py` | Identificación del algoritmo de checksum del directorio FRID |
| `bin_compare.py` | Comparación sector por sector de imágenes `.bin` |
| `pntool.py` | Preexistente: `header`, `scan`, `gaps`, `probe`, `dpk-list`, `dpk-diff` |
| `comparar_arboles.py` | Compara árboles extraídos con `dumpsxiso` |
| `render4bpp.py` | Renderiza bitmaps 4 bpp con paleta (`NAMEID1.DAT`) |
| `decomp_probe.py` | **Obsoleto** (buscaba compresión que no existe) |
| `GUIA.md` | Guía general de fases del proyecto |

---

## 10. Hitos validados en emulador (DuckStation)

1. **Texto "leet" en 25 scripts:** todo el diálogo aparece modificado → la reinserción funciona.
2. **"Read Error 4097":** aparece al dejar un checksum inválido → el juego verifica el CRC; con el CRC-32/BZIP2 correcto carga.
3. **Crecimiento:** un script más largo que el original se reinserta y se lee bien → no hay límite de tamaño fijo.
4. **Acentos:** `¿Cómo está el niño? ¡Sí, vamos!` se muestra con todos los glifos correctos; tras la reconstrucción de la tabla, `Pingüino` incluye la `ü` y el espaciado de `ñ`, `ú` e `í` es uniforme. **Resultado aceptado como bueno** ("perfecto para lo que se puede lograr con estos recursos").

---

## 11. Reglas para quien traduzca

- **No escribir a mano** `* < ; > $ = [ ]` (dibujan acentos). Escribir siempre la letra acentuada real; la herramienta la convierte.
- **Sin punto y coma** (la ranura es de la `í`): usar coma, punto o dos puntos.
- **Sin mayúsculas acentuadas** por ahora: `Ángel` → `Angel`.
- **Saltos de línea manuales (`\n`).** Hallazgo de la última prueba: el motor **no corta por palabras**; cuando una línea supera el ancho de la caja continúa en la siguiente **en mitad de la palabra** (`Pi` / `ngüino.`). En la prueba entraron unos 34 caracteres por línea antes de cortar. Cada frase larga debe partirse a mano con `\n`, respetando el ancho aproximado de las líneas del original.
- Conservar los códigos `{...}` tal cual aparecen en `original`.
- Si `traduccion` queda vacía, se conserva el texto inglés (permite traducir por partes).

---

## 12. Pendientes y hoja de ruta

### Alta prioridad (para poder traducir en serio)
- [ ] **Verificador de ancho de línea:** herramienta que, usando la tabla de anchos de la fuente, mida cada línea de la traducción en píxeles y avise cuando exceda el ancho de la caja. Falta medir también el número máximo de líneas por caja.
- [ ] **Traducción de prueba de un script completo** (p. ej. `GAME01`) y recorrido en emulador para detectar problemas reales.
- [ ] **Revisar los scripts marcados** (GAME00, GAME03, GAME04, GAME08, GAME16) y decidir qué hacer con los bytes sin dueño / `0x91` desconocidos.
- [ ] **GAME00:** separar diálogo real de tablas de datos (etiquetas `ACT1`, `c19`, …). **GAME17:** idéntico JP/EN, decidir si hay algo que traducir.

### Textos fuera de los scripts
- [ ] **Voces sincronizadas** (p. ej. "Thank you for wasting my time."): probablemente en `PN_VOX1.PAC`; posible relación con el "XDT hack" de JunkerHQ.
- [ ] **Subtítulos de cinemáticas** (`.MOV` / `VZ003XA.STR`): otro mecanismo, requiere jPSXdec.
- [ ] **Menús y textos del motor** (`KERNEL.SC` / `BIN.DPK`): ubicar cadenas y comprobar qué usa la fuente.
- [ ] **Gráficos:** redibujar los 33 rótulos de `NAMEID1.DAT` y buscar otros gráficos con texto.

### Extensión y entrega
- [ ] **Disco 2** (`GAME2.DPK`): repetir `dump-all` / `build-all` / `repack`.
- [ ] **Mayúsculas acentuadas:** rediseñar acentos más bajos o ampliar el área de la baldosa.
- [ ] Confirmar la hipótesis del campo de cabecera de los `.SZ` (tamaño − constante) en todos los scripts.
- [ ] **Distribución:** generar parche con xdelta3 contra la imagen EN1/EN2 original; probar en consola real con ODE (comprobar EDC/ECC de la imagen reconstruida).

---

## 13. Lecciones de proceso

1. **Un diagnóstico erróneo cuesta caro.** "Compresión" era en realidad negación de bytes; ~128 pruebas de LZSS no lo podían encontrar. Antes de buscar algoritmos exóticos, probar transformaciones triviales (negar, XOR, rotar) y medir entropía.
2. **Consumir el 100 % de la entrada no prueba nada** si el decodificador no puede fallar; solo el texto legible lo prueba.
3. **Validar siempre por ida y vuelta** (leer → escribir → comparar byte a byte). Fue lo que dio confianza para rehacer la tabla de la fuente.
4. **El emulador como instrumento:** alterar datos a propósito (poner a cero, aleatorizar, máscaras) y mirar la captura permitió descifrar el formato de la fuente sin ver el código.
5. **Medir antes de opinar sobre el diseño:** el espaciado de los acentos se corrigió tras medir los márgenes de cada glifo, no a ojo.
6. **Hacer imposibles los errores previsibles:** las herramientas rechazan los caracteres reservados en lugar de confiar en que nadie los escriba.

## 14. Subtítulos de cinemáticas (`.MOV`) — resuelto

### 14.1 Formato descubierto

- **Header de chunk:** `00 38 <tipo:2 bytes variable> 01 00`. El campo de tipo varía según el chunk (`0001` para la mayoría, `0004` visto en al menos un caso) — un patrón fijo sin comodín se pierde chunks reales.
- **El texto vive pegado en la cola de cada chunk**, justo antes del header del chunk siguiente — no en una posición fija ni con un puntero dedicado.
- **Descubierto por qué:** comparando JP vs EN byte a byte (`pntool.py diffcheck`) alrededor de un subtítulo confirmado, aparecen cientos de diferencias irregulares en cascada, consistentes con que el equipo de DATCH tuvo que **recomprimir el frame de video MDEC completo** de esa escena para hacer lugar al subtítulo en inglés (el japonés no tiene texto en pantalla ahí, tiene audio nativo). El único bloque de diferencias *no* asociado a texto resultó ser una tabla de índice de ~24 registros al principio del archivo, de naturaleza todavía no confirmada pero irrelevante para la reinserción (no se toca).

### 14.2 Extracción — `pntool.py movscan` / `movscan-all`

Escanea el `.MOV` completo, chunk por chunk, buscando ASCII imprimible en la cola de cada uno. Filtros en capas, cada uno agregado a partir de un falso positivo real encontrado durante las pruebas:

1. **Caracteres prohibidos:** un conjunto de símbolos ASCII que el guion nunca usa (los mismos reservados por la fuente para dibujar acentos) — cualquier racha que los contenga se descarta antes de cualquier otra heurística.
2. **Ruido repetitivo:** cadenas cortas en mayúsculas que se repiten periódicamente cada N chunks (`SCIPPDTS`, `SCTEGOLD`, `SDNSSDTS`) — resultaron ser un marcador estructural del formato, no ruido de audio, pero tampoco es texto de diálogo.
3. **Token único sospechoso:** una racha larga (10+ caracteres) sin ningún espacio interno y sin ninguna palabra inglesa común reconocible — casi siempre son bytes de audio ADPCM que cayeron en rango imprimible por pura casualidad estadística.
4. **Palabra reconocible:** clasifica cada hallazgo por confianza (contiene una palabra inglesa común vs. no) para priorizar revisión manual.

**Fusión de líneas partidas:** el motor usa dos marcadores de salto de línea distintos — un `0x0A` simple, y un esquema de `0x80` seguido del carácter visible `|` (que sobrevive como parte de la racha siguiente, no del separador). Sin fusionar, una frase de dos líneas pierde su segunda mitad si es más corta que el `--minlen`. La herramienta detecta ambos esquemas y los reconstruye en una sola entrada.

### 14.3 Incidente de seguridad — margen de crecimiento

Primera versión: `offset_fin` (el límite hasta donde se puede escribir una traducción) se calculaba asumiendo que **todo** el tramo hasta el próximo landmark (siguiente texto detectado, o el header del siguiente chunk) era relleno libre. Esto **corrompió el video de una escena real** — glitches en cuadrícula, clásicos de un bitstream MDEC desincronizado — porque una parte de ese tramo resultó ser datos reales de video, no padding.

**Corrección aplicada:** `offset_fin` ahora se extiende únicamente mientras el archivo original tenga bytes `0x00` reales y consecutivos, verificados byte a byte, nunca por ausencia de otra explicación. Se aplicó la misma corrección a `textscan` (ver §15) por el mismo motivo.

**Lección:** un patrón de padding "descubierto" en un solo ejemplo no se puede generalizar como regla sin verificarlo byte a byte en cada caso — coherente con la lección #2 de la sección 13 del informe original ("consumir el 100% de la entrada no prueba nada").

### 14.4 Reinserción — `pntool.py textbuild` / `textset`

- **`textbuild`:** lee un binario original + un CSV con columnas `offset/offset_fin/encoding/texto/traduccion`, reinserta cada traducción. Nunca crece más allá del espacio verificado — si una fila no entra, se rechaza esa fila puntual (queda en inglés) en vez de cortar el texto o arriesgar el resto del archivo. Verifica el resultado releyendo el archivo escrito antes de darlo por bueno.
- Soporta `--charmap`, reutilizando `apply_charmap`/`from_tokens`/`check_reserved` directamente desde `sz_text.py` (nunca reimplementado aparte, para que los dos flujos de traducción compartan exactamente la misma lógica de acentos).
- **`textset`:** editar una traducción puntual desde la terminal por offset, equivalente de `sz_text.py set` para este formato de CSV (que no tiene un `id` numérico).
- **`restore`:** dado que estos archivos no están en un contenedor DPK, cada uno tiene su propio backup `.orig` suelto (ver §18).

---

## 15. Voces sobre imagen estática (`PN_VOX1.PAC`) — resuelto

Este archivo resultó tener **dos estructuras distintas mezcladas**, descubiertas por partes:

### 15.1 Tramos con formato de registro real

Una franja del archivo (algunas escenas puntuales, cerca del principio) sigue un formato de registro genuino: header de 16 bytes (`total_len` / `campo_b` / `campo_c` / reservado, los cuatro `u32` little-endian) + texto + padding, hasta completar `total_len`. Los registros están pegados uno atrás del otro sin espacio perdido entre ellos.

- **`pntool.py voxwalk`:** camina una cadena de registros desde un offset ancla ya confirmado, con resincronización opcional hacia atrás para encontrar el comienzo real de la cadena.
- **`pntool.py voxscan`:** recorre el archivo **entero** buscando todas las cadenas válidas de una sola pasada, sin necesitar ningún ancla — acepta una cadena como real solo si encuentra un mínimo de registros consecutivos válidos (configurable), para evitar falsos positivos de puro azar.

### 15.2 El resto del archivo — audio real, sin header fijo

La inmensa mayoría del archivo (el audio ADPCM en sí) no sigue el formato de registro — el texto está pegado en algún punto de cada muestra de voz, sin ningún header detectable. Se resolvió reusando **`pntool.py textscan`** (el mismo escaneo lineal sin estructura descripto en §16.1, con los mismos filtros en capas de §14.2).

### 15.3 Reinserción

Mismo `textbuild`/`textset` genéricos de §14.4 — no hizo falta ninguna herramienta nueva, ya que `voxscan`/`textscan` producen el mismo formato de CSV que `movscan`.

---

## 16. Menús y textos del motor (`BIN.DPK`) — resuelto (parcial)

### 16.1 `KERNEL.SC` — resuelto: no es texto traducible

Es un **manifiesto de compilación**: rutas de desarrollo (`hostpath d:\nauts\cdrom.img\`) y la lista de archivos `.DPK` que van al disco (`fsfile GAME1.DPK`, etc.). ASCII plano, sin negar. El cambio de 6 bytes entre JP/EN que lo había priorizado en la sesión anterior es casi seguro solo una ruta de desarrollo de largo distinto entre equipos — no hay diálogo ni menú acá.

### 16.2 `BIN.DPK` — contenedor FRID, 14 subarchivos

Mismo formato FRID que `GAME1.DPK` (§4 del informe original), confirmado con `pntool.py dpk-list`. Contenido: `BLOOD.BIN`, `DISPLAY.BIN`, `ENGLISH.BIN`, `ITP.BIN`, `MASQ1.BIN`, `MASQ2.BIN`, `MENU.BIN`, `MV.BIN`, `OPEN.BIN`, `ROLL.BIN`, `SUBS1.BIN`–`SUBS4.BIN`.

### 16.3 `MENU.BIN` / `ITP.BIN` — los dos candidatos reales, con contenido duplicado

- **ASCII plano, sin negar** (a diferencia de `GAME*.SZ`) — confirmado con `negprobe.py`.
- **~70% del contenido de `MENU.BIN` se repite byte a byte en `ITP.BIN`**, en al menos dos regiones separadas (una copia contigua confirmada de 1224 bytes byte a byte idéntica), a offsets distintos con un desplazamiento constante por región pero *no* uniforme entre regiones. Evidencia de que ambos archivos comparten una tabla de strings de sistema, compilada por separado en dos subsistemas distintos (hipótesis: chequeo de tarjeta de memoria al arrancar el disco, vs. el mismo chequeo accedido desde el menú de pausa in-game — sin confirmar en emulador todavía).
- **El margen disponible (`offset_fin - offset`) no es necesariamente igual entre las dos copias del mismo texto en inglés** — confirmado empíricamente: 19 de 324 pares con margen distinto, algunos notablemente más chicos en `ITP.BIN` (hasta 17 bytes menos).
- **`pntool.py synctrad`:** copia traducciones ya cargadas de un CSV fuente a un CSV destino cuando el texto en inglés coincide (normalizado), pero **siempre valida que la traducción entre en el margen específico de esa fila del destino** antes de copiar — nunca asume que el margen de la fuente aplica igual. Si no entra, la fila queda vacía (se conserva el inglés al hacer `build`) y se reporta el detalle (bytes necesarios vs. disponibles) para revisión manual.

### 16.4 Metodología de clasificación: traducir vs. texto interno

Desarrollada sobre las 280 entradas de `MENU.BIN` y las 161 únicas de `ITP.BIN`, reutilizable para cualquier archivo de sistema nuevo:

| Patrón | Ejemplo | Decisión |
|---|---|---|
| `CLAVE:%d` / `%x` / `%lx` (nombre + especificador de formato C) | `CARDINF:%08lx`, `READERR CNT:%d` | **No traducir.** Telemetría interna, nunca visible. El especificador de formato en sí **nunca se toca** — riesgo real de crash. |
| Palabra o frase suelta sin `%` | `New Game`, `Format complete.` | **Traducir.** Probablemente visible al jugador. |
| Nombres de archivo fuente (`*.c`), flags de build (`-debug`), mensajes de assert (`sorry, ...`), identificadores de opcode (`EF:algo`) | `itpobj.c`, `-pilot`, `EF:windowon1` | **No traducir**, descartar sin dudar. |
| Fragmentos cortos sin sentido | `'4!('`, `'B0%'` | **Ignorar** — ruido que sobrevivió el filtro de largo mínimo, no es texto real. |
| Palabra suelta ambigua | `stop`, `pause`, `SAVING...` | Marcar para **confirmar en emulador** antes de decidir. |

### 16.5 Hallazgo práctico: números en vez de palabras largas

Para la opción "Cursor Speed" (`Slowest`/`Slow`/`Medium`/`Fast`/`Fastest`, slots de apenas 8-12 bytes — insuficiente para las palabras españolas equivalentes), se optó por reemplazar directamente por una escala numérica `1`-`5` en vez de abreviar. Resuelve el problema de espacio de raíz sin perder claridad, aprovechando que el título de la opción ya da contexto. Aplicable a cualquier otro caso de opciones ordenadas con slots ajustados.

### 16.6 Contenedores hermanos, listados vía `KERNEL.SC`

`KERNEL.SC` (§16.1) reveló la lista completa de `.DPK` del disco, incluyendo tres nunca explorados hasta esta sesión:

- **`DATA.DPK`** (37 MB, 327 subarchivos `S00XX.XDT`): **sin explorar todavía.** Posible relación con el "XDT hack" de JunkerHQ mencionado en pendientes de sesiones anteriores (voces sincronizadas) — pendiente de investigación seria, potencialmente un hallazgo grande.
- **`DEV.DPK`** (26 KB, 8 subarchivos): tablas de audio (`PCMBGM*.TBL`, `PCMVOX*.TBL`, `PCMSE*.TBL`, `NOPAC*.TBL`) — sin texto, no aplica.
- **`PAK.DPK`** (8.8 MB, 85 subarchivos): mayormente assets gráficos y de minijuego. Candidatos sin explorar: `CREDITPS.PAK` / `CREDITPT.PAK` (créditos finales — nombre lo sugiere fuertemente). `ENGLISH.PAK` sí se investigó en profundidad — ver §16.8.
- **`SHOTPAC.DPK`** (7.6 MB, 32 subarchivos) y, dentro de `BIN.DPK`: `SUBS1.BIN`–`SUBS4.BIN`, `BLOOD.BIN`, `DISPLAY.BIN`, `MASQ1.BIN`, `MASQ2.BIN` — todos assets y texto de depuración de minijuegos (persecución en auto, práctica de tiro, manejo de bombas). Ver §16.7.

### 16.7 Minijuegos — baja prioridad

`SUBS1-4.BIN`, `BLOOD.BIN`, `DISPLAY.BIN`, `MASQ1-2.BIN` y `SHOTPAC.DPK` comparten la misma familia de nombres de asset (`CARCHAR`, `CARANIM`, `BOMBCHAR`, `SHOTCH`, `MASCHAR`, `TOKUCHAR`) — **no son subtítulos** pese al nombre "SUBS" (corregido de una suposición incorrecta de una sesión anterior). Prácticamente todo el texto encontrado es depuración interna del motor gráfico/de animación (errores de memoria, carga de assets). Un puñado de exclamaciones cortas estilo arcade (`LIFE UP !!`, `BONUS !!`, `game over !!`, `game clear !!`) podrían ser visibles durante algún minijuego — sin confirmar. Dado el volumen mínimo de texto real frente al resto del proyecto, **queda deprioritizado** salvo confirmación positiva en emulador.

### 16.8 `ENGLISH.PAK` — investigado en profundidad, sin resolver

Pese al nombre, no se pudo identificar con ninguna técnica ya validada en el proyecto:

- No es ASCII plano ni negado (el truco de `GAME*.SZ`)
- No es texto ancho de 2 bytes por carácter (esquema agregado a `negprobe.py` específicamente para esta prueba — ver §17)
- No tiene firma TIM estándar de PSX
- Renderizado como bitmap 4bpp con paleta (mismo esquema que `NAMEID1.DAT`, §5 del informe original) da ruido visual en todos los anchos probados
- Tiene una posible tabla de offsets al inicio del archivo que no llevó a contenido reconocible en ninguna de las posiciones candidatas

Entropía moderada (4.3-4.6 bits/byte) — hay estructura real, no es basura aleatoria ni compresión de alta entropía, pero el esquema concreto no fue identificado. Es candidato fuerte a contener el mensaje de sistema `"Autosave File will be deleted... Are you sure you want to start a new game?"`, que tampoco se localizó en ningún otro archivo del disco revisado hasta el momento (`GAME*.SZ`, los 14 subarchivos de `BIN.DPK`). **Pendiente, prioridad media-baja** dado el costo de investigación ya invertido frente al resto de tareas disponibles.

---

## 17. Herramientas nuevas de esta sesión

### `pntool.py` (ampliado significativamente sobre la base preexistente)

| Subcomando | Función |
|---|---|
| `grep` | Buscar frases literales (ASCII/ASCII80) en un archivo o carpeta entera |
| `hexdump` | Volcar bytes alrededor de un offset arbitrario, sin atarse a límites de chunk |
| `around` | Ver chunks/sectores de tamaño fijo alrededor de un offset (mapear estructura) |
| `findhex` | Buscar un patrón de bytes crudo con comodines (`??`), ver campos u16/u32 posteriores |
| `chunkmap` | Mapear todos los chunks de un archivo por un patrón de header, detectar outliers de tamaño |
| `movscan` / `movscan-all` | Extracción automática de subtítulos de `.MOV` (uno o toda una carpeta) — §14.2 |
| `voxwalk` / `voxscan` | Caminar/escanear cadenas de registros tamaño-variable tipo `PN_VOX1.PAC` — §15.1 |
| `textscan` | Escaneo lineal sin estructura de chunk/registro, para archivos sin header conocido — §15.2, §16 |
| `textbuild` / `textset` | Reinserción/edición genérica para CSV de `movscan`/`textscan`, con `--charmap` — §14.4 |
| `synctrad` | Copiar traducciones entre dos CSV cuando el texto coincide, validando margen del destino — §16.3 |
| `diff` / `diffcheck` | Comparar dos binarios JP/EN; `diffcheck` cruza las regiones distintas contra un CSV de subtítulos ya conocidos |
| `restore` | Restaurar todos los `.orig` sueltos de una carpeta a su lugar en el árbol, por nombre — §18 |
| `margen` | Analizar cuánto margen real hay en cada fila de un CSV para que la traducción crezca |

### `negprobe.py`

Se agregaron dos esquemas de detección nuevos (texto ancho de 2 bytes por carácter, little- y big-endian) — descartados para todo lo investigado en esta sesión (`ENGLISH.PAK`, `MENU.BIN`, `ITP.BIN`), pero quedan disponibles para el próximo archivo sin identificar.

### `sz_text.py`

Corrección de simetría en `apply_charmap`: ahora escapa `\`, `{` **y `}`** como token hexadecimal cuando una ranura de acento cae en uno de esos tres caracteres (antes solo cubría `\` y `{`). Relevante si en el futuro se asignan las ranuras libres (`{`, `|`) a glifos de dígrafos comunes en español, como técnica de compresión de espacio en slots ajustados (ver §19, pendiente sin desarrollar todavía).

---

## 18. Convención de trabajo consolidada: `.orig` + `restore`

Extendida a los archivos nuevos de esta sesión, sobre la misma lógica que ya existía para `GAME1.DPK.orig`/`FONT_EN.DPK`:

- Cada binario que se traduce (`.MOV` individuales, `PN_VOX1.PAC`, `BIN.DPK`) tiene su propio backup `.orig` suelto en `bin\`.
- `pntool.py restore <carpeta_origs> <árbol>` busca cada `.orig` por nombre en todo el árbol de extracción y lo restaura, sin necesitar que se le indique la ruta exacta — soluciona el caso de archivos que viven dentro de un contenedor DPK (no hay "MENU.BIN suelto" en el árbol, pero sí hay `BIN.DPK`; el `.orig` que corresponde es el del contenedor completo).
- Una vez que un archivo se da por definitivo (la fuente con acentos ya lo está; en el futuro, `BIN.DPK` con el menú traducido), su `.orig` se saca de la carpeta que escanea `restore`, para que un `restore` posterior (por ejemplo al arreglar un `.MOV`) no lo revierta por accidente.

---

## 19. Pendientes actualizados

### Alta prioridad
- [ ] **Ubicar** `"Autosave File will be deleted... Are you sure you want to start a new game?"` — candidato más fuerte es `ENGLISH.PAK`, sin resolver.

### Prioridad media
- [ ] Descifrar el formato de `ENGLISH.PAK`.
- [ ] Investigar `DATA.DPK` (327 archivos `.XDT`) — posible relación con el XDT hack / voces sincronizadas, potencial hallazgo grande sin explorar.
- [ ] `CREDITPS.PAK` / `CREDITPT.PAK` (créditos finales, sin extraer todavía).
- [ ] Confirmar en emulador si las exclamaciones de minijuego (`LIFE UP !!`, `BONUS !!`, etc.) son visibles antes de decidir si vale la pena traducirlas.

### Baja prioridad / probablemente no vale la pena
- [ ] Texto de minijuegos en `SUBS*.BIN` / `SHOTPAC.DPK` — casi todo depuración interna, volumen mínimo de texto real.

### Ya resuelto en esta sesión (bajado de pendientes anteriores)
- [x] Subtítulos de cinemáticas (`.MOV`) — mecanismo, extracción y reinserción completos.
- [x] Voces sincronizadas (`PN_VOX1.PAC`) — mecanismo, extracción y reinserción completos.
- [x] Menús y textos del motor (`BIN.DPK`) — ubicados, clasificados, con pipeline de reinserción y detección de duplicados entre archivos.

### Sin cambios desde el informe anterior
- [ ] Disco 2 (`GAME2.DPK`): repetir `dump-all`/`build-all`/`repack`.
- [ ] Mayúsculas acentuadas: rediseñar acentos más bajos o ampliar el área de la baldosa.
- [ ] Distribución final con `xdelta3`, prueba en consola real con ODE.

---

## 20. Estimación de cobertura

Con el trabajo de esta sesión sumado a lo ya resuelto (diálogo completo vía `GAME*.SZ`, subtítulos de video, voces de sistema, menú principal y de opciones), la porción de texto **traducible con las herramientas ya construidas** ronda el 90% del contenido visible al jugador. Lo que queda afuera de ese número es, en su mayoría, contenido de bajo impacto (minijuegos, un puñado de mensajes de sistema aislados) más un archivo de formato todavía no identificado (`ENGLISH.PAK`) y el disco 2 completo (mismo mecanismo que el disco 1, solo falta repetir el pipeline ya validado).