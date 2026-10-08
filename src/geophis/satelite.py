"""Imagen Sentinel-2 del predio: búsqueda STAC en Earth Search y lectura por ventana.

QUÉ HACE. `buscar_sentinel2` pregunta a Earth Search (Element 84, sobre AWS)
qué escenas de la colección `sentinel-2-c1-l2a` pisan el predio en unas
fechas, y las agrupa por fecha. `imagen_sentinel2` elige la fecha más limpia
sobre el predio (con la banda `scl`), rellena huecos de nube con otras fechas
si hace falta y devuelve las bandas en reflectancia, con nombres en español
listos para `indice_vegetacion(**bandas)`. Solo se bajan los bloques de COG
que pisa el predio, nunca la escena.

Sin cuenta, sin token y sin dependencias nuevas: la red va con `urllib` +
`json` de la stdlib, la lectura con rasterio (`mosaico`), las huellas con
shapely. Va ENCIMA de `raster.py`, que no importa nada de red.

QUÉ NO HACE, Y POR QUÉ:

- **Ni mapas base comerciales, ni ninguna fuente XYZ.** Sus licencias
  prohíben guardar, cachear, uso offline o análisis de la imagen, o solo la
  dejan offline dentro de su propio software. Sentinel-2 es libre, también
  para uso comercial, con cita.
- **No Copernicus Data Space:** pide cuenta, token y tiene cuotas. Earth
  Search no.
- **Sin máscara de nubes de plataforma propietaria.** La máscara de nubes
  es la banda `scl`, que es PEOR que esas: confunde suelo muy brillante con nube y se le
  escapan sombras. Se documenta así, no se esconde.
- **No mediana de temporada:** mejor fecha, que en secas basta y cuesta una
  fracción del tiempo.

CITA OBLIGATORIA en todo entregable: `Contains modified Copernicus Sentinel
data AAAA` (el año de la imagen). La licencia la exige.
"""

import json
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Any

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import pandas as pd
from rasterio import Affine
from rasterio.crs import CRS
from rasterio.warp import Resampling
from scipy.ndimage import binary_dilation
from shapely.geometry import shape

from .archivo import detectar_utm
from .proyeccion import _exigir_crs
from .raster import (
    FloatArray,
    _paso,
    alinear_rasters,
    mosaico,
    rasterizar,
)
from ._salida import _callado, _mostrar

URL_STAC = "https://earth-search.aws.element84.com/v1/search"
COLECCION = "sentinel-2-c1-l2a"

# asset de Earth Search -> nombre de `indice_vegetacion`
BANDAS_S2 = {
    "blue": "azul",
    "green": "verde",
    "red": "rojo",
    "nir": "nir",
    "swir16": "swir1",
    "swir22": "swir2",
}

# clases `scl` que no son suelo visto: sin dato, saturado, sombra de nube,
# sin clasificar, nube media, nube alta, cirros. La 7 entra porque en la
# practica es borde de nube o bruma. La 2 (sombra del terreno) NO: es relieve,
# y quitarla vaciaria las cañadas.
SCL_SUCIO = (0, 1, 3, 7, 8, 9, 10)

_MAX_PAGINAS = 50
_REINTENTOS = 3


