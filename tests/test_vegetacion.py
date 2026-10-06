"""Tests de `cargar_bandas` e `indice_vegetacion`.

Los dos eslabones propios de la cadena de deteccion de areas sin vegetacion
(docs/GEOPHIS.md, receta de vegetacion).

Lo que se comprueba es lo que se rompe en silencio:

- que el array sale en FLOAT aunque el .tif sea uint8. En entero,
  `nir - rojo` hace underflow y el indice sale invertido y creible.
- que las bandas se piden por nombre y falta la que falta, no la que toca
  por posicion.
- que el denominador 0 da NaN y no infinito ni un aviso de numpy.
"""

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine

import geophis as geo

UTM = CRS.from_epsg(32613)
TF = Affine(10, 0, 500_000, 0, -10, 2_000_000)


def _rgb_uint8(tmp_path, rojo, verde, azul, nodata=None):
    """Escribe un .tif RGB de 3 bandas uint8, como una captura de pantalla."""
    ruta = tmp_path / "rgb.tif"
    pila = np.stack([rojo, verde, azul]).astype("uint8")
    perfil = {
        "driver": "GTiff",
        "crs": UTM,
        "transform": TF,
        "count": 3,
        "dtype": "uint8",
        "height": pila.shape[1],
        "width": pila.shape[2],
    }
    if nodata is not None:
        perfil["nodata"] = nodata
    with rasterio.open(ruta, "w", **perfil) as dst:
        dst.write(pila)
    return ruta


# ---- cargar_bandas --------------------------------------------------------


def test_cargar_bandas_devuelve_3d_en_el_orden_pedido(tmp_path):
    r = np.full((4, 5), 10)
    g = np.full((4, 5), 20)
    b = np.full((4, 5), 30)
    arr, tf, crs, res = geo.cargar_bandas(_rgb_uint8(tmp_path, r, g, b), (3, 1))
    assert arr.shape == (2, 4, 5)
    assert arr[0, 0, 0] == 30 and arr[1, 0, 0] == 10  # el orden es el pedido
    assert (tf, crs, res) == (TF, UTM, 10.0)  # misma tupla de 4 que cargar_raster


def test_cargar_bandas_sale_en_float_aunque_el_tif_sea_uint8(tmp_path):
    # el test que pilla el underflow: en uint8, 10 - 200 no es -190, es 66.
    # El indice sale con el signo cambiado y el mapa parece correcto.
    arr, *_ = geo.cargar_bandas(
        _rgb_uint8(tmp_path, np.full((3, 3), 200), np.full((3, 3), 10), np.zeros((3, 3))),
        (1, 2),
    )
    assert arr.dtype == float
    assert (arr[1] - arr[0])[0, 0] == -190.0


def test_cargar_bandas_banda_inexistente_falla_diciendo_cuantas_hay(tmp_path):
    ruta = _rgb_uint8(tmp_path, *[np.ones((2, 2))] * 3)
    with pytest.raises(ValueError, match="tiene 3 banda"):
        geo.cargar_bandas(ruta, (1, 7))
    # y el cero, que es el error de quien viene de numpy: se numeran desde 1
    with pytest.raises(ValueError, match="desde 1"):
        geo.cargar_bandas(ruta, (0, 1))


def test_cargar_bandas_nodata_entra_como_nan(tmp_path):
    r = np.full((3, 3), 50)
    r[0, 0] = 255
    arr, *_ = geo.cargar_bandas(_rgb_uint8(tmp_path, r, r, r, nodata=255), (1,))
    assert np.isnan(arr[0, 0, 0]) and arr[0, 1, 1] == 50


# ---- indice_vegetacion ----------------------------------------------------


def test_ndvi_signo_correcto():
    # vegetacion: NIR alto y rojo bajo => positivo. Suelo desnudo: al reves.
    veg = geo.indice_vegetacion("ndvi", rojo=np.array([[20.0]]), nir=np.array([[180.0]]))
    suelo = geo.indice_vegetacion(
        "ndvi", rojo=np.array([[180.0]]), nir=np.array([[20.0]])
    )
    assert veg[0, 0] == pytest.approx(0.8) and suelo[0, 0] == pytest.approx(-0.8)


