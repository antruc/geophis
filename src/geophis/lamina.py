"""Lamina: varios paneles sobre la misma malla en un PNG de revision.

Para REVISAR un resultado con color, leyenda, escala y cita, no para entregar
cartografia: la cartografia final se arma en el SIG. El PNG sale sin
georreferencia (para eso esta `guardar_raster`) y sin mapa base (las licencias
prohiben guardarlo). Solo Pillow, sin matplotlib.

Las trampas, resueltas de entrada:

- Estirado compartido: los paneles con la misma paleta y unidad usan UN rango
  (p2-p98 conjunto), o "antes" y "despues" no se comparan.
- Divergente simetrico: el 0 cae siempre en el color central.
- "Sin dato" con color propio, distinto de toda paleta, y su % en la leyenda.
- Misma malla obligatoria: dos arrays de igual forma y distinto transform se
  dibujarian desalineados sin error.
- Las clases nunca se interpolan: se reducen por vecino mas cercano y se
  agrandan solo por un multiplo entero.

Capa de arriba: usa `raster` y vector a la vez, como `zonas`.
"""

import io
import math
import os
from importlib.resources import files
from os import PathLike
from typing import Any

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from pyproj import CRS
from rasterio import Affine
from rasterio.enums import Resampling

from ._salida import _mostrar
from .proyeccion import _a_crs
from .raster import rasterizar, remuestrear

# anclas de cada paleta, repartidas de 0 a 1
PALETAS: dict[str, list[tuple[int, int, int]]] = {
    "gris": [(0, 0, 0), (255, 255, 255)],
    # BrBG de ColorBrewer: cafe = perdida, verde = ganancia, casi blanco = 0
    "divergente": [
        (140, 81, 10),
        (216, 179, 101),
        (245, 245, 245),
        (90, 180, 172),
        (1, 102, 94),
    ],
    "vegetacion": [(140, 81, 10), (254, 224, 139), (166, 217, 106), (26, 152, 80)],
}
# lavanda y no gris claro: la paleta "gris" pasa por todos los grises, y un
# hueco de datos se confundiria con un valor
SIN_DATO = (203, 184, 230)
CONTORNO = (255, 0, 255)
MESES = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)


def _color(c: Any) -> tuple[int, int, int]:
    """'#rrggbb' o (r, g, b) -> (r, g, b)."""
    if isinstance(c, str):
        h = c.lstrip("#")
        if len(h) != 6:
            raise ValueError(f"color {c!r}: va como '#rrggbb' o (r, g, b)")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    r, g, b = (int(v) for v in c)
    return (r, g, b)


def _panel(
    tipo: str,
    datos: Any,
    transform: Affine,
    crs: Any,
    titulo: str,
    contorno: gpd.GeoDataFrame | None,
    **extra: Any,
) -> dict[str, Any]:
    datos = np.asarray(datos, dtype=float)
    if datos.ndim != (3 if tipo == "rgb" else 2):
        raise ValueError(
            f"{titulo!r}: forma {datos.shape} no valida para un panel {tipo}"
        )
    estirado = extra.get("estirado")
    if estirado is not None:
        vmin, vmax = (float(v) for v in estirado)
        if not vmin < vmax:
            raise ValueError(
                f"{titulo!r}: estirado {estirado} debe ser (vmin, vmax) con vmin < vmax"
            )
        extra["estirado"] = (vmin, vmax)
    return {
        "tipo": tipo,
        "datos": datos,
        "transform": transform,
        "crs": CRS.from_user_input(crs),
        "titulo": str(titulo),
        "contorno": contorno,
        **extra,
    }


