"""Las capas del paquete, y que las dependencias solo apunten hacia abajo.

Un import hacia arriba no rompe nada, no sale en ningún test y se escribe en
treinta segundos, así que las capas se deshacen solas si nada lo vigila. Esto
lo vigila.

La regla es una sola: un módulo solo puede importar de capas ESTRICTAMENTE por
debajo de la suya, y a nivel de módulo. Los imports diferidos (dentro de una
función) son la válvula de escape, y por eso están enumerados uno a uno: cada
uno tiene que ganarse su sitio por escrito.
"""

import ast
from pathlib import Path

import pytest

PAQUETE = Path(__file__).resolve().parent.parent / "src" / "geophis"

# De abajo hacia arriba. El numero es la capa; empatados = no pueden importarse
# entre si.
CAPAS = {
    "proyeccion": 0,  # CRS. No depende de nada.
    "metrologia": 0,  # las REGLAS: aritmetica pura, sin rasters ni geometrias.
    "_salida": 0,  # el interruptor de reportes (`geo.MOSTRAR`). No importa nada.
    "archivo": 1,  # E/S vectorial. Solo necesita proyeccion.
    "raster": 1,  # E/S raster, MDE, pendiente. Usa metrologia y proyeccion.
    "geometria": 2,  # vector. Usa raster (rasterizar / poligonizar).
    # pysheds. Empatada con geometria y sin importarse con ella: solo necesita
    # rellenar_nodata / poligonizar / rasterizar, publicas y de la capa de abajo.
    "hidrologia": 2,
    # la tabla de atributos. Solo necesita proyeccion (_exigir_crs) y archivo
    # (el aviso de medida obsoleta, que es de E/S y ya vivia ahi). Empatada con
    # geometria y sin importarla: seleccionar_por_atributo y estadisticas_de_resumen no tocan la geometria, y
    # seleccionar_por_ubicacion se resuelve con `sjoin` de geopandas.
    "tabla": 2,
    # Sentinel-2 por Earth Search: red (urllib) + STAC. Usa raster (mosaico),
    # archivo (detectar_utm) y proyeccion. `raster` no importa nada de red.
    "satelite": 2,
    # raster continuo -> poligonos por rangos. Necesita raster y geometria a la vez.
    "zonas": 3,
    "barridos": 4,  # lo que MIDE. Corre la cadena de `zonas` a varios `h`.
    "parametros": 5,  # procedencia. Encadena todo lo anterior.
    "cli": 6,  # la interfaz. Encima de todo.
}

# Imports DIFERIDOS permitidos: (modulo, modulo_importado, motivo).
# Cualquier otro import dentro de una funcion es un fallo: o sube a la cabecera,
# o el modulo esta en la capa equivocada.
DIFERIDOS_OK = {
    ("raster", "geometria"): (
        "la UNICA arista que sube. `interpolar_mde` deriva sus defaults de "
        "lambda, y lambda se mide con una operacion VECTORIAL "
        "(`separacion_media`), que vive una capa arriba. Es inherente: el "
        "interpolador necesita una medicion de la cartografia. Diferido a "
        "proposito y documentado en `_separacion_horizontal`."
    ),
    ("cli", "geophis"): (
        "import perezoso a proposito: `geophis ayuda` no tiene por que cargar "
        "geopandas, que son segundos de arranque para imprimir un texto."
    ),
}


def _modulos():
    for ruta in sorted(PAQUETE.glob("*.py")):
        if ruta.stem != "__init__":
            yield ruta.stem, ast.parse(ruta.read_text(encoding="utf-8"))


def _imports_internos(arbol):
    """(nombre_importado, es_diferido) de cada `from .x import ...` del modulo."""
    cuerpo = set(map(id, arbol.body))
    for nodo in ast.walk(arbol):
        if not (isinstance(nodo, ast.ImportFrom) and nodo.level > 0):
            continue
        # `from . import convertir` (module None) es el paquete entero
        yield (nodo.module or "geophis"), id(nodo) not in cuerpo


def test_todos_los_modulos_tienen_capa():
    reales = {n for n, _ in _modulos()}
    assert reales == set(CAPAS), (
        "modulo nuevo sin capa asignada (o capa de un modulo que ya no existe). "
        "Decide en que capa vive ANTES de escribirlo:\n"
        f"   en disco: {sorted(reales)}\n"
        f"   en CAPAS: {sorted(CAPAS)}"
    )


@pytest.mark.parametrize("modulo", sorted(CAPAS))
def test_las_dependencias_apuntan_hacia_abajo(modulo):
    arbol = next(a for n, a in _modulos() if n == modulo)
    fallos = []
    for importado, diferido in _imports_internos(arbol):
        if diferido:
            continue  # se juzgan en el test de abajo
        if importado not in CAPAS:
            fallos.append(f"'{importado}' no esta en CAPAS")
        elif CAPAS[importado] >= CAPAS[modulo]:
            fallos.append(
                f"'{modulo}' (capa {CAPAS[modulo]}) importa '{importado}' "
                f"(capa {CAPAS[importado]}) a nivel de modulo"
            )
    assert not fallos, (
        f"dependencia que no baja en {modulo}.py:\n   "
        + "\n   ".join(fallos)
        + "\nO el import sube a una capa mas baja, o el modulo esta mal colocado."
    )


@pytest.mark.parametrize("modulo", sorted(CAPAS))
def test_los_imports_diferidos_estan_justificados(modulo):
    arbol = next(a for n, a in _modulos() if n == modulo)
    vistos = {imp for imp, dif in _imports_internos(arbol) if dif}
    sobran = {i for i in vistos if (modulo, i) not in DIFERIDOS_OK}
    assert not sobran, (
        f"{modulo}.py difiere imports sin justificar: {sorted(sobran)}.\n"
        "Un import dentro de una funcion esconde que la dependencia existe. Si "
        "de verdad hace falta, anadelo a DIFERIDOS_OK con el motivo escrito; si "
        "no, subelo a la cabecera."
    )


def test_parametros_no_toca_las_tripas_de_raster():
    """`parametros.py` no importa privados de `raster`.

    `_detectar_equidistancia`, `_p_max` y `_pendiente_espuria` son REGLAS, no
    rasters: viven en `metrologia`. Si alguien las devuelve a `raster`, esto
    lo dice.
    """
    arbol = next(a for n, a in _modulos() if n == "parametros")
    privados = [
        f"{nodo.module}.{a.name}"
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.ImportFrom) and nodo.module == "raster"
        for a in nodo.names
        if a.name.startswith("_")
    ]
    assert not privados, (
        f"parametros.py vuelve a importar privados de raster: {privados}. "
        "Las reglas van en metrologia.py; si el nombre es de verdad del raster, "
        "es que la funcion que lo usa tambien lo es."
    )
