---
name: geophis
description: Escribe y corre scripts SIG en Python con la librería geophis (API en español sobre geopandas y rasterio). Úsalo para trabajo forestal y territorial con shapefile, GeoPackage, KMZ o GPX, curvas de nivel, MDE, pendiente, clases por rangos, hidrología y cuencas, superficie en hectáreas, Sentinel-2 y NDVI, o cuando el usuario mencione geophis.
license: GPL-3.0-or-later
compatibility: Requiere Python 3.11 o superior con geophis instalado (pip install geophis) y un agente que ejecute comandos.
---

# geophis

La documentación completa viene dentro del paquete instalado, en `GEOPHIS.md`, y corresponde siempre a la versión instalada.

1. Ubica el archivo con el mismo intérprete de Python que correrá el script:
   ```
   python -c "import geophis, pathlib; print(geophis.__version__, pathlib.Path(geophis.__file__).with_name('GEOPHIS.md'))"
   ```
2. Si geophis no está instalado, instálalo con `pip install geophis` y repite el paso 1. Si el archivo no existe, la versión es anterior a la que lo incluye: `pip install -U geophis`.
3. Lee `GEOPHIS.md` completo antes de escribir, corregir o convertir código con geophis, y antes de responder sobre la librería. No lo leas por fragmentos ni escribas de memoria. Si no puedes leerlo, avisa y detente.
4. Sigue sus reglas (R1 a R9), sus recetas (P1 a P3) y su sección "Para la IA" (IA1 a IA5).
5. Corre el script tú y lee la salida de consola completa: los avisos de la librería salen por consola. IA3 (pedir la consola) aplica solo cuando el usuario es quien ejecuta.

Las normas propias del usuario o del equipo (nombres de archivo, rutas, entrega) van en el `AGENTS.md` o `CLAUDE.md` de su carpeta de trabajo, no en este skill.
