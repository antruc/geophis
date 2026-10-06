"""Hidrologia: acondicionado del MDE, flujo D8, red de drenaje y cuencas.

**pysheds esta confinado aqui.** Estas funciones son las unicas del paquete que
lo tocan, y con ellas viaja `_parchear_pysheds_numpy2`, un parche global sobre
numpy que no tiene por que vivir en el modulo de E/S raster.

CAPA 2, junto a `geometria` y sin importarse con el: solo necesita tres cosas
de `raster` (`rellenar_nodata`, `poligonizar`, `rasterizar`), las tres publicas
y una capa por debajo. No hay ninguna arista de vuelta: nada de lo que quedo en
`raster.py` necesita nada de aqui.

El orden de la cadena, que tiene trampas y por eso se escribe:

    rellenar_nodata -> quemar_cauces -> acondicionar_mde -> acumulacion_flujo
    -> red_drenaje / orden_cauces / delimitar_cuenca / microcuencas

`rellenar_sumideros` es opcional y va entre `acondicionar_mde` y
`acumulacion_flujo`, cuando `acumulacion_flujo` avisa de celdas interiores sin
salida. No esta en la cadena de arriba a proposito: repara el MDE, y eso lo
decide quien llama.

El relleno va ANTES de quemar (una celda de cauce sobre NoData se pierde en
silencio) y `acondicionar_mde` rellena por su cuenta si le llega un MDE con
huecos, que es lo correcto cuando NO se quema.
"""

import math
import os
from os import PathLike
from typing import Any, NamedTuple, TYPE_CHECKING

import geopandas as gpd
import numpy as np
import pandas as pd
import numpy.typing as npt
from rasterio import Affine
from rasterio.transform import array_bounds
from shapely.geometry import shape
from shapely.ops import unary_union
from scipy.ndimage import (
    label as _label,  # etiqueta regiones conectadas
    binary_fill_holes,  # rellena huecos en mascara binaria
)

from .proyeccion import _a_crs
from .raster import poligonizar, rasterizar, rellenar_nodata
from ._salida import _mostrar

if TYPE_CHECKING:
    from pysheds.grid import Grid
    from pysheds.sview import Raster

FloatArray = npt.NDArray[np.floating[Any]]


def _parchear_pysheds_numpy2() -> None:
    """Repone `numpy.in1d`, que numpy 2.0 quitó y pysheds 0.5 todavía llama.

    pysheds la usa en 12 sitios de `sgrid.py`, todos con la misma forma
    `~np.in1d(fdir.ravel(), dirmap)`, para marcar las celdas con un código D8
    inválido. Con numpy >= 2 eso es un `AttributeError` y se lleva por delante
    `accumulation`, `catchment`, `extract_river_network` y las demás: la rama
    hidrológica entera detrás de `flowdir`. pysheds 0.5 es la última versión
    publicada, así que no hay upgrade que lo cierre.

    El alias es exacto para este uso: `in1d` es `isin` aplanando la entrada, y
    aquí la entrada ya viene de un `.ravel()`, así que la forma coincide.

    # ponytail: es un parche global sobre numpy, y por eso va condicionado y
    # llamado desde el único sitio por el que se entra a la rama (no se puede
    # tener un `grid` sin pasar por `acondicionar_mde`). Techo: el día que
    # pysheds publique una versión que no llame a `in1d`, esta función deja de
    # hacer nada sola y se puede borrar junto con su llamada.
    """
    if not hasattr(np, "in1d"):
        np.in1d = np.isin  # type: ignore[attr-defined]


def _sin_salida(fdir: "Raster") -> tuple[npt.NDArray[np.bool_], npt.NDArray[np.bool_]]:
    """Separa las celdas sin salida de flujo en (interiores, de borde).

    `flowdir` marca con codigo negativo la celda sin salida (-1 llano, -2 pit).
    En el borde eso es correcto y esperado, ahi el agua abandona el raster; en
    el interior es una fuga. La distincion la usan `acumulacion_flujo` para
    avisar y `rellenar_sumideros` para reparar, y por eso vive en un solo sitio.
    """
    d = np.asarray(fdir)
    fuera = d <= 0
    borde = np.zeros(d.shape, dtype=bool)
    borde[0, :] = borde[-1, :] = borde[:, 0] = borde[:, -1] = True
    return fuera & ~borde, fuera & borde


def acondicionar_mde(mde_path: str | PathLike[str]) -> tuple["Grid", "Raster"]:
    """Acondiciona un MDE para análisis hidrológico (pysheds).

    Rellena el NoData, rellena pits (píxeles aislados bajos), rellena
    depresiones endorreicas y resuelve zonas planas (añade micro-pendiente).
    Devuelve (grid, mde_acondicionado), objetos pysheds listos para el análisis.

    El relleno del NoData va DENTRO, y este es el único sitio de la librería
    que lo necesita: pysheds detecta el nodata con `dem == dem.nodata`, y con
    `nodata=NaN` (lo que escribe `guardar_raster`) esa comparación
    es siempre falsa, así que los NaN entrarían como elevación real y se
    propagarían por fill_pits / fill_depressions / resolve_flats.

    Va aquí y no en quien llama para no arrastrar el relleno a TODA la cadena:
    la rama de pendiente heredaría un MDE relleno que nunca pidió. `rellenar_nodata` copia
    el píxel válido más cercano, así que la zona rellenada sale plana por
    construcción (0 % de pendiente) con escalones falsos en las costuras entre
    vecinos copiados, y nada la marcaría. El relleno no sale de aquí:
    `calcular_pendiente` recibe el NaN y lo trata con la regla 7 de 8.

    ÚNICA excepción: si vas a `quemar_cauces`, rellena tú antes de quemar. Un
    cauce sobre una celda sin dato da `NaN - profundidad`, que sigue siendo
    NaN, y el relleno de aquí copiaría el vecino SIN quemar, perdiendo el cauce
    en silencio. En ese caso esta función reporta 0.00 ha, que es la señal de
    que llegó ya relleno.

    `geo.MOSTRAR` reporta cuántas hectáreas se rellenaron. Vale la pena mirarlo:
    ese número es superficie SIN dato de origen (fuera de la envolvente convexa
    de las curvas), y la hidrología que salga de ahí es geometría inventada. Si
    no es 0, la respuesta es dar más curvas (ampliar `cuadro_recorte` y volver
    a recortar del shapefile completo), no aceptar el relleno.
    """
    _parchear_pysheds_numpy2()
    from pysheds.grid import Grid
    from pysheds.sview import Raster

    # pysheds hace `isinstance(data, str)` y revienta con un Path, que la firma
    # de esta funcion sí acepta (y es lo que devuelve `guardar_raster`)
    mde_path = os.fspath(mde_path)
    grid = Grid.from_raster(mde_path)
    dem = grid.read_raster(mde_path)

    arr = np.asarray(dem, dtype=float)
    sin_dato = int(np.isnan(arr).sum())
    if sin_dato:
        # ponytail: se reconstruye el Raster con el mismo viewfinder en vez de
        # escribir un .tif intermedio. El viewfinder lleva affine, crs y shape,
        # que es todo lo que pysheds necesita del original.
        dem = Raster(rellenar_nodata(arr).astype(np.float32), dem.viewfinder)
    if _mostrar():
        res = abs(dem.viewfinder.affine.a)
        ha = sin_dato * res**2 / 10_000
        print(f"acondicionar_mde: {ha:.2f} ha sin dato parcheadas")
        if ha:
            # el diagnostico completo lo da interpolar_mde, que conoce las curvas
            # y el cuadro; aqui solo se avisa de que el hueco llego hasta el agua
            print(
                "   Superficie sin dato de origen, rellenada copiando el vecino "
                "mas cercano.\n   La red de drenaje que salga de ahi es geometria "
                "inventada: revisa el aviso\n   de `interpolar_mde` y da mas curvas "
                "antes de citar estos cauces."
            )

    # OJO al leer la salida: el mensaje de arriba cuenta NaN SIN DATO DE ORIGEN,
    # no depresiones. Estas tres lineas si rellenan depresiones y no informan de
    # nada, asi que un `0.00 ha sin dato parcheadas` NO dice que no hubiera
    # depresiones ni que se quitaran todas: los sumideros que sobreviven los
    # cuenta `acumulacion_flujo`, que es la unica que tiene fdir y acumulacion a
    # la vez. Las dos frases son compatibles.
    dem = grid.fill_pits(dem)
    dem = grid.fill_depressions(dem)
    dem = grid.resolve_flats(dem)
    return grid, dem


