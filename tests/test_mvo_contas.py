from datetime import date, datetime, timedelta

import pytest

from app.models import ProgramaMilhas
from app.models_mvo import Colaborador, ContaPrograma, Lancamento
from app.mvo.contas import (
    ErroMVO,
    cancelar_lancamento,
    disponibilidade,
    lancar,
    recalcular_vencidos,
    resumo_por_programa,
    situacao,
    transferir,
)
from app.mvo.emissoes import cancelar_emissao, criar_emissao


def _conta(db, nome="Ana Souza", programa="Smiles", **extra) -> ContaPrograma:
    col = db.query(Colaborador).filter_by(nome=nome).first() or Colaborador(nome=nome, cpf="11122233344")
    conta = ContaPrograma(colaborador=col, programa=programa, **extra)
    db.add(conta)
    db.flush()
    return conta


def test_custo_medio_compras_bonus_e_saidas(db):
    c = _conta(db)
    lancar(db, c, "compra", 10_000, preco_milheiro=20)
    assert (c.saldo, c.custo_medio) == (10_000, 20.0)

    lancar(db, c, "compra", 10_000, valor=300)  # R$ 30 o milheiro
    assert (c.saldo, c.custo_medio) == (20_000, 25.0)

    emissao = lancar(db, c, "emissao", 5_000)
    assert emissao.pontos == -5_000 and emissao.valor == 125.0  # leva o custo médio do momento
    assert (c.saldo, c.custo_medio) == (15_000, 25.0)

    lancar(db, c, "bonus", 5_000)  # bônus entra sem custo e baixa a média
    assert (c.saldo, c.custo_total, c.custo_medio) == (20_000, 375.0, 18.75)


def test_compra_com_data_anterior_refaz_custo_das_saidas(db):
    c = _conta(db)
    hoje = date.today()
    lancar(db, c, "compra", 10_000, preco_milheiro=20, data_efetivacao=hoje - timedelta(days=5))
    saida = lancar(db, c, "resgate", 10_000, data_efetivacao=hoje - timedelta(days=1))
    assert saida.valor == 200.0
    # compra esquecida, lançada depois com data anterior ao resgate
    lancar(db, c, "compra", 10_000, preco_milheiro=40, data_efetivacao=hoje - timedelta(days=3))
    assert saida.valor == 300.0  # agora o resgate saiu ao custo médio de R$ 30
    assert (c.saldo, c.custo_medio) == (10_000, 30.0)


def test_lancamento_futuro_entra_no_saldo_na_data(db):
    c = _conta(db)
    hoje = date.today()
    lancar(db, c, "compra", 8_000, preco_milheiro=25, data_efetivacao=hoje + timedelta(days=10))
    assert (c.saldo, c.saldo_futuro) == (0, 8_000)
    c.calculado_em = datetime.now() - timedelta(days=11)
    assert recalcular_vencidos(db, hoje=hoje + timedelta(days=10)) == 1
    assert (c.saldo, c.saldo_futuro, c.custo_medio) == (8_000, 0, 25.0)


def test_transferencia_com_bonus_leva_o_custo(db):
    livelo = _conta(db, programa="Livelo")
    smiles = _conta(db, programa="Smiles")
    lancar(db, livelo, "compra", 10_000, preco_milheiro=35)
    saida, entrada = transferir(db, livelo, smiles, 10_000, bonus_pct=100)
    assert (livelo.saldo, smiles.saldo) == (0, 20_000)
    assert saida.valor == 350.0 and entrada.valor == 350.0
    assert smiles.custo_medio == 17.5

    # cancelar a transferência devolve os pontos dos dois lados
    cancelar_lancamento(db, saida)
    assert (livelo.saldo, smiles.saldo) == (10_000, 0)
    assert entrada.status == "cancelado"


def test_saldo_reservado_pelo_colaborador(db):
    c = _conta(db, saldo_reservado=3_000)
    lancar(db, c, "compra", 10_000, preco_milheiro=20)
    assert c.disponivel == 7_000