def test_exg_verde_puro_es_el_maximo():
    # ExG sobre coordenadas cromaticas: verde puro da 2, el techo del rango.
    v = geo.indice_vegetacion(
        "exg",
        rojo=np.array([[0.0]]),
        verde=np.array([[255.0]]),
        azul=np.array([[0.0]]),
    )
    assert v[0, 0] == pytest.approx(2.0)


def test_gris_no_es_vegetacion():
    # r = g = b: ExG da 0 exacto. Es la identidad que separa color de brillo,
    # y falla si algun coeficiente se cambia.
    gris = np.full((2, 2), 128.0)
    v = geo.indice_vegetacion("exg", rojo=gris, verde=gris, azul=gris)
    assert (v == 0).all()


def test_denominador_cero_da_nan_no_infinito():
    # negro puro: ausencia de dato, no un indice infinito que luego reclasifica
    # como la clase mas alta.
    cero = np.zeros((2, 2))
    v = geo.indice_vegetacion("exg", rojo=cero, verde=cero, azul=cero)
    assert np.isnan(v).all()


def test_indice_no_avisa_al_dividir_por_cero():
    # un RuntimeWarning por pixel en una ortofoto de millones de pixeles llena
    # la consola y esconde los avisos que si importan.
    cero = np.zeros((2, 2))
    with np.errstate(all="raise"):  # cualquier fuga de numpy revienta aqui
        geo.indice_vegetacion("ndvi", rojo=cero, nir=cero)


def test_indice_pide_las_bandas_por_nombre_y_dice_cual_falta():
    with pytest.raises(ValueError, match="nir"):
        geo.indice_vegetacion("ndvi", rojo=np.zeros((2, 2)))
    with pytest.raises(ValueError, match="azul"):
        geo.indice_vegetacion("vari", rojo=np.zeros((2, 2)), verde=np.zeros((2, 2)))


def test_indice_desconocido_lista_los_validos():
    with pytest.raises(ValueError, match="ndvi"):
        geo.indice_vegetacion("savi", rojo=np.zeros((2, 2)), nir=np.zeros((2, 2)))


def test_indice_formas_distintas_falla():
    # dos recortes desalineados: sin esta guarda, numpy hace broadcasting y
    # devuelve un array de la forma equivocada sin decir nada.
    with pytest.raises(ValueError, match="formas distintas"):
        geo.indice_vegetacion("ndvi", rojo=np.zeros((2, 2)), nir=np.zeros((3, 3)))


def test_indice_entra_en_reclasificar_rangos():
    # la razon por la que `indice_vegetacion` NO lleva umbral: el escalon
    # siguiente de la cadena ya existe y ya resuelve la convencion de bordes.
    idx = geo.indice_vegetacion(
        "ndvi",
        rojo=np.array([[200.0, 20.0]]),
        nir=np.array([[20.0, 200.0]]),
    )
    codigos, etiquetas = geo.reclasificar_rangos(
        idx, [("sin vegetacion", -1.0, 0.2), ("vegetacion", 0.2, 1.0)]
    )
    assert codigos.tolist() == [[1, 2]]
    assert etiquetas[2] == "vegetacion"


# ---- ndmi y nbr ------------------------------------------------------------


def test_ndmi_y_nbr_a_mano():
    nir, s1, s2 = np.array([0.30]), np.array([0.10]), np.array([0.20])
    assert geo.indice_vegetacion("ndmi", nir=nir, swir1=s1)[0] == pytest.approx(0.5)
    assert geo.indice_vegetacion("nbr", nir=nir, swir2=s2)[0] == pytest.approx(0.2)


def test_ndwi_y_mndwi_a_mano():
    # el orden importa: verde primero, agua en positivo
    verde, nir, s1 = np.array([0.30]), np.array([0.10]), np.array([0.20])
    assert geo.indice_vegetacion("ndwi", verde=verde, nir=nir)[0] == pytest.approx(0.5)
    assert geo.indice_vegetacion("mndwi", verde=verde, swir1=s1)[0] == pytest.approx(0.2)


def test_ndmi_sin_swir1_dice_cual_falta():
    with pytest.raises(ValueError, match="swir1"):
        geo.indice_vegetacion("ndmi", nir=np.ones(2))
