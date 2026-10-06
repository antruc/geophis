"""Tests de geophis.archivo (round-trip en disco)."""

import geopandas as gpd
import pytest
from shapely.geometry import Point

import geophis as geo


def test_guardar_y_cargar_roundtrip(tmp_path, gdf, cuadrado):
    g = gdf(cuadrado(), tipo=["a"])
    ruta = str(tmp_path / "capa.gpkg")
    geo.guardar(g, ruta)
    r = geo.cargar(ruta)
    assert len(r) == 1 and r["tipo"].iloc[0] == "a"


def test_exportar_tabla_sin_geometria(tmp_path, gdf, cuadrado):
    import pandas as pd

    g = gdf(cuadrado(), tipo=["a"])
    ruta = tmp_path / "tabla.csv"
    geo.exportar_tabla(g, ruta)
    t = pd.read_csv(ruta)
    assert list(t.columns) == ["tipo"] and t["tipo"].iloc[0] == "a"


def test_exportar_tabla_acepta_dataframe(tmp_path, gdf, cuadrado):
    import pandas as pd

    g = gdf(cuadrado(), tipo=["a"])
    ruta = tmp_path / "tabla.csv"
    geo.exportar_tabla(g[["tipo"]], ruta)  # gdf[cols] sin geometria: DataFrame
    assert list(pd.read_csv(ruta).columns) == ["tipo"]


def test_exportar_tabla_csv_con_bom_para_excel(tmp_path, gdf, cuadrado):
    import pandas as pd

    g = gdf(cuadrado(), LAT_GMS=["20°32'29.44\" N"])
    ruta = tmp_path / "tabla.csv"
    geo.exportar_tabla(g, ruta)
    assert ruta.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM de UTF-8
    assert pd.read_csv(ruta)["LAT_GMS"].iloc[0] == "20°32'29.44\" N"


def test_exportar_tabla_extension_invalida(tmp_path, gdf, cuadrado):
    with pytest.raises(ValueError, match="no es de tabla"):
        geo.exportar_tabla(gdf(cuadrado()), tmp_path / "t.shp")


def test_guardar_shp_traduce_aviso_a_espanol(tmp_path, gdf, cuadrado, capsys):
    # columna >10 chars: GDAL avisa en inglés; debe salir el aviso en español.
    g = gdf(cuadrado(), nombre_de_columna_larga=["x"])
    geo.guardar(g, str(tmp_path / "capa.shp"))
    out = capsys.readouterr().out
    assert "recorta los nombres de columna" in out
    assert "Column names longer" not in out


def test_cargar_ruta_inexistente_falla():
    with pytest.raises(FileNotFoundError):
        geo.cargar("no_existe.shp")


@pytest.mark.filterwarnings("ignore:'crs' was not provided")
def test_cargar_dxf_exige_crs_y_lo_asigna(tmp_path, gdf, capsys):
    import pyogrio
    from shapely.geometry import LineString

    ruta = tmp_path / "plano.dxf"
    lineas = [LineString([(0, 0), (10, 0)]), LineString([(0, 0), (0, 10)])]
    g = gdf(*lineas, crs=None, Layer=["curvas", "lindero"])
    pyogrio.write_dataframe(g, ruta, driver="DXF")
    with pytest.raises(ValueError, match="crs="):
        geo.cargar(ruta)
    r = geo.cargar(ruta, crs=32613)
    assert r.crs.to_epsg() == 32613
    assert list(r["Layer"]) == ["curvas", "lindero"]
    assert r.geometry.geom_equals(g.geometry.set_crs(32613)).all()
    assert "mezcla" not in capsys.readouterr().out


@pytest.mark.filterwarnings("ignore:'crs' was not provided")
def test_cargar_dxf_avisa_geometrias_mezcladas(tmp_path, gdf, capsys):
    import pyogrio
    from shapely.geometry import LineString

    ruta = tmp_path / "mezcla.dxf"
    g = gdf(LineString([(0, 0), (10, 0)]), Point(3, 3), crs=None, Layer=["a", "b"])
    pyogrio.write_dataframe(g, ruta, driver="DXF")
    geo.cargar(ruta, crs=32613)
    assert "mezcla geometrias" in capsys.readouterr().out


def test_cargar_no_avisa_de_polygon_con_multipolygon(tmp_path, gdf, capsys):
    from shapely.geometry import MultiPolygon, box

    ruta = tmp_path / "veg.shp"
    multi = MultiPolygon([box(20, 0, 30, 10), box(40, 0, 50, 10)])
    geo.guardar(gdf(box(0, 0, 10, 10), multi), str(ruta))
    capsys.readouterr()
    geo.cargar(ruta)
    assert "mezcla" not in capsys.readouterr().out