class Flujo(NamedTuple):
    """Lo que devuelve `acumulacion_flujo` y consume el resto de la rama.

    `red_drenaje`, `orden_cauces`, `delimitar_cuenca` y `microcuencas` necesitan
    las tres piezas juntas; viajar como una sola evita pasarlas en orden.
    """

    grid: "Grid"
    fdir: "Raster"  # dirección de flujo D8: cada píxel apunta a su vecino más bajo
    acumulacion: npt.NDArray[Any]  # celdas que drenan hacia cada celda


def acumulacion_flujo(grid: "Grid", dem: "Raster") -> Flujo:
    """Dirección y acumulación de flujo D8. Devuelve `Flujo(grid, fdir, acumulacion)`.

    Recibe `(grid, dem)` de `acondicionar_mde`. `acumulacion` es un array numpy:
    cuántos píxeles drenan hacia cada celda. Se puede desempaquetar,
    `grid, fdir, acum = acumulacion_flujo(grid, dem)`, o pasar entero a
    `red_drenaje`, `orden_cauces`, `delimitar_cuenca` y `microcuencas`.

    **AVISA cuando el flujo MUERE DENTRO del ráster**, y ésta es la razón de que
    la guarda viva aquí y no en `acondicionar_mde`: es el único sitio de la
    cadena que tiene la dirección de flujo y la acumulación a la vez, o sea el
    único que puede decir no sólo *cuántas* celdas se tragan el agua sino
    *cuánta*, que es la cifra con la que se decide. Un "hay 53 sumideros" no
    dice si hay que parar; un "el mayor recoge el 21 % del ráster" sí.

    `flowdir` marca con código negativo la celda sin salida (−1 llano, −2 pit).
    En el BORDE eso es correcto y esperado: ahí el agua abandona el ráster. En
    el INTERIOR no, porque `fill_depressions` existe justamente para que desde
    cualquier celda haya un camino no ascendente hasta el borde. Una celda
    interior sin salida es una fuga: todo lo que drena hacia ella desaparece de
    la red, y `red_drenaje`, `orden_cauces` y `delimitar_cuenca` corren encima
    sin enterarse.

    No es hipotético: en un MDE entregable real, un pit interior recogía la
    quinta parte del ráster. Y no es float32, ni el orden del acondicionado,
    ni `eps` (subirlo reparte el daño, no lo quita): son celdas sueltas con
    derrame de milímetros, residuo numérico del priority-flood, no cuencas
    endorreicas. Medido en un predio real: 55 sumideros, el mayor recoge el 21 % del ráster;
    float64 da los mismos 55, `eps` 1e-1 da 5059, y los 55 son de UNA celda
    con derrame de 0.000 a 0.005 m.

    Por eso la librería **avisa y no repara**: subir esas celdas sería inventar
    cota, y la profundidad del defecto (décimas de milímetro) es del orden del
    ruido de la propia interpolación. Quien decide qué hacer con una fuga así
    es quien conoce el terreno, no esta función.

    La condición del aviso fuerte NO lleva umbral inventado, que es lo que la
    haría discutible: salta cuando **el sumidero mayor del ráster es interior en
    vez de estar en el borde**. Eso es estructural y se lee solo: si el agua
    saliera del ráster, el sumidero mayor estaría en el borde por construcción.

    El aviso sale SIEMPRE: es una señal de defecto, no una línea de reporte
    (misma regla que el "sin dato" de `interpolar_mde`).
    """
    return _acumular(grid, grid.flowdir(dem))


def _acumular(grid: "Grid", fdir: "Raster") -> Flujo:
    """`acumulacion_flujo` con la dirección ya calculada (los tests la fabrican rota)."""
    acumulacion = np.array(grid.accumulation(fdir))
    dentro, en_borde = _sin_salida(fdir)
    if not dentro.any():
        return Flujo(grid, fdir, acumulacion)

    total = dentro.size
    mayor_dentro = float(acumulacion[dentro].max())
    pct = 100.0 * mayor_dentro / total
    print(
        f"Aviso: {int(dentro.sum())} celda(s) INTERIOR(es) sin salida de flujo.\n"
        f"   La mayor recoge {mayor_dentro:,.0f} de {total:,} celdas ({pct:.1f} % "
        "del raster):\n   todo eso desaparece de la red de drenaje."
    )
    mayor_borde = float(acumulacion[en_borde].max()) if en_borde.any() else 0.0
    if mayor_dentro > mayor_borde:
        # Sin umbral tecleado: si el agua saliera del raster, el sumidero mayor
        # estaria en el borde. Que sea interior dice que la red NO llega al
        # borde, y entonces las cuencas que salgan de aqui son de media finca.
        print(
            f"   El sumidero MAYOR del raster es interior ({mayor_dentro:,.0f} "
            f"celdas) y no del borde ({mayor_borde:,.0f}).\n   La red no llega "
            "al borde: `delimitar_cuenca` entregara la cuenca de ese sumidero,\n"
            "   no la del predio. Amplia el cuadro o revisa el MDE antes de "
            "citar estas cuencas.\n   Si los sumideros son residuo del relleno "
            "(1 celda, derrame de milimetros), `rellenar_sumideros`\n   los cierra "
            "y dice cuanto subio: si subio mas de milimetros, no eran residuo."
        )
    return Flujo(grid, fdir, acumulacion)


