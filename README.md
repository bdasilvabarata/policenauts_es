# Policenauts ES

Traducción fan al español de **Policenauts** (PlayStation), construida sobre la base del parche en inglés de JunkerHQ.

<p align="center">
  <em>Herramientas de romhacking + documentación técnica + parche jugable</em>
</p>

---

## Sobre el proyecto

Este repositorio no es solo un parche — es un **kit de herramientas de romhacking documentado**, pensado para que el trabajo de análisis del formato del juego (contenedores, codificación de texto, fuente bitmap, subtítulos de video, voces, menús de sistema) sirva de base para traducir Policenauts a **cualquier idioma**, no solo al español.

Todo lo que se fue descubriendo sobre el funcionamiento interno del juego —formatos de archivo, algoritmos de codificación, mecanismos de punteros, estructura de la fuente— está documentado en [`Informe de avances.md`](./Informe%20de%20avances%20—%20Traducción%20de.md), junto con los scripts Python que implementan cada pieza. Alguien que quiera traducir el juego a otro idioma puede reusar directamente las herramientas y saltarse meses de ingeniería inversa.

## Créditos

Este proyecto no existiría sin el trabajo previo de:

- **[JunkerHQ](https://www.junkerhq.net/)** — por el hack DATCH original, que resolvió el problema de punteros del bytecode compilado del japonés y produjo la primera traducción jugable al inglés. Este proyecto parte de esa base en vez de re-resolver el romhacking desde el disco japonés original.
- **[slowbeef](https://www.lparchive.org/)** — por documentar en detalle el proceso de ese hack en su Let's Play *"Tales of ROMhacking"* en lparchive.org, un recurso que aceleró enormemente el entendimiento del formato del juego.

## Estado actual

El proyecto está **en desarrollo activo**. A la fecha:

| Contenido | Estado |
|---|---|
| Diálogo principal (`GAME1.DPK`, disco 1) | Mecanismo resuelto, herramientas de extracción/reinserción funcionando |
| Fuente bitmap y acentos españoles | Resuelto y validado en emulador |
| Subtítulos de cinemáticas (`.MOV`) | Mecanismo resuelto, herramientas funcionando |
| Voces sobre imagen estática (`PN_VOX1.PAC`) | Mecanismo resuelto, herramientas funcionando |
| Menús y textos de sistema (`BIN.DPK`) | Ubicados y clasificados, en traducción |
| Disco 2 (`GAME2.DPK`) | Pendiente — mismo mecanismo que disco 1 |
| Créditos finales, minijuegos, un archivo de formato sin identificar | Pendiente / baja prioridad |

El detalle completo, decisión por decisión, está en el informe de avances — incluye tanto lo que funcionó como los callejones sin salida, porque documentar los errores ahorra tiempo a quien retome este trabajo.

## Estructura del repositorio

```
policenauts_es/
├── Informe de avances — Traducción de.md   # bitácora técnica completa del proyecto
├── GUIA.md                                  # guía de fases, para arrancar de cero
├── pntool.py                                # toolkit general: contenedores FRID, .MOV, PAC, BIN
├── sz_text.py                                # extracción/reinserción de guion (GAME*.SZ)
├── dpk_patch.py                              # reempaquetado de contenedores FRID
├── fontlab.py                                # fuente bitmap, tabla de acentos
├── negprobe.py                               # detección de codificación (negación, texto ancho)
├── ckfind.py                                 # identificación de algoritmos de checksum
├── render4bpp.py                             # visor de bitmaps 4bpp con paleta
├── comparar_arboles.py / bin_compare.py      # comparación de árboles/imágenes JP vs EN
└── ...                                        # resto de utilidades de diagnóstico
```

## Cómo está pensado para reusarse

Cada herramienta se construyó para ser **general**, no atada a la traducción al español específicamente:

- El pipeline de extracción/reinserción (`dump` → traducir CSV → `build` → reempaquetar) no asume ningún idioma de destino.
- El sistema de acentos (`fontlab.py accents`) genera la tabla de glifos y el mapeo de caracteres a partir de una lista de acentos dada — se puede adaptar a otro alfabeto.
- Todo el análisis de formato (contenedor FRID, codificación por negación de bytes, mecanismo de subtítulos de video, estructura de `BIN.DPK`) es válido sin importar a qué idioma se traduzca.

Si tu objetivo es traducir Policenauts a otro idioma, el punto de partida más rápido es leer el informe de avances de punta a punta y reusar las herramientas tal cual — la mayor parte del trabajo pesado (entender *dónde* y *cómo* vive cada tipo de texto) ya está hecho.

## Uso rápido

Requiere Python 3 (solo librería estándar) y [`mkpsxiso`](https://github.com/Lameguy64/mkpsxiso)/`dumpsxiso` en el `PATH`.

```powershell
# extraer el guion principal a CSV
python sz_text.py dump-all bin\extraido_game1_en bin\extraido_game1_jp -o bin\csv

# completar la columna "traduccion" de cada CSV, luego reconstruir
python sz_text.py build-all bin\extraido_game1_en bin\extraido_game1_jp bin\csv -o bin\sz_out --charmap bin\charmap.json
python dpk_patch.py repack bin\GAME1.DPK.orig bin\sz_out -o bin\GAME1_nuevo.DPK
```

El detalle completo de cada paso —incluidos subtítulos de video, voces y menús— está en la sección de pipeline del informe de avances.

## Aviso legal

Este es un proyecto de traducción fan, sin fines de lucro, hecho por y para la comunidad. No se distribuyen archivos del juego original ni del parche de JunkerHQ — el repositorio contiene únicamente herramientas y, cuando corresponda, un parche diferencial para aplicar sobre una copia legalmente obtenida del juego.

## Contribuir

El proyecto está abierto a colaboración, especialmente en:
- Revisión de traducción y corrección de estilo
- Pruebas en emulador y en consola real (ODE)
- Extensión de las herramientas a contenido todavía sin resolver (ver la sección de pendientes del informe)

Abrí un issue o un pull request si querés sumarte.
