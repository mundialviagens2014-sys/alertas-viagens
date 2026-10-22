"""Ajustes de dados feitos ao iniciar o site, para bancos de versões anteriores do MVO."""
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import Usuario
from ..models_mvo import Emissao


def preparar_mvo(db: Session) -> None:
    # emissões registradas antes do campo "emissor": quem registrou é quem emitiu
    sem_emissor = db.scalars(select(Emissao).where(Emissao.emissor_nome.is_(None), Emissao.usuario.is_not(None))).all()
    if sem_emissor:
        por_nome = {u.nome: u.id for u in db.scalars(select(Usuario))}
        for e in sem_emissor:
            e.emissor_nome = e.usuario
            e.emissor_id = por_nome.get(e.usuario)
    db.execute(update(Usuario).where(Usuario.comissao_base.is_(None)).values(comissao_base="lucro"))
    db.commit()