def rellenar_sumideros(
    grid: "Grid", dem: "Raster", cauces: gpd.GeoDataFrame | None = None
) -> tuple["Raster", dict[str, Any]]:
    """Rellenar sumideros, la parte que TERMINA `acondicionar_mde`: sube cada sumidero interior por
    encima de su cota de derrame, hasta que no quede ninguno.

    Devuelve (mde reparado, informe). El informe lleva `rondas`, `celdas`,
    `subida_max_m`, `pits_iniciales`, `pits_finales` y `convergio`, y **hay que
    hacerlo viajar con cualquier cifra que salga de este MDE**: un MDE reparado
    no es el MDE interpolado, y la procedencia cambia.

    **Por que es una funcion aparte y no una bandera de `acondicionar_mde`.**
    Esa funcion avisa y no repara, y es una decision explicita: la fuga del 21 %
    que `acumulacion_flujo` denuncia costo encontrarla, y una reparacion
    automatica la callaria. Aqui la reparacion la pide quien llama, despues de
    haber leido el aviso y haber comprobado de que son los sumideros.

    **Comprobar ANTES que los sumideros son residuo numerico, no endorreicas.**
    El residuo típico es de UNA celda con derrame por debajo de 2 mm, del
    priority-flood de pysheds. Una endorreica de verdad tiene fondo ancho y contorno bastante mas alto, y
    esta funcion **la taparia sin avisar**. Las endorreicas reales de un predio
    no se descartan por forma, se descartan por campo.

    Lo que hace, y es lo minimo que funciona: a cada celda sin salida interior
    le suma `derrame + eps`, o sea lo que le faltaba para desbordar por su
    vecino mas bajo. Eso es RELLENAR, no breaching: sube terreno en vez de bajar
    un canal de salida. A milimetros la diferencia esta por debajo del ruido de
    la interpolacion, pero no es la misma operacion y por eso se dice.

    Rellenar un sumidero destapa el siguiente aguas abajo, asi que itera. En
    la practica converge en pocas rondas subiendo milimetros.

    **Cada ronda vuelve a pasar `fill_depressions` y `resolve_flats`, y eso NO
    es decorativo.** Subir una celda hasta su cota de derrame la deja IGUAL que
    su vecino, o sea fabrica un llano nuevo, y un llano sin micro-pendiente es
    otro sumidero. Sin cerrar la ronda con el mismo acondicionado que la abrio,
    la iteracion entra en ciclo limite (derrame de picometros) y lo que mide la ronda siguiente es el parche, no el terreno.

    TRAMPA, y muerde a cualquiera que toque un MDE acondicionado: **el ráster
    que sale de aqui, como el de `acondicionar_mde`, es float64 y no float32
    como el de disco.** `resolve_flats` lo promueve porque su micro-pendiente es
    del orden de 1e-5 m, y a cota 1620 m el paso de float32 es 1.2e-4 m.
    Castearlo de vuelta a float32 BORRA la micro-pendiente: los sumideros se
    multiplican por cientos o miles.
    Manejarlo en float64 de principio a fin, y guardarlo con float32 solo si se
    acepta perder el acondicionado.

    Si al agotar las 8 rondas siguen quedando sumideros, `convergio` sale False y lo
    dice en voz alta: eso ya no son milimetros, es estructura, y el camino es
    otro (breaching de verdad, o imponer las aristas del cauce).

    **Si se quemo, pasa los mismos `cauces`.** Rellenar sube el lecho, y sobre
    una zanja quemada eso es DES-QUEMAR: en un predio real subio 1,612 celdas de cauce
    mas de 4.9 m. Con `cauces` avisa siempre de las celdas de cauce que suben
    mas de 2 mm (el techo del residuo, ver arriba), con la subida maxima.
    """
    from pysheds.sview import Raster

    z = np.asarray(dem, dtype=np.float64).copy()
    z_ini = z.copy()
    vista = dem.viewfinder
    # ponytail: el eps no se teclea, sale del espaciado de float64 en la cota mas
    # alta del raster, que es donde el paso es mayor. Subir menos que eso no
    # cambia el numero almacenado y la ronda no avanzaria.
    eps = float(np.spacing(np.abs(z).max())) * 4

    n_ini = 0
    subida_max = 0.0
    tocadas = 0
    ronda = rondas = 8
    for ronda in range(rondas + 1):
        dentro, _ = _sin_salida(grid.flowdir(Raster(z, vista)))
        n = int(dentro.sum())
        if ronda == 0:
            n_ini = n
        if n == 0 or ronda == rondas:
            break

        for f, c in zip(*np.nonzero(dentro)):
            s = _derrame(z, int(f), int(c)) + eps
            z[f, c] += s
            subida_max = max(subida_max, s)
        tocadas += n

        # cierra la ronda con el mismo acondicionado que la abrio, ver docstring
        z = np.asarray(
            grid.resolve_flats(grid.fill_depressions(Raster(z, vista))),
            dtype=np.float64,
        )

    if cauces is not None:
        c = _a_crs(cauces, grid.crs, "cauces")
        en_cauce = rasterizar(c, z.shape, vista.affine) == 1  # la de quemar_cauces
        subida = np.where(en_cauce, z - z_ini, 0.0)
        n_cauce = int((subida > 0.002).sum())
        if n_cauce:
            print(
                f"Aviso: {n_cauce} celda(s) de cauce subidas mas de 2 mm (hasta "
                f"{subida.max():.3f} m).\n   Eso deshace el quemado: no es "
                "residuo, es la zanja rellenada. Mira esos tramos\n   antes de "
                "citar la red."
            )

    informe = {
        "rondas": ronda,
        "celdas": tocadas,
        "subida_max_m": subida_max,
        "pits_iniciales": n_ini,
        "pits_finales": n,
        "convergio": n == 0,
    }
    if _mostrar():
        print(
            f"rellenar_sumideros: {n_ini} sumidero(s) interior(es) -> {n} en "
            f"{ronda} ronda(s).\n   {tocadas} celda(s) de {z.size:,} subida(s), "
            f"como mucho {1000 * subida_max:.3f} mm."
        )
        if n:
            print(
                f"   NO CONVERGE: quedan {n}. Eso ya no son milimetros, es "
                "estructura del MDE.\n   Mas rondas no lo arreglan: mira los "
                "que quedan antes de citar esta red."
            )
        else:
            print(
                "   El MDE esta REPARADO, y por tanto ya no es el interpolado: "
                "haz viajar este\n   informe con cualquier cifra que salga de el."
            )
    return Raster(z, vista), informe


def _derrame(z: npt.NDArray[Any], f0: int, c0: int) -> float:
    """Cuanto hay que subir (f0,c0) para que desborde por su vecino mas bajo.

    Devuelve 0.0 si no tiene ningun vecino por encima, o sea si ya es el punto
    mas alto de su vecindad y lo que le falta es micro-pendiente, no cota.
    """
    alto, ancho = z.shape
    z0 = float(z[f0, c0])
    mejor = math.inf
    for df in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if df == dc == 0:
                continue
            nf, nc = f0 + df, c0 + dc
            if 0 <= nf < alto and 0 <= nc < ancho and z[nf, nc] > z0:
                mejor = min(mejor, float(z[nf, nc]))
    return 0.0 if mejor == math.inf else mejor - z0


