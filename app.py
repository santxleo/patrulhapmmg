"""Protótipo acadêmico de apoio ao policiamento preventivo da 39ª CIA PM.

Execute com: streamlit run app.py
Os arquivos de dados podem ficar em ./upload/ ou ser enviados pela interface.
"""

from __future__ import annotations

import json
import math
import hashlib
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Fullscreen
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import GeometryCollection, Point, Polygon, shape, mapping
from shapely.ops import transform as transform_geometry, unary_union
from shapely.strtree import STRtree
from streamlit_folium import st_folium


st.set_page_config(page_title="Patrulha 39 | PMMG", page_icon="🚓", layout="wide")

PESOS = {"A": 4, "B": 5, "C": 1, "D": 1, "E": 2, "F": 3}
CORES_PRIORIDADE = {
    "Baixa": "#d9e2ec",
    "Média": "#f6d365",
    "Alta": "#f39c3d",
    "Muito alta": "#d9534f",
}
CORES_VIATURA = [
    "#155eef", "#7a3e9d", "#00875a", "#c4320a", "#027a9b",
    "#b54708", "#344054", "#c11574", "#4e5ba6", "#087e8b",
]
CRS_METRICO = "EPSG:31983"  # SIRGAS 2000 / UTM 23S, adequado à área de Belo Horizonte.


@st.cache_data(show_spinner="Lendo e preparando os dados...")
def carregar_ocorrencias(conteudo: bytes) -> pd.DataFrame:
    bruto = json.loads(conteudo.decode("utf-8-sig"))
    if isinstance(bruto, dict):
        registros = bruto.get("dados", [])
    elif bruto and isinstance(bruto[0], dict) and "dados" in bruto[0]:
        registros = bruto[0]["dados"]
    else:
        registros = bruto
    linhas = []
    for item in registros:
        latlon = item.get("latLon", {})
        try:
            lat, lon = float(latlon["lat"]), float(latlon["lon"])
            data = pd.to_datetime(item.get("dataHora"), dayfirst=True, errors="coerce")
            natureza = str(item.get("natureza", "")).strip().upper()
            if pd.isna(data) or natureza not in PESOS:
                continue
            linhas.append({
                "natureza": natureza,
                "data_hora": data,
                "hora": int(data.hour),
                "lat": lat,
                "lon": lon,
                "peso": PESOS[natureza],
            })
        except (TypeError, ValueError, KeyError):
            continue
    if not linhas:
        raise ValueError("Não encontrei ocorrências com os campos esperados.")
    return pd.DataFrame(linhas)