def test_emissao_desconta_pontos_cpfs_e_calcula_lucro(db):
    smiles = db.query(ProgramaMilhas).filter_by(nome="Smiles").one()
    smiles.limite_cpfs, smiles.janela_cpfs = 2, "ano"
    c = _conta(db)
    lancar(db, c, "compra", 50_000, preco_milheiro=18)

    e = criar_emissao(db, conta=c, pontos=20_000, localizador="abc123", origem="for", destino="gru",
                      preco_venda=900, taxa_embarque=40, passageiros=[("Maria Lima", "123.456.789-00")])
    assert e.localizador == "ABC123" and (e.origem, e.destino) == ("FOR", "GRU")
    assert (c.saldo, e.custo_milhas, e.lucro, e.total) == (30_000, 360.0, 540.0, 940.0)
    assert situacao(db, c).cpfs_livres == 1

    # o mesmo passageiro de novo não ocupa outro CPF
    criar_emissao(db, conta=c, pontos=5_000, passageiros=[("Maria Lima", "12345678900")])
    assert situacao(db, c).cpfs_livres == 1

    with pytest.raises(ErroMVO, match="CPF"):
        criar_emissao(db, conta=c, pontos=5_000, passageiros=[("João", "1"), ("Pedro", "2")])
    with pytest.raises(ErroMVO, match="pontos disponíveis"):
        criar_emissao(db, conta=c, pontos=99_000, passageiros=[("Maria Lima", "12345678900")])
    with pytest.raises(ErroMVO, match="localizador ABC123"):
        criar_emissao(db, conta=c, pontos=1_000, localizador="ABC123")

    # registrar mesmo assim (saldo desatualizado, por exemplo)
    forcada = criar_emissao(db, conta=c, pontos=99_000, passageiros=[("Maria Lima", "12345678900")], forcar=True)
    assert c.saldo == 25_000 - 99_000
    cancelar_emissao(db, forcada)
    assert c.saldo == 25_000 and forcada.status == "cancelada"


def test_emissao_por_fornecedor_nao_mexe_em_conta(db):
    e = criar_emissao(db, forma="fornecedor", programa="LATAM Pass", pontos=30_000, outros_custos=750,
                      preco_venda=1200, localizador="XYZ999")
    assert e.conta_id is None and e.custo == 750 and e.lucro == 450
    assert db.query(Lancamento).count() == 0


def test_disponibilidade_ordena_pelo_menor_custo(db):
    barata = _conta(db, "Bia Barata")
    cara = _conta(db, "Caio Caro")
    pouca = _conta(db, "Paulo Pouco")
    pessoal = _conta(db, "Pedro Pessoal", uso_pessoal=True)
    lancar(db, barata, "compra", 40_000, preco_milheiro=15)
    lancar(db, cara, "compra", 40_000, preco_milheiro=22)
    lancar(db, pouca, "compra", 5_000, preco_milheiro=10)
    lancar(db, pessoal, "compra", 90_000, preco_milheiro=5)

    nomes = [l.conta.colaborador.nome for l in disponibilidade(db, programa="Smiles", pontos_min=20_000)]
    assert nomes == ["Bia Barata", "Caio Caro"]
    todas = [l.conta.colaborador.nome for l in disponibilidade(db, incluir_pessoal=True)]
    assert todas[:2] == ["Pedro Pessoal", "Paulo Pouco"]  # menor custo primeiro (R$ 5, R$ 10)
    assert [l.conta.colaborador.nome for l in disponibilidade(db, busca="caio")] == ["Caio Caro"]

    resumo = {r["programa"]: r for r in resumo_por_programa(db)}
    assert resumo["Smiles"]["pontos"] == 85_000  # sem a conta de uso pessoal


def test_saldo_inicial_vale_desde_o_comeco(db):
    """Emissão com data anterior à importação do saldo usa o custo do saldo inicial (não fica sem custo)."""
    c = _conta(db)
    hoje = date.today()
    lancar(db, c, "saldo_inicial", 10_000, preco_milheiro=20)
    saida = lancar(db, c, "emissao", 5_000, data_efetivacao=hoje - timedelta(days=3))
    assert saida.valor == 100.0 and (c.saldo, c.custo_medio) == (5_000, 20.0)