def quemar_cauces(
    mde: FloatArray, cauces: gpd.GeoDataFrame, transform: Affine, profundidad: float
) -> tuple[FloatArray, int]:
    """Baja la elevación del MDE en los cauces (stream burning).

    Rasteriza la red hídrica y resta `profundidad` (unidades del MDE) a esos
    píxeles para que el análisis de flujo siga los cauces conocidos. Devuelve
    (array quemado, nº de píxeles quemados); el 2º valor sirve para avisar si
    la hidrología no intersecta el raster.

    **Cuando no quema nada, lo DICE, con los dos rectángulos.** Sigue sin
    fallar, eso lo decide quien llama porque quemar es opcional, pero un `0`
    pelado no es una guarda, y aquí el 0 tiene dos causas que se arreglan distinto (los cauces
    en otro CRS, o los cauces de otro predio). La función recibe `transform` y
    no `crs`, así que no puede nombrar el sistema; lo que sí puede es enseñar
    los dos extents, y ahí la causa se lee sola (con otro CRS las coordenadas
    difieren en un orden de magnitud).

    OJO al orden con `recortar`: la regla de la librería es que se mueve el
    SEGUNDO argumento, así que `recortar(cauces, cuadro)` deja los cauces en su
    CRS de origen. Para quemar hay que reproyectarlos al del ráster, y eso es
    trabajo de quien llama porque aquí no llega el CRS.
    """
    # ponytail: todo_tocado=False a propósito, no por herencia. La máscara ES el
    # resultado (no un paso intermedio que se recorte en vector después), así que
    # la fuga de superficie de todo_tocado=False no aplica. Lo que sí importa
    # es que el cauce no se corte en diagonal, porque eso rompe la acumulación
    # de flujo en silencio: la rasterización de líneas de GDAL sale 8-conectada,
    # verificado en test_quemar_cauces_diagonal_queda_8_conectado. Si ese test
    # falla algún día, voltear a True.
    ras = rasterizar(cauces, mde.shape, transform)
    salida = mde.copy()
    salida[ras == 1] -= profundidad
    sin_salida = _pendiente_en_cauce(salida, (ras == 1) & ~np.isnan(salida))
    if sin_salida:
        print(
            f"Aviso: {sin_salida} celda(s) de cauce quemado en tramos sin salida "
            "(no llegan al borde ni\n   desbordan a terreno mas bajo): "
            "`acondicionar_mde` los rellenara y el\n   cauce quedara des-quemado "
            "ahi. Revisa que la red este conectada."
        )
    # el lecho por ENCIMA del terreno sin quemar: la linea sube donde el terreno
    # no (mal digitalizada o fuera del fondo del valle). En un predio real, 2,128 celdas
    # subidas mas de 1 m, hasta 15.5 m; `fill_depressions` hacia lo mismo callado
    encima = salida - mde
    alto = (ras == 1) & (encima > 0)
    if alto.any():
        f, c = np.unravel_index(np.where(alto, encima, -np.inf).argmax(), mde.shape)
        x, y = transform * (c + 0.5, f + 0.5)
        print(
            f"Aviso: {int(alto.sum())} celda(s) de cauce quedan por ENCIMA del "
            f"terreno sin quemar (hasta\n   {encima[f, c]:.2f} m, en x {x:,.0f} "
            f"y {y:,.0f}): ahi la linea sube cuesta arriba y el lecho se\n   "
            "relleno hasta su derrame. Revisa esos tramos de la hidrologia."
        )

    # Guarda sobre la SALIDA, y avisa siempre, sin perilla que la apague: por lo
    # mismo que el "sin dato" de `interpolar_mde`. Quemar sobre una celda sin
    # dato da NaN - profundidad, que sigue siendo NaN, y el relleno posterior
    # copia el vecino valido mas cercano, que esta SIN quemar. El lecho SUBE ahi
    # y queda una presa dentro del cauce. El MDE resultante es valido, la red de
    # drenaje que salga esta cortada, y nada lo delata.
    celdas_cauce = int(ras.sum())
    perdidas = int(np.isnan(salida[ras == 1]).sum())
    if perdidas:
        # La FRACCION, no solo la cuenta: un numero de celdas no dice si hay que
        # parar. Un aviso sin la cifra que se decide no es guarda.
        pct = 100.0 * perdidas / celdas_cauce
        print(
            f"Aviso: {perdidas} de {celdas_cauce} celda(s) de cauce ({pct:.0f} %) "
            "caen sobre NoData\n   y siguen sin dato tras quemar. Al rellenar "
            "despues, el lecho subira ahi (se\n   copia un vecino sin quemar) y "
            "el cauce quedara cortado en silencio.\n   Llama a `rellenar_nodata` "
            "ANTES de quemar."
        )

    quemados = celdas_cauce
    if quemados == 0 and len(cauces):
        # Sin esto el 0 es mudo y quien llama solo puede adivinar la causa.
        alto, ancho = mde.shape
        rx0, ry0, rx1, ry1 = array_bounds(alto, ancho, transform)
        cx0, cy0, cx1, cy1 = cauces.total_bounds
        print(
            f"Aviso: 0 pixeles quemados con {len(cauces)} entidad(es) de cauce.\n"
            f"   cauces  x[{cx0:,.0f} {cx1:,.0f}]  y[{cy0:,.0f} {cy1:,.0f}]\n"
            f"   raster  x[{rx0:,.0f} {rx1:,.0f}]  y[{ry0:,.0f} {ry1:,.0f}]\n"
            "   Si los rectangulos ni se rozan es el CRS (`recortar` mueve el\n"
            "   SEGUNDO argumento, asi que recortar los cauces NO los reproyecta:\n"
            "   pasalos por `reproyectar` al CRS del raster). Si se solapan, la\n"
            "   hidrologia es de otro predio. El MDE vuelve intacto."
        )
    return salida, quemados