def _pedir(url: str, cuerpo: dict[str, Any] | None, metodo: str) -> dict[str, Any]:
    """Una petición a la API STAC. Fallo de red = RuntimeError con URL y código.

    HTTP 429, 5xx y red caída se reintentan `_REINTENTOS` veces (espera 1, 2,
    4 s): Earth Search suelta 502 sueltos. Otro 4xx no se reintenta, es la
    petición la que está mal.
    """
    datos = None if cuerpo is None else json.dumps(cuerpo).encode()
    for intento in range(_REINTENTOS + 1):
        req = urllib.request.Request(
            url, data=datos, method=metodo, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if r.status != 200:
                    raise RuntimeError(f"Earth Search respondio HTTP {r.status} en {url}")
                return json.load(r)
        except urllib.error.HTTPError as e:
            e.close()  # suelta la conexion antes de reintentar
            if (e.code != 429 and e.code < 500) or intento == _REINTENTOS:
                raise RuntimeError(
                    f"Earth Search respondio HTTP {e.code} en {url}"
                ) from None
        except (urllib.error.URLError, TimeoutError) as e:
            if intento == _REINTENTOS:
                motivo = getattr(e, "reason", e)
                raise RuntimeError(f"sin respuesta de {url}: {motivo}") from None
        time.sleep(2**intento)
    raise AssertionError("inalcanzable")


def _tesela(item: dict[str, Any]) -> str:
    """Tesela MGRS del ítem: `grid:code` ('MGRS-13QDC'), o el 2o campo del id."""
    codigo = item["properties"].get("grid:code", "")
    if codigo:
        return codigo.removeprefix("MGRS-")
    return item["id"].split("_")[1].removeprefix("T")


def _agrupar_escenas(
    features: list[dict[str, Any]], predio: gpd.GeoDataFrame
) -> list[dict[str, Any]]:
    """Ítems STAC ya bajados -> una escena por fecha, de menos a más nubes.

    Separado de la red para poder probarlo con un JSON escrito a mano.

    - Duplicados: en Earth Search la tesela viene en `properties["grid:code"]`
      y una fecha puede traer la misma tesela dos veces: DOS PASADAS del mismo
      día desde órbitas relativas distintas (la huella de una puede ser media
      franja) o dos productos de la misma pasada. Por eso gana la huella que
      cubre MÁS predio y, a igualdad, la de `properties.updated` más reciente.
      Elegir solo por `updated` toma a veces la media franja.
    - Ítems cuya huella no toca el predio se descartan: el bbox de búsqueda es
      más grande que el polígono.
    - `cubre` = % del predio dentro de la unión de huellas, medido en el UTM
      del predio, no en grados. Una fecha a la que le falta una tesela sale
      con `cubre < 100` aunque su nubosidad sea baja.
    """
    utm = predio.estimate_utm_crs()
    zona = predio.to_crs(utm).union_all()
    zona_geo = predio.to_crs(4326).union_all()

    def _prioridad(f: dict[str, Any]) -> tuple[float, str]:
        # area en grados: solo compara huellas de la misma tesela, basta
        pisa = shape(f["geometry"]).intersection(zona_geo).area
        return round(pisa, 12), f["properties"].get("updated", "")

    por_fecha: dict[str, dict[str, dict[str, Any]]] = {}
    for f in features:
        if not shape(f["geometry"]).intersects(zona_geo):
            continue
        fecha = f["properties"]["datetime"][:10]
        t = _tesela(f)
        previo = por_fecha.setdefault(fecha, {}).get(t)
        if previo is None or _prioridad(f) > _prioridad(previo):
            por_fecha[fecha][t] = f

    escenas = []
    for fecha, teselas in por_fecha.items():
        items = [teselas[t] for t in sorted(teselas)]
        huellas = gpd.GeoSeries([shape(i["geometry"]) for i in items], crs=4326)
        cubre = 100 * zona.intersection(huellas.to_crs(utm).union_all()).area / zona.area
        escenas.append(
            {
                "fecha": fecha,
                "teselas": sorted(teselas),
                "cubre": round(cubre, 2),
                "nubes": max(i["properties"].get("eo:cloud_cover", 100.0) for i in items),
                "urls": {
                    k: [i["assets"][k]["href"] for i in items]
                    for k in items[0]["assets"]
                    if all(k in i["assets"] for i in items)
                },
            }
        )
    escenas.sort(key=lambda e: (e["nubes"], -e["cubre"], e["fecha"]))
    return escenas


def buscar_sentinel2(
    predio: gpd.GeoDataFrame,
    fechas: tuple[str, str],
    nubes_max: float = 80,
) -> list[dict[str, Any]]:
    """Escenas Sentinel-2 L2A que pisan el predio, una por fecha, de la más limpia a la más sucia.

    `predio` en cualquier CRS. `fechas` = `("AAAA-MM-DD", "AAAA-MM-DD")`,
    OBLIGATORIA: qué temporada mirar es decisión de campo, no hay default.
    `nubes_max` filtra por la nubosidad de la TESELA entera (`eo:cloud_cover`),
    no la del predio: una tesela al 60 % puede tener el predio despejado.

    Cada escena es un dict: `fecha`, `teselas` (MGRS), `cubre` (% del predio
    dentro de las huellas), `nubes` (máx. de sus teselas) y `urls`
    (`{asset: [una url por tesela]}`, p. ej. `urls["red"]`, `urls["scl"]`).
    Se pasa tal cual a `imagen_sentinel2(escenas=...)`.

    Pagina hasta el final: con un `limit` lleno la API corta en silencio y
    se pierden fechas. Sin
    resultados: ValueError con el bbox. Red caída: RuntimeError con la URL.
    """
    _exigir_crs(predio, "predio")
    if not (isinstance(fechas, (tuple, list)) and len(fechas) == 2):
        raise ValueError(
            f"`fechas` va como ('AAAA-MM-DD', 'AAAA-MM-DD'); llego {fechas!r}"
        )
    bbox = [round(float(v), 6) for v in predio.to_crs(4326).total_bounds]
    cuerpo: dict[str, Any] | None = {
        "collections": [COLECCION],
        "bbox": bbox,
        "datetime": f"{fechas[0]}T00:00:00Z/{fechas[1]}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": nubes_max}},
        "limit": 100,
    }
    url, metodo = URL_STAC, "POST"
    features: list[dict[str, Any]] = []
    for _ in range(_MAX_PAGINAS):
        pagina = _pedir(url, cuerpo, metodo)
        features += pagina.get("features", [])
        sig = next(
            (ln for ln in pagina.get("links", []) if ln.get("rel") == "next"), None
        )
        if sig is None:
            break
        # Earth Search da el siguiente como POST con su propio body completo
        url, metodo = sig["href"], sig.get("method", "GET").upper()
        if metodo == "GET":
            cuerpo = None
        elif sig.get("merge"):
            cuerpo = {**(cuerpo or {}), **sig.get("body", {})}
        else:
            cuerpo = sig.get("body", cuerpo)
    else:
        raise RuntimeError(
            f"mas de {_MAX_PAGINAS} paginas de resultados: acorta `fechas` o baja `nubes_max`"
        )

    escenas = _agrupar_escenas(features, predio) if features else []
    if not escenas:
        raise ValueError(
            f"Earth Search no tiene escenas de {COLECCION} para bbox {bbox}, fechas "
            f"{fechas[0]} a {fechas[1]} y nubes_max={nubes_max}. Amplia las fechas "
            f"o sube nubes_max."
        )
    if _mostrar():
        teselas = sorted({t for e in escenas for t in e["teselas"]})
        completas = sum(e["cubre"] >= 100 for e in escenas)
        print(
            f"buscar_sentinel2: {len(features)} items -> {len(escenas)} fechas "
            f"({completas} cubren el predio entero) | teselas {', '.join(teselas)}"
        )
    return escenas


