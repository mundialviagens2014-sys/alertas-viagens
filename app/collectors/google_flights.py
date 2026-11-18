"""Busca de preços no Google Flights por meio da SerpApi (https://serpapi.com).

O Google Flights não tem API oficial; a SerpApi faz a consulta e devolve o resultado em JSON.
Preencha SERPAPI_KEY nas variáveis do Render. A SerpApi conta toda busca feita com sucesso
(com ou sem promoção no resultado), então o coletor economiza buscas assim:

1. Explorar: UMA busca por cidade de origem dos clientes (Google Travel Explore) traz o preço
   para vários destinos de uma vez, nas melhores datas dos próximos 6 meses. O coletor cruza o
   resultado com os destinos que os clientes pediram (no período e até o preço máximo de cada um).
2. Confirmar: só para o que bateu, uma busca normal no Google Flights naquele trecho e data; entra
   no sistema só se o monitor de preços do Google marcar o preço como "baixo".
3. Trechos que o Explorar não mostrou recebem, se sobrar cota, uma busca direta (uma data).

Teto: um limite de buscas por mês (Configurações → Google Flights; padrão 240, o plano gratuito
tem 250), espalhado pelos dias que faltam no mês e pelas rodadas de cada dia. Origens e trechos
com mais clientes vêm primeiro, e um trecho conferido há pouco espera a vez.
"""
import logging
import math
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

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
DATAS_POR_TRECHO = 1        # busca direta: uma data por trecho (a do meio do período pedido)
DIAS_ENTRE_CONFIRMACOES = 2  # um trecho confirmado espera esse tempo para gastar outra busca
CONFIRMACOES_POR_ORIGEM = 3
LIMITE_PADRAO = 240


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
    if fim == ini or DATAS_POR_TRECHO == 1:
        return [ini + (fim - ini) / 2]
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