@st.cache_data(show_spinner="Construindo a malha hexagonal e pontuando as áreas...")
def preparar_malha(_ocorrencias: pd.DataFrame, geojson_bytes: bytes,
                   ocorrencias_hash: str) -> tuple[list[dict], dict, dict, dict]:
    """Cria hexágonos de 200 m, atribui ocorrências e calcula prioridades e horários."""
    doc = json.loads(geojson_bytes.decode("utf-8-sig"))
    feature = doc["features"][0]
    limite_wgs84 = make_valid(shape(feature["geometry"]))
    para_metros = Transformer.from_crs("EPSG:4326", CRS_METRICO, always_xy=True).transform
    para_graus = Transformer.from_crs(CRS_METRICO, "EPSG:4326", always_xy=True).transform
    limite_m = transform_geometry(para_metros, limite_wgs84)

    # Hexágonos pointy-top com raio de 100 m: 200 m entre vértices opostos.
    # Os centros seguem a malha axial correta: passo horizontal sqrt(3)R,
    # passo vertical 1,5R e deslocamento horizontal de metade do passo.
    raio = 100.0
    passo_x, passo_y = math.sqrt(3) * raio, 1.5 * raio
    minx, miny, maxx, maxy = limite_m.bounds
    linhas = []
    y = miny - passo_y
    row = 0
    while y <= maxy + passo_y:
        x = minx - passo_x + (passo_x / 2 if row % 2 else 0)
        col = 0
        while x <= maxx + passo_x:
            hex_full = Polygon([
                (x + raio * math.cos(math.radians(30 + 60 * k)),
                 y + raio * math.sin(math.radians(30 + 60 * k)))
                for k in range(6)
            ])
            if hex_full.intersects(limite_m):
                clip = hex_full.intersection(limite_m)
                if not clip.is_empty:
                    linhas.append({"id": f"H{row:03d}-{col:03d}", "geometry": clip,
                                   "centro": clip.representative_point(), "score": 0.0})
            x += passo_x
            col += 1
        y += passo_y
        row += 1

    # STRtree permite localizar rapidamente o hexágono que contém cada ocorrência.
    geometrias = [h["geometry"] for h in linhas]
    arvore = STRtree(geometrias)
    id_por_indice = [h["id"] for h in linhas]
    pontos_m = [transform_geometry(para_metros, Point(row.lon, row.lat))
                for row in _ocorrencias.itertuples(index=False)]
    score_hora: dict[int, dict[str, float]] = {h: {} for h in range(24)}
    score_total: dict[str, float] = {h["id"]: 0.0 for h in linhas}
    contagem_total: dict[str, int] = {h["id"]: 0 for h in linhas}
    ocorrencias_mapeadas = 0

    for occ, ponto in zip(_ocorrencias.itertuples(index=False), pontos_m):
        candidatos = arvore.query(ponto)
        escolhido = None
        for candidato in candidatos:
            i = int(candidato)
            if geometrias[i].covers(ponto):
                escolhido = id_por_indice[i]
                break
        if escolhido is None:
            continue
        ocorrencias_mapeadas += 1
        peso = float(occ.peso)
        score_total[escolhido] += peso
        contagem_total[escolhido] += 1
        bucket = score_hora[int(occ.hora)]
        bucket[escolhido] = bucket.get(escolhido, 0.0) + peso

    positivos = sorted(v for v in score_total.values() if v > 0)
    if positivos:
        q50, q75, q90 = (float(pd.Series(positivos).quantile(q)) for q in (0.50, 0.75, 0.90))
    else:
        q50 = q75 = q90 = 0

    for h in linhas:
        hid = h["id"]
        h["score"] = score_total[hid]
        h["ocorrencias"] = contagem_total[hid]
        if score_total[hid] <= 0:
            h["prioridade"] = "Baixa"
        elif score_total[hid] <= q50:
            h["prioridade"] = "Baixa"
        elif score_total[hid] <= q75:
            h["prioridade"] = "Média"
        elif score_total[hid] <= q90:
            h["prioridade"] = "Alta"
        else:
            h["prioridade"] = "Muito alta"

    totais_hora = {
        hora: sum(score_hora[hora].values()) for hora in range(24)
    }
    ordenadas = sorted(range(24), key=lambda h: (-totais_hora[h], h))
    classes = {}
    for posicao, hora in enumerate(ordenadas):
        classe = "Classe 1" if posicao < 8 else ("Classe 2" if posicao < 16 else "Classe 3")
        classes[hora] = {"classe": classe, "ordem": posicao + 1, "pontuacao": totais_hora[hora]}

    # Converter geometrias para GeoJSON em WGS84 para exibição no mapa.
    features = []
    for h in linhas:
        geom_wgs84 = transform_geometry(para_graus, h["geometry"])
        centro_wgs84 = transform_geometry(para_graus, h["centro"])
        features.append({
            "type": "Feature",
            "geometry": mapping(geom_wgs84),
            "properties": {
                "id": h["id"], "score": h["score"], "ocorrencias": h["ocorrencias"],
                "prioridade": h["prioridade"], "centro_lon": centro_wgs84.x,
                "centro_lat": centro_wgs84.y,
            },
        })
    limite_geojson = {"type": "Feature", "geometry": mapping(limite_wgs84), "properties": feature.get("properties", {})}
    centros_m = {h["id"]: h["centro"] for h in linhas}
    return features, limite_geojson, score_hora, {
        "classes": classes,
        "totais_hora": totais_hora,
        "score_total": score_total,
        "centros_m": centros_m,
        "geometrias_m": geometrias,
        "q": (q50, q75, q90),
        "territorio_m": limite_m,
        "ocorrencias_mapeadas": ocorrencias_mapeadas,
        "prioridade": {h["id"]: h["prioridade"] for h in linhas},
    }


