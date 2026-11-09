# geophis

**Una función en español por tarea SIG, del shapefile y las curvas de nivel a
las hectáreas por clase de una memoria técnica. Scripts que se leen como la
tarea y que una IA puede escribir por ti**

[![PyPI](https://img.shields.io/pypi/v/geophis)](https://pypi.org/project/geophis/)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![geopandas + rasterio](https://img.shields.io/badge/base-geopandas%20%2B%20rasterio-139C5A)
![Idioma: español](https://img.shields.io/badge/API-en%20espa%C3%B1ol-red)
[![Licencia: GPL v3+](https://img.shields.io/badge/licencia-GPL--3.0--or--later-blue)](LICENSE)

Funciones en español para tareas SIG forestales y territoriales: de shapefiles y curvas de nivel a rásters, clases y superficies listas para una memoria técnica. Por debajo usa geopandas y shapely (vector), rasterio (raster), pyproj (proyecciones) y scipy (análisis espacial), sin que tengas que repetir su sintaxis en cada script

![Logo de geophis](https://raw.githubusercontent.com/antruc/geophis/main/logo.svg)

## Contenido

- [Para qué sirve](#para-qué-sirve)
- [Scripts hechos con IA](#scripts-hechos-con-ia)
- [Instalación](#instalación)
- [Ejemplo básico](#ejemplo-básico)
- [Qué hace](#qué-hace)
- [Comando de consola](#comando-de-consola)
- [Documentación](#documentación)
- [Convenciones](#convenciones)
- [Desarrollo](#desarrollo)
- [Licencia](#licencia)
- [Créditos](#créditos)

## Para qué sirve

Para el trabajo técnico forestal y territorial que termina en un entregable: shapefiles, rásters y cifras de superficie para una memoria técnica. En vez de repetir clics en QGIS o ArcGIS, automatizas la tarea en Python con un script que se lee como la tarea que hace (`cargar`, `recortar`, `interpolar_mde`, `calcular_pendiente`, `guardar`) y que se vuelve a correr cuando cambia un dato

**Tareas sueltas, en pocas líneas:**

- La superficie en hectáreas de cada polígono y el total, con la zona UTM detectada sola
- La tabla de atributos a un Excel listo para leer, con encabezado fijo y fechas DD/MM/AAAA
- La imagen Sentinel-2 más limpia de una temporada sobre el predio, y su NDVI
- Pasar un predio de shapefile a KMZ para abrirlo en el celular, o un GPX de campo a shapefile (también desde la consola, ver abajo)

**Flujos completos, del dato crudo al entregable.** Las recetas de [`docs/GEOPHIS.md`](https://github.com/antruc/geophis/blob/main/docs/GEOPHIS.md) encadenan todo en un script: curvas de nivel, MDE, pendiente, clases sin moteado y shapefile con la superficie de cada clase; o MDE, red de drenaje y cuencas. Cambiar un rango o una capa de entrada es cambiar una línea del bloque `CONFIG` y volver a correr

**Lo que te ahorra frente a un script con geopandas y rasterio a secas,** donde estos errores pasan sin avisar:

- Capas en CRS distintos: `recortar`, `intersecar`, `borrar` y `union_espacial` reproyectan la segunda capa solas
- Superficie vieja: si recortas después de calcular las hectáreas, `guardar` avisa que la columna ya no corresponde
- Huecos o doble conteo: `comprobar_particion` dice en hectáreas cuánto falta o sobra para que las clases sumen el predio
- Terreno inventado: el MDE no extrapola fuera de las curvas, deja sin dato y avisa cuántas hectáreas quedaron así
- Errores en español que dicen qué falló, en vez de un volcado de GDAL tres llamadas abajo

**Cifras que se pueden defender.** Los números que el script no decide (resolución del MDE, umbrales, superficie mínima de una mancha) se miden desde tus propios datos o se derivan de lo que tú declaras (la escala, los rangos), no se copian de otro predio. Si alguno quedó sin elegir, el script se detiene, y la librería imprime en consola de dónde salió cada número para revisarlo

## Scripts hechos con IA

**¿Se puede automatizar el trabajo SIG con una IA, en español?** Sí: le pides la tarea a Claude, ChatGPT u otro asistente en lenguaje normal, y te devuelve un script de geophis que corres en tu equipo con tus propios datos.

La librería está documentada para que una IA escriba los scripts por ti. [`docs/GEOPHIS.md`](https://github.com/antruc/geophis/blob/main/docs/GEOPHIS.md) trae todas las funciones con su firma y sus trampas, más las reglas de trabajo, y basta con cargarlo como contexto:

1. Crea un Proyecto en Claude (o el asistente que uses) y sube [`docs/GEOPHIS.md`](https://github.com/antruc/geophis/blob/main/docs/GEOPHIS.md) como contexto del proyecto
2. Pide la tarea en lenguaje normal: "recorta las curvas al predio, saca el MDE y clasifica la pendiente en 0 a 15, 15 a 30 y más de 30 %". Lo que decides tú (los rangos, la escala) va en el pedido; lo que se puede medir, como la resolución del MDE, lo mide el script. Si falta una decisión, la IA pregunta antes de escribir código
3. Corre el script que te devuelve: `python mi_script.py`
4. Pega la salida de consola completa en la conversación. Los avisos de la librería salen por consola, y es lo que la IA necesita para corregir

Con Claude Code, u otro agente que ejecute código, los pasos 3 y 4 los hace el agente: escribe el script, lo corre y lee la salida él mismo

[`docs/NORMAS_PROYECTO.md`](https://github.com/antruc/geophis/blob/main/docs/NORMAS_PROYECTO.md) es un ejemplo de instrucciones para un Proyecto de Claude Desktop: dónde entregar los scripts, cómo nombrarlos y cuándo ejecutar el proceso o devolverlo. Se pega directamente en el campo "Instrucciones" del Proyecto, **no se sube como archivo del proyecto**: como archivo es solo contexto que se consulta, y como instrucciones se aplica en cada conversación. Remite a un `NOMENCLATURA_SIG.md` propio, con la convención de nombres, que sí va como archivo junto a `GEOPHIS.md`; adáptalo o quita la norma 3

Sin IA, el mismo [`docs/GEOPHIS.md`](https://github.com/antruc/geophis/blob/main/docs/GEOPHIS.md) sirve para escribir los scripts a mano: se salta la sección "Para la IA", se parte de la receta que se parezca a la tarea y cada función se busca por nombre en [`docs/API.md`](https://github.com/antruc/geophis/blob/main/docs/API.md)

## Instalación

Requiere Python 3.11 o superior (probado en 3.14)

```bash
pip install geophis
```

`pip` instala por sí mismo las dependencias; la lista con sus versiones mínimas está en `pyproject.toml`

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

Del predio y sus curvas de nivel a clases de pendiente: receta [P1 en `docs/GEOPHIS.md`](https://github.com/antruc/geophis/blob/main/docs/GEOPHIS.md#p1-tabla-de-rangos-con-spline). La resolución del MDE y los umbrales no se teclean, los mide `derivar_parametros` desde las curvas

Scripts completos en [`ejemplos/`](https://github.com/antruc/geophis/tree/main/ejemplos)

## Qué hace

Cubre el pipeline de entregables, no el lienzo de un SIG de escritorio:

- **Vector:** cargar y guardar entre `.shp`, `.gpkg`, `.geojson`, `.gpx`, `.kml`, `.kmz`, CSV y Excel; recortar, disolver, zona de influencia, fusionar, solapes y prioridad entre capas, superficies y longitudes; ruta más rápida e isócronas sobre una red de calles o caminos (OSM incluido), con sentidos únicos
- **Ráster:** recorte, remuestreo, reproyección, MDE desde curvas de nivel, pendiente y aspecto, reclasificación por rangos, sieve, poligonización, índices de vegetación, mosaico de teselas, ruta a pie más rápida sobre el MDE y ruta de costo mínimo
- **Imagen:** Sentinel-2 L2A del predio por Earth Search, sin cuenta ni token: la mejor fecha limpia, varias teselas en un mosaico y la cita obligatoria
- **Tabla de atributos:** seleccionar por atributo o por ubicación, resumir, unir una planilla, crear, calcular, renombrar, borrar y cambiar el tipo de campos, crear una capa desde cero, ver el ancho de campo de un .shp, nombres y valores en mayúsculas o minúsculas
- **Lámina de revisión:** PNG de varios paneles sobre la misma malla (antes, después, cambio, clases) con color, leyenda, escala, norte y la cita de Copernicus; no es el mapa final
- **Hidrología:** dirección y acumulación de flujo, red de drenaje, orden de cauces, cuencas y microcuencas
- **Parámetros con procedencia:** `derivar_parametros` mide desde las curvas, `tabla_procedencia` clasifica cada número como medido, declarado, derivado, convención o default

No hace teledetección clasificada, ni geocodificación, ni se conecta con SIG de escritorio. No se conecta a servicios externos, salvo Earth Search para bajar Sentinel-2

## Comando de consola

```bash
geophis predio.shp kmz     # convierte entre formatos, en lote
geophis ayuda
```

Convierte entre `shp`, `gpkg`, `geojson`, `gpx`, `kml` y `kmz`. CSV y Excel se leen y escriben desde Python, no desde la consola

## Documentación

| Archivo | Para qué |
|---|---|
| [`docs/GEOPHIS.md`](https://github.com/antruc/geophis/blob/main/docs/GEOPHIS.md) | La documentación completa, para personas y para IA: todas las funciones de `__all__` con su firma y sus trampas, las reglas y las recetas de MDE, pendientes e hidrología. La sección "Para la IA" es solo para la IA. Es también el único archivo de geophis que hay que cargar en un Proyecto de Claude |
| [`docs/DECISIONES.md`](https://github.com/antruc/geophis/blob/main/docs/DECISIONES.md) | Para personas: las mediciones que justifican cada regla y las alternativas descartadas. No se sube al Proyecto |
| [`docs/API.md`](https://github.com/antruc/geophis/blob/main/docs/API.md) | Índice para buscar una función por nombre: una línea por función, con enlace a su entrada completa en `GEOPHIS.md` |
| [`docs/NORMAS_PROYECTO.md`](https://github.com/antruc/geophis/blob/main/docs/NORMAS_PROYECTO.md) | Ejemplo de instrucciones para un Proyecto de Claude Desktop. Se pega en "Instrucciones"; no se sube como archivo |
| [`ejemplos/`](https://github.com/antruc/geophis/tree/main/ejemplos) | Scripts completos: `caminos.py` (buffer jerárquico), `hidrologia.py` (buffer por condición), `mde.py` (curvas a MDE) |

## Convenciones

- Una función por tarea SIG, con nombre en español. Las simples envuelven una operación de la librería base; las de MDE, pendiente, zonificación y rutas encadenan varias
- La lógica de dominio (claves de rodal, umbrales del predio) se queda en los scripts; la librería solo aporta operaciones SIG genéricas
- Distancias y márgenes en unidades del CRS activo; usar UTM para metros. Lo que mide en metros sin decirlo en la firma falla con un CRS en grados o en pies
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
ruff format src tests  # formateo
```

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