def panel_rgb(
    bandas: dict[str, npt.NDArray[Any]],
    transform: Affine,
    crs: Any,
    titulo: str,
    contorno: gpd.GeoDataFrame | None = None,
    estirado: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Panel en color natural con `rojo`, `verde` y `azul` del dict de `imagen_sentinel2`.

    El estirado se toma sobre las TRES bandas juntas: estirar cada una por su
    lado cambia el tono entre fechas. `estirado=(vmin, vmax)` lo fija a mano.
    """
    faltan = sorted({"rojo", "verde", "azul"} - set(bandas))
    if faltan:
        raise ValueError(
            f"a `bandas` le faltan {faltan}: panel_rgb usa rojo, verde y azul"
        )
    datos = np.stack(
        [np.asarray(bandas[k], dtype=float) for k in ("rojo", "verde", "azul")]
    )
    return _panel("rgb", datos, transform, crs, titulo, contorno, estirado=estirado)


def panel_continuo(
    arr: npt.NDArray[Any],
    transform: Affine,
    crs: Any,
    titulo: str,
    paleta: str,
    unidad: str,
    contorno: gpd.GeoDataFrame | None = None,
    estirado: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Panel de un continuo (NDVI, pendiente, dNDVI) con barra de color.

    `paleta`: "gris", "divergente" (centro en 0, rango simetrico) o "vegetacion".
    """
    if paleta not in PALETAS:
        raise ValueError(f"paleta {paleta!r}: elige entre {sorted(PALETAS)}")
    return _panel(
        "continuo",
        arr,
        transform,
        crs,
        titulo,
        contorno,
        paleta=paleta,
        unidad=str(unidad),
        estirado=estirado,
    )


def panel_clases(
    arr: npt.NDArray[Any],
    transform: Affine,
    crs: Any,
    titulo: str,
    etiquetas: dict[int, Any],
    colores: dict[int, Any],
    contorno: gpd.GeoDataFrame | None = None,
) -> dict[str, Any]:
    """Panel de clases: los codigos de `reclasificar_rangos`, con leyenda por clase.

    `etiquetas` es el dict `{codigo: etiqueta}` que devuelve `reclasificar_rangos`;
    `colores` es `{codigo: '#rrggbb' o (r, g, b)}` con las mismas claves. Un
    codigo fuera de `etiquetas` (el -1 de NoData, NaN) cuenta como sin dato.
    """
    if set(colores) != set(etiquetas):
        raise ValueError(
            f"`colores` y `etiquetas` deben tener los mismos codigos: "
            f"{sorted(colores)} contra {sorted(etiquetas)}"
        )
    cols = {k: _color(c) for k, c in colores.items()}
    choca = [k for k, c in cols.items() if c == SIN_DATO]
    if choca:
        raise ValueError(f"las clases {choca} usan el color de sin dato {SIN_DATO}")
    return _panel(
        "clases",
        arr,
        transform,
        crs,
        titulo,
        contorno,
        etiquetas=dict(etiquetas),
        colores=cols,
    )


def _sin_dato(p: dict[str, Any], datos: npt.NDArray[np.float64]) -> npt.NDArray[np.bool_]:
    if p["tipo"] == "rgb":
        return np.isnan(datos).any(axis=0)
    if p["tipo"] == "clases":
        return ~np.isin(datos, list(p["etiquetas"]))
    return np.isnan(datos)


def _rango(valores: npt.NDArray[np.float64], paleta: str | None) -> tuple[float, float]:
    """p2-p98 de los valores finitos; simetrico alrededor de 0 si es divergente."""
    v = valores[np.isfinite(valores)]
    if v.size == 0:
        raise ValueError("panel sin datos: todas las celdas son NaN")
    p2, p98 = (float(x) for x in np.percentile(v, [2, 98]))
    if paleta == "divergente":
        m = max(abs(p2), abs(p98))
        return (-m, m)
    return (p2, p98)


def _rampa(t: npt.NDArray[np.float64], paleta: str) -> npt.NDArray[np.float64]:
    """t en [0, 1] -> RGB interpolado entre las anclas, sin tabla: el 0.5 cae exacto."""
    anclas = np.array(PALETAS[paleta], dtype=float)
    xs = np.linspace(0, 1, len(anclas))
    return np.stack([np.interp(t, xs, anclas[:, c]) for c in range(3)], axis=-1)


def _colorear(
    p: dict[str, Any], datos: npt.NDArray[np.float64], rango: tuple[float, float] | None
) -> npt.NDArray[np.uint8]:
    """Datos de un panel -> imagen (alto, ancho, 3) uint8, sin dato en `SIN_DATO`."""
    hueco = _sin_dato(p, datos)
    if p["tipo"] == "clases":
        img = np.zeros(datos.shape + (3,), dtype=np.uint8)
        for codigo, color in p["colores"].items():
            img[datos == codigo] = color
    else:
        vmin, vmax = rango
        t = np.clip((np.nan_to_num(datos) - vmin) / (vmax - vmin), 0, 1)
        if p["tipo"] == "rgb":
            rgb = np.moveaxis(t, 0, -1) * 255
        else:
            rgb = _rampa(t, p["paleta"])
        img = np.rint(rgb).astype(np.uint8)
    img[hueco] = SIN_DATO
    return img


def _reducir(p: dict[str, Any], res: float) -> npt.NDArray[np.float64]:
    """Los datos a `res`: promedio en continuos, vecino mas cercano en clases."""
    datos = p["datos"]
    if p["tipo"] == "clases":
        # el NoData de las clases a NaN, o el vecino mas cercano lo trataria como clase
        datos = np.where(_sin_dato(p, datos), np.nan, datos)
        metodo = Resampling.nearest
    else:
        metodo = Resampling.average
    capas = datos if datos.ndim == 3 else datos[None]
    fuera = [remuestrear(c, p["transform"], p["crs"], res, metodo)[0] for c in capas]
    return np.stack(fuera) if datos.ndim == 3 else fuera[0]


def _longitud_limpia(m: float) -> float:
    """La mayor longitud 1, 2 o 5 x 10^n que no pasa de `m`."""
    n = math.floor(math.log10(m))
    for f in (5, 2, 1):
        if f * 10**n <= m:
            return float(f * 10**n)
    return float(10**n)


def _fuente(tam: int) -> ImageFont.FreeTypeFont:
    # empaquetada: la de Pillow (`load_default`) dibuja la Ñ y la é como caja vacia
    datos = files("geophis").joinpath("fuentes", "DejaVuSans.ttf").read_bytes()
    return ImageFont.truetype(io.BytesIO(datos), tam)


def _envolver(texto: str, fuente: ImageFont.FreeTypeFont, ancho: int) -> list[str]:
    """Parte `texto` en lineas que caben en `ancho` px, por palabras."""
    lineas: list[str] = []
    for parrafo in texto.split("\n"):
        actual = ""
        for palabra in parrafo.split(" "):
            prueba = f"{actual} {palabra}".strip()
            if actual and fuente.getlength(prueba) > ancho:
                lineas.append(actual)
                actual = palabra
            else:
                actual = prueba
        lineas.append(actual)
    return lineas


def _num(v: float) -> str:
    return f"{v:.3g}"


def lamina(
    paneles: list[dict[str, Any]],
    ruta: str | PathLike[str],
    *,
    cita: str | None,
    columnas: int | None = None,
    titulo: str | None = None,
    pie: str | None = None,
    ancho_px: int = 2000,
) -> dict[str, Any]:
    """Junta paneles de la misma malla en un PNG de revision, con leyenda, escala y cita.

    `paneles` sale de `panel_rgb`, `panel_continuo` y `panel_clases`. `cita` no
    tiene default a proposito: con Sentinel-2 va `cita_sentinel2(informe)` (la
    licencia la exige); con datos propios, `cita=None` y la lamina sale sin
    linea de cita. Olvidarla levanta `TypeError`.

    Devuelve dict: `ruta`, `ancho`, `alto`, `escala_m` (la barra de escala),
    `escala_px` y `paneles`, una fila por panel con `titulo`, `estirado`
    (vmin, vmax; None en clases) y `sin_dato_pct`.

    TRAMPAS: (1) los paneles con la misma paleta y unidad (y todos los RGB
    entre si) comparten estirado, salvo los que traen `estirado=`; (2) falla si
    dos paneles no comparten forma, `transform` y `crs`; (3) falla con CRS en
    grados o `transform` rotado: la escala se mide en metros y la flecha de
    norte supone norte arriba; (4) un raster mas ancho que su celda se reduce
    (promedio, o vecino mas cercano en clases) y uno mas angosto se agranda
    solo por un multiplo entero, por vecino mas cercano.
    """
    if not paneles:
        raise ValueError("`paneles` vacio: no hay nada que dibujar")
    if os.path.splitext(os.fspath(ruta))[1].lower() != ".png":
        raise ValueError(f"la ruta debe terminar en .png: {ruta}")
    columnas = columnas or min(len(paneles), 3)
    if columnas < 1:
        raise ValueError(f"columnas {columnas} debe ser >= 1")

    ref = paneles[0]
    forma, tf, crs = ref["datos"].shape[-2:], ref["transform"], ref["crs"]
    for p in paneles[1:]:
        if p["datos"].shape[-2:] != forma or p["transform"] != tf or p["crs"] != crs:
            raise ValueError(
                f"{p['titulo']!r} no comparte malla con {ref['titulo']!r} "
                "(forma, transform y crs): alinea antes con `alinear_rasters`"
            )
    if crs.is_geographic or crs.axis_info[0].unit_name not in ("metre", "meter"):
        raise ValueError(
            f"la lamina mide la escala en metros y el CRS es {crs.name}: reproyecta"
        )
    if tf.b != 0 or tf.d != 0 or tf.e >= 0:
        raise ValueError(
            "transform rotado o con norte abajo: la flecha de norte supone norte arriba"
        )

    # estirado compartido por (paleta, unidad); los RGB forman su propio grupo
    grupos: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for p in paneles:
        if p["tipo"] != "clases" and p["estirado"] is None:
            clave = (
                ("rgb",) if p["tipo"] == "rgb" else ("continuo", p["paleta"], p["unidad"])
            )
            grupos.setdefault(clave, []).append(p)
    rangos: dict[int, tuple[float, float] | None] = {
        id(p): p.get("estirado") for p in paneles
    }
    for clave, miembros in grupos.items():
        juntos = np.concatenate([m["datos"].ravel() for m in miembros])
        r = _rango(juntos, clave[1] if len(clave) > 1 else None)
        for m in miembros:
            rangos[id(m)] = r

    # ── medidas ──
    m = max(8, ancho_px // 60)
    f_tit, f_pan, f_ley = (
        _fuente(max(t, 9)) for t in (ancho_px // 45, ancho_px // 70, ancho_px // 90)
    )
    t_ley = f_ley.size
    ancho_celda = (ancho_px - m * (columnas + 1)) // columnas
    if ancho_celda < 2:
        raise ValueError(f"ancho_px {ancho_px} no alcanza para {columnas} columnas")

    # ── cada panel a imagen, todos a la misma escala ──
    alto, ancho = forma
    if ancho > ancho_celda:
        res = abs(tf.a) * ancho / ancho_celda
        imagenes = [_colorear(p, _reducir(p, res), rangos[id(p)]) for p in paneles]
        tf_img = Affine(res, 0.0, tf.c, 0.0, -res, tf.f)
    else:
        k = ancho_celda // ancho
        imagenes = [
            _colorear(p, p["datos"], rangos[id(p)]).repeat(k, 0).repeat(k, 1)
            for p in paneles
        ]
        tf_img = tf @ Affine.scale(1 / k)
    alto_img, ancho_img = imagenes[0].shape[:2]

    filas_info = []
    for p, img in zip(paneles, imagenes):
        hueco = _sin_dato(p, p["datos"])
        dentro, donde = np.ones(forma, dtype=bool), "de la malla"
        if p["contorno"] is not None:
            c = _a_crs(p["contorno"], crs, "contorno")
            trazos = c.geometry.map(
                lambda g: g.boundary if g.geom_type in ("Polygon", "MultiPolygon") else g
            )
            tinta = rasterizar(
                gpd.GeoDataFrame(geometry=trazos, crs=crs),
                img.shape[:2],
                tf_img,
                valor=1,
                todo_tocado=True,
            )
            img[tinta == 1] = CONTORNO
            poligonos = c[c.geom_type.isin(["Polygon", "MultiPolygon"])]
            if not poligonos.empty:
                interior = rasterizar(poligonos, forma, tf) == 1
                if interior.any():
                    dentro, donde = interior, "del contorno"
        pct = float(100 * (hueco & dentro).sum() / dentro.sum())
        filas_info.append(
            {
                "titulo": p["titulo"],
                "estirado": rangos[id(p)],
                "sin_dato_pct": round(pct, 2),
                "_donde": donde,
            }
        )

    # ── alturas ──
    tit_lineas = _envolver(titulo, f_tit, ancho_px - 2 * m) if titulo else []
    pan_lineas = [_envolver(p["titulo"], f_pan, ancho_celda) for p in paneles]
    n_pan = max(len(x) for x in pan_lineas)

    def alto_leyenda(p: dict[str, Any]) -> int:
        if p["tipo"] == "clases":
            filas = len(p["etiquetas"])
        elif p["tipo"] == "rgb":
            filas = 1
        else:
            filas = 2  # barra + numeros
        return int((filas + 1) * t_ley * 1.5)  # +1: la fila de sin dato

    h_ley = max(alto_leyenda(p) for p in paneles)
    h_celda = int(n_pan * f_pan.size * 1.3) + m // 2 + alto_img + m // 2 + h_ley
    n_filas = math.ceil(len(paneles) / columnas)
    pie_lineas = _envolver(pie, f_ley, ancho_px - 2 * m) if pie else []
    cita_lineas = _envolver(cita, f_ley, ancho_px - 2 * m) if cita else []
    h_tit = int(len(tit_lineas) * f_tit.size * 1.3) + (m if tit_lineas else 0)
    h_pie = int(t_ley * 3) + int((len(pie_lineas) + len(cita_lineas)) * t_ley * 1.4)
    alto_px = m + h_tit + n_filas * (h_celda + m) + h_pie + m

    lienzo = Image.new("RGB", (ancho_px, alto_px), "white")
    d = ImageDraw.Draw(lienzo)
    negro = (0, 0, 0)

    y = m
    for linea in tit_lineas:
        d.text((m, y), linea, font=f_tit, fill=negro)
        y += int(f_tit.size * 1.3)
    y0 = m + h_tit

    for i, (p, img, info) in enumerate(zip(paneles, imagenes, filas_info)):
        x = m + (i % columnas) * (ancho_celda + m)
        y = y0 + (i // columnas) * (h_celda + m)
        for linea in pan_lineas[i]:
            d.text((x, y), linea, font=f_pan, fill=negro)
            y += int(f_pan.size * 1.3)
        y = y0 + (i // columnas) * (h_celda + m) + int(n_pan * f_pan.size * 1.3) + m // 2
        xi = x + (ancho_celda - ancho_img) // 2
        lienzo.paste(Image.fromarray(img), (xi, y))
        d.rectangle([xi - 1, y - 1, xi + ancho_img, y + alto_img], outline=negro)
        y += alto_img + m // 2

        caja = int(t_ley * 0.9)
        if p["tipo"] == "continuo":
            vmin, vmax = rangos[id(p)]
            ancho_barra = min(ancho_img, ancho_celda)
            barra = np.rint(_rampa(np.linspace(0, 1, ancho_barra), p["paleta"])).astype(
                np.uint8
            )
            lienzo.paste(Image.fromarray(np.repeat(barra[None], caja, 0)), (xi, y))
            d.rectangle([xi, y, xi + ancho_barra - 1, y + caja - 1], outline=negro)
            yn = y + caja + t_ley // 4
            d.text((xi, yn), _num(vmin), font=f_ley, fill=negro)
            fin = f"{_num(vmax)} {p['unidad']}"
            d.text(
                (xi + ancho_barra - f_ley.getlength(fin), yn), fin, font=f_ley, fill=negro
            )
            if p["paleta"] == "divergente":
                d.text(
                    (xi + ancho_barra / 2 - f_ley.getlength("0") / 2, yn),
                    "0",
                    font=f_ley,
                    fill=negro,
                )
            y += int(2 * t_ley * 1.5)
        elif p["tipo"] == "rgb":
            vmin, vmax = rangos[id(p)]
            d.text(
                (xi, y),
                f"Color natural, estirado {_num(vmin)} a {_num(vmax)}",
                font=f_ley,
                fill=negro,
            )
            y += int(t_ley * 1.5)
        else:
            for codigo, etiqueta in p["etiquetas"].items():
                d.rectangle(
                    [xi, y, xi + caja, y + caja], fill=p["colores"][codigo], outline=negro
                )
                d.text((xi + caja + t_ley // 2, y), str(etiqueta), font=f_ley, fill=negro)
                y += int(t_ley * 1.5)
        d.rectangle([xi, y, xi + caja, y + caja], fill=SIN_DATO, outline=negro)
        d.text(
            (xi + caja + t_ley // 2, y),
            f"Sin dato: {info['sin_dato_pct']:.1f} % {info.pop('_donde')}",
            font=f_ley,
            fill=negro,
        )

    # ── pie: escala, norte, pie y cita ──
    y = y0 + n_filas * (h_celda + m)
    res_img = abs(tf_img.a)
    escala_m = _longitud_limpia(ancho_img * 0.3 * res_img)
    escala_px = round(escala_m / res_img)
    grosor = max(3, t_ley // 3)
    d.rectangle([m, y, m + escala_px, y + grosor], fill=negro)
    rot = f"{escala_m / 1000:g} km" if escala_m >= 1000 else f"{escala_m:g} m"
    d.text((m, y + grosor + 2), "0", font=f_ley, fill=negro)
    d.text(
        (m + escala_px - f_ley.getlength(rot) / 2, y + grosor + 2),
        rot,
        font=f_ley,
        fill=negro,
    )
    xn = m + escala_px + 3 * t_ley
    d.polygon(
        [
            (xn, y + 2 * t_ley),
            (xn + t_ley / 2, y - t_ley / 2),
            (xn + t_ley, y + 2 * t_ley),
        ],
        fill=negro,
    )
    d.text((xn + 1.5 * t_ley, y), "N", font=f_ley, fill=negro)
    y += int(t_ley * 3)
    for linea in pie_lineas + cita_lineas:
        d.text((m, y), linea, font=f_ley, fill=negro)
        y += int(t_ley * 1.4)

    lienzo.save(ruta, format="PNG")
    salida = {
        "ruta": os.fspath(ruta),
        "ancho": ancho_px,
        "alto": alto_px,
        "escala_m": escala_m,
        "escala_px": escala_px,
        "paneles": filas_info,
    }
    if _mostrar():
        print(
            f"Lamina '{os.fspath(ruta)}': {ancho_px}x{alto_px} px, {len(paneles)} paneles, "
            f"barra de escala {rot}"
        )
        for f in filas_info:
            r = f["estirado"]
            rango = f"estirado {_num(r[0])} a {_num(r[1])}" if r else "clases"
            print(f"  {f['titulo']}: {rango} | sin dato {f['sin_dato_pct']:.1f} %")
    return salida


def _usadas(informe: pd.DataFrame) -> pd.DataFrame:
    filas = informe[informe["usada"]].sort_values("fecha")
    if filas.empty:
        raise ValueError("el informe no tiene fechas usadas (`usada` es False en todas)")
    return filas


def rotulo_escenas(informe: pd.DataFrame) -> str:
    """Las fechas usadas del `informe` de `imagen_sentinel2`, con su % aportado.

    `"03-oct-2026 (64.9 %) y 05-oct-2026 (2.4 %)"`, en orden de fecha. Sale del
    dato para que el rotulo no se teclee a mano.
    """
    partes = []
    for _, f in _usadas(informe).iterrows():
        a, mes, dia = str(f["fecha"])[:10].split("-")
        partes.append(f"{dia}-{MESES[int(mes) - 1]}-{a} ({f['aporta_pct']:.1f} %)")
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]


def cita_sentinel2(*informes: pd.DataFrame) -> str:
    """La cita que exige la licencia de Copernicus, con los años de las fechas usadas.

    Acepta varios `informe` (antes y despues) para que el script no tenga que
    juntarlos con pandas: la cita lleva los años de todas las imagenes.
    """
    if not informes:
        raise ValueError("pasa al menos un `informe` de `imagen_sentinel2`")
    anios = sorted({str(f)[:4] for inf in informes for f in _usadas(inf)["fecha"]})
    return "Contains modified Copernicus Sentinel data " + ", ".join(anios)