def raio_por_faixa(classe: str, ordem: int) -> tuple[float, str]:
    if classe == "Classe 1":
        return 1.0, "Ponto-base de 20 min + 40 min de patrulhamento nas adjacências (até 1 km)."
    if classe == "Classe 2" and ordem <= 12:
        return 1.0, "Ponto-base de 20 min + 40 min de patrulhamento nas adjacências (até 1 km)."
    if classe == "Classe 2":
        return 2.0, "Patrulhamento preventivo no entorno da quadrícula (até 2 km)."
    return 3.0, "Patrulhamento preventivo no entorno das áreas prioritárias (até 3 km)."


def gerar_cartoes(
    ativos: list[str], tipos: dict[str, str], scores_hora: dict[int, dict[str, float]],
    meta: dict,
) -> pd.DataFrame:
    """Distribui focos por hora, ponderando risco, cobertura e tipo de viatura."""
    if not ativos:
        return pd.DataFrame()

    score_total = meta["score_total"]
    centros = meta["centros_m"]
    classes = meta["classes"]
    top_geral = sorted(score_total, key=lambda hid: (-score_total[hid], hid))
    # O fallback da Classe 3 usa focos escolhidos entre as áreas de maior
    # incidência das faixas da Classe 1, conforme a regra do desafio.
    pool_classe1: list[str] = []
    for hora in range(24):
        if classes[hora]["classe"] != "Classe 1":
            continue
        focos = sorted(
            scores_hora[hora],
            key=lambda hid: (-scores_hora[hora][hid], -score_total.get(hid, 0.0), hid),
        )[:max(1, len(ativos))]
        pool_classe1.extend(focos)
    pool_classe1 = list(dict.fromkeys(pool_classe1))
    # Mantém alternativas suficientes para alternar focos sem sair do conjunto
    # de referência da Classe 1; completa com os maiores escores territoriais.
    # O mesmo conjunto alimenta o fallback da Classe 3.
    tamanho_pool = max(2 * len(ativos), len(ativos) + 1)
    for hid in top_geral:
        if len(pool_classe1) >= tamanho_pool:
            break
        if hid not in pool_classe1:
            pool_classe1.append(hid)

    visitas: dict[str, int] = {hid: 0 for hid in score_total}
    saida = []
    focos_hora_anterior: set[str] = set()
    alternancia_anterior = False
    repeticoes_forcadas = 0

    # A alternância se aplica às faixas de ponto-base da Classe 1 e às quatro
    # faixas de ponto-base da Classe 2, conforme as regras do desafio.
    for hora in range(24):
        info = classes[hora]
        alternancia_atual = (info["classe"] == "Classe 1" or
                             (info["classe"] == "Classe 2" and info["ordem"] <= 12))
        risco_hora = dict(scores_hora[hora])
        fallback_classe3 = info["classe"] == "Classe 3" and not risco_hora
        if fallback_classe3:
            risco_hora = {hid: score_total.get(hid, 0.0) for hid in pool_classe1}
        elif not risco_hora:
            risco_hora = {hid: score_total[hid] for hid in top_geral[:max(8, len(ativos))]}
        candidatos = sorted(
            [hid for hid, score in risco_hora.items() if score > 0],
            key=lambda hid: (-risco_hora[hid], -score_total[hid], hid),
        ) or top_geral
        if info["classe"] == "Classe 1":
            candidatos_classe1 = [hid for hid in candidatos if hid in pool_classe1]
            candidatos = candidatos_classe1 or list(pool_classe1)
        if alternancia_atual and alternancia_anterior:
            sem_repeticao = [hid for hid in candidatos if hid not in focos_hora_anterior]
            if sem_repeticao:
                candidatos = sem_repeticao
            else:
                # Se todos os focos horários coincidem com a faixa anterior,
                # amplia a busca para outros hexágonos antes de repetir.
                fonte = pool_classe1 if info["classe"] == "Classe 1" else top_geral
                candidatos = [hid for hid in fonte if hid not in focos_hora_anterior] or candidatos
        if len(candidatos) < len(ativos) and not fallback_classe3:
            # Também amplia quando há menos focos elegíveis do que viaturas,
            # preservando focos distintos no mesmo horário sempre que possível.
            bloqueados = focos_hora_anterior if alternancia_atual and alternancia_anterior else set()
            fonte_extras = pool_classe1 if info["classe"] == "Classe 1" or fallback_classe3 else top_geral
            extras = [hid for hid in fonte_extras
                      if hid not in candidatos and hid not in bloqueados]
            candidatos.extend(extras[:len(ativos) - len(candidatos)])

        usados_nesta_hora: list[str] = []
        para_hora = []
        raio_km, atividade = raio_por_faixa(info["classe"], info["ordem"])

        # Alternar a ordem evita que a mesma viatura receba sempre o primeiro
        # candidato quando há empate ou focos muito próximos.
        deslocamento = hora % len(ativos)
        ordem_viaturas = ativos[deslocamento:] + ativos[:deslocamento]
        for veiculo in ordem_viaturas:
            disponiveis = [hid for hid in candidatos if hid not in usados_nesta_hora]
            if not disponiveis:
                # Só ocorre em territórios com menos células do que viaturas.
                # Mantém o cartão utilizável e registra a exceção.
                disponiveis = candidatos

            def utilidade(hid: str) -> float:
                max_risco = max((risco_hora.get(x, score_total.get(x, 0.0)) for x in candidatos), default=1.0) or 1.0
                risco = risco_hora.get(hid, score_total.get(hid, 0.0)) / max_risco
                cobertura = 0.0
                if usados_nesta_hora:
                    dist = min(centros[hid].distance(centros[x]) for x in usados_nesta_hora)
                    cobertura = min(dist / 2000.0, 1.0)
                tipo = tipos.get(veiculo, "Policiamento preventivo")
                if tipo == "Atendimento + prevenção":
                    peso_risco, peso_cobertura = 0.80, 0.20
                else:
                    peso_risco, peso_cobertura = 0.55, 0.45
                concentracao = 1 + 0.6 * visitas.get(hid, 0)
                return (peso_risco * risco + peso_cobertura * cobertura) / concentracao

            escolhido = max(disponiveis, key=utilidade)
            if alternancia_atual and alternancia_anterior and escolhido in focos_hora_anterior:
                repeticoes_forcadas += 1
            visitas[escolhido] = visitas.get(escolhido, 0) + 1
            usados_nesta_hora.append(escolhido)
            para_hora.append((veiculo, escolhido))

        contagem_focos = {hid: sum(1 for _, escolhido in para_hora if escolhido == hid)
                          for _, hid in para_hora}
        for veiculo, hid in para_hora:
            score = risco_hora.get(hid, score_total.get(hid, 0.0))
            notas = []
            if alternancia_atual and alternancia_anterior and hid in focos_hora_anterior:
                notas.append("Alternância limitada: não havia foco alternativo elegível.")
            if contagem_focos.get(hid, 0) > 1:
                notas.append("Foco repetido por falta de quadrículas distintas disponíveis.")
            saida.append({
                "Viatura": veiculo,
                "Tipo": tipos.get(veiculo, "Policiamento preventivo"),
                "Horário": f"{hora:02d}:00–{hora:02d}:59",
                "Classe": info["classe"],
                "Ordem de incidência": info["ordem"],
                "Hexágono foco": hid,
                "Prioridade": meta["prioridade"][hid],
                "Incidência ponderada na faixa": round(score, 2),
                "Raio de patrulhamento (km)": raio_km,
                "Atividade sugerida": atividade,
                "Observação": " ".join(notas),
            })
        focos_hora_anterior = set(usados_nesta_hora)
        alternancia_anterior = alternancia_atual

    resultado = pd.DataFrame(saida)
    resultado.attrs["repeticoes_forcadas"] = repeticoes_forcadas
    return resultado