def _pendiente_en_cauce(z: FloatArray, cauce: npt.NDArray[np.bool_]) -> int:
    """Fuerza que todo el cauce baje hacia su salida, en el sitio. Devuelve las
    celdas de cauce sin salida.

    Priority-flood + epsilon (Barnes 2014) restringido al cauce: se siembra en
    las celdas de cauce del borde, en las que desbordan a un vecino fuera del
    cauce mas bajo y en las que tocan NoData (ahi no se sabe, y forzar el tramo
    a drenar al otro lado inventaria cota; el NoData ya lo avisa quien llama), y cada celda alcanzada queda al menos `eps` por encima de
    la que la alcanzo. Un tramo que ya baja no se toca; uno llano recibe
    micro-pendiente y uno que sube se rellena hasta su derrame. Hace falta
    porque `resolve_flats` no drena una zanja llana larga: en un predio real, 16 km de
    cauce sobre una meseta del TIN a 860 quedaban como 1,613 llanos.

    # ponytail: heap de Python celda a celda, solo sobre el cauce (cientos de
    # miles de celdas). Techo: si el cauce fuera millones, a numba o richdem.
    """
    import heapq

    alto, ancho = z.shape
    # eps del paso de float32 en la cota mas alta: guardar_raster escribe
    # float32 y pysheds lee de disco, asi que menos que eso se pierde
    eps = float(np.spacing(np.float32(np.nanmax(np.abs(z))))) * 4
    vecinos = [(df, dc) for df in (-1, 0, 1) for dc in (-1, 0, 1) if df or dc]

    # el vecino fuera del cauce mas bajo de cada celda; NoData cuenta como salida
    fuera = np.where(np.isnan(z), -np.inf, np.where(cauce, np.inf, z))
    zp = np.pad(fuera, 1, constant_values=np.inf)
    fuera_min = np.full(z.shape, np.inf)
    for df, dc in vecinos:
        fuera_min = np.minimum(fuera_min, zp[1 + df : 1 + df + alto, 1 + dc : 1 + dc + ancho])
    borde = np.zeros(z.shape, dtype=bool)
    borde[0, :] = borde[-1, :] = borde[:, 0] = borde[:, -1] = True
    semilla = cauce & (borde | (fuera_min < z))

    visto = semilla.copy()
    cola = [(float(z[f, c]), int(f), int(c)) for f, c in zip(*np.nonzero(semilla))]
    heapq.heapify(cola)
    while cola:
        z0, f, c = heapq.heappop(cola)
        for df, dc in vecinos:
            nf, nc = f + df, c + dc
            if 0 <= nf < alto and 0 <= nc < ancho and cauce[nf, nc] and not visto[nf, nc]:
                visto[nf, nc] = True
                if z[nf, nc] < z0 + eps:
                    z[nf, nc] = z0 + eps
                heapq.heappush(cola, (float(z[nf, nc]), nf, nc))
    return int((cauce & ~visto).sum())


def etiquetar_laderas(
    acumulacion: npt.NDArray[Any], umbral: float
) -> tuple[npt.NDArray[np.int32], int]:
    """Etiqueta las laderas que el cauce deja separadas.

    Cauce = acumulación > `umbral`. Las zonas de no-cauce (laderas) se
    rellenan y se etiqueta cada región conectada con un ID único. Devuelve
    (array etiquetado, n_regiones).

    Cuenta LADERAS SEPARADAS POR EL CAUCE, no cuencas de drenaje, y las dos
    cosas no coinciden: para que una vertiente salga aparte, el cauce tiene
    que cruzar el raster de lado a lado. En un valle en V las dos vertientes
    se tocan por encima de la cabecera, donde la acumulación todavía no llega
    al umbral, así que sale UNA región por mucho que el ojo vea dos. Bajar el
    umbral no siempre lo arregla: mueve la cabecera, no la elimina. Si lo que
    hace falta es la cuenca vertiente de un punto, eso es `catchment` de
    pysheds sobre la dirección de flujo, no esto.

    El relleno de huecos absorbe la celda suelta que pasa el umbral sin llegar
    a ningún borde: es ruido de la acumulación, no un parteaguas, y sin
    rellenar partiría la ladera en dos.
    """
    red = np.asarray(acumulacion) > umbral
    no_drenaje = binary_fill_holes(~red)
    return _label(no_drenaje)


def _mascara_cauce(acumulacion: npt.NDArray[Any], umbral: float, fdir: "Raster") -> Any:
    """Máscara booleana de cauce, como el `Raster` que pide pysheds.

    Las tres funciones de abajo empiezan igual, y pysheds no acepta un array
    pelado: quiere un `Raster` con el mismo viewfinder que la dirección de
    flujo, o el resultado sale desalineado sin avisar.
    """
    from pysheds.sview import Raster

    return Raster(np.asarray(acumulacion) > umbral, fdir.viewfinder)


def red_drenaje(
    flujo: Flujo,
    umbral: float,
) -> gpd.GeoDataFrame:
    """Red de drenaje vectorial: los cauces como líneas. Devuelve GeoDataFrame.

    Es el entregable de la rama hidrológica.

    `umbral` está en CELDAS que drenan, no en superficie: cauce = acumulación >
    umbral. Es la decisión de campo de esta función y no tiene default, porque
    depende de la resolución y del terreno. A `h` metros por píxel, un umbral
    de `k` celdas equivale a `k * h^2 / 10000` ha de cuenca aportadora, que es
    la forma de razonarlo.

    Sube con la resolución: el mismo umbral sobre un MDE de 5 m da diez veces
    más cauces que sobre uno de 20 m, porque hay dieciséis veces más celdas
    aportando. Un umbral heredado de otro MDE no significa lo mismo.
    """
    grid, fdir, acumulacion = flujo
    red = grid.extract_river_network(fdir, _mascara_cauce(acumulacion, umbral, fdir))
    tramos = [shape(f["geometry"]) for f in red["features"]]
    gdf = gpd.GeoDataFrame(geometry=tramos, crs=grid.crs)
    # guarda sobre la SALIDA: la mascara puede traer celdas sueltas y aun asi
    # no formar un solo tramo, asi que mirar la entrada no cerraria el caso
    if gdf.empty:
        raise ValueError(
            f"red vacia con umbral {umbral:g} celdas (la acumulacion maxima es "
            f"{np.asarray(acumulacion).max():.0f}): baja el umbral"
        )
    if _mostrar():
        res = abs(fdir.viewfinder.affine.a)
        print(
            f"red_drenaje: {len(gdf)} tramos | {gdf.length.sum() / 1000:.2f} km | "
            f"umbral {umbral:g} celdas = {umbral * res**2 / 10_000:.2f} ha"
        )
    return gdf


