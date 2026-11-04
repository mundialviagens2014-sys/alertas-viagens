"""Entrada de promoções vindas de fora (Telegram, bot do WhatsApp, API): decide se a mensagem vai pela leitura
por regras (texto de passagem) ou pela IA (imagem, ou texto que as regras não entendem) e registra a promoção.

Orçamento da IA: cada rodada de leitura tem um limite de chamadas (GEMINI_MAX_POR_RODADA) e o dia tem outro
(GEMINI_MAX_POR_DIA). O que não couber, ou o que o Google recusar por cota, fica em `leituras_pendentes` e é
lido nas próximas rodadas, da mais antiga para a mais nova. Pendências de mais de 3 dias são descartadas: a
promoção já passou.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ia
from .config import settings
from .models import LeituraPendente
from .parser import extrair
from .servicos import Resultado, registrar_promocao

log = logging.getLogger(__name__)
VALIDADE_PENDENTE_DIAS = 3
TAMANHO_MAX_IMAGEM = 8 * 1024 * 1024


@dataclass
class Orcamento:
    """Quantas leituras por IA ainda cabem nesta rodada; `parou` quando o Google recusou por cota."""

    restantes: int = field(default_factory=lambda: settings.gemini_max_por_rodada)
    parou: bool = False
    usadas: int = 0

    def pode(self, db: Session) -> bool:
        return not self.parou and self.restantes > 0 and ia.cota_disponivel(db)

    def gastar(self, db: Session) -> None:
        self.restantes -= 1
        self.usadas += 1
        ia.registrar_uso(db)


def _pendente(db: Session, fonte: str, fonte_detalhe: str | None, link_fonte: str | None, texto: str | None,
              imagem: bytes | None, mime: str | None, erro: str | None = None) -> None:
    db.add(LeituraPendente(fonte=fonte, fonte_detalhe=fonte_detalhe, link_fonte=link_fonte, texto=texto,
                           imagem=imagem, mime=mime, erro=(erro or "")[:300] or None))
    db.commit()


def processar(db: Session, *, fonte: str, fonte_detalhe: str | None, link_fonte: str | None,
              texto: str | None = None, imagem: bytes | None = None, mime: str | None = None,
              orcamento: Orcamento | None = None, cliente_http: httpx.Client | None = None) -> Resultado:
    """Registra a promoção de uma mensagem (texto e/ou imagem). Motivos possíveis: criada, duplicada, incompleta
    (não é promoção ou faltam dados), pendente (IA sem cota agora), sem_ia (imagem sem IA configurada)."""
    texto = (texto or "").strip() or None
    if imagem and len(imagem) > TAMANHO_MAX_IMAGEM:
        imagem = None
    # 1) texto de passagem que as regras entendem: não gasta IA
    if texto:
        dados = extrair(texto)
        if dados.completa and (dados.preco or dados.milhas):
            return registrar_promocao(db, texto=texto, dados=dados, fonte=fonte, fonte_detalhe=fonte_detalhe,
                                      link_fonte=link_fonte, imagem=imagem, mime=mime)
    # 2) imagem, ou texto que parece oferta mas as regras não leram: IA
    if not imagem and not ia.vale_a_pena_ler(texto):
        return Resultado(None, "incompleta")
    if not ia.configurada():
        return Resultado(None, "sem_ia" if imagem else "incompleta")
    orcamento = orcamento or Orcamento()
    if not orcamento.pode(db):
        _pendente(db, fonte, fonte_detalhe, link_fonte, texto, imagem, mime, "sem cota nesta rodada")
        return Resultado(None, "pendente")
    try:
        leitura = ia.ler(texto=texto, imagem=imagem, mime=mime or "image/jpeg", cliente_http=cliente_http)
    except ia.CotaExcedida as erro:
        orcamento.parou = True
        _pendente(db, fonte, fonte_detalhe, link_fonte, texto, imagem, mime, str(erro))
        return Resultado(None, "pendente")
    except httpx.HTTPError as erro:
        log.warning("Falha na leitura por IA (%s): %s", fonte_detalhe, erro)
        _pendente(db, fonte, fonte_detalhe, link_fonte, texto, imagem, mime, f"falha: {erro}")
        return Resultado(None, "pendente")
    orcamento.gastar(db)
    if not leitura.eh_promocao:
        return Resultado(None, "incompleta")
    dados = ia.para_extraida(leitura)
    if not dados.completa:
        return Resultado(None, "incompleta")
    return registrar_promocao(db, texto=texto, dados=dados, fonte=fonte, fonte_detalhe=fonte_detalhe,
                              link_fonte=link_fonte, imagem=imagem, mime=mime)


def reler_pendentes(db: Session, orcamento: Orcamento, cliente_http: httpx.Client | None = None) -> dict:
    """Tenta de novo as leituras que ficaram pendentes (da mais antiga para a mais nova) enquanto houver cota."""
    resumo = {"criadas": 0, "duplicadas": 0, "descartadas": 0, "matches": 0}
    limite = datetime.now() - timedelta(days=VALIDADE_PENDENTE_DIAS)
    pendentes = db.scalars(select(LeituraPendente).order_by(LeituraPendente.criado_em, LeituraPendente.id)).all()
    for item in pendentes:
        if item.criado_em < limite or item.tentativas >= 5:
            db.delete(item)
            resumo["descartadas"] += 1
            continue
        if not orcamento.pode(db):
            break
        db.delete(item)  # se não der certo agora, processar() cria uma pendência nova (com a tentativa contada)
        db.flush()
        r = processar(db, fonte=item.fonte, fonte_detalhe=item.fonte_detalhe, link_fonte=item.link_fonte,
                      texto=item.texto, imagem=item.imagem, mime=item.mime, orcamento=orcamento,
                      cliente_http=cliente_http)
        if r.motivo == "pendente":
            nova = db.scalars(select(LeituraPendente).order_by(LeituraPendente.id.desc())).first()
            if nova:
                nova.tentativas = (item.tentativas or 0) + 1
                nova.criado_em = item.criado_em
        elif r.motivo == "criada":
            resumo["criadas"] += 1
            resumo["matches"] += r.matches
        elif r.motivo == "duplicada":
            resumo["duplicadas"] += 1
    db.commit()
    return resumo


def pendentes(db: Session) -> int:
    return len(db.scalars(select(LeituraPendente.id)).all())