def cobertura_horaria(cartoes: pd.DataFrame, hora: int, meta: dict) -> dict:
    """Calcula a cobertura indicativa dos raios, recortada ao território."""
    territorio = meta["territorio_m"]
    faixa = f"{hora:02d}:00–{hora:02d}:59"
    recortes = []
    if not cartoes.empty:
        focos = cartoes[cartoes["Horário"] == faixa]
        for _, row in focos.iterrows():
            centro = meta["centros_m"].get(row["Hexágono foco"])
            if centro is None:
                continue
            raio_m = float(row["Raio de patrulhamento (km)"]) * 1000
            recortes.append(centro.buffer(raio_m).intersection(territorio))
    geometria = unary_union(recortes) if recortes else GeometryCollection()
    porcentagem = 100.0 * geometria.area / territorio.area if territorio.area else 0.0
    alta = [hid for hid, p in meta["prioridade"].items() if p in ("Alta", "Muito alta")]
    fora_prioritaria = 0
    for hid in alta:
        ponto = meta["centros_m"].get(hid)
        if ponto is not None and not geometria.covers(ponto):
            fora_prioritaria += 1
    return {"geometria": geometria, "porcentagem": porcentagem,
            "area_km2": geometria.area / 1_000_000,
            "prioridade_fora": fora_prioritaria, "total_prioridade": len(alta)}


