"""Emissões: registrar (descontando pontos e CPFs da conta), editar dados e cancelar."""
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models_mvo import FORMAS_EMISSAO, ContaPrograma, Emissao, EmissaoPassageiro, Lancamento
from .contas import (
    ErroMVO,
    cancelar_lancamento,
    formatar_pontos,
    lancar,
    passageiros_novos,
    recalcular_conta,
    situacao,
    so_digitos,
)


def criar_emissao(
    db: Session,
    *,
    forma: str = "programa_proprio",
    conta: ContaPrograma | None = None,
    fornecedor_id: int | None = None,
    programa: str | None = None,
    companhia: str | None = None,
    localizador: str | None = None,
    origem: str | None = None,
    destino: str | None = None,
    tipo_viagem: str | None = None,
    data_ida: date | None = None,
    data_volta: date | None = None,
    pontos: int | None = None,
    preco_venda: float | None = None,
    taxa_embarque: float | None = None,
    taxa_bagagem: float | None = None,
    outros_custos: float | None = None,
    passageiros: list[tuple[str, str | None]] | None = None,
    qtd_passageiros: int | None = None,
    cliente_id: int | None = None,
    cliente_nome: str | None = None,
    promocao_id: int | None = None,
    observacao: str | None = None,
    usuario: str | None = None,
    data_emissao: datetime | None = None,
    forcar: bool = False,
    emissor_id: int | None = None,
    emissor_nome: str | None = None,
    metodo_pagamento: str | None = None,
    conta_recebedora: str | None = None,
    taxa_cartao: float | None = None,
    conexoes: int | None = None,
    agencia_id: int | None = None,
    pedido_id: int | None = None,
) -> Emissao:
    """Registra a emissão. Em "programa próprio", desconta os pontos da conta (lançamento de emissão)
    e confere saldo e CPFs livres; `forcar=True` registra mesmo assim (ex.: saldo ainda não atualizado)."""
    if forma not in FORMAS_EMISSAO:
        raise ErroMVO("Forma de emissão inválida.")
    passageiros = [(n.strip(), so_digitos(c) or None) for n, c in (passageiros or []) if n and n.strip()]
    qtd = len(passageiros) or max(int(qtd_passageiros or 1), 1)
    localizador = (localizador or "").strip().upper() or None

    if localizador and not forcar:
        repetida = db.scalar(select(Emissao).where(Emissao.localizador == localizador, Emissao.status == "ativa"))
        if repetida:
            raise ErroMVO(f"Já existe uma emissão ativa com o localizador {localizador} "
                          f"({repetida.data_emissao:%d/%m/%Y}). Marque “registrar mesmo assim” se estiver certo.")

    if forma == "programa_proprio":
        if conta is None:
            raise ErroMVO("Escolha a conta (colaborador e programa) usada na emissão.")
        if not pontos or pontos <= 0:
            raise ErroMVO("Informe quantos pontos a emissão usou.")
        programa = conta.programa
        if not forcar:
            if pontos > conta.disponivel:
                raise ErroMVO(f"A conta de {conta.colaborador.nome} ({conta.programa}) tem "
                              f"{formatar_pontos(conta.disponivel)} pontos disponíveis e a emissão usa "
                              f"{formatar_pontos(pontos)}. Marque “registrar mesmo assim” se o saldo estiver desatualizado.")
            sit = situacao(db, conta)
            novos = passageiros_novos(db, conta, passageiros) if passageiros else qtd
            if sit.cpfs_livres is not None and novos > sit.cpfs_livres:
                raise ErroMVO(f"A conta tem {sit.cpfs_livres} CPF(s) livre(s) no período e a emissão precisa de "
                              f"{novos}. Marque “registrar mesmo assim” se estiver certo.")

    emissao = Emissao(
        data_emissao=data_emissao or datetime.now(), forma=forma,
        conta_id=conta.id if conta else None, fornecedor_id=fornecedor_id,
        programa=programa, companhia=(companhia or "").strip() or None, localizador=localizador,
        origem=(origem or "").strip().upper() or None, destino=(destino or "").strip().upper() or None,
        tipo_viagem=tipo_viagem if tipo_viagem in ("ida", "ida_volta") else None,
        data_ida=data_ida, data_volta=data_volta, qtd_passageiros=qtd, pontos=pontos,
        preco_venda=preco_venda, taxa_embarque=taxa_embarque, taxa_bagagem=taxa_bagagem,
        outros_custos=outros_custos, cliente_id=cliente_id,
        cliente_nome=(cliente_nome or "").strip() or None, promocao_id=promocao_id,
        observacao=(observacao or "").strip() or None, usuario=usuario,
        emissor_id=emissor_id, emissor_nome=(emissor_nome or usuario or "").strip() or None,
        metodo_pagamento=(metodo_pagamento or "").strip() or None,
        conta_recebedora=(conta_recebedora or "").strip() or None, taxa_cartao=taxa_cartao,
        conexoes=conexoes, agencia_id=agencia_id, pedido_id=pedido_id,
    )
    emissao.passageiros = [EmissaoPassageiro(nome=n, cpf=c) for n, c in passageiros]
    db.add(emissao)
    db.flush()

    if forma == "programa_proprio":
        texto = f"Emissão {localizador}" if localizador else "Emissão"
        if emissao.origem and emissao.destino:
            texto += f" {emissao.origem}-{emissao.destino}"
        lancar(db, conta, "emissao", pontos, data_efetivacao=emissao.data_emissao.date(),
               emissao_id=emissao.id, observacao=texto, usuario=usuario)
    elif forma == "fornecedor":
        emissao.custo_milhas = None  # custo vem em outros_custos (o que foi pago ao fornecedor)
    db.flush()
    return emissao


def editar_emissao(db: Session, emissao: Emissao, **campos) -> Emissao:
    """Altera dados que não mexem em pontos (para trocar conta ou pontos: cancele e registre de novo)."""
    permitidos = {"localizador", "companhia", "origem", "destino", "tipo_viagem", "data_ida", "data_volta",
                  "preco_venda", "taxa_embarque", "taxa_bagagem", "outros_custos", "cliente_id",
                  "cliente_nome", "observacao", "emissor_id", "emissor_nome", "metodo_pagamento",
                  "conta_recebedora", "taxa_cartao", "conexoes", "agencia_id"}
    for chave, valor in campos.items():
        if chave in permitidos:
            if chave == "localizador" and valor:
                valor = valor.strip().upper()
            if chave in ("origem", "destino") and valor:
                valor = valor.strip().upper()
            setattr(emissao, chave, valor)
    db.flush()
    return emissao


def cancelar_emissao(db: Session, emissao: Emissao) -> None:
    """Cancela a emissão e devolve os pontos à conta (o lançamento de emissão é cancelado)."""
    if emissao.status != "ativa":
        return
    emissao.status = "cancelada"
    lancs = db.scalars(select(Lancamento).where(Lancamento.emissao_id == emissao.id, Lancamento.status == "ativo")).all()
    for l in lancs:
        cancelar_lancamento(db, l)
    if emissao.conta and not lancs:
        recalcular_conta(db, emissao.conta)
    db.flush()
