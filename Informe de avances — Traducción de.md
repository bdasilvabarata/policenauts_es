# Informe de avances — Traducción de Policenauts (PSX) al español

**Objetivo del proyecto:** crear una traducción al español de Policenauts, jugable en PS1 original (modeada), partiendo del hack en inglés de JunkerHQ/slowbeef y reutilizando su infraestructura de punteros en vez de re-hacer el romhacking desde cero sobre el japonés.

---

## 1. Contexto y decisión estratégica

Existe un proyecto de traducción español en curso (TraduSquare / "Traducciones del Tío Víctor", con SkyBladeCloud, GameZelda, FacundoARG, IlDucci y otros), pero está muy poco activo desde hace años. Se decidió arrancar un proyecto independiente en vez de sumarse.

**Decisión clave de arquitectura:** en vez de partir del disco japonés y re-resolver el problema de punteros en el bytecode compilado (que JunkerHQ ya resolvió con su hack DATCH), se parte del **disco en inglés ya parcheado**, heredando esa infraestructura.

### Qué es DATCH (Double Addressing Text-Chaining Hack)
Documentado por slowbeef en los updates "Tales of ROMhacking" de su LP en lparchive.org. El problema original: el japonés guarda texto y punteros dentro de un script compilado tipo bytecode, donde un mismo valor numérico puede ser un puntero de texto o un opcode cualquiera — no se puede armar una tabla de punteros por búsqueda simple. La solución de JunkerHQ: dejar los punteros originales intactos, e insertar punteros *nuevos* embebidos en el propio flujo de texto que redirigen a donde quedó reubicado el texto en inglés (más largo), parcheando también la rutina de lectura de texto en ASM para reconocer el marcador.

**Consecuencia práctica:** comparar JP vs EN muestra el formato original + DATCH ya aplicado, no el formato original puro.

---

## 2. Toolchain decidido

| Herramienta | Uso |
|---|---|
| `dumpsxiso` / `mkpsxiso` (Lameguy64) | Desarmar y rearmar la imagen ISO respetando XA/Mode2. Standalone, no requiere PSn00bSDK completo |
| Python 3 (stdlib) | Todas las herramientas de análisis propias (ver sección 5) |
| ImHex / HxD | Edición hexadecimal manual |
| Ghidra + PSX Loader | Para cuando haga falta tocar la rutina ASM de lectura de texto |
| PCSX-Redux | Emulador con debugger integrado y GDB stub, reemplaza al viejo pSX debugger que usó slowbeef |
| DuckStation | Testeo de juego con BIOS real |
| ODE (xStation/MODE/PSIO) recomendado | Para iterar en consola real sin quemar CDs |
| xdelta3 | Distribución final como parche, nunca como imagen |
| jPSXdec | Para cuando se aborde el subtitulado de video STR/XA |

---

## 3. Estructura del disco: lo que se mapeó

Extracción con `dumpsxiso` de JP1/JP2/EN1/EN2. Comparación de árboles completos (`comparar_arboles.py`) sobre `NAUTS/`:

- **45 archivos** en total, 14 idénticos, 24 con mismo tamaño pero contenido distinto, 7 con tamaño distinto.
- La mayoría de los cambios de tamaño son **múltiplos exactos de 2048 bytes** (un sector PSX) — señal de repadding a nivel contenedor, no necesariamente texto reescrito.
- Los `.MOV` (video STR) tienen `ascii80%` bajo esperado (no son texto).
- `PN_VOX1.PAC` creció ~1.2MB en sectores exactos — sospechoso de ser reordenamiento, no texto; deprioritizado.

### Archivos priorizados como candidatos a guion
`GAME1.DPK` y `GAME2.DPK` (nombre + crecimiento de tamaño) y `KERNEL.SC` (cambio de 6 bytes sin alinear a sector, señal de edición de contenido real).

---

## 4. El contenedor `GAME1.DPK` — formato FRID, resuelto

Deducido por inspección manual de cabecera + validado con parser (`pntool.py dpk-list`):

```
Firma "FRID" en offset 0x00
Directorio de registros de 24 bytes cada uno, desde offset 0x20:
  12 bytes: nombre del subarchivo (ASCII, padding con \0)
  4 bytes:  offset (LE)
  4 bytes:  tamaño (LE)
  4 bytes:  checksum (probable CRC32, no confirmado)
```

**Contenido confirmado de `GAME1.DPK`:** 27 subarchivos `GAME00.SZ` a `GAME1A.SZ` (uno por escena/capítulo, todos alineados a sector) + `NAMEID1.DAT`.

### Priorización por crecimiento (JP→EN), vía `pntool.py dpk-diff`
`GAME18.SZ` es el que más creció (+9920 bytes, +23%) — prioridad #1 para descifrar la compresión. `GAME07`, `GAME0A`, `GAME11`, `GAME12`, `GAME14`, `GAME16` no cambiaron (`+0`) — bajar prioridad. `GAME17.SZ` es byte-a-byte idéntico — ignorar.

---

## 5. `NAMEID1.DAT` — resuelto: es gráfico, no texto

**Conclusión confirmada visualmente:** bitmap de 4 bits por píxel (nibble) con paleta de color al inicio.

- **Cabecera:** 32 bytes = paleta de 16 colores BGR555 (formato TIM estándar de PSX)
- **Caja de cada entrada:** 40×15 píxeles, confirmado visualmente (mostraba "Ed", "Jonathan", "Man", "Woman", "Lorraine", "Officer" con borde)
- **Total:** 33 entradas exactas (495 filas de datos / 15 = 33; y 33×300+32 = 9932 bytes, coincide exacto con el tamaño del archivo)
- Es un sprite sheet de nombres/roles de personaje. La paleta JP es un gradiente suave (antialiasing de kanji); la paleta EN es casi plana (JunkerHQ simplificó a texto sin antialiasing)