def geometria_para_geojson(geometria) -> dict:
    para_graus = Transformer.from_crs(CRS_METRICO, "EPSG:4326", always_xy=True).transform
    convertido = transform_geometry(para_graus, geometria)
    return {"type": "Feature", "geometry": mapping(convertido), "properties": {}}


def construir_mapa(features: list[dict], limite: dict, ocorrencias: pd.DataFrame,
                   cartoes: pd.DataFrame, hora_selecionada: int, mostrar_ocorrencias: bool,
                   meta: dict, cobertura: dict, cobertura_perdida: dict) -> folium.Map:
    centros = [f["properties"] for f in features]
    lat0 = sum(p["centro_lat"] for p in centros) / max(len(centros), 1)
    lon0 = sum(p["centro_lon"] for p in centros) / max(len(centros), 1)
    mapa = folium.Map(location=[lat0, lon0], zoom_start=13, tiles="OpenStreetMap", control_scale=True)
    Fullscreen(position="topright").add_to(mapa)

    folium.GeoJson(
        limite,
        name="Limite da 39ª CIA PM",
        style_function=lambda _: {"color": "#182230", "weight": 3, "fillOpacity": 0.02},
        tooltip="Limite territorial da 39ª CIA PM",
    ).add_to(mapa)

    folium.GeoJson(
        {"type": "FeatureCollection", "features": features},
        style_function=lambda f: {
            "fillColor": CORES_PRIORIDADE.get(f["properties"]["prioridade"], "#d9e2ec"),
            "color": "#475467", "weight": 0.35,
            "fillOpacity": 0.58 if f["properties"]["score"] > 0 else 0.16,
        },
        tooltip=folium.GeoJsonTooltip(fields=["id", "prioridade", "score", "ocorrencias"],
                                      aliases=["Hexágono", "Prioridade", "Pontuação ponderada", "Ocorrências"]),
        name="Hexágonos e prioridade",
    ).add_to(mapa)

    if mostrar_ocorrencias:
        grupo_ocorrencias = folium.FeatureGroup(name="Ocorrências", show=True)
        # Amostra visual para o mapa continuar ágil; a análise usa todos os registros.
        amostra = ocorrencias if len(ocorrencias) <= 900 else ocorrencias.sample(900, random_state=7)
        for row in amostra.itertuples(index=False):
            folium.CircleMarker([row.lat, row.lon], radius=2, color="#1d2939", weight=0.4,
                                fill=True, fill_opacity=0.55, tooltip=f"Natureza {row.natureza} · peso {row.peso}").add_to(grupo_ocorrencias)
        grupo_ocorrencias.add_to(mapa)

    foco = cartoes[cartoes["Horário"] == f"{hora_selecionada:02d}:00–{hora_selecionada:02d}:59"] if not cartoes.empty else cartoes
    props_por_id = {f["properties"]["id"]: f["properties"] for f in features}
    for _, row in foco.iterrows():
        props = props_por_id.get(row["Hexágono foco"], {})
        lat, lon = props.get("centro_lat"), props.get("centro_lon")
        if lat is None:
            continue
        try:
            numero = int(str(row["Viatura"]).split("-")[-1]) - 1
        except ValueError:
            numero = 0
        cor = CORES_VIATURA[numero % len(CORES_VIATURA)]
        centro_m = meta["centros_m"].get(row["Hexágono foco"])
        if centro_m is not None:
            raio_m = float(row["Raio de patrulhamento (km)"]) * 1000
            area = centro_m.buffer(raio_m).intersection(meta["territorio_m"])
            grupo = folium.FeatureGroup(name=f"Área sugerida · {row['Viatura']}", show=True)
            folium.GeoJson(geometria_para_geojson(area), style_function=lambda _, c=cor: {
                "color": c, "weight": 1.5, "fillColor": c, "fillOpacity": 0.08
            }, tooltip=f"Área sugerida: {row['Viatura']} · raio indicativo de {row['Raio de patrulhamento (km)']} km").add_to(grupo)
            grupo.add_to(mapa)
        folium.Marker([lat, lon], tooltip=f"{row['Viatura']} · {row['Classe']} · foco {row['Hexágono foco']}",
                      icon=folium.DivIcon(html=f"<div style='background:{cor};color:white;border:2px solid white;border-radius:50%;width:28px;height:28px;text-align:center;line-height:24px;font-weight:bold;box-shadow:0 1px 4px #555'>{numero + 1}</div>")).add_to(mapa)

    if not cobertura["geometria"].is_empty:
        folium.GeoJson(geometria_para_geojson(cobertura["geometria"]),
                       name="Cobertura sugerida total",
                       style_function=lambda _: {"color": "#175cd3", "weight": 1,
                                                 "fillColor": "#528bff", "fillOpacity": 0.08},
                       tooltip=f"Cobertura indicativa: {cobertura['porcentagem']:.1f}% do território").add_to(mapa)
    if not cobertura_perdida["geometria"].is_empty:
        folium.GeoJson(geometria_para_geojson(cobertura_perdida["geometria"]),
                       name="Área antes coberta e agora descoberta",
                       style_function=lambda _: {"color": "#d92d20", "weight": 1.2,
                                                 "fillColor": "#f97066", "fillOpacity": 0.28},
                       tooltip="Área coberta no cenário inicial e sem cobertura sugerida no cenário atual").add_to(mapa)
    sem_cobertura = meta["territorio_m"].difference(cobertura["geometria"])
    if not sem_cobertura.is_empty:
        folium.GeoJson(geometria_para_geojson(sem_cobertura),
                       name="Território sem cobertura sugerida",
                       style_function=lambda _: {"color": "#667085", "weight": 0.4,
                                                 "fillColor": "#98a2b3", "fillOpacity": 0.10},
                       tooltip="Área sem cobertura pelos raios indicativos deste cenário").add_to(mapa)
    # Inicia recolhido para não cobrir a área do mapa; o usuário ainda pode
    # abrir o controle quando quiser ativar ou ocultar camadas.
    folium.LayerControl(collapsed=True, position="topright").add_to(mapa)
    return mapa