def orden_cauces(
    flujo: Flujo,
    umbral: float,
) -> npt.NDArray[np.int32]:
    """Orden de Strahler de cada celda de cauce.

    Jerarquiza la red: orden 1 son las cabeceras, y el orden sube solo cuando
    se juntan DOS cauces del mismo orden (un afluente menor no incrementa al
    mayor). Sirve para cartografiar la red por jerarquía, para quedarse con el
    cauce principal (`orden >= k`) y para comparar dos cuencas.

    Devuelve un array int32 alineado con el MDE. Como ráster, no como vector.
    Para llevarlo a vector: `poligonizar`, o `red_drenaje` con el mismo
    `umbral` si lo que quieres son las líneas.

    Mismo `umbral` en CELDAS que `red_drenaje`.

    EL 0 SIGNIFICA DOS COSAS, y conviene saberlo antes de contar celdas por
    orden: fuera de la máscara, y dentro de la máscara pero sin orden asignado.
    Lo segundo pasa de verdad, y es informativo: `acumulación > umbral` es un
    criterio por celda, mientras que el orden se asigna recorriendo la red por
    la dirección de flujo. Una celda con mucha acumulación que no está sobre un
    tramo trazado (típicamente cerca del desagüe, donde media ladera supera el
    umbral) entra en la máscara y se queda sin orden.

    Por eso `geo.MOSTRAR` reporta las dos cifras. Si la máscara es mucho mayor que
    la red numerada, `umbral` no está separando un cauce: está seleccionando
    la parte baja de la cuenca. Sube el umbral.
    """
    grid, fdir, acumulacion = flujo
    mascara = np.asarray(acumulacion) > umbral
    orden = np.asarray(
        grid.stream_order(fdir, _mascara_cauce(acumulacion, umbral, fdir))
    ).astype(np.int32)
    if _mostrar():
        n_mascara, n_red = int(mascara.sum()), int((orden > 0).sum())
        print(
            f"orden_cauces: Strahler 1 a {orden.max()} | umbral {umbral:g} celdas | "
            f"{n_red} celdas numeradas de {n_mascara} en la mascara"
        )
        if n_red < n_mascara:
            print(
                f"   {n_mascara - n_red} celdas pasan el umbral y NO estan sobre un "
                "tramo trazado:\n   quedan en 0, igual que el fuera de cauce. Si son "
                "muchas, el umbral esta\n   marcando ladera baja en vez de cauce."
            )
    return orden


def delimitar_cuenca(
    flujo: Flujo,
    desague: gpd.GeoDataFrame,
    umbral: float,
) -> gpd.GeoDataFrame:
    """Cuenca vertiente de un desagüe.

    `desague` es una capa de UNA geometría (se reproyecta al CRS del ráster), y
    se puede dar de dos maneras:

    - **un PUNTO**, cuando la coordenada existe: GPS, aforo, obra de toma.
    - **una ZONA** (polígono), y entonces significa *"el desagüe de esto"*: se
      toma la celda de MAYOR ACUMULACIÓN dentro de ella, que es por donde el
      agua sale. Se reporta cuál se eligió.

    Devuelve la cuenca como un polígono en un GeoDataFrame.

    La variante de ZONA es el caso normal: la cuenca que drena un predio, donde
    no hay punto de campo que valga.

    TECHO conocido, y hay que escribirlo: **una zona con DOS salidas devuelve
    solo la mayor.** Un predio a caballo entre dos cuencas drena por dos sitios,
    y el argmax elige uno; la cuenca que sale es correcta pero es de media
    finca. La señal está en la salida: si la cuenca es mucho menor que la zona,
    la zona no era una sola cuenca. Cuando eso pase de verdad, lo que hace falta
    es `microcuencas`, que parte la zona entera, no tocar ésta. Medido en un predio real:
    737.82 ha de cuenca para un predio de 1765.15 ha.

    AJUSTA el punto al cauce más cercano ANTES de delinear, y REPORTA cuánto lo
    movió. Ese ajuste no es una comodidad, es la función: el punto de desagüe
    casi nunca cae exactamente sobre una celda de cauce (viene de un GPS, de un
    clic en pantalla, de una coordenada de gabinete), y a UNA celda del cauce la
    cuenca que sale es la de esa celda suelta, de unas pocas hectáreas. No falla
    ni avisa: devuelve un polígono pequeño y perfectamente formado. Es el error
    clásico de este cálculo, y ajustar el desagüe al cauce (snap) es la práctica estándar por lo mismo.

    La distancia movida es la cifra que hay que mirar. Si supera la diagonal de
    una celda, el punto no estaba sobre el cauce que crees, y hay dos causas
    posibles: la coordenada está mal, o el `umbral` es tan alto que el cauce que
    buscas no existe en la máscara. La función lo dice pero no decide: mover el
    desagüe cientos de metros puede ser correcto si el umbral es grueso a
    propósito.
    """
    grid, fdir, acumulacion = flujo
    if len(desague) != 1:
        raise ValueError(
            f"`desague` debe traer UNA geometria (punto o zona), trae {len(desague)}"
        )
    p = _a_crs(desague, grid.crs, "desague")
    g0 = p.geometry.iloc[0]

    tf_zona = fdir.viewfinder.affine
    acu = np.asarray(acumulacion)
    de_zona = None
    if g0.geom_type in ("Polygon", "MultiPolygon"):
        dentro = rasterizar(p, acu.shape, tf_zona).astype(bool)
        if not dentro.any():
            raise ValueError(
                "la zona de desague no toca ni una celda del raster: revisa el CRS "
                "o que sea del mismo predio"
            )
        # El agua sale por donde mas se ha acumulado. Es una MEDICION, no una
        # coordenada tecleada, y por eso se reporta cual salio.
        f, c = np.unravel_index(int(np.where(dentro, acu, -1.0).argmax()), acu.shape)
        gx, gy = tf_zona * (c + 0.5, f + 0.5)
        de_zona = (float(acu[f, c]), int(dentro.sum()))
    else:
        gx, gy = g0.x, g0.y

    cauce = _mascara_cauce(acumulacion, umbral, fdir)
    ajustado, distancia = grid.snap_to_mask(cauce, np.array([[gx, gy]]), return_dist=True)
    x, y = ajustado[0]
    movido = float(distancia[0])

    mascara = np.asarray(grid.catchment(x, y, fdir, xytype="coordinate"))
    tf = fdir.viewfinder.affine
    res = abs(tf.a)
    piezas = poligonizar(mascara.astype(np.int32), tf, grid.crs)
    # union en shapely y no `disolver`: `geometria` esta ENCIMA de este modulo
    # y llamarla desde aqui añadiria una arista que sube al grafo de capas
    gdf = gpd.GeoDataFrame(geometry=[unary_union(piezas.geometry.values)], crs=grid.crs)

    if _mostrar():
        diagonal = res * math.sqrt(2)
        cuenca_ha = mascara.sum() * res**2 / 10_000
        if de_zona is not None:
            acu_max, celdas_zona = de_zona
            zona_ha = celdas_zona * res**2 / 10_000
            print(
                f"delimitar_cuenca: desague MEDIDO en la zona ({zona_ha:.2f} ha), "
                f"celda de mayor acumulacion ({acu_max:,.0f} celdas)"
            )
            # La senal del techo de las dos salidas, sobre la SALIDA y con cifra.
            if cuenca_ha < 0.5 * zona_ha:
                print(
                    f"   OJO: la cuenca ({cuenca_ha:.2f} ha) es menos de la mitad "
                    f"de la zona ({zona_ha:.2f} ha).\n   La zona no drena por un "
                    "solo sitio: el resto sale por otro desague y no esta aqui."
                )
        print(
            f"delimitar_cuenca: {cuenca_ha:.2f} ha | "
            f"desague movido {movido:.1f} m al cauce"
        )
        if movido > diagonal:
            print(
                f"   El punto NO caia sobre cauce (mas de {diagonal:.1f} m, la "
                "diagonal de una celda).\n   O la coordenada esta mal, o el "
                "umbral deja fuera el cauce que buscas. Comprueba cual."
            )
    return gdf


