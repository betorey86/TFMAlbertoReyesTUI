"""
Piloto de geocodificación de las VUT de Galicia contra Cartociudad (IGN).

Cartociudad es el geocodificador oficial español: combina el callejero del IGN, el Catastro
y la base de Correos. A diferencia del servicio del Catastro —que indexa estrictamente por
vía y portal— resuelve también topónimos y entidades de población, que es justo lo que
abunda en el registro gallego ("LUGAR DE PEREIRIÑA", "HURRAQUIÑA").

Este script NO procesa el lote completo. Usa **la misma muestra** que
`geocode_catastro_galicia_piloto.py` (misma semilla y mismo filtro) para poder comparar los
dos geocodificadores dirección a dirección.

Servicio: https://www.cartociudad.es/geocoder/api/geocoder/find
Devuelve `lat`/`lng` ya en EPSG:4326, así que no hace falta reproyectar; aun así se valida
que los valores estén en rango geográfico antes de darlos por buenos.

Campo `type` de la respuesta, que indica la precisión alcanzada:
    portal      — punto de portal concreto. La mejor.
    callejero   — eje de vía: sitúa en la calle, sin el número exacto.
    poblacion / entidad — núcleo de población.
    municipio   — centroide municipal. Precisión falsa para densidad, se descarta.

Uso:
    python etl/transform/geocode_cartociudad_galicia_piloto.py
    python etl/transform/geocode_cartociudad_galicia_piloto.py --muestra 300 --semilla 42
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Se reutiliza la limpieza de direcciones del piloto de Catastro para que la comparación
# sea justa: ambos geocodificadores reciben exactamente el mismo texto de partida.
from geocode_catastro_galicia_piloto import (
    RE_COLA,
    RE_NUMERO,
    RE_PARENTESIS,
    normalizar,
    sin_articulos,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
CACHE_DIR = PROCESSED_DIR / "geocache"

ENTRADA = PROCESSED_DIR / "vut_normalizado_galicia.csv"
PILOTO_CATASTRO = PROCESSED_DIR / "vut_galicia_piloto_geocodificado.csv"
CACHE_PATH = CACHE_DIR / "cartociudad_galicia_piloto.jsonl"
SALIDA = PROCESSED_DIR / "vut_galicia_piloto_cartociudad.csv"

BASE = "https://www.cartociudad.es/geocoder/api/geocoder/find"
UA = {"User-Agent": "TFM-TUI-Dashboard/0.1 (proyecto academico TFM)"}

DELAY = 0.3
TIMEOUT = 30
CAJA_GALICIA = (41.7, 43.9, -9.4, -6.7)

# Tipos que sitúan de verdad. `municipio` se rechaza: devolvería el centroide del concello
# para todas sus viviendas, que es exactamente el sesgo que queremos evitar en el mapa de
# densidad.
TIPOS_ACEPTADOS = {"portal", "callejero", "poblacion", "entidad", "toponimo"}


def limpiar_direccion(direccion: object) -> str:
    """Misma limpieza que el piloto de Catastro: quita paréntesis, planta y puerta."""
    s = RE_PARENTESIS.sub(" ", str(direccion))
    m = RE_NUMERO.search(s)
    numero = m.group(1) if m else ""
    if m:
        s = s[: m.start()]
    s = RE_COLA.sub(" ", s)
    s = re.sub(r"\bs/?n\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"[,\s]+$", "", s.strip())
    s = re.sub(r"\s+", " ", s).strip(" ,-")
    return f"{s} {numero}".strip() if numero else s


def consultas(fila: pd.Series) -> list[str]:
    """Consultas a probar, de más a menos específica."""
    limpia = limpiar_direccion(fila.get("direccion", ""))
    municipio = str(fila.get("municipio", "")).strip()
    provincia = str(fila.get("provincia", "")).strip()
    if not limpia:
        return []

    lista = [f"{limpia}, {municipio}, {provincia}"]

    # Sin número: si el portal no está en el callejero, el eje de vía ya sirve.
    sin_num = re.sub(r"\s+\d+$", "", limpia).strip()
    if sin_num and sin_num != limpia:
        lista.append(f"{sin_num}, {municipio}, {provincia}")
    return lista


# ---------------------------------------------------------------------------
# Caché
# ---------------------------------------------------------------------------

def cargar_cache() -> dict[str, dict]:
    if not CACHE_PATH.exists():
        return {}
    cache = {}
    with CACHE_PATH.open(encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                reg = json.loads(linea)
            except ValueError:
                continue
            cache[reg["clave"]] = reg
    return cache


def anexar_cache(registro: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------

def consultar(q: str, sesion: requests.Session) -> dict | None:
    """Devuelve el mejor resultado de Cartociudad, o None si no hay ninguno."""
    try:
        r = sesion.get(BASE, params={"q": q}, headers=UA, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    # 204 = sin coincidencias; el cuerpo vacío también aparece con 200 en algunos casos.
    if r.status_code != 200 or not r.text.strip():
        return None
    try:
        datos = r.json()
    except ValueError:
        return None
    if isinstance(datos, list):
        datos = datos[0] if datos else None
    return datos if isinstance(datos, dict) else None


def resolver(fila: pd.Series, sesion: requests.Session) -> dict:
    clave = f"{fila['provincia']}|{fila['municipio']}|{fila['direccion']}"
    reg = {"clave": clave, "lat": None, "lon": None, "tipo": None, "muni": None,
           "address": None, "portal": None, "motivo": None,
           "fecha": datetime.now(timezone.utc).isoformat()}

    lista = consultas(fila)
    if not lista:
        reg["motivo"] = "sin direccion en origen"
        return reg

    for q in lista:
        datos = consultar(q, sesion)
        time.sleep(DELAY)
        if not datos:
            continue

        tipo = str(datos.get("type") or "").lower()
        lat, lon = datos.get("lat"), datos.get("lng")
        if lat is None or lon is None:
            continue
        if tipo not in TIPOS_ACEPTADOS:
            reg["motivo"] = f"precision insuficiente (type={tipo})"
            reg["tipo"] = tipo
            continue
        if not (CAJA_GALICIA[0] <= lat <= CAJA_GALICIA[1]
                and CAJA_GALICIA[2] <= lon <= CAJA_GALICIA[3]):
            reg["motivo"] = "coordenada fuera de Galicia"
            continue

        # Comprobación imprescindible: Cartociudad hace coincidencia difusa y, si no
        # encuentra la vía en el municipio pedido, devuelve una homónima de otro sin
        # avisar. En el piloto, los casos con municipio distinto estaban a una mediana de
        # 71 km del resultado del Catastro; los que coincidían, a 38 m.
        muni = datos.get("muni")
        if not sin_articulos(muni) or sin_articulos(muni) != sin_articulos(fila["municipio"]):
            reg["motivo"] = f"municipio devuelto distinto ({muni})"
            reg["muni"] = muni
            continue

        reg.update({"lat": float(lat), "lon": float(lon), "tipo": tipo,
                    "muni": muni, "address": datos.get("address"),
                    "portal": datos.get("portalNumber"), "motivo": None})
        return reg

    if not reg["motivo"]:
        reg["motivo"] = "sin coincidencias"
    return reg


# ---------------------------------------------------------------------------
# Comparación con el piloto de Catastro
# ---------------------------------------------------------------------------

def comparar_con_catastro(muestra: pd.DataFrame) -> None:
    if not PILOTO_CATASTRO.exists():
        print("\n  (No hay piloto de Catastro con el que comparar.)")
        return

    cat = pd.read_csv(PILOTO_CATASTRO, low_memory=False)
    clave = ["provincia", "municipio", "direccion"]
    columnas = clave + [c for c in ("lat_catastro", "lon_catastro") if c in cat.columns]
    cat_ok = cat[columnas].copy()
    cat_ok["catastro_ok"] = cat_ok["lat_catastro"].notna()

    fusion = muestra.merge(cat_ok.drop_duplicates(clave), on=clave, how="left")
    if fusion["catastro_ok"].isna().all():
        print("\n  (Las muestras no coinciden; no se puede comparar dirección a dirección.)")
        return

    fusion["catastro_ok"] = fusion["catastro_ok"].fillna(False)
    fusion["cc_ok"] = fusion["lat_cartociudad"].notna()

    ambos = int((fusion["cc_ok"] & fusion["catastro_ok"]).sum())
    solo_cc = int((fusion["cc_ok"] & ~fusion["catastro_ok"]).sum())
    solo_cat = int((~fusion["cc_ok"] & fusion["catastro_ok"]).sum())
    ninguno = int((~fusion["cc_ok"] & ~fusion["catastro_ok"]).sum())
    n = len(fusion)

    print("\n" + "-" * 72)
    print("  COMPARATIVA SOBRE LAS MISMAS DIRECCIONES")
    print("-" * 72)
    print(f"    Resuelven ambos:          {ambos:>5}  ({100 * ambos / n:.1f} %)")
    print(f"    Sólo Cartociudad:         {solo_cc:>5}  ({100 * solo_cc / n:.1f} %)")
    print(f"    Sólo Catastro:            {solo_cat:>5}  ({100 * solo_cat / n:.1f} %)")
    print(f"    Ninguno:                  {ninguno:>5}  ({100 * ninguno / n:.1f} %)")
    print(f"\n    Cobertura Catastro:      {100 * fusion['catastro_ok'].mean():>5.1f} %")
    print(f"    Cobertura Cartociudad:   {100 * fusion['cc_ok'].mean():>5.1f} %")
    union = int((fusion["cc_ok"] | fusion["catastro_ok"]).sum())
    print(f"    Cobertura combinada:     {100 * union / n:>5.1f} %")

    # Distancia entre ambos cuando los dos resuelven: mide si concuerdan.
    dobles = fusion[fusion["cc_ok"] & fusion["catastro_ok"]].copy()
    if len(dobles) and {"lat_catastro", "lon_catastro"} <= set(dobles.columns):
        cat_lat = pd.to_numeric(dobles["lat_catastro"], errors="coerce")
        cat_lon = pd.to_numeric(dobles["lon_catastro"], errors="coerce")
        if cat_lat.notna().any() and cat_lon.notna().any():
            # Aproximación plana suficiente a estas distancias: 1º de latitud ~111,3 km y
            # 1º de longitud ~0,73 de eso a la latitud de Galicia (43º).
            dlat = (dobles["lat_cartociudad"] - cat_lat).abs() * 111_320
            dlon = (dobles["lon_cartociudad"] - cat_lon).abs() * 111_320 * 0.73
            dist = ((dlat**2 + dlon**2) ** 0.5).dropna()
            if len(dist):
                print(f"\n    Distancia entre ambos (n={len(dist):,}): "
                      f"mediana {dist.median():.0f} m, p90 {dist.quantile(0.9):.0f} m")
                print(f"    A menos de 100 m: {100 * (dist < 100).mean():.1f} %")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Piloto: geocodifica una muestra de VUT de Galicia contra Cartociudad."
    )
    parser.add_argument("--muestra", type=int, default=300)
    parser.add_argument("--semilla", type=int, default=42,
                        help="Misma semilla que el piloto de Catastro, para comparar.")
    args = parser.parse_args()

    if not ENTRADA.exists():
        print(f"ERROR: falta {ENTRADA}", file=sys.stderr)
        return 1

    df = pd.read_csv(ENTRADA, low_memory=False)
    sin_coord = df[df["lat"].isna()]
    print(f"Galicia: {len(df):,} registros, {len(sin_coord):,} sin coordenadas")

    muestra = sin_coord.sample(n=min(args.muestra, len(sin_coord)), random_state=args.semilla)
    print(f"Muestra aleatoria: {len(muestra)} (semilla {args.semilla})\n")

    cache = cargar_cache()
    sesion = requests.Session()
    inicio = time.time()
    resultados = []

    for i, (_, fila) in enumerate(muestra.iterrows(), start=1):
        clave = f"{fila['provincia']}|{fila['municipio']}|{fila['direccion']}"
        if clave in cache:
            resultados.append(cache[clave])
            continue
        reg = resolver(fila, sesion)
        cache[clave] = reg
        anexar_cache(reg)
        resultados.append(reg)
        if i % 50 == 0:
            ok = sum(1 for r in resultados if r["lat"] is not None)
            print(f"  [{i}/{len(muestra)}] {ok} resueltas ({100 * ok / len(resultados):.0f}%)")

    muestra = muestra.copy()
    muestra["lat_cartociudad"] = [r["lat"] for r in resultados]
    muestra["lon_cartociudad"] = [r["lon"] for r in resultados]
    muestra["cartociudad_tipo"] = [r["tipo"] for r in resultados]
    muestra["cartociudad_muni"] = [r["muni"] for r in resultados]
    muestra["cartociudad_via"] = [r["address"] for r in resultados]
    muestra["cartociudad_motivo"] = [r["motivo"] for r in resultados]

    # Coincidencia de municipio: el registro escribe "O CAMPO LAMEIRO" y el IGN
    # "Campo Lameiro", así que se compara sin artículos ni acentos.
    coincide = [
        bool(sin_articulos(m)) and sin_articulos(m) == sin_articulos(d)
        for m, d in zip(muestra["municipio"], muestra["cartociudad_muni"])
    ]
    muestra["cartociudad_muni_coincide"] = coincide

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    muestra.to_csv(SALIDA, index=False, encoding="utf-8")

    # ---------------- Resumen ----------------
    total = len(muestra)
    ok = int(muestra["lat_cartociudad"].notna().sum())
    con_muni = int((muestra["lat_cartociudad"].notna()
                    & muestra["cartociudad_muni_coincide"]).sum())
    duracion = time.time() - inicio

    print("\n" + "=" * 72)
    print("PILOTO — CARTOCIUDAD (IGN) / GALICIA")
    print("=" * 72)
    print(f"  Muestra:                  {total:>6,}")
    print(f"  Geocodificadas:           {ok:>6,}  ({100 * ok / total:.1f} %)")
    print(f"  Con municipio coincidente:{con_muni:>6,}  ({100 * con_muni / total:.1f} %)")
    print(f"  Sin resolver:             {total - ok:>6,}  ({100 * (total - ok) / total:.1f} %)")
    print(f"\n  Duración: {duracion / 60:.1f} min ({duracion / total:.1f} s por dirección)")
    print(f"  Extrapolado a las 28.253 pendientes: "
          f"{28253 * duracion / total / 3600:.1f} h")

    tipos = Counter(t for t in muestra.loc[muestra["lat_cartociudad"].notna(),
                                           "cartociudad_tipo"] if t)
    if tipos:
        print("\n  Precisión alcanzada:")
        for tipo, n in tipos.most_common():
            print(f"    {n:>5}  ({100 * n / total:>4.1f} %)  {tipo}")

    motivos = Counter(m for m in muestra["cartociudad_motivo"] if m)
    if motivos:
        print("\n  Motivos de fallo:")
        for motivo, n in motivos.most_common(8):
            print(f"    {n:>5}  ({100 * n / total:>4.1f} %)  {motivo[:56]}")

    comparar_con_catastro(muestra)

    print(f"\n  Salida: {SALIDA.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