def carregar_arquivo_local(nome: str) -> bytes | None:
    caminhos = [Path("upload") / nome, Path(nome)]
    for caminho in caminhos:
        if caminho.exists():
            return caminho.read_bytes()
    return None


st.title("Patrulha 39")
st.caption("Protótipo acadêmico de apoio ao policiamento preventivo orientado por dados")
st.info("A distribuição é uma sugestão de planejamento baseada no histórico. Ela não substitui a avaliação e a decisão dos profissionais da PMMG.")

with st.sidebar:
    st.header("Dados de entrada")
    occ_default = carregar_arquivo_local("ocorrencias.txt")
    geo_default = carregar_arquivo_local("39_cia.geojson")
    up_occ = st.file_uploader("Base de ocorrências (JSON/TXT)", type=["json", "txt"])
    up_geo = st.file_uploader("Limite territorial (GeoJSON)", type=["geojson", "json"])

    if up_occ is not None:
        occ_bytes = up_occ.getvalue()
    elif occ_default is not None:
        occ_bytes = occ_default
        st.caption("Base carregada de ./upload/ocorrencias.txt")
    else:
        occ_bytes = None

    if up_geo is not None:
        geo_bytes = up_geo.getvalue()
    elif geo_default is not None:
        geo_bytes = geo_default
        st.caption("Limite carregado de ./upload/39_cia.geojson")
    else:
        geo_bytes = None