# Codigo D8 de pysheds -> (df, dc). El dirmap por defecto es (N, NE, E, SE, S,
# SW, W, NW) = (64, 128, 1, 2, 4, 8, 16, 32), y aqui hace falta explicito porque
# se camina la direccion de flujo a mano: pysheds no expone "a que celda apunta".
_D8: dict[int, tuple[int, int]] = {
    64: (-1, 0),
    128: (-1, 1),
    1: (0, 1),
    2: (1, 1),
    4: (1, 0),
    8: (1, -1),
    16: (0, -1),
    32: (-1, -1),
}


def microcuencas(
    flujo: Flujo,
    zona: gpd.GeoDataFrame,
    umbral: float | None = None,
) -> gpd.GeoDataFrame:
    """Parte una ZONA en las microcuencas que la drenan. Un polígono por desagüe.

    Es la respuesta a *"las microcuencas de este predio"*, que `delimitar_cuenca`
    no puede dar: aquélla toma UN desagüe y devuelve UNA cuenca, y un predio que
    drena por varios sitios se queda a medias.

    **Y la forma obvia, "la cuenca de cada desagüe", está DESCARTADA MIDIENDO.**
    Deja la mayor parte de la zona sin cubrir y solapa, porque la cuenca de un
    desagüe contiene a otro desagüe aguas arriba. Sumar sus superficies cuenta
    doble, y no es un fallo del cálculo sino de la definición: la cuenca de un
    punto no es una pieza de la zona.

    Lo que sí es una partición, y por eso es lo que se implementa: **etiquetar
    cada celda de la zona por el desagüe por el que SALE**. D8 es una función, o
    sea que cada celda tiene exactamente un camino aguas abajo y por tanto un
    solo desagüe. Sin solape y sin huérfanas POR CONSTRUCCIÓN, no por ajuste, y
    el cierre contra la zona se comprueba EXACTO (lo que tiene que cerrar
    exacto se comprueba exacto), no con una tolerancia porcentual.

    Un desagüe es un hecho geométrico y no una elección: la celda de la zona cuyo
    vecino de aguas abajo cae fuera. No se inventa ninguno y no se descarta
    ninguno.

    Devuelve un GeoDataFrame con una fila por microcuenca y estas columnas
    (nombres de ≤10 caracteres, para que un .shp no las renombre):

        ID_MICRO    entero, la etiqueta de la microcuenca
        DESAGUE_X   coordenada del centro de la celda de desagüe
        DESAGUE_Y
        ACUM        acumulación en el desagüe, en celdas
        EN_CAUCE    sólo si se pasa `umbral`: el desagüe está sobre cauce
        ES_SUMIDER  el flujo MUERE ahí. Ojo a la distinción, que no es la
                    obvia: una celda sin aguas abajo en el BORDE del ráster es
                    una salida legítima (el agua se va del ráster), y sólo
                    cuenta como sumidero si está en el interior. Es el mismo
                    criterio del aviso de `acumulacion_flujo`, y por eso las dos
                    cifras se pueden comparar.

    **NO trae superficie**, y es a propósito: la regla de la librería es que la
    superficie va detrás del último paso que mueva la geometría, así que la
    calcula quien llame, con `calcular_superficie`, cuando haya terminado de
    recortar.

    **NO filtra**, tampoco por gusto. La partición completa trae cientos o miles
    de microcuencas, pero unas pocas decenas cubren casi toda la zona y el
    resto son esquirlas de la orla, de una y dos celdas, por donde el agua sale
    de ladera. Filtrar por dentro obligaría a teclear un tamaño mínimo, que es
    exactamente el número inventado contra el que avisa la REGLA DE LOS NÚMEROS;
    filtrando fuera, quien llama usa el criterio que ya declaró. Con `umbral` (el
    mismo de `red_drenaje` y `orden_cauces`, o sea la superficie aportadora
    declarada) la columna `EN_CAUCE` da ese corte sin inventar nada.

    `ES_SUMIDER` conecta con el aviso de `acumulacion_flujo`: si el flujo muere
    en una celda interior, esa celda sale aquí como desagüe aunque no sea una
    salida de la zona, y la microcuenca que cuelga de ella es agua que no va a
    ninguna parte. Es dato, no error: la partición sigue siendo exacta.
    """
    grid, fdir, acumulacion = flujo
    p = _a_crs(zona, grid.crs, "zona")
    tf = fdir.viewfinder.affine
    acu = np.asarray(acumulacion)
    mascara = rasterizar(p, acu.shape, tf).astype(bool)
    if not mascara.any():
        raise ValueError(
            "la zona no toca ni una celda del raster: revisa el CRS o que sea "
            "del mismo predio"
        )

    d = np.asarray(fdir)
    alto, ancho = d.shape
    # indice plano de aguas abajo; -1 = el flujo sale de la malla o no tiene
    # direccion (celda sin salida, el codigo negativo de pysheds)
    filas, cols = np.meshgrid(np.arange(alto), np.arange(ancho), indexing="ij")
    abajo = np.full(d.size, -1, dtype=np.int64)
    for codigo, (df, dc) in _D8.items():
        nf, nc = filas + df, cols + dc
        ok = (d == codigo) & (nf >= 0) & (nf < alto) & (nc >= 0) & (nc < ancho)
        abajo[filas[ok] * ancho + cols[ok]] = nf[ok] * ancho + nc[ok]

    plana = mascara.ravel()
    origen = np.nonzero(plana)[0]
    pos = origen.copy()
    andando = np.arange(origen.size)
    # Se camina UN paso por vuelta y se para en la ULTIMA celda de la zona. No se
    # duplican punteros (`pos[pos]`) porque el destino no es una raiz comun sino
    # "la ultima de dentro", que la duplicacion se saltaria.
    for _ in range(d.size):
        if not andando.size:
            break
        siguiente = abajo[pos[andando]]
        sigue = (siguiente >= 0) & plana[np.maximum(siguiente, 0)]
        pos[andando[sigue]] = siguiente[sigue]
        andando = andando[sigue]
    else:  # pragma: no cover - solo con un ciclo en la direccion de flujo
        raise ValueError(
            "la direccion de flujo tiene un ciclo: no se puede partir la zona"
        )

    desagues, etiquetas = np.unique(pos, return_inverse=True)
    marcado = np.zeros(d.size, dtype=np.int32)
    marcado[origen] = etiquetas + 1  # 0 lo ignora `poligonizar`
    marcado = marcado.reshape(d.shape)

    # El cierre EXACTO, sobre la salida y antes de vectorizar. Solo puede fallar
    # por un defecto de aqui, y por eso revienta en vez de avisar.
    if int((marcado > 0).sum()) != int(mascara.sum()):
        raise ValueError(
            f"la particion no cierra: {int((marcado > 0).sum())} celdas "
            f"etiquetadas contra {int(mascara.sum())} de la zona"
        )

    piezas = poligonizar(marcado, tf, grid.crs, mascara=mascara, campo="ID_MICRO")
    # `poligonizar` vectoriza con conectividad 4, asi que una microcuenca
    # pellizcada en diagonal sale en varias piezas. Se unen por etiqueta para que
    # haya UNA fila por desague, que es lo que promete la firma. La union no
    # mueve superficie: las piezas son disjuntas.
    unidas = piezas.groupby("ID_MICRO")["geometry"].apply(
        lambda g: g.iloc[0] if len(g) == 1 else unary_union(g.values)
    )
    df_, dc_ = np.divmod(desagues, ancho)
    x, y = tf * (dc_ + 0.5, df_ + 0.5)
    # Sumidero es que el flujo MUERA, y eso no es lo mismo que no tener celda de
    # aguas abajo: en el BORDE del raster el agua se va del raster, que es una
    # salida legitima. Sin esta distincion, una zona que llega al borde marcaria
    # sus desagues buenos como sumideros. Mismo criterio que el aviso de
    # `acumulacion_flujo`, y por eso las dos cifras se pueden comparar.
    en_borde = (df_ == 0) | (df_ == alto - 1) | (dc_ == 0) | (dc_ == ancho - 1)
    es_sumidero = (d[df_, dc_] <= 0) & ~en_borde
    acu_desague = acu[df_, dc_].astype(float)
    atributos = pd.DataFrame(
        {
            "ID_MICRO": np.arange(1, desagues.size + 1, dtype=np.int32),
            "DESAGUE_X": x,
            "DESAGUE_Y": y,
            "ACUM": acu_desague,
            "ES_SUMIDER": es_sumidero,
        }
    )
    if umbral is not None:
        atributos["EN_CAUCE"] = acu_desague > umbral
    atributos = atributos.set_index("ID_MICRO").loc[unidas.index].reset_index()
    gdf = gpd.GeoDataFrame(atributos, geometry=unidas.to_numpy(), crs=grid.crs)

    if _mostrar():
        res = abs(tf.a)
        celda_ha = res**2 / 10_000
        cuentas = np.bincount(etiquetas, minlength=desagues.size)
        zona_ha = int(mascara.sum()) * celda_ha
        # La MASCARA, no "la zona": el cierre exacto es contra las celdas
        # rasterizadas, y el poligono que entro mide otra cosa: quien compruebe la
        # SALIDA contra su poligono (`comprobar_particion`) vera ese residuo. La
        # diferencia es la rasterizacion y se da medida.
        poligono_ha = float(p.geometry.area.sum()) / 10_000
        print(
            f"microcuencas: {desagues.size} sobre una mascara de {zona_ha:.2f} ha "
            f"| cierre EXACTO contra la mascara"
        )
        print(
            f"   el poligono mide {poligono_ha:.2f} ha: la salida vectorizada "
            f"cerrara contra el con {abs(poligono_ha - zona_ha):.4f} ha de "
            f"residuo, que es la rasterizacion y no un hueco de la particion"
        )
        mayor = cuentas.max() * celda_ha
        print(
            f"   la mayor {mayor:.2f} ha ({100 * mayor / zona_ha:.1f} % de la "
            f"zona) | la menor {cuentas.min() * celda_ha:.2f} ha"
        )
        if umbral is not None:
            en_cauce = acu_desague > umbral
            ha_cauce = cuentas[en_cauce].sum() * celda_ha
            print(
                f"   {int(en_cauce.sum())} desague(s) sobre cauce se llevan "
                f"{ha_cauce:.2f} ha ({100 * ha_cauce / zona_ha:.1f} %); "
                f"el resto sale de ladera"
            )
        sumideros = es_sumidero
        if sumideros.any():
            ha_muerta = cuentas[sumideros].sum() * celda_ha
            print(
                f"   OJO: {int(sumideros.sum())} 'desague(s)' NO salen de la "
                f"zona: el flujo MUERE ahi.\n   Son {ha_muerta:.2f} ha "
                f"({100 * ha_muerta / zona_ha:.1f} %) que no drenan a ningun "
                "sitio.\n   Mira el aviso de `acumulacion_flujo`: es el mismo "
                "defecto, contado por microcuenca."
            )
    return gdf


