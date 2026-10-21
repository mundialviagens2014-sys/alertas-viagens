"""Busca de preços no Google Flights por meio da SerpApi (https://serpapi.com/google-flights-api).

O Google Flights não tem API oficial; a SerpApi faz a consulta e devolve o resultado em JSON.
Preencha SERPAPI_KEY no .env. Cada consulta consome créditos do seu plano, por isso o número
de buscas por rodada é limitado por GOOGLE_FLIGHTS_MAX_BUSCAS e os trechos são revezados
entre as rodadas.

Para cada trecho de interesse com destino definido, o coletor consulta algumas datas de ida
(só ida) e registra uma promoção quando o menor preço:
  * está dentro do preço máximo de algum cliente daquele trecho, ou
  * é classificado pelo Google como "baixo" (price_level = low).
"""
import logging
from collections import defaultdict
from datetime import date, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import aeroportos
from ..config import settings
from ..models import Cliente, Interesse
from ..parser import PromocaoExtraida
from ..servicos import gravar_estado, ler_estado, registrar_promocao

log = logging.getLogger(__name__)
URL = "https://serpapi.com/search.json"
DATAS_POR_TRECHO = 3


class GoogleFlightsNaoConfigurado(RuntimeError):
    pass


def _ids_aeroporto(codigo: str) -> str:
    """Para códigos de cidade (SAO, RIO...) consulta todos os aeroportos do grupo."""
    g = aeroportos.grupo(codigo)
    membros = [c for c, (_, gr) in aeroportos.AEROPORTOS.items() if gr == g and c != g]
    return ",".join(membros) if codigo == g and membros else codigo


def _datas_para(interesses: list[Interesse], hoje: date) -> list[date]:
    minimo = hoje + timedelta(days=7)
    inicios = [i.periodo_inicio for i in interesses if i.periodo_inicio]
    fins = [i.periodo_fim for i in interesses if i.periodo_fim]
    ini = max(min(inicios), minimo) if inicios else minimo + timedelta(days=23)
    fim = max(fins) if fins else ini + timedelta(days=60)
    if fim < ini:
        return []
    if fim == ini:
        return [ini]
    passo = (fim - ini) / (DATAS_POR_TRECHO - 1)
    return sorted({ini + passo * n for n in range(DATAS_POR_TRECHO)})


def _trechos(db: Session) -> list[tuple[str, str, list[Interesse]]]:
    grupos: dict[tuple[str, str], list[Interesse]] = defaultdict(list)
    interesses = db.scalars(
        select(Interesse).join(Cliente).where(
            Interesse.ativo.is_(True), Cliente.ativo.is_(True), Interesse.destino.is_not(None)
        )
    ).all()
    for i in interesses:
        grupos[(i.origem, i.destino)].append(i)
    return sorted((o, d, lst) for (o, d), lst in grupos.items())


def _melhor_voo(resposta: dict) -> tuple[float, str | None] | None:
    voos = (resposta.get("best_flights") or []) + (resposta.get("other_flights") or [])
    com_preco = [v for v in voos if isinstance(v.get("price"), (int, float))]
    if not com_preco:
        return None
    melhor = min(com_preco, key=lambda v: v["price"])
    trechos = melhor.get("flights") or []
    companhia = trechos[0].get("airline") if trechos else None
    return float(melhor["price"]), companhia


def buscar(cliente_http: httpx.Client, origem: str, destino: str, ida: date) -> dict:
    params = {
        "engine": "google_flights",
        "api_key": settings.serpapi_key,
        "departure_id": _ids_aeroporto(origem),
        "arrival_id": _ids_aeroporto(destino),
        "outbound_date": ida.isoformat(),
        "type": "2",  # só ida
        "currency": "BRL",
        "hl": "pt-BR",
        "gl": "br",
    }
    r = cliente_http.get(URL, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def coletar(db: Session, hoje: date | None = None, cliente_http: httpx.Client | None = None) -> dict:
    if not settings.serpapi_key:
        raise GoogleFlightsNaoConfigurado("Preencha SERPAPI_KEY no .env para buscar no Google Flights.")
    hoje = hoje or date.today()
    resumo = {"buscas": 0, "criadas": 0, "duplicadas": 0, "acima_do_preco": 0, "matches": 0, "erros": []}

    trechos = _trechos(db)
    if not trechos:
        return resumo
    # reveza os trechos entre as rodadas para respeitar o limite de buscas
    inicio = int(ler_estado(db, "google_flights:proximo_trecho") or 0) % len(trechos)
    fila = trechos[inicio:] + trechos[:inicio]

    proprio = cliente_http is None
    cliente_http = cliente_http or httpx.Client()
    trechos_feitos = 0
    try:
        for origem, destino, interesses in fila:
            datas = _datas_para(interesses, hoje)
            if resumo["buscas"] + len(datas) > settings.google_flights_max_buscas:
                break
            teto = max((i.preco_maximo or 0) for i in interesses) or None
            for ida in datas:
                resumo["buscas"] += 1
                try:
                    resposta = buscar(cliente_http, origem, destino, ida)
                except httpx.HTTPError as erro:
                    log.warning("Google Flights %s-%s %s: %s", origem, destino, ida, erro)
                    resumo["erros"].append(f"{origem}-{destino} {ida}: {erro}")
                    continue
                melhor = _melhor_voo(resposta)
                if not melhor:
                    continue
                preco, companhia = melhor
                nivel = (resposta.get("price_insights") or {}).get("price_level")
                if not ((teto and preco <= teto) or nivel == "low"):
                    resumo["acima_do_preco"] += 1
                    continue
                dados = PromocaoExtraida(
                    origem=origem, destino=destino, preco=preco, data_inicio=ida, data_fim=ida,
                    companhia=companhia, confianca=1.0, tipo_viagem="ida",
                    link=(resposta.get("search_metadata") or {}).get("google_flights_url"),
                )
                r = registrar_promocao(
                    db, dados=dados, fonte="google_flights",
                    fonte_detalhe=f"Google Flights · preço {nivel or 'n/d'}",
                    texto=f"Google Flights: {origem} → {destino} em {ida:%d/%m/%Y}, só ida, R$ {preco:.0f}",
                )
                if r.motivo == "criada":
                    resumo["criadas"] += 1
                    resumo["matches"] += r.matches
                elif r.motivo == "duplicada":
                    resumo["duplicadas"] += 1
            trechos_feitos += 1
    finally:
        if proprio:
            cliente_http.close()
    gravar_estado(db, "google_flights:proximo_trecho", str(inicio + max(trechos_feitos, 1)))
    return resumo