if not occ_bytes or not geo_bytes:
    st.warning("Envie os dois arquivos pela barra lateral ou coloque-os na pasta upload/ ao lado de app.py.")
    st.stop()

try:
    ocorrencias = carregar_ocorrencias(occ_bytes)
    hex_features, limite, scores_hora, meta = preparar_malha(
        ocorrencias, geo_bytes, hashlib.sha1(occ_bytes).hexdigest()
    )
except Exception as erro:
    st.error(f"Não consegui preparar os dados: {erro}")
    st.stop()

with st.sidebar:
    st.header("Cenário operacional")
    quantidade = st.slider("Quantidade de viaturas no cenário", min_value=1, max_value=10, value=8)
    nomes = [f"V-{i:02d}" for i in range(1, quantidade + 1)]
    tipos = {}
    with st.expander("Tipo de cada viatura", expanded=False):
        for nome in nomes:
            tipos[nome] = st.selectbox(nome, ["Policiamento preventivo", "Atendimento + prevenção"], key=f"tipo_{nome}")
    indisponiveis = st.multiselect("Marcar viaturas empenhadas", nomes, help="As viaturas escolhidas saem do plano preventivo; os cartões são recalculados.")
    ativos = [v for v in nomes if v not in indisponiveis]
    if not ativos:
        st.warning("Todas as viaturas estão indisponíveis. O mapa continuará mostrando as áreas prioritárias, sem cartões ativos.")
    hora = st.select_slider("Faixa horária exibida no mapa", options=list(range(24)), value=18,
                            format_func=lambda h: f"{h:02d}:00")
    mostrar_pontos = st.checkbox("Exibir ocorrências no mapa (amostra de até 900)", value=False)

cards = gerar_cartoes(ativos, tipos, scores_hora, meta) if ativos else pd.DataFrame()
cards_referencia = gerar_cartoes(nomes, tipos, scores_hora, meta)
cobertura_atual = cobertura_horaria(cards, hora, meta)
cobertura_referencia = cobertura_horaria(cards_referencia, hora, meta)
cobertura_perdida = {
    "geometria": cobertura_referencia["geometria"].difference(cobertura_atual["geometria"])
}
totais_hora = meta["totais_hora"]
classes = meta["classes"]
fora_da_malha = max(0, len(ocorrencias) - meta["ocorrencias_mapeadas"])

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Ocorrências analisadas", f"{len(ocorrencias):,}".replace(",", "."))
c2.metric("Hexágonos na área", f"{len(hex_features):,}".replace(",", "."))
c3.metric("Viaturas em patrulhamento", f"{len(ativos)} de {quantidade}")
c4.metric("Cobertura sugerida", f"{cobertura_atual['porcentagem']:.1f}%",
          delta=f"{cobertura_atual['porcentagem'] - cobertura_referencia['porcentagem']:+.1f} p.p. vs. cenário inicial")
c5.metric("Ocorrências fora da malha", f"{fora_da_malha}")
st.caption(f"Faixa exibida: {hora:02d}:00–{hora:02d}:59 · {classes[hora]['classe']} · {totais_hora[hora]:.0f} pontos ponderados.")
if indisponiveis:
    st.warning("Redistribuição recalculada. As camadas do mapa mostram a cobertura atual, as áreas que perderam cobertura e os trechos ainda sem cobertura sugerida.")

