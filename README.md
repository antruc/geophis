# geophis

Librería SIG Wrapper en español sobre geopandas y shapely (vector), rasterio (raster) y pyproj (proyecciones), con scipy para el análisis espacial. Una función clara por operación SIG, en lugar de repetir la sintaxis de cada librería en cada script

![Logo de geophis](https://raw.githubusercontent.com/antruc/geophis/main/logo.png)

**Versión:** 1.2.1 | **Python:** >= 3.11 (probado en 3.14) | **Licencia:** GPL-3.0-or-later

## Para qué sirve

geophis existe para que un script SIG se lea como la tarea que hace: `cargar`, `recortar`, `interpolar_mde`, `calcular_pendiente`, `guardar`. Está pensado para el trabajo técnico forestal y territorial que termina en un entregable (shapefiles, rásters y cifras de superficie para una memoria técnica), donde cada número tiene que poder justificarse

## Scripts hechos con IA

La librería está documentada para que una IA escriba los scripts por ti. [`docs/GEOPHIS.md`](docs/GEOPHIS.md) trae todas las funciones con su firma y sus trampas, más las reglas de trabajo, y basta con cargarlo como contexto:

1. Crea un Proyecto en Claude (o el asistente que uses) y sube [`docs/GEOPHIS.md`](docs/GEOPHIS.md) como contexto del proyecto
2. Pide la tarea en lenguaje normal: "recorta las curvas al predio, saca el MDE a 5 m y clasifica la pendiente en tres rangos"
3. Corre el script que te devuelve: `python mi_script.py`
4. Pega la salida de consola completa en la conversación. Los avisos de la librería salen por consola, y es lo que la IA necesita para corregir

Con Claude Code, u otro agente que ejecute código, los pasos 3 y 4 los hace el agente: escribe el script, lo corre y lee la salida él mismo

## Instalación

Requiere Python >= 3.11

```bash
pip install geophis
```

La última versión de GitHub, antes de que llegue a PyPI:

```bash
pip install git+https://github.com/antruc/geophis.git
```

Instalación local, para editar la librería o correr los tests:

```bash
git clone https://github.com/antruc/geophis.git
cd geophis
pip install -e ".[dev]"   # -e: los cambios en src/ se ven sin reinstalar; [dev] suma pytest y ruff
```

Depende de geopandas >= 1.1, shapely >= 2.1, pyproj >= 3.5, pandas, numpy, rasterio, affine >= 3.0, scipy, threadpoolctl, contourpy, pysheds, scikit-image, openpyxl y Pillow; `pip` las instala solas

## Ejemplo básico

```python
import geophis as geo

predio = geo.cargar("predio.shp")
predio = geo.reproyectar(predio, 32613)                     # UTM 13N: distancias en metros
predio = geo.calcular_superficie(predio, campo="SUP")       # hectáreas por polígono
print(geo.superficie_total_ha(predio), "ha")
geo.guardar(predio, "predio_utm.shp")
```

Un paso más, del predio y sus curvas de nivel a un ráster de pendiente:

```python
curvas = geo.cargar("curvas.shp")
mde, tf = geo.interpolar_mde(geo.recortar(curvas, predio), "COTA", resolucion=5)
pendiente = geo.calcular_pendiente(mde, tf)
```

Scripts completos en [`ejemplos/`](ejemplos/)

## Qué hace

Cubre el pipeline de entregables, no el lienzo de un SIG de escritorio:

- **Vector:** cargar y guardar entre `.shp`, `.gpkg`, `.geojson`, `.gpx`, `.kml`, `.kmz`, CSV y Excel; recortar, disolver, zona de influencia, fusionar, solapes y prioridad entre capas, superficies y longitudes; ruta más rápida e isócronas sobre una red de calles o caminos (OSM incluido), con sentidos únicos
- **Ráster:** recorte, remuestreo, reproyección, MDE desde curvas de nivel, pendiente y aspecto, reclasificación por rangos, sieve, poligonización, índices de vegetación, mosaico de teselas, ruta a pie más rápida sobre el MDE y ruta de costo mínimo
- **Imagen:** Sentinel-2 L2A del predio por Earth Search, sin cuenta ni token: la mejor fecha limpia, varias teselas en un mosaico y la cita obligatoria
- **Tabla de atributos:** seleccionar por atributo o por ubicación, resumir, unir una planilla, crear, calcular, renombrar, borrar y cambiar el tipo de campos, crear una capa desde cero, ver el ancho de campo de un .shp, nombres y valores en mayúsculas o minúsculas
- **Lámina de revisión:** PNG de varios paneles sobre la misma malla (antes, después, cambio, clases) con color, leyenda, escala, norte y la cita de Copernicus; no es el mapa final
- **Hidrología:** dirección y acumulación de flujo, red de drenaje, orden de cauces, cuencas y microcuencas
- **Parámetros con procedencia:** cada número de una memoria técnica se mide o se declara con su razón, y la librería puede decir de dónde salió

No hace teledetección clasificada, ni geocodificación, ni se conecta con SIG de escritorio, ni dibuja mapas finales: arma láminas de revisión, y la cartografía final se arma en el SIG. No se conecta a servicios externos, salvo Earth Search para bajar Sentinel-2

## Comando de consola

```bash
geophis predio.shp kmz     # convierte entre formatos, en lote
geophis ayuda
```

Convierte entre `shp`, `gpkg`, `geojson`, `gpx`, `kml` y `kmz`. CSV y Excel se leen y escriben desde Python, no desde la consola

## Documentación

| Archivo | Para qué |
|---|---|
| [`docs/GEOPHIS.md`](docs/GEOPHIS.md) | La documentación completa: todas las funciones de `__all__` con su firma y sus trampas, los flujos de MDE, pendientes e hidrología, y las instrucciones de trabajo. Es también el único archivo que hay que cargar en un Proyecto de Claude |
| [`docs/API.md`](docs/API.md) | Índice compacto para buscar una función por nombre |
| [`ejemplos/`](ejemplos/) | Scripts completos: `caminos.py` (buffer jerárquico), `hidrologia.py` (buffer por condición), `mde.py` (curvas a MDE) |

## Estructura

```
src/geophis/      la librería (un módulo por capa; las dependencias solo bajan)
tests/           suite pytest
docs/            documentación
ejemplos/        scripts de referencia
```

Los módulos están en capas y las dependencias solo bajan: `proyeccion` y `metrologia`, luego `archivo` y `raster`, luego `geometria`, `hidrologia`, `tabla` y `satelite`, luego `zonas` y `lamina`, luego `barridos`, luego `parametros`, y arriba `cli`. `tests/test_capas.py` lo vigila. Los scripts de trabajo de cada predio no son parte de la librería: traen rutas absolutas y cifras de un predio

## Tests

```bash
pytest                    # los 2 tests de red corren solo con GEOPHIS_RED=1
ruff check .              # lint
ruff format .             # formato (line-length 90, configurado en pyproject)
```

`tests/test_docs.py` comprueba que las firmas escritas en `docs/GEOPHIS.md` no se desfasen de las reales, que el índice de `docs/API.md` cubra toda la API, y que la cadena de pendientes documentada corra entera. `tests/test_defectos.py` fija uno a uno los defectos ya corregidos, tres de ellos de falla silenciosa

## Convenciones

- Wrapper delgado: una función = una operación de la librería base, con nombre simple en español
- La lógica de dominio (claves de rodal, umbrales del predio) se queda en los scripts; la librería solo aporta operaciones SIG genéricas
- Distancias y márgenes en unidades del CRS activo; usar UTM para metros. Lo que mide en metros sin decirlo en la firma falla con un CRS en grados o en pies. EPSG de trabajo típico: 32613 (UTM 13N)
- Las funciones que devuelven capa trabajan sobre una copia y no mutan la entrada. Excepción: `reproyectar` devuelve la misma capa si ya está en el CRS pedido
- `recortar`, `intersecar`, `borrar` y `union_espacial` alinean el CRS solos, reproyectando el segundo argumento. `fusionar` no lo hace, a propósito
- Avisos y reportes por consola con `print`; los errores con `raise ValueError` de mensaje claro

El detalle de cada convención, con la medición que la justifica, está en `docs/GEOPHIS.md`