**Implicancia para el proyecto:** este archivo no se toca con herramientas de punteros de texto — es trabajo de edición gráfica (Fase 4 de la guía), hay que redibujar los 33 bitmaps en español respetando la paleta y el recuadro.

**Lección de proceso:** la detección automática de ancho/alto por heurística (autocorrelación, contraste de uniformidad) resultó frágil y fue descartada como método confiable; lo que funcionó fue generar recortes chicos con dimensiones exactas conocidas (`--start-row`/`--max-rows`) y contar cajas a ojo sobre imágenes que no sufrieran recompresión de la plataforma de chat.

---

## 6. Compresión de `GAME*.SZ` — SIN RESOLVER (bloqueante principal)

**Confirmado:** el 97% del contenido de `GAME1.DPK` (todo el guion, presumiblemente) está comprimido con un algoritmo propietario. `ascii80%` da ~0% en crudo tanto en JP como en EN, para todos los archivos `.SZ`.

**Cabecera de `GAME18.SZ`:** primeros 2 bytes `2F 3F` idénticos entre JP y EN (firma). Bytes 2-3 difieren. El resto de los primeros 64 bytes es byte a byte idéntico entre JP y EN — consistente con que esa zona sea el prólogo de bytecode que JunkerHQ nunca toca.

### Intentos de descifrado (todos con resultado negativo hasta el momento)

Se construyó `decomp_probe.py`: un arnés que prueba variantes de LZSS clásico automáticamente y puntúa por:
1. **`ascii80%`**: cobertura del patrón `0x80+ascii`
2. **`diversity`** (corregida dos veces tras falsos positivos): cantidad de *rachas* de 6+ caracteres con 12+ caracteres distintos — filtra patrones degenerados tipo `@@@@@@@` que pasaban el filtro de cobertura sin ser texto real

**Dimensiones del espacio de búsqueda cubierto (~128 combinaciones), todas con resultado negativo (`diversity=0`) en la corrida más reciente:**
- Orden de bits del byte de flags: LSB-first / MSB-first
- Ancho offset+largo: (12,4), (11,5), (10,6), (13,3) bits
- Largo mínimo de coincidencia: 2 o 3
- Offset absoluto vs. relativo al final del buffer
- Orden de campos: [offset][largo] vs [largo][offset]
- Convención de bit literal: 1=literal vs 0=literal (invertida)
- Endianness del par offset+largo: little vs big-endian
- `--skip-header`: 0, 2, 4, 6, 8 bytes

**Dos falsos positivos identificados y corregidos en el proceso** (documentados como lección): una config con `ascii80%=19%` y otra con `36%` parecían prometedoras por consumir ~100% del stream de entrada, pero al inspeccionar visualmente resultaron ser patrones degenerados (`@@@@@@@`, bucles de auto-referencia cortos) — la lección es que **consumir el 100% de la entrada no prueba que el algoritmo sea correcto** si el decodificador nunca puede fallar limpio; solo mirar el texto decodificado (`pntool.py scan` + inspección manual) es prueba real.

### Hipótesis pendientes para la próxima sesión
1. Confirmar el resultado de la última corrida con los 128 parámetros (en curso al cierre de este informe)
2. Si sigue en cero: es evidencia fuerte de que **no es LZSS clásico de 16 bits**. Próximas familias a explorar:
   - Códigos de largo variable / Huffman (no alineado a byte)
   - Formato propietario de Konami sin relación con LZ77
   - Esquema de flag con más de 1 bit por token (2 bits = 4 casos posibles)
3. Revisar si `KERNEL.SC` (aún no analizado en profundidad) podría dar pistas del algoritmo por ser un archivo mucho más chico y editado de forma más quirúrgica

---

## 7. Herramientas propias construidas (todas en `/mnt/user-data/outputs/`)

| Archivo | Función |
|---|---|
| `pntool.py` | Suite principal: `scan`/`gaps`/`probe` (detectar y validar formato de texto+punteros), `dump`/`build` (extraer y reinsertar traducciones), `header` (inspección hex + heurística de tabla de offsets), `dpk-list`/`dpk-extract`/`dpk-diff` (parser del contenedor FRID), `diff` (comparar binarios JP/EN) |
| `comparar_arboles.py` | Compara dos árboles completos extraídos con dumpsxiso, prioriza archivos por cobertura de texto |
| `render4bpp.py` | Decodifica y renderiza bitmaps de 4bpp con paleta (usado para `NAMEID1.DAT`); incluye `--grid`, `--start-row`/`--max-rows`, `--detect-height` (autocorrelación, confiable solo con ancho ya conocido) |
| `decomp_probe.py` | Arnés de prueba de descompresión LZSS con matriz de parámetros y detector de texto real vs. patrones degenerados |
| `GUIA.md` | Guía paso a paso completa del proyecto con herramientas, fases y orden de ataque recomendado |

---

## 8. Estado actual y próximo paso inmediato

**Bloqueante activo:** el algoritmo de compresión de `GAME*.SZ` sigue sin identificarse. Es el ítem que más impacta el proyecto porque ahí vive prácticamente todo el guion.

**Resuelto y cerrado:** extracción de discos, formato del contenedor FRID, identificación y priorización de archivos de guion candidatos, formato gráfico de `NAMEID1.DAT` completo.

**Siguiente sesión debería arrancar por:** revisar el resultado de la corrida de 128 combinaciones sobre `GAME18.SZ`, y si sigue negativa, pivotar la investigación hacia familias de compresión no-LZSS (punto 6.3 de este informe).