def explorar(cliente_http: httpx.Client, origem: str) -> dict:
    """Uma busca: destinos e preços saindo da origem (só ida, próximos 6 meses, melhores datas)."""
    params = {
        "engine": "google_travel_explore",
        "api_key": settings.serpapi_key,
        "departure_id": _ids_aeroporto(origem),
        "type": "2",          # só ida, como as buscas diretas
        "travel_mode": "1",   # só voo
        "currency": "BRL",
        "hl": "pt-BR",
        "gl": "br",
    }
    r = cliente_http.get(URL, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- coleta automática (Configurações → Google Flights)

UNIDADES = {"minutos": 1, "horas": 60, "dias": 1440}
MINIMO_MINUTOS = 15   # menos que isso queimaria a cota sem trazer preço novo


def _hora(texto: str | None) -> time | None:
    try:
        return time.fromisoformat((texto or "").strip()[:5]) if texto and texto.strip() else None
    except ValueError:
        return None


def agenda(db: Session) -> dict:
    """Como a coleta automática roda: ligada, de quanto em quanto tempo, quantas buscas por rodada e em que horário."""
    try:
        a_cada = max(1, int(ler_estado(db, "google_flights:a_cada") or settings.intervalo_google_horas))
    except ValueError:
        a_cada = settings.intervalo_google_horas
    unidade = ler_estado(db, "google_flights:unidade") or "horas"
    unidade = unidade if unidade in UNIDADES else "horas"
    try:
        por_rodada = max(0, int(ler_estado(db, "google_flights:por_rodada") or 0))
    except ValueError:
        por_rodada = 0
    inicio, fim = _hora(ler_estado(db, "google_flights:inicio")), _hora(ler_estado(db, "google_flights:fim"))
    minutos = max(MINIMO_MINUTOS, a_cada * UNIDADES[unidade])
    janela = 1440
    if inicio and fim:
        janela = ((fim.hour * 60 + fim.minute) - (inicio.hour * 60 + inicio.minute)) % 1440 or 1440
    # "a cada 2 dias" dá meia rodada por dia, em média; dentro do dia, quantas cabem no horário escolhido
    rodadas_dia = 1440 / minutos if minutos >= 1440 else max(1, janela // minutos)
    return {"ligada": ler_estado(db, "google_flights:auto") != "0", "a_cada": a_cada, "unidade": unidade,
            "minutos": minutos, "por_rodada": por_rodada, "inicio": inicio, "fim": fim, "rodadas_dia": rodadas_dia,
            "ultima": ler_estado(db, "google_flights:ultima_rodada")}


def no_horario(ag: dict, agora: datetime) -> bool:
    if not (ag["inicio"] and ag["fim"]):
        return True
    t = agora.time()
    return ag["inicio"] <= t < ag["fim"] if ag["inicio"] < ag["fim"] else (t >= ag["inicio"] or t < ag["fim"])


def na_hora(db: Session, agora: datetime | None = None) -> bool:
    """A coleta automática deve rodar agora? (ligada, dentro do horário e passou o intervalo desde a última rodada)"""
    agora = agora or datetime.now()
    ag = agenda(db)
    if not ag["ligada"] or not no_horario(ag, agora):
        return False
    try:
        ultima = datetime.fromisoformat(ag["ultima"]) if ag["ultima"] else None
    except ValueError:
        ultima = None
    return ultima is None or agora - ultima >= timedelta(minutes=ag["minutos"]) - timedelta(minutes=2)


def estimativa(db: Session) -> dict:
    """Quanto a configuração atual gasta: buscas por dia e no mês (para mostrar na tela)."""
    ag, orc = agenda(db), orcamento(db)
    if ag["por_rodada"]:
        por_rodada = ag["por_rodada"]
        dia = por_rodada * ag["rodadas_dia"]
        mes = round(dia * 30)
    else:   # automático: a cota do dia e o limite do mês seguram a conta
        rodadas = max(1, math.ceil(ag["rodadas_dia"]))
        por_rodada = max(1, math.ceil(orc["cota_hoje"] / rodadas)) if orc["cota_hoje"] else 0
        dia = min(por_rodada * ag["rodadas_dia"], orc["cota_hoje"])
        mes = min(round(dia * 30), orc["limite"])
    return {"rodadas_dia": ag["rodadas_dia"], "por_rodada": por_rodada, "dia": dia, "mes": mes,
            "estoura": bool(ag["por_rodada"]) and mes > orc["limite"]}


# ---------------------------------------------------------------- teto de buscas do mês


def limite_mes(db: Session) -> int:
    try:
        return max(0, int(ler_estado(db, "google_flights:limite_mes") or settings.google_flights_limite_mes))
    except ValueError:
        return LIMITE_PADRAO


def _chaves(hoje: date) -> tuple[str, str]:
    return f"google_flights:usadas:{hoje:%Y-%m}", f"google_flights:usadas_dia:{hoje:%Y-%m-%d}"


def orcamento(db: Session, hoje: date | None = None) -> dict:
    """Quanto do teto do mês já foi usado, a cota de hoje (o que sobra dividido pelos dias que faltam) e a desta rodada."""
    hoje = hoje or date.today()
    chave_mes, chave_dia = _chaves(hoje)
    limite = limite_mes(db)
    usadas_mes = int(ler_estado(db, chave_mes) or 0)
    usadas_hoje = int(ler_estado(db, chave_dia) or 0)
    proximo_mes = (hoje.replace(day=1) + timedelta(days=32)).replace(day=1)
    dias_restantes = (proximo_mes - hoje).days
    sobra_no_inicio_do_dia = max(0, limite - (usadas_mes - usadas_hoje))
    cota_hoje = math.ceil(sobra_no_inicio_do_dia / dias_restantes) if dias_restantes else 0
    ag = agenda(db)
    if ag["por_rodada"]:
        # quantidade fixa escolhida na tela: vale o número, só o limite do mês segura
        agora = max(0, min(ag["por_rodada"], limite - usadas_mes))
    else:
        rodadas_por_dia = max(1, math.ceil(ag["rodadas_dia"]))
        por_rodada = max(1, math.ceil(cota_hoje / rodadas_por_dia)) if cota_hoje else 0
        agora = max(0, min(cota_hoje - usadas_hoje, limite - usadas_mes, por_rodada, settings.google_flights_max_buscas))
    return {"limite": limite, "usadas_mes": usadas_mes, "usadas_hoje": usadas_hoje, "cota_hoje": cota_hoje,
            "rodada": agora, "restantes_mes": max(0, limite - usadas_mes)}


def _contar(db: Session, hoje: date) -> None:
    for chave in _chaves(hoje):
        gravar_estado(db, chave, str(int(ler_estado(db, chave) or 0) + 1))


# ---------------------------------------------------------------- coleta


def _no_periodo(i: Interesse, dia: date | None) -> bool:
    if dia is None:
        return True
    return (not i.periodo_inicio or dia >= i.periodo_inicio) and (not i.periodo_fim or dia <= i.periodo_fim)


def _data(texto) -> date | None:
    try:
        return date.fromisoformat(str(texto)[:10])
    except (TypeError, ValueError):
        return None


def _visto_ha_pouco(db: Session, origem: str, destino: str, hoje: date) -> bool:
    quando = _data(ler_estado(db, f"google_flights:visto:{origem}-{destino}"))
    return bool(quando and (hoje - quando).days < DIAS_ENTRE_CONFIRMACOES)


def _registrar(db: Session, resumo: dict, origem: str, destino: str, ida: date, resposta: dict) -> None:
    """Busca direta feita: entra no sistema só o preço que o Google marca como baixo."""
    melhor = _melhor_voo(resposta)
    if not melhor:
        return
    preco, companhia = melhor
    insights = resposta.get("price_insights") or {}
    nivel = insights.get("price_level")
    if nivel is None:  # o Google não avaliou o preço: não dá para saber se é baixo
        resumo["sem_nivel"] += 1
        return
    if nivel != "low":
        resumo["acima_do_preco"] += 1
        return
    faixa = insights.get("typical_price_range")
    detalhe = "Google Flights · preço baixo"
    if isinstance(faixa, list) and len(faixa) == 2:
        detalhe += f" (normal: R$ {faixa[0]:.0f} a R$ {faixa[1]:.0f})"
    dados = PromocaoExtraida(
        origem=origem, destino=destino, preco=preco, data_inicio=ida, data_fim=ida,
        companhia=companhia, confianca=1.0, tipo_viagem="ida",
        link=(resposta.get("search_metadata") or {}).get("google_flights_url"),
    )
    r = registrar_promocao(db, dados=dados, fonte="google_flights", fonte_detalhe=detalhe,
                           texto=f"Google Flights: {origem} → {destino} em {ida:%d/%m/%Y}, só ida, R$ {preco:.0f}")
    if r.motivo == "criada":
        resumo["criadas"] += 1
        resumo["matches"] += r.matches
    elif r.motivo == "duplicada":
        resumo["duplicadas"] += 1


def coletar(db: Session, hoje: date | None = None, cliente_http: httpx.Client | None = None) -> dict:
    if not settings.serpapi_key:
        raise GoogleFlightsNaoConfigurado("Preencha SERPAPI_KEY no .env para buscar no Google Flights.")
    hoje = hoje or date.today()
    gravar_estado(db, "google_flights:ultima_rodada", datetime.now().isoformat(timespec="seconds"))
    resumo = {"buscas": 0, "exploradas": 0, "candidatas": 0, "confirmacoes": 0, "diretas": 0, "criadas": 0,
              "duplicadas": 0, "acima_do_preco": 0, "acima_do_cliente": 0, "sem_nivel": 0, "matches": 0, "erros": []}
    trechos = _trechos(db)
    orc = orcamento(db, hoje)
    resumo["orcamento"] = orc
    if not trechos or orc["rodada"] <= 0:
        if trechos:
            resumo["sem_cota"] = True
        return resumo
    resta = orc["rodada"]

    def gastar() -> bool:
        nonlocal resta
        if resta <= 0:
            return False
        resta -= 1
        resumo["buscas"] += 1
        _contar(db, hoje)
        return True

    por_origem: dict[str, list[tuple[str, list[Interesse]]]] = defaultdict(list)
    for origem, destino, interesses in trechos:
        por_origem[origem].append((destino, interesses))
    clientes_por_origem = Counter({o: sum(len(lst) for _, lst in ds) for o, ds in por_origem.items()})
    origens = [o for o, _ in clientes_por_origem.most_common()]
    # reveza as origens entre as rodadas (as com mais clientes primeiro)
    inicio = int(ler_estado(db, "google_flights:proxima_origem") or 0) % len(origens)
    fila_origens = origens[inicio:] + origens[:inicio]

    proprio = cliente_http is None
    cliente_http = cliente_http or httpx.Client()
    cobertos: set[tuple[str, str]] = set()
    origens_feitas = 0
    try:
        for origem in fila_origens:
            if resta <= 0:
                break
            gastar()
            try:
                achados = explorar(cliente_http, origem).get("destinations") or []
            except httpx.HTTPError as erro:
                log.warning("Google Explore %s: %s", origem, erro)
                resumo["erros"].append(f"Explorar {origem}: {erro}")
                origens_feitas += 1
                continue
            resumo["exploradas"] += 1
            origens_feitas += 1
            precos: dict[str, tuple[float, date | None]] = {}
            for d in achados:
                codigo = ((d.get("destination_airport") or {}).get("code") or "").upper()
                preco = d.get("flight_price")
                if not codigo or not isinstance(preco, (int, float)):
                    continue
                g = aeroportos.grupo(codigo)
                if g not in precos or preco < precos[g][0]:
                    precos[g] = (float(preco), _data(d.get("start_date")))
            candidatos = []
            for destino, interesses in por_origem[origem]:
                g = aeroportos.grupo(destino)
                if g not in precos:
                    continue
                cobertos.add((origem, destino))
                preco, dia = precos[g]
                compativeis = [i for i in interesses if _no_periodo(i, dia)]
                if not compativeis:
                    continue
                tetos = [i.preco_maximo for i in compativeis]
                if all(t is not None for t in tetos) and preco > max(tetos):
                    resumo["acima_do_cliente"] += 1
                    continue
                if _visto_ha_pouco(db, origem, destino, hoje):
                    continue
                candidatos.append((len(compativeis), destino, dia or _datas_para(compativeis, hoje)[0]))
            resumo["candidatas"] += len(candidatos)
            for _, destino, dia in sorted(candidatos, key=lambda c: -c[0])[:CONFIRMACOES_POR_ORIGEM]:
                if not gastar():
                    break
                resumo["confirmacoes"] += 1
                gravar_estado(db, f"google_flights:visto:{origem}-{destino}", hoje.isoformat())
                try:
                    _registrar(db, resumo, origem, destino, dia, buscar(cliente_http, origem, destino, dia))
                except httpx.HTTPError as erro:
                    log.warning("Google Flights %s-%s %s: %s", origem, destino, dia, erro)
                    resumo["erros"].append(f"{origem}-{destino} {dia}: {erro}")

        # sobrou cota: busca direta nos trechos que o Explorar não mostrou (revezando entre as rodadas)
        restantes = [(o, d, i) for o, d, i in trechos
                     if (o, d) not in cobertos and not _visto_ha_pouco(db, o, d, hoje)]
        if restantes and resta > 0:
            inicio_t = int(ler_estado(db, "google_flights:proximo_trecho") or 0) % len(restantes)
            fila = restantes[inicio_t:] + restantes[:inicio_t]
            feitos = 0
            for origem, destino, interesses in fila:
                datas = _datas_para(interesses, hoje)
                if not datas or resta < len(datas):
                    break
                for ida in datas:
                    gastar()
                    resumo["diretas"] += 1
                    try:
                        _registrar(db, resumo, origem, destino, ida, buscar(cliente_http, origem, destino, ida))
                    except httpx.HTTPError as erro:
                        log.warning("Google Flights %s-%s %s: %s", origem, destino, ida, erro)
                        resumo["erros"].append(f"{origem}-{destino} {ida}: {erro}")
                gravar_estado(db, f"google_flights:visto:{origem}-{destino}", hoje.isoformat())
                feitos += 1
            gravar_estado(db, "google_flights:proximo_trecho", str(inicio_t + max(feitos, 1)))
    finally:
        if proprio:
            cliente_http.close()
    gravar_estado(db, "google_flights:proxima_origem", str(inicio + max(origens_feitas, 1)))
    resumo["orcamento"] = orcamento(db, hoje)
    return resumo