tab_mapa, tab_cartoes, tab_dados = st.tabs(["Mapa e cobertura", "Cartões-Programa", "Análise dos dados"])
with tab_mapa:
    col_mapa, col_legenda = st.columns([4, 1])
    with col_legenda:
        st.subheader("Legenda")
        for nome, cor in CORES_PRIORIDADE.items():
            st.markdown(f"<div style='display:flex;align-items:center;gap:8px;margin:7px 0'><span style='width:15px;height:15px;background:{cor};display:inline-block;border:1px solid #667085'></span>{nome}</div>", unsafe_allow_html=True)
        st.caption("Marcadores numerados: foco de cada viatura na faixa selecionada. As áreas coloridas mostram os raios sugeridos, recortados ao território.")
        st.markdown("**Método de prioridade**")
        st.caption("A malha usa hexágonos de 200 m entre vértices opostos. A prioridade soma os pesos A–F e separa as áreas com registros pelos percentis 50, 75 e 90; áreas sem registros ficam como baixa prioridade.")
        st.metric("Área antes coberta e agora descoberta", f"{cobertura_perdida['geometria'].area / 1_000_000:.2f} km²")
        st.metric("Células de alta prioridade fora da cobertura", f"{cobertura_atual['prioridade_fora']} de {cobertura_atual['total_prioridade']}")
        st.caption("A cobertura é uma estimativa geométrica pelos raios sugeridos, recortada ao limite territorial. Não representa rotas, trânsito ou tempo real de deslocamento.")
        if indisponiveis:
            st.caption(f"Indisponíveis neste cenário: {', '.join(indisponiveis)}.")
    with col_mapa:
        mapa = construir_mapa(hex_features, limite, ocorrencias, cards, hora, mostrar_pontos,
                              meta, cobertura_atual, cobertura_perdida)
        st_folium(mapa, height=650, use_container_width=True, returned_objects=[])

with tab_cartoes:
    st.subheader("Cartões por viatura e faixa horária")
    st.caption("As 24 faixas aparecem em ordem cronológica. A heurística combina risco horário e dispersão entre focos na mesma faixa: 80%/20% para atendimento + prevenção e 55%/45% para policiamento preventivo. Nas faixas de ponto-base, evita repetir focos entre faixas consecutivas quando há alternativas.")
    if cards.empty:
        st.warning("Não há viaturas disponíveis para gerar cartões neste cenário.")
    else:
        for nome in ativos:
            dados = cards[cards["Viatura"] == nome].drop(columns=["Viatura"])
            with st.expander(f"{nome} · {tipos[nome]}", expanded=(nome == ativos[0])):
                st.dataframe(dados, hide_index=True, use_container_width=True)
        csv = cards.to_csv(index=False).encode("utf-8-sig")
        st.download_button("Baixar todos os cartões em CSV", csv, "cartoes_programa_patrulha39.csv", "text/csv")

with tab_dados:
    st.subheader("Incidência ponderada por horário")
    st.caption(f"A base contém {len(ocorrencias)} registros válidos; {meta['ocorrencias_mapeadas']} foram associados a hexágonos e {fora_da_malha} ficaram fora da malha territorial.")
    tabela_horas = pd.DataFrame([
        {"Horário": f"{h:02d}:00–{h:02d}:59", "Pontuação ponderada": totais_hora[h],
         "Classe": classes[h]["classe"], "Ordem": classes[h]["ordem"]}
        for h in range(24)
    ]).sort_values("Ordem")
    st.bar_chart(tabela_horas.set_index("Horário")["Pontuação ponderada"])
    st.dataframe(tabela_horas, hide_index=True, use_container_width=True)
    naturezas = ocorrencias["natureza"].value_counts().rename_axis("Natureza").reset_index(name="Quantidade")
    naturezas["Peso usado"] = naturezas["Natureza"].map(PESOS)
    st.markdown("**Ocorrências por natureza**")
    st.dataframe(naturezas, hide_index=True, use_container_width=True)
    st.caption("A natureza está anonimizada como A–F. Os pesos são os fornecidos nos materiais do desafio.")

st.divider()
st.caption("Protótipo de prova de conceito. Não estima deslocamento em tempo real, tempo de resposta, tráfego ou efetivo operacional além do cenário configurado.")
