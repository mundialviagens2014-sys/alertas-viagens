"""Exclusão de cadastros sem quebrar o histórico.

Regra geral: o que foi cadastrado por engano (sem vendas nem movimento ligado a outros cadastros) é apagado de
vez. Quando há histórico que a agência precisa manter (emissões ativas, transferências com outras contas,
compras de um fornecedor), a exclusão é recusada com a explicação e a alternativa (desativar).
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models_mvo import (
    Colaborador,
    CompraExterna,
    ContaPrograma,
    Emissao,
    Fornecedor,
    Lancamento,
    PedidoEmissao,
)
from .contas import ErroMVO


def _emissoes_ativas(db: Session, conta_ids: list[int]) -> int:
    if not conta_ids:
        return 0
    return db.scalar(select(func.count(Emissao.id)).where(Emissao.conta_id.in_(conta_ids), Emissao.status == "ativa")) or 0


def _transferencias_com_outras(db: Session, conta_ids: list[int]) -> int:
    """Transferências ativas entre estas contas e contas de fora (apagar uma ponta mexeria no saldo da outra)."""
    if not conta_ids:
        return 0
    total = 0
    for lanc in db.scalars(select(Lancamento).where(Lancamento.conta_id.in_(conta_ids), Lancamento.par_id.is_not(None),
                                                    Lancamento.status == "ativo")):
        par = db.get(Lancamento, lanc.par_id)
        if par is not None and par.status == "ativo" and par.conta_id not in conta_ids:
            total += 1
    return total


def _soltar_compras(db: Session, conta_ids: list[int]) -> None:
    """Compras externas que caíram nestas contas ficam sem conta (os pontos saem junto com a conta)."""
    for compra in db.scalars(select(CompraExterna).where(CompraExterna.conta_id.in_(conta_ids or [0]))):
        compra.conta_id = None
        compra.lancamento_id = None
        if compra.status == "recebida":
            compra.status = "paga"


def _conferir_contas(db: Session, contas: list[ContaPrograma], quem: str) -> None:
    ids = [c.id for c in contas]
    ativas = _emissoes_ativas(db, ids)
    if ativas:
        raise ErroMVO(f"{quem} tem {ativas} emissão(ões) ativa(s) registrada(s). Para não perder o histórico de vendas "
                      "e comissões, não dá para excluir: desative o cadastro (ele sai das listas e da disponibilidade). "
                      "Se as emissões foram registradas por engano, cancele-as e exclua na página de cada emissão.")
    transferencias = _transferencias_com_outras(db, ids)
    if transferencias:
        raise ErroMVO(f"{quem} tem {transferencias} transferência(s) de pontos com outras contas. Cancele essas "
                      "transferências em Lançamentos (os pontos voltam para a conta de origem) ou desative o cadastro.")


def excluir_conta(db: Session, conta: ContaPrograma) -> None:
    _conferir_contas(db, [conta], f"A conta {conta.programa} de {conta.colaborador.nome}")
    _soltar_compras(db, [conta.id])
    db.delete(conta)  # os lançamentos da conta vão junto; emissões canceladas ficam, sem a conta
    db.flush()


def excluir_colaborador(db: Session, col: Colaborador) -> None:
    _conferir_contas(db, list(col.contas), col.nome)
    _soltar_compras(db, [c.id for c in col.contas])
    db.delete(col)  # contas, lançamentos e contratos vão junto
    db.flush()


def excluir_fornecedor(db: Session, forn: Fornecedor) -> None:
    usos = []
    n = db.scalar(select(func.count(Lancamento.id)).where(Lancamento.fornecedor_id == forn.id,
                                                          Lancamento.status == "ativo")) or 0
    if n:
        usos.append(f"{n} lançamento(s) de compra")
    n = db.scalar(select(func.count(Emissao.id)).where(Emissao.fornecedor_id == forn.id, Emissao.status == "ativa")) or 0
    if n:
        usos.append(f"{n} emissão(ões)")
    n = db.scalar(select(func.count(CompraExterna.id)).where(CompraExterna.fornecedor_id == forn.id,
                                                             CompraExterna.status != "cancelada")) or 0
    if n:
        usos.append(f"{n} compra(s) externa(s)")
    if usos:
        raise ErroMVO(f"{forn.nome} aparece em {', '.join(usos)}. Para manter esse histórico, não dá para excluir: "
                      "desmarque “Fornecedor ativo” para ele sair das listas.")
    db.delete(forn)
    db.flush()


def excluir_emissao(db: Session, emissao: Emissao) -> None:
    """Só emissão cancelada (os pontos já voltaram para a conta)."""
    if emissao.status == "ativa":
        raise ErroMVO("Cancele a emissão antes de excluir (os pontos voltam para a conta).")
    for lanc in db.scalars(select(Lancamento).where(Lancamento.emissao_id == emissao.id, Lancamento.status != "ativo")):
        db.delete(lanc)  # o lançamento cancelado da emissão sai junto
    for pedido in db.scalars(select(PedidoEmissao).where(PedidoEmissao.emissao_id == emissao.id)):
        pedido.emissao_id = None
    db.delete(emissao)
    db.flush()