def _limpio(scl: FloatArray, margen: int = 0) -> npt.NDArray[np.bool_]:
    """Píxeles `scl` con dato y a más de `margen` píxeles de `SCL_SUCIO`.

    Sin dato cuenta como sucio pero NO se ensancha: es borde de tesela o del
    área leída, no nube, y ensancharlo se comería el lindero del predio.
    """
    sucio = np.isin(scl, SCL_SUCIO)
    if margen:
        sucio = binary_dilation(sucio, np.ones((3, 3), bool), iterations=margen)
    return np.isfinite(scl) & ~sucio


def imagen_sentinel2(
    predio: gpd.GeoDataFrame,
    fechas: tuple[str, str] | None = None,
    escenas: list[dict[str, Any]] | None = None,
    bandas: dict[str, str] | None = None,
    limpio_min: float = 99.0,
    max_fechas: int = 3,
    max_revisar: int = 10,
    crs: Any = None,
    margen_nube: int = 2,
    max_dias: int | None = 30,
    escenas_relleno: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, FloatArray], Affine, CRS, float, pd.DataFrame, FloatArray]:
    """Bandas Sentinel-2 del predio en reflectancia, de la fecha más limpia.

    Pasa `fechas` (y se llama a `buscar_sentinel2`) o `escenas` ya buscadas,
    exactamente uno. Devuelve `(bandas, transform, crs, resolucion,
    informe, origen)`:

    - `bandas`: dict con nombres en español (`azul`, `verde`, `rojo`, `nir`,
      `swir1`, `swir2`), listo para `indice_vegetacion(tipo, **bandas)`.
      Reflectancia 0 a 1 (escala y desfase del COG aplicados), NaN fuera del
      predio, en nube y donde no hay dato. `bandas=` es `{asset: nombre}` si
      quieres otras; None = `BANDAS_S2`.
    - `informe`: DataFrame, una fila por fecha revisada: `fecha`, `teselas`,
      `nubes` (de la tesela), `cubre`, `dias` (distancia a la principal),
      `limpio_pct`, `aporta_pct` (qué % del predio salió de esa fecha), `usada`
      (`aporta_pct > 0`).
    - `origen`: array de la forma de las bandas con la FILA del `informe` de
      la que salió cada píxel, NaN donde no hay dato. Con más de una fecha
      usada es lo que explica una costura en el mapa, y va junto al NBR en el
      entregable.

    Cómo elige: recorre las escenas de menos a más nubes y mide el % del
    predio limpio en `scl` (sin dato cuenta como sucio). Para en la primera con
    `limpio_pct >= limpio_min` que cubra el predio entero. Si ninguna llega,
    toma la mejor y rellena sus huecos, píxel a píxel, con las fechas a
    `max_dias` o menos de ella, la más cercana primero, hasta `max_fechas` o
    hasta llegar a `limpio_min`. Cercana antes que limpia: rellenar abril con
    febrero mezcla fenologías en el mismo predio. `max_dias=None` quita el
    tope. Una fecha a la que le falta una tesela no puede ser la principal,
    pero sí sirve para rellenar. Con `escenas` a mano, el orden manda: la
    principal es la primera que llega a `limpio_min`, no la más limpia.

    `escenas_relleno`: escenas (de `buscar_sentinel2`) que NUNCA son la
    principal y solo rellenan lo que las de `fechas`/`escenas` dejaron sin
    dato, después de ellas y con los mismos topes (`max_dias`, `max_fechas`,
    `limpio_min`). Sirve cuando la ventana que importa es una (antes de un
    evento) y la fecha limpia más cercana cae fuera de ella: estirar `fechas`
    haría principal a la de fuera. Su `scl` solo se lee si la principal no
    llega a `limpio_min`. Salen en el `informe` (y en `origen`) después de las
    revisadas. Una fecha no puede ir en las dos listas.

    `margen_nube`: la nube de `scl` se ensancha estos píxeles de 20 m (2 = 40
    m) antes de medir nada, porque a `scl` se le escapan los bordes de nube y
    de sombra. 0 = la `scl` tal cual. Es perilla de calibración: súbelo si la
    `vista_previa` muestra halos de índice bajo junto a los huecos.

    `max_revisar` es el tope de escenas cuya `scl` se lee (una lectura por red
    cada una). Sin tope, una temporada sin ninguna fecha limpia las leería TODAS. Si
    se alcanza sin que ninguna baste, se avisa y se elige entre las leídas: las
    siguientes son las más nubladas según `eo:cloud_cover`, pero una de ellas
    podría tener el predio despejado. Súbelo si el aviso sale.

    CRS de salida: `crs` o, si no, `detectar_utm(predio)`. Si el predio cruza
    dos husos, `detectar_utm` falla: pasa `crs=`.

    TRAMPAS:
    - `scl` es una máscara de nubes pobre: confunde suelo muy
      brillante con nube y se le escapan sombras. Mira el resultado.
    - `swir1`, `swir2` (y la `scl`) son de 20 m: se llevan a la malla de 10 m
      con nearest. No inventa valores, pero
      el píxel real sigue siendo de 20 m.
    - En lluvias lo normal es que quede predio sin dato: se avisa con el %, no
      se falla. Amplía `fechas`.
    - Con el desfase de -0.1 del COG, agua y sombra salen con reflectancia
      NEGATIVA. No se recorta: es el dato. Un índice
      normalizado ahí puede salir fuera de [-1, 1].

    CITA OBLIGATORIA en el entregable: `Contains modified Copernicus Sentinel
    data AAAA`. Se imprime con el año de la imagen.
    """
    if (fechas is None) == (escenas is None):
        raise ValueError("pasa `fechas` o `escenas`, exactamente uno de los dos")
    _exigir_crs(predio, "predio")
    bandas = BANDAS_S2 if bandas is None else bandas
    if escenas is None:
        escenas = buscar_sentinel2(predio, fechas)
    if not escenas:
        raise ValueError("`escenas` va vacia")
    if max_revisar < 1:
        raise ValueError(f"max_revisar {max_revisar} debe ser >= 1")
    if margen_nube < 0:
        raise ValueError(f"margen_nube {margen_nube} debe ser >= 0")
    escenas_relleno = escenas_relleno or []
    repetidas = {e["fecha"] for e in escenas} & {e["fecha"] for e in escenas_relleno}
    if repetidas:
        raise ValueError(
            f"fechas en `escenas` y en `escenas_relleno` a la vez: {sorted(repetidas)}"
        )
    crs_sal = (
        CRS.from_user_input(crs)
        if crs is not None
        else CRS.from_epsg(_callado(detectar_utm)(predio))
    )
    zona = predio.to_crs(crs_sal)
    # Se LEE con holgura y se recorta al predio en la malla fina. Enmascarado
    # con el poligono a 20 m, la `scl` y el SWIR dejarian sin dato el anillo de
    # celdas de 10 m cuyo pixel de 20 m tiene el centro fuera.
    # La holgura crece con `margen_nube`: una nube a 30 m del lindero tiene que
    # leerse para que su margen entre al predio.
    # ponytail: 20 m = el pixel mas grueso de BANDAS_S2; una banda de 60 m pide 60
    holgura = gpd.GeoDataFrame(geometry=zona.buffer(20 * (1 + margen_nube)), crs=crs_sal)

    def _dentro(forma: tuple[int, ...], tf: Affine) -> npt.NDArray[np.bool_]:
        d = rasterizar(zona, forma, tf, valor=1) == 1
        if not d.any():
            raise ValueError("el predio no llega a cubrir el centro de un pixel")
        return d

    def _revisar(e: dict[str, Any]) -> tuple[dict[str, Any], float, FloatArray, Affine]:
        scl, tf, _, _ = _callado(mosaico)(e["urls"]["scl"], holgura, crs_sal)
        limpia = _limpio(scl, margen_nube)
        limpio = float(100 * limpia[_dentro(scl.shape, tf)].mean())
        return e, limpio, limpia.astype(float), tf

    # 1. % limpio de cada escena, hasta la primera que baste. Se guarda la
    # mascara limpia (1/0) y no la scl: el margen se aplica UNA vez, a 20 m
    revisadas = []  # (escena, limpio_pct, limpia, tf)
    for e in escenas[:max_revisar]:
        revisadas.append(_revisar(e))
        if revisadas[-1][1] >= limpio_min and e.get("cubre", 100.0) >= 100:
            break
    else:
        if len(escenas) > max_revisar:
            print(
                f"Aviso: ninguna de las {max_revisar} escenas revisadas llega a "
                f"{limpio_min:g} % limpio; quedan {len(escenas) - max_revisar} sin "
                "leer. Se elige entre las revisadas; sube `max_revisar` para mirar mas."
            )

    # 2. principal y relleno, medidos sobre la malla de la scl principal
    candidatas = [r for r in revisadas if r[0].get("cubre", 100.0) >= 100] or revisadas
    principal = max(candidatas, key=lambda r: r[1])
    n_propias = len(revisadas)
    if principal[1] < limpio_min:
        revisadas += [_revisar(e) for e in escenas_relleno]
    dia_p = date.fromisoformat(principal[0]["fecha"])
    dias = [abs((date.fromisoformat(r[0]["fecha"]) - dia_p).days) for r in revisadas]
    usadas = [revisadas.index(principal)]
    _, _, lim_p, tf_p = principal
    dentro_p = _dentro(lim_p.shape, tf_p)
    libre = dentro_p & (lim_p != 1)
    total = dentro_p.sum()
    # las de `escenas_relleno` despues de todas las propias, aunque esten mas cerca
    orden = sorted(
        range(len(revisadas)), key=lambda i: (i >= n_propias, dias[i], -revisadas[i][1])
    )
    for i in orden:
        if len(usadas) >= max_fechas or 100 * (1 - libre.sum() / total) >= limpio_min:
            break
        if i in usadas or (max_dias is not None and dias[i] > max_dias):
            continue
        (al,) = alinear_rasters(
            (lim_p, tf_p, crs_sal),
            (revisadas[i][2], revisadas[i][3], crs_sal),
            metodo=Resampling.nearest,
        )
        gana = libre & (al == 1)
        if gana.any():
            usadas.append(i)
            libre &= ~gana

    # 3. bandas, fecha por fecha, en la malla más fina de la principal
    ref: tuple[FloatArray, Affine, CRS] | None = None
    salida: dict[str, FloatArray] = {}
    aporta: dict[str, float] = {}
    for i in usadas:
        e, _, lim, tf_lim = revisadas[i]
        mos = {k: _callado(mosaico)(e["urls"][k], holgura, crs_sal) for k in bandas}
        if ref is None:
            # malla de la banda mas fina, recortada a las celdas del predio (la
            # holgura solo sirve para leer)
            fina = min(mos.values(), key=lambda m: m[3])
            d = _dentro(fina[0].shape, fina[1])
            f, c = np.flatnonzero(d.any(axis=1)), np.flatnonzero(d.any(axis=0))
            ventana = (slice(f[0], f[-1] + 1), slice(c[0], c[-1] + 1))
            ref = (fina[0][ventana], fina[1] @ Affine.translation(c[0], f[0]), crs_sal)
            dentro = d[ventana]
            libre = dentro.copy()
            salida = {n: np.full(dentro.shape, np.nan) for n in bandas.values()}
            origen = np.full(dentro.shape, np.nan)
        alineadas = {
            k: m[0]
            if m[0].shape == ref[0].shape and m[1] == ref[1]
            else alinear_rasters(ref, (m[0], m[1], crs_sal), metodo=Resampling.nearest)[0]
            for k, m in mos.items()
        }
        (lim_al,) = alinear_rasters(ref, (lim, tf_lim, crs_sal), metodo=Resampling.nearest)
        toma = libre & (lim_al == 1)
        for a in alineadas.values():
            toma &= np.isfinite(a)
        for k, n in bandas.items():
            salida[n][toma] = alineadas[k][toma]
        origen[toma] = i
        aporta[e["fecha"]] = float(100 * toma.sum() / dentro.sum())
        libre &= ~toma

    sin_dato = float(100 * libre.sum() / dentro.sum())
    informe = pd.DataFrame(
        [
            {
                "fecha": e["fecha"],
                "teselas": ", ".join(e.get("teselas", [])),
                "nubes": e.get("nubes", np.nan),
                "cubre": e.get("cubre", np.nan),
                "dias": d,
                "limpio_pct": round(limpio, 2),
                "aporta_pct": round(aporta.get(e["fecha"], 0.0), 2),
                "usada": aporta.get(e["fecha"], 0.0) > 0,
            }
            for (e, limpio, _, _), d in zip(revisadas, dias)
        ]
    )
    if sin_dato > 100 - limpio_min:
        print(
            f"Aviso: {sin_dato:.1f} % del predio sin dato limpio (nube o sin tesela) "
            f"tras {len(usadas)} fecha(s). Amplia `fechas`, sube `max_fechas` o "
            "`max_dias`, o baja `margen_nube`."
        )
    if _mostrar():
        print(
            f"imagen_sentinel2: fechas {', '.join(aporta)} | "
            f"{100 - sin_dato:.1f} % del predio limpio | {_paso(abs(ref[1].a), crs_sal)}"
        )
    # FUERA de `geo.MOSTRAR`: la licencia la exige en el entregable, no es reporte
    anios = sorted({f[:4] for f, a in aporta.items() if a > 0})
    print(f"Cita obligatoria: Contains modified Copernicus Sentinel data {', '.join(anios)}")
    tf_ref = ref[1]
    return salida, tf_ref, crs_sal, abs(tf_ref.a), informe, origen