def altura_sobre_cauce(
    flujo: Flujo, mde: FloatArray, cauces: gpd.GeoDataFrame
) -> FloatArray:
    """Altura sobre el cauce (HAND): cuanto sube cada celda sobre la celda de
    cauce a la que drena. Devuelve un array float64 con la forma de `mde`.

    `flujo` sale de `acumulacion_flujo` sobre el MDE QUEMADO (el recorrido del
    agua lo decide el quemado). `mde` es el MDE BASE, sin quemar ni
    acondicionar: las cotas salen de ahi, asi que no hay profundidad de quemado
    que devolver. `cauces` son las mismas lineas que se quemaron (se reproyectan
    al CRS del raster).

    La mascara de cauce es la MISMA rasterizacion que usa `quemar_cauces`
    (`todo_tocado=False`). Con `todo_tocado=True` entran celdas que no se
    quemaron y el HAND se mide contra cauce que el flujo no sigue. Medido en
    un predio real a 5 m: 71.6 ha de mascara contra 51.2, y con las cotas quemadas
    menos 5 m salian 163 ha de HAND negativo; con esta variante, 12 ha.

    HAND < 0 queda: son depresiones del MDE base junto al cauce (lo que
    `acondicionar_mde` relleno en el quemado), y en una clasificacion de
    inundacion cuentan como hondonada. NaN donde el MDE base no tiene dato y
    donde la celda no llega a ningun cauce (drena al borde o a un sumidero);
    esto ultimo se avisa siempre con las ha, porque esa superficie sale de
    cualquier clase en silencio.
    """
    from pysheds.sview import Raster

    grid, fdir, _ = flujo
    if mde.shape != fdir.shape:
        raise ValueError(
            f"mde {mde.shape} y flujo {fdir.shape} no son la misma malla: pasa "
            "el MDE base del que salio el quemado"
        )
    c = _a_crs(cauces, grid.crs, "cauces")
    vista = fdir.viewfinder
    cauce = rasterizar(c, mde.shape, vista.affine) == 1  # la de quemar_cauces
    if not cauce.any():
        raise ValueError("ninguna celda de cauce cae en el raster: revisa el CRS o el predio")

    sin_dato = np.isnan(mde)
    # relleno solo para que un NaN sobre el cauce no corte el HAND aguas arriba
    z = rellenar_nodata(mde).astype(np.float64) if sin_dato.any() else mde.astype(np.float64)
    hand = np.asarray(grid.compute_hand(fdir, Raster(z, vista), Raster(cauce, vista)), dtype=float)
    hand[sin_dato] = np.nan

    sueltas = np.isnan(hand) & ~sin_dato
    if sueltas.any():
        celda_ha = abs(vista.affine.a * vista.affine.e) / 10_000
        print(
            f"Aviso: {sueltas.sum() * celda_ha:,.2f} ha con dato no llegan a "
            "ningun cauce (drenan al borde o\n   a un sumidero): HAND NaN, "
            "fuera de cualquier clase."
        )
    return hand
