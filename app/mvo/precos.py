"""Lista de preços (R$ por milheiro) e markups: o preço sugerido de uma emissão para cada tipo de cliente.

O preço "geral" de um programa e tipo de negócio é a linha sem agência. Os preços gerais ficam copiados
no cadastro do programa (preco_b2b/b2c/b2f) para as telas rápidas; o B2C geral converte as promoções em
milhas dos alertas (valor_milha = B2C ÷ 1.000).
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ProgramaMilhas
from ..models_mvo import TIPOS_NEGOCIO, Agencia, Markup, PrecoPrograma


def preco_para(db: Session, programa: str, tipo: str = "B2C", agencia_id: int | None = None) -> PrecoPrograma | None:
    """Preço da agência (se houver) ou o geral do programa para o tipo de negócio."""
    linhas = db.scalars(
        select(PrecoPrograma).where(PrecoPrograma.programa == programa, PrecoPrograma.tipo_negocio == tipo,
                                    PrecoPrograma.ativo.is_not(False))
    ).all()
    if agencia_id:
        da_agencia = [l for l in linhas if l.agencia_id == agencia_id]
        if da_agencia:
            return da_agencia[0]
    gerais = [l for l in linhas if l.agencia_id is None]
    return gerais[0] if gerais else None


def markup_para(db: Session, alvo: str, nome: str | None, agencia_id: int | None = None) -> Markup | None:
    if not nome:
        return None
    linhas = db.scalars(
        select(Markup).where(Markup.alvo == alvo, Markup.ativo.is_not(False))
    ).all()
    linhas = [m for m in linhas if (m.nome or "").strip().lower() == nome.strip().lower()]
    if agencia_id:
        da_agencia = [m for m in linhas if m.agencia_id == agencia_id]
        if da_agencia:
            return da_agencia[0]
    gerais = [m for m in linhas if m.agencia_id is None]
    return gerais[0] if gerais else None


def tipo_do_cliente(agencia: Agencia | None) -> str:
    """Agência parceira compra a preço B2B, franquia a B2F; sem agência é cliente final (B2C)."""
    if agencia is None:
        return "B2C"
    if agencia.tipo_negocio in TIPOS_NEGOCIO:
        return agencia.tipo_negocio
    return "B2F" if agencia.franquia_id else "B2B"


@dataclass
class Sugestao:
    tipo: str
    pontos: int
    milheiro: float | None
    base: float | None
    markup_pct: float
    markup_valor: float
    limite_pontos: int | None

    @property
    def total(self) -> float | None:
        if self.base is None:
            return None
        return round(self.base * (1 + self.markup_pct / 100) + self.markup_valor, 2)

    @property
    def acima_do_limite(self) -> bool:
        return bool(self.limite_pontos and self.pontos > self.limite_pontos)


def sugerir_preco(db: Session, programa: str | None, pontos: int | None, agencia: Agencia | None = None,
                  companhia: str | None = None) -> Sugestao | None:
    """Preço de venda sugerido: pontos × preço do milheiro (da agência ou geral) + markup do programa."""
    if not programa or not pontos:
        return None
    tipo = tipo_do_cliente(agencia)
    preco = preco_para(db, programa, tipo, agencia.id if agencia else None)
    if preco is None and tipo != "B2C":
        preco = preco_para(db, programa, "B2C")
    milheiro = preco.preco_venda if preco else None
    base = round(pontos / 1000 * milheiro, 2) if milheiro else None
    markup = markup_para(db, "programa", programa, agencia.id if agencia else None)
    if markup is None and companhia:
        markup = markup_para(db, "companhia", companhia, agencia.id if agencia else None)
    return Sugestao(tipo, pontos, milheiro, base, (markup.taxa_pct or 0) if markup else 0.0,
                    (markup.valor or 0) if markup else 0.0, preco.limite_pontos if preco else None)


def sincronizar_programa(db: Session, programa: str) -> None:
    """Copia os preços gerais (sem agência) para o cadastro do programa e atualiza a conversão dos alertas."""
    from ..servicos import limpar_cache_programas

    prog = db.scalar(select(ProgramaMilhas).where(ProgramaMilhas.nome == programa))
    if prog is None:
        return
    for tipo, campo in (("B2B", "preco_b2b"), ("B2C", "preco_b2c"), ("B2F", "preco_b2f")):
        linha = db.scalar(select(PrecoPrograma).where(
            PrecoPrograma.programa == programa, PrecoPrograma.tipo_negocio == tipo,
            PrecoPrograma.agencia_id.is_(None), PrecoPrograma.ativo.is_not(False)))
        setattr(prog, campo, linha.preco_venda if linha else None)
    prog.valor_milha = round(prog.preco_b2c / 1000, 6) if prog.preco_b2c else None
    db.flush()
    limpar_cache_programas()


def migrar_precos_antigos(db: Session) -> int:
    """Preços B2B/B2C/B2F guardados no programa (versão anterior) viram linhas da lista de preços."""
    if db.scalar(select(PrecoPrograma.id).limit(1)):
        return 0
    criadas = 0
    for prog in db.scalars(select(ProgramaMilhas)):
        for tipo, valor in (("B2B", prog.preco_b2b), ("B2C", prog.preco_b2c), ("B2F", prog.preco_b2f)):
            if valor:
                db.add(PrecoPrograma(programa=prog.nome, tipo_negocio=tipo, preco_venda=valor, limite_pontos=0))
                criadas += 1
    db.flush()
    return criadas