def test_cargar_aviso_de_mezcla_sugiere_la_familia_mayoritaria(tmp_path, gdf, capsys):
    import pyogrio
    from shapely.geometry import LineString

    ruta = tmp_path / "mezcla.gpkg"
    lineas = [LineString([(0, i), (10, i)]) for i in range(3)]
    pyogrio.write_dataframe(gdf(*lineas, Point(3, 3)), ruta, driver="GPKG")
    geo.cargar(ruta)
    out = capsys.readouterr().out
    assert "mezcla geometrias" in out
    assert "isin(['LineString', 'MultiLineString'])" in out


def test_cargar_crs_distinto_al_del_archivo_falla(tmp_path, gdf, cuadrado):
    ruta = tmp_path / "c.gpkg"
    geo.guardar(gdf(cuadrado()), str(ruta))
    with pytest.raises(ValueError, match="no reproyecta"):
        geo.cargar(ruta, crs=4326)


# ---- detectar_utm ----------------------------------------------------------


def test_detectar_utm_centroide(gdf, cuadrado):
    # cerca del meridiano central de la 13N (-105): debe volver al mismo EPSG
    g = gdf(cuadrado(500000, 2600000, 100))
    assert geo.detectar_utm(g) == 32613


def test_detectar_utm_dos_husos_falla():
    dos = gpd.GeoDataFrame(geometry=[Point(-100, 20), Point(10, 20)], crs=4326)
    with pytest.raises(ValueError, match="cruza dos husos"):
        geo.detectar_utm(dos)


# ---- detectar_cota ----------------------------------------------------------


def test_detectar_cota_escalera_gana_al_indice(gdf, cuadrado):
    # ELEVACION en escalera de paso 20; IDENTIFICA es un indice (paso 1, se descarta)
    curvas = gdf(
        cuadrado(0, 0, 10),
        cuadrado(100, 0, 10),
        cuadrado(200, 0, 10),
        ELEVACION=[0, 20, 40],
        IDENTIFICA=[1, 2, 3],
    )
    campo, e = geo.detectar_cota(curvas)
    assert campo == "ELEVACION" and e == 20


def test_detectar_cota_desempate_por_paso_grueso(gdf, cuadrado):
    # FOLIO tiene regularidad 1.0 con paso 1 (no cae en la rama de "enteros
    # consecutivos" porque trae un hueco): el desempate por paso MAS GRUESO
    # es lo unico que hace ganar a ELEVACION.
    curvas = gdf(
        *[cuadrado(x * 100, 0, 10) for x in range(5)],
        ELEVACION=[0, 20, 40, 60, 80],
        FOLIO=[1, 2, 3, 4, 10],
    )
    campo, e = geo.detectar_cota(curvas)
    assert campo == "ELEVACION" and e == 20


def test_detectar_cota_avisa_de_la_cota_fuera_de_escalera(gdf, cuadrado, capsys):
    # el caso de una carta real de curvas: 3302 tras 2760 con e = 20. El hueco de 2 pasos
    # (40 -> 80, una curva que falta en un llano) no avisa; la carta sigue
    # detectandose bien, la cota rara solo se denuncia.
    cotas = [0, 20, 40, 80, 100, 120, 140, 160, 180, 200, 220, 742]
    curvas = gdf(*[cuadrado(x * 100, 0, 10) for x in range(len(cotas))], ELEVACION=cotas)
    assert geo.detectar_cota(curvas) == ("ELEVACION", 20)
    out = capsys.readouterr().out
    assert "1 cota(s) de 'ELEVACION' fuera de la escalera" in out
    assert "742 (salto 522 m desde 220, 1 entidad(es))  fuera de escalera" in out
    assert " 80 (" not in out


def test_detectar_cota_sin_escalera_falla(gdf, cuadrado):
    curvas = gdf(
        cuadrado(0, 0, 10),
        cuadrado(100, 0, 10),
        cuadrado(200, 0, 10),
        cuadrado(300, 0, 10),
        COTA=[1, 4, 9, 15],
    )
    with pytest.raises(ValueError, match="ninguna columna con cotas en escalera"):
        geo.detectar_cota(curvas)


# ---- detectar_campo ----------------------------------------------------------


def test_detectar_campo_encuentra_vocabulario(gdf, cuadrado):
    hidro = gdf(
        cuadrado(0, 0, 10), cuadrado(100, 0, 10), COND=["perenne", "intermitente"]
    )
    assert geo.detectar_campo(hidro, {"PERENNE", "INTERMITENTE"}) == "COND"


def test_detectar_campo_sin_vocabulario_falla(gdf, cuadrado):
    hidro = gdf(cuadrado(), COND=["x"])
    with pytest.raises(ValueError, match="ninguna columna contiene"):
        geo.detectar_campo(hidro, {"PERENNE"})


def test_guardar_puntos_con_longitud_geografica_no_avisa(tmp_path, capsys):
    # LONGITUD en una capa de puntos es la coordenada, no un largo
    pts = gpd.GeoDataFrame(
        {"LATITUD": [20.6], "LONGITUD": [-103.4]}, geometry=[Point(650000, 2280000)], crs=32613
    )
    geo.guardar(pts, tmp_path / "p.gpkg")
    assert "no cuadra" not in capsys.readouterr().out
