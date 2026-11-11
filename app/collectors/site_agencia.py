"""Promoções do site da agência (mundialviagens.com) → MVO. Só leitura, num sentido só.

O site guarda as viagens no Firestore (Firebase), na coleção `promocoes`, que as regras do próprio site deixam
qualquer visitante LER. O MVO lê essa lista pela API REST pública do Firestore — sem senha, sem chave secreta e
sem nenhum caminho de escrita: nada do MVO vai para o site.

Cada viagem vira uma promoção do tipo pacote, saindo de Fortaleza, com o lugar, as datas, o valor e a primeira foto.
Quem acompanha aquela cidade entra na fila, como nas outras fontes. Se a viagem mudar no site (valor, datas), a
promoção do MVO é atualizada; se sair do site (apagada ou passou do "exibir até"), ela sai da fila do MVO.
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import aeroportos
from ..config import settings
from ..models import Promocao
from ..parser import PromocaoExtraida
from ..servicos import guardar_imagem, registrar_promocao

log = logging.getLogger(__name__)

FONTE = "site"
CATEGORIAS = {"promocao": "Promoção", "grupo": "Saída em grupo", "pacote": "Pacote", "peregrinacao": "Peregrinação"}
CAMPOS = ("categoria", "lugar", "etiqueta", "descricao", "sobre", "dataSaida", "dataVolta", "exibirAte", "preco", "parcelas")
ORIGEM_PADRAO = "FOR"   # o site vende saídas de Fortaleza


def _url_colecao() -> str:
    return (f"https://firestore.googleapis.com/v1/projects/{settings.site_firebase_projeto}"
            "/databases/(default)/documents/promocoes")


def _valor(v: dict | None):
    """Converte um valor do formato REST do Firestore ({"stringValue": "..."}) em Python."""
    if not v:
        return None
    for chave in ("stringValue", "booleanValue", "timestampValue"):
        if chave in v:
            return v[chave]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "arrayValue" in v:
        return [_valor(x) for x in v["arrayValue"].get("values", [])]
    return None


def _data(texto) -> date | None:
    try:
        return date.fromisoformat(str(texto)[:10]) if texto else None
    except ValueError:
        return None


def _preco(valor) -> float | None:
    """8640 → 8640.0; "8.640" ou "R$ 8.640,00" → 8640.0; "A definir" → None."""
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor) if valor > 0 else None
    numeros = re.sub(r"[^\d,]", "", str(valor or "")).replace(",", ".")
    try:
        return float(numeros) if numeros and float(numeros) > 0 else None
    except ValueError:
        return None


# destinos do site sem aeroporto próprio: o aeroporto de chegada (para casar com quem acompanha a cidade)
PERTO = {"maragogi": "MCZ", "gramado": "POA", "canela": "POA", "serra gaucha": "POA", "porto de galinhas": "REC",
         "fatima": "LIS", "terra santa": "TLV", "jerusalem": "TLV", "israel": "TLV", "aparecida": "SAO",
         "foz do iguacu": "IGU", "bonito": "CGR", "beto carrero": "NVT", "balneario camboriu": "NVT", "capitolio": "CNF"}


def _destino(lugar: str) -> str | None:
    """"Lisboa e Porto" → LIS, "Curitiba-PR" → CWB, "Temporada de Inverno em Gramado" → POA."""
    texto = lugar or ""
    partes = [texto, *re.split(r"\s+e\s+|,|/|-|\(|\s+em\s+|\s+para\s+", texto)]
    for parte in partes:
        parte = parte.strip(" )")
        codigo = aeroportos.resolver(parte) or PERTO.get(aeroportos.sem_acento(parte))
        if codigo:
            return codigo
    chave = aeroportos.sem_acento(texto)
    return next((codigo for nome, codigo in PERTO.items() if nome in chave), None)


def ler_site(cliente_http: httpx.Client) -> list[dict]:
    """As viagens do site (sem as fotos, que são pesadas), já convertidas em dicionários."""
    params = [("pageSize", "300")] + [("mask.fieldPaths", c) for c in CAMPOS]
    viagens, token = [], None
    while True:
        r = cliente_http.get(_url_colecao(), params=params + ([("pageToken", token)] if token else []), timeout=30)
        r.raise_for_status()
        corpo = r.json()
        for doc in corpo.get("documents", []):
            campos = {k: _valor(v) for k, v in (doc.get("fields") or {}).items()}
            viagens.append({"id": doc["name"].rsplit("/", 1)[-1], **campos})
        token = corpo.get("nextPageToken")
        if not token:
            return viagens


def primeira_foto(cliente_http: httpx.Client, doc_id: str) -> tuple[bytes, str] | None:
    """A primeira foto da viagem (o site guarda como data:image/...;base64 ou como link https)."""
    r = cliente_http.get(f"{_url_colecao()}/{doc_id}", params=[("mask.fieldPaths", "fotos")], timeout=30)
    if r.status_code != 200:
        return None
    fotos = _valor((r.json().get("fields") or {}).get("fotos")) or []
    for foto in fotos:
        if isinstance(foto, str) and foto.startswith("data:image/"):
            cabeca, _, dados = foto.partition(",")
            try:
                return base64.b64decode(dados), cabeca[5:].split(";")[0]
            except ValueError:
                continue
        if isinstance(foto, str) and foto.startswith("https://"):
            try:
                img = cliente_http.get(foto, timeout=30)
                if img.status_code == 200 and img.headers.get("content-type", "").startswith("image/"):
                    return img.content, img.headers["content-type"]
            except httpx.HTTPError:
                continue
    return None


def _link(doc_id: str) -> str:
    return f"{settings.site_agencia_url.rstrip('/')}/promo.html?id={doc_id}"


def _dados(v: dict) -> PromocaoExtraida:
    categoria = CATEGORIAS.get(v.get("categoria") or "promocao", "Promoção")
    lugar = (v.get("lugar") or "").strip()
    saida, volta = _data(v.get("dataSaida")), _data(v.get("dataVolta"))
    partes = [v.get("descricao"), f"em até {v['parcelas']}x" if v.get("parcelas") else None,
              None if _preco(v.get("preco")) or not v.get("preco") else str(v["preco"]),   # "A definir", "Sob consulta"
              (v.get("sobre") or "")[:600] or None]
    return PromocaoExtraida(
        tipo="pacote", origem=ORIGEM_PADRAO, destino=_destino(lugar), lugar=lugar[:120] or None,
        titulo=f"{categoria}: {lugar}" if lugar else categoria, resumo="\n".join(p for p in partes if p) or None,
        preco=_preco(v.get("preco")), data_inicio=saida, data_fim=volta or saida,
        tipo_viagem="ida_volta" if volta else None, link=_link(v["id"]), confianca=1.0)


def coletar(db: Session, hoje: date | None = None, cliente_http: httpx.Client | None = None) -> dict:
    hoje = hoje or date.today()
    resumo = {"no_site": 0, "criadas": 0, "atualizadas": 0, "retiradas": 0, "duplicadas": 0, "matches": 0}
    proprio = cliente_http is None
    cliente_http = cliente_http or httpx.Client(headers={"User-Agent": "MVO (leitura do site da agencia)"})
    try:
        viagens = [v for v in ler_site(cliente_http)
                   if (_data(v.get("exibirAte")) or _data(v.get("dataSaida")) or hoje) >= hoje and v.get("lugar")]
        resumo["no_site"] = len(viagens)
        ja = {p.link_fonte: p for p in db.scalars(select(Promocao).where(Promocao.fonte == FONTE))}
        vistos = set()
        for v in viagens:
            link = _link(v["id"])
            vistos.add(link)
            dados = _dados(v)
            existente = ja.get(link)
            if existente:
                mudou = False
                for campo in ("preco", "data_inicio", "data_fim", "lugar", "titulo", "resumo", "destino", "tipo_viagem"):
                    novo = getattr(dados, campo)
                    if getattr(existente, campo) != novo:
                        setattr(existente, campo, novo)
                        mudou = True
                if existente.status != "ativa":
                    existente.status, mudou = "ativa", True
                resumo["atualizadas"] += mudou
                continue
            r = registrar_promocao(db, dados=dados, fonte=FONTE, link_fonte=link,
                                   fonte_detalhe=f"Site · {CATEGORIAS.get(v.get('categoria') or 'promocao', 'Promoção')}")
            if r.motivo == "duplicada":
                resumo["duplicadas"] += 1
                continue
            if r.promocao is None:
                continue
            resumo["criadas"] += 1
            resumo["matches"] += r.matches
            foto = primeira_foto(cliente_http, v["id"])
            if foto:
                guardar_imagem(db, r.promocao, *foto)
        # o que saiu do site (apagado ou fora do prazo) sai também da fila do MVO
        for link, promo in ja.items():
            if link not in vistos and promo.status == "ativa":
                promo.status = "descartada"
                resumo["retiradas"] += 1
        db.commit()
    finally:
        if proprio:
            cliente_http.close()
    return resumo
