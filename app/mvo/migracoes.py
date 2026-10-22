"""Ajustes de dados feitos ao iniciar o site, para bancos de versões anteriores do MVO."""
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import ProgramaMilhas, Usuario
from ..models_mvo import Emissao
from ..programas import PROGRAMAS_PONTOS
from .precos import migrar_precos_antigos


def preparar_mvo(db: Session) -> None:
    # emissões registradas antes do campo "emissor": quem registrou é quem emitiu
    sem_emissor = db.scalars(select(Emissao).where(Emissao.emissor_nome.is_(None), Emissao.usuario.is_not(None))).all()
    if sem_emissor:
        por_nome = {u.nome: u.id for u in db.scalars(select(Usuario))}
        for e in sem_emissor:
            e.emissor_nome = e.usuario
            e.emissor_id = por_nome.get(e.usuario)
    db.execute(update(Usuario).where(Usuario.comissao_base.is_(None)).values(comissao_base="lucro"))
    # preços B2B/B2C/B2F da versão anterior passam para a lista de preços
    migrar_precos_antigos(db)
    # programas de pontos de banco (Livelo, Esfera) contam em pontos, não em milhas
    for prog in db.scalars(select(ProgramaMilhas).where(ProgramaMilhas.nome.in_(PROGRAMAS_PONTOS))):
        if prog.unidade in (None, "Milhas"):
            prog.unidade = "Pontos"
        if prog.tipo_parceiro in (None, "Fidelizado"):
            prog.tipo_parceiro = "Banco / cartão"
    db.commit()
