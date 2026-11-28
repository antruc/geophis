# geophis

**Una función en español por tarea SIG, del shapefile y las curvas de nivel a
las hectáreas por clase de una memoria técnica. Scripts que se leen como la
tarea y que una IA puede escribir por ti**

[![PyPI](https://img.shields.io/pypi/v/geophis)](https://pypi.org/project/geophis/)
![Python 3.11 a 3.14](https://img.shields.io/badge/python-3.11%20a%203.14-3776AB?logo=python&logoColor=white)
![geopandas + rasterio](https://img.shields.io/badge/base-geopandas%20%2B%20rasterio-139C5A)
![Idioma: español](https://img.shields.io/badge/API-en%20espa%C3%B1ol-red)
[![Licencia: GPL v3+](https://img.shields.io/badge/licencia-GPL--3.0--or--later-blue)](LICENSE)

![Logo de geophis](https://raw.githubusercontent.com/antruc/geophis/main/logo.svg)

## Contenido

- [Para qué sirve](#para-qué-sirve)
- [Instalación](#instalación)
- [Scripts hechos con IA](#scripts-hechos-con-ia)
- [Ejemplo básico](#ejemplo-básico)
- [Comando de consola](#comando-de-consola)
- [Documentación](#documentación)
- [Convenciones](#convenciones)
- [Desarrollo](#desarrollo)
- [Licencia](#licencia)
- [Créditos](#créditos)

## Para qué sirve

Para automatizar el trabajo forestal y territorial que termina en un entregable: shapefiles, rásters y cifras de superficie. En vez de repetir clics en QGIS o ArcGIS, escribes un script que se lee como la tarea (`cargar`, `recortar`, `interpolar_mde`, `calcular_pendiente`, `guardar`) y lo vuelves a correr cuando cambia un dato. Por debajo usa geopandas y shapely (vector), rasterio (ráster), pyproj (proyecciones) y scipy (análisis espacial), sin que tengas que repetir su sintaxis

Úsala cuando repites el mismo proceso en cada predio, o cambia un dato y hay que rehacer el MDE, la pendiente y las superficies

**Qué cubre:**

- **Vector y tabla:** cargar y guardar entre shp, gpkg, geojson, gpx, kml, kmz, CSV y Excel; recortar, disolver, zona de influencia, superficies, uniones y edición de campos
- **Ráster y terreno:** MDE desde curvas de nivel, pendiente, clases por rangos sin moteado, índices de vegetación y rutas sobre el terreno o una red de caminos
- **Hidrología:** flujo, red de drenaje, orden de cauces, cuencas y microcuencas
- **Sentinel-2:** la imagen más limpia de una temporada sobre el predio, sin cuenta ni token, con la cita obligatoria y una lámina PNG de revisión
- **Recetas completas** en [`GEOPHIS.md`](https://github.com/antruc/geophis/blob/main/src/geophis/GEOPHIS.md): de las curvas a las hectáreas por clase de pendiente, o del MDE a las cuencas, con todo lo que se decide en un bloque `CONFIG`

**Lo que te ahorra frente a un script con geopandas y rasterio a secas,** donde estos errores pasan sin avisar:

- Capas en CRS distintos: `recortar`, `intersecar`, `borrar` y `union_espacial` reproyectan la segunda capa solas
- Superficie vieja: si recortas después de calcular las hectáreas, `guardar` avisa que la columna ya no corresponde
- Huecos o doble conteo: `comprobar_particion` dice en hectáreas cuánto falta o sobra para que las clases sumen el predio
- Terreno inventado: el MDE no extrapola fuera de las curvas, deja sin dato y avisa cuántas hectáreas quedaron así
- Errores en español que dicen qué falló y cómo arreglarlo, en vez de un error técnico de GDAL en inglés

**Cifras que se pueden defender.** Los números que el script no decide (resolución del MDE, umbrales, superficie mínima de una mancha) se miden desde tus propios datos o se derivan de lo que tú declaras (la escala, los rangos), no se copian de otro predio. Si alguno quedó sin elegir, la comprobación que exige `GEOPHIS.md` detiene el script, y la librería imprime en consola de dónde salió cada número para revisarlo

No hace teledetección clasificada ni geocodificación, ni se conecta con SIG de escritorio. Solo usa internet para bajar Sentinel-2 desde Earth Search

## Instalación

Requiere Python 3.11 a 3.14. Python 3.15 todavía no: una dependencia (scikit-image) no publica versión para 3.15

```bash
pip install geophis
```

En Windows, si `pip` no se reconoce, usa `py -m pip install geophis`

- Comprueba la instalación: `python -c "import geophis; print(geophis.__version__)"`
- Para actualizar: `pip install -U geophis`
- Si tienes ArcGIS o QGIS, instala geophis en un Python propio (de python.org), no en el que trae ese programa: sus librerías de GDAL pueden chocar

## Scripts hechos con IA

**¿Se puede automatizar el trabajo SIG con una IA, en español?** Sí: le pides la tarea a Claude, ChatGPT u otro asistente en lenguaje normal, y te devuelve un script de geophis que corres en tu equipo con tus propios datos. Basta con darle [`GEOPHIS.md`](https://github.com/antruc/geophis/blob/main/src/geophis/GEOPHIS.md) como contexto: trae todas las funciones con su firma y sus trampas, más las reglas de trabajo

**En un chat (Claude Desktop, ChatGPT u otro):**

1. Crea un Proyecto en Claude (o el asistente que uses) y sube [`GEOPHIS.md`](https://github.com/antruc/geophis/blob/main/src/geophis/GEOPHIS.md) como contexto del proyecto
2. Pide la tarea en lenguaje normal: "recorta las curvas al predio, saca el MDE y clasifica la pendiente en 0 a 15, 15 a 30 y más de 30 %". Lo que decides tú (los rangos, la escala) va en el pedido; lo que se puede medir, como la resolución del MDE, lo mide el script. Si falta una decisión, la IA pregunta antes de escribir código
3. Corre el script que te devuelve: `python mi_script.py`
4. Pega la salida de consola completa en la conversación. Los avisos de la librería salen por consola, y es lo que la IA necesita para corregir

**Con un agente que ejecuta código** (Claude Code, Codex, Cursor, Gemini CLI, GitHub Copilot), los pasos 3 y 4 los hace el agente: escribe el script, lo corre y lee la salida él mismo. En vez de subir `GEOPHIS.md`, instala una vez el skill [`skills/geophis`](https://github.com/antruc/geophis/tree/main/skills/geophis). El skill lee el `GEOPHIS.md` que viene dentro del paquete instalado, así que siempre corresponde a tu versión de geophis y no hay que actualizarlo con cada `pip install -U geophis`:

- Con Node.js 22.20 o superior, para todos tus agentes a la vez: `npx skills add antruc/geophis -g`
- A mano: copia la carpeta `skills/geophis` a `~/.claude/skills/` (Claude Code) o a `~/.agents/skills/` (Codex, y Cursor, Gemini CLI y Copilot a nivel de carpeta de trabajo)

**Normas de trabajo (opcional).** [`docs/NORMAS.md`](https://github.com/antruc/geophis/blob/main/docs/NORMAS.md) es un ejemplo de normas: dónde entregar los scripts, cómo nombrarlos y cuándo ejecutar el proceso. Para usarlo:

1. Adáptalo a tu equipo. La norma 3 remite a un archivo propio de nombres de archivo (`NOMENCLATURA_SIG.md`): créalo o borra esa norma
2. En Claude Desktop: pega el texto en el campo "Instrucciones" del Proyecto (no lo subas como archivo) y sube tu `NOMENCLATURA_SIG.md` junto a `GEOPHIS.md`
3. Con un agente: guárdalo como `AGENTS.md` en la carpeta donde trabajas, junto a tu `NOMENCLATURA_SIG.md`
4. Solo con Claude Code: en esa misma carpeta crea un `CLAUDE.md` con una sola línea, `@AGENTS.md`. Claude Code no lee `AGENTS.md` si no se lo indicas así

Sin IA, el mismo [`GEOPHIS.md`](https://github.com/antruc/geophis/blob/main/src/geophis/GEOPHIS.md) sirve para escribir los scripts a mano: se salta la sección "Para la IA", se parte de la receta que se parezca a la tarea y cada función se busca por nombre en [`docs/API.md`](https://github.com/antruc/geophis/blob/main/docs/API.md)

## Ejemplo básico

```python
import geophis as geo

predio = geo.cargar("predio.shp")
EPSG_UTM = geo.detectar_utm(predio)                         # la zona UTM se mide, no se teclea
predio = geo.reproyectar(predio, EPSG_UTM)                  # UTM: distancias en metros
predio = geo.calcular_superficie(predio, campo="SUP")       # hectáreas por polígono
print(geo.superficie_total_ha(predio), "ha")
geo.guardar(predio, "predio_utm.shp")
```

Scripts completos en [`ejemplos/`](https://github.com/antruc/geophis/tree/main/ejemplos)

## Comando de consola

Convierte capas entre formatos. Queda disponible al instalar geophis

```bash
geophis predio.shp kmz          # crea predio.kmz
geophis ruta.gpx shp            # crea ruta.shp
geophis a.shp b.shp c.shp kmz   # varios a la vez
geophis ayuda
```

- Formatos: `shp`, `gpkg`, `geojson`, `gpx`, `kml`, `kmz`
- La salida queda en la misma carpeta, con el mismo nombre y la extensión nueva
- Para varios archivos, arrástralos juntos a la terminal y escribe el formato al final
- Si la salida ya existe, pregunta antes de sobrescribir; `--si` sobrescribe sin preguntar

## Documentación

| Archivo | Para qué |
|---|---|
| [`src/geophis/GEOPHIS.md`](https://github.com/antruc/geophis/blob/main/src/geophis/GEOPHIS.md) | La documentación completa, para personas y para IA: todas las funciones de `__all__` con su firma y sus trampas, las reglas y las recetas de MDE, pendientes e hidrología. La sección "Para la IA" es solo para la IA. Es también el único archivo de geophis que hay que cargar en un Proyecto de Claude |
| [`docs/DECISIONES.md`](https://github.com/antruc/geophis/blob/main/docs/DECISIONES.md) | Para personas: las mediciones que justifican cada regla y las alternativas descartadas. No se sube al Proyecto |
| [`docs/API.md`](https://github.com/antruc/geophis/blob/main/docs/API.md) | Índice para buscar una función por nombre: una línea por función, con enlace a su entrada completa en `GEOPHIS.md` |
| [`docs/NORMAS.md`](https://github.com/antruc/geophis/blob/main/docs/NORMAS.md) | Ejemplo de normas de trabajo. En Claude Desktop se pega su texto en "Instrucciones" (no se sube como archivo); con un agente se copia como `AGENTS.md` en la carpeta de trabajo |
| [`skills/geophis/`](https://github.com/antruc/geophis/tree/main/skills/geophis) | Skill para agentes que ejecutan código (Claude Code, Codex, Cursor...): les hace leer el `GEOPHIS.md` del paquete instalado |
| [`ejemplos/`](https://github.com/antruc/geophis/tree/main/ejemplos) | Scripts completos: `caminos.py` (buffer jerárquico), `hidrologia.py` (buffer por condición), `mde.py` (curvas a MDE) |

## Convenciones

- Una función por tarea SIG, con nombre en español. Las simples envuelven una operación de la librería base; las de MDE, pendiente, zonificación y rutas encadenan varias
- La lógica de dominio (claves de rodal, umbrales del predio) se queda en los scripts; la librería solo aporta operaciones SIG genéricas
- Distancias y márgenes en unidades del CRS activo; usar UTM para metros. `zona_de_influencia` y `crear_cuadro` fallan con un CRS en grados si la distancia o el margen no son 0. Lo que mide en metros sin decirlo en la firma falla con un CRS en grados o en pies
- Las funciones que devuelven capa trabajan sobre una copia y no mutan la entrada. Excepción: `reproyectar` devuelve la misma capa si ya está en el CRS pedido
- `recortar`, `intersecar`, `borrar` y `union_espacial` alinean el CRS solos, reproyectando el segundo argumento. `fusionar` no lo hace, a propósito
- Avisos y reportes por consola con `print`; los errores con `raise ValueError` de mensaje claro

El detalle de cada convención, con la medición que la justifica, está en [`docs/DECISIONES.md`](https://github.com/antruc/geophis/blob/main/docs/DECISIONES.md)

## Desarrollo

```bash
git clone https://github.com/antruc/geophis.git
cd geophis && pip install -e ".[dev]"
pytest                 # pruebas
ruff check src tests   # linter
ruff format src tests ejemplos  # formateo
```

[`.claude/CLAUDE.md`](.claude/CLAUDE.md) son las instrucciones para desarrollar la librería con Claude Code. No son para usar geophis: para eso están el skill y [`docs/NORMAS.md`](https://github.com/antruc/geophis/blob/main/docs/NORMAS.md)

| Ruta | Qué hace |
|---|---|
| [src/geophis/archivo.py](src/geophis/archivo.py) | Cargar y guardar capas: shp, gpkg, geojson, gpx, kml, kmz, CSV y Excel |
| [src/geophis/geometria.py](src/geophis/geometria.py) | Recortar, disolver, zona de influencia, fusionar, solapes, superficies |
| [src/geophis/tabla.py](src/geophis/tabla.py) | Tabla de atributos: seleccionar, resumir, unir y editar campos |
| [src/geophis/proyeccion.py](src/geophis/proyeccion.py) | CRS, reproyección y detección de la zona UTM |
| [src/geophis/raster.py](src/geophis/raster.py) | E/S ráster, remuestreo, MDE desde curvas, pendiente y rutas |
| [src/geophis/zonas.py](src/geophis/zonas.py) | De un ráster continuo a polígonos por rangos (reclasificar, sieve, poligonizar) |
| [src/geophis/hidrologia.py](src/geophis/hidrologia.py) | Flujo D8, red de drenaje y cuencas (único módulo que usa pysheds) |
| [src/geophis/satelite.py](src/geophis/satelite.py) | Sentinel-2 L2A por Earth Search y lectura por ventana |
| [src/geophis/parametros.py](src/geophis/parametros.py), [metrologia.py](src/geophis/metrologia.py) | Parámetros con procedencia y las reglas que los derivan |
| [src/geophis/barridos.py](src/geophis/barridos.py) | Cadenas completas sobre el MDE y barridos de parámetros |
| [src/geophis/lamina.py](src/geophis/lamina.py) | PNG de revisión de varios paneles |
| [src/geophis/cli.py](src/geophis/cli.py) | El comando `geophis` de conversión entre formatos |

## Licencia

Copyright (C) 2026 antruc

geophis es software libre: puedes redistribuirlo y modificarlo bajo los
términos de la [Licencia Pública General de GNU](LICENSE) publicada por la
Free Software Foundation, versión 3 o (a tu elección) cualquier posterior
(`GPL-3.0-or-later`). Se entrega sin garantía

La fuente DejaVu Sans que usa `lamina` va incluida sin cambios, con su propia
licencia en [src/geophis/fuentes/LICENSE_DEJAVU.txt](src/geophis/fuentes/LICENSE_DEJAVU.txt).
Las imágenes Sentinel-2 son datos Copernicus: `lamina` e `imagen_sentinel2`
incluyen la cita obligatoria

## Créditos

- [geopandas](https://geopandas.org) y [shapely](https://shapely.readthedocs.io): vector
- [rasterio](https://rasterio.readthedocs.io) y [pyproj](https://pyproj4.github.io/pyproj/): ráster y proyecciones
- [scipy](https://scipy.org), [scikit-image](https://scikit-image.org) y [contourpy](https://contourpy.readthedocs.io): interpolación, rutas y curvas
- [pysheds](https://github.com/mdbartos/pysheds): hidrología
- [Earth Search](https://element84.com/earth-search/) de Element 84 y [Copernicus](https://www.copernicus.eu): imágenes Sentinel-2
