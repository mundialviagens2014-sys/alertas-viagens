"""Pedidos de emissão: registrar, emitir com tudo preenchido (o pedido fica emitido) e preço sugerido por agência."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models_mvo import Agencia, Colaborador, ContaPrograma, Emissao, Markup, PedidoEmissao, PrecoPrograma
from app.mvo.contas import lancar
from app.mvo.precos import sincronizar_programa
from app.rotas.pedidos import ler_passageiros


@pytest.fixture()
def site(db):
    with TestClient(app) as s:
        s.post("/login", data={"login": "admin", "senha": "admin123"})
        yield s


def test_ler_passageiros():
    texto = "Maria Lima; 123.456.789-09\nJOÃO LIMA - CPF 98765432100\n\n  Pedro Sem Cpf  \n"
    assert ler_passageiros(texto) == [("Maria Lima", "12345678909"), ("JOÃO LIMA", "98765432100"),
                                      ("Pedro Sem Cpf", "")]


def test_pedido_vira_emissao(db, site):
    ag = Agencia(nome="Viaje Bem", tipo_negocio="B2B")
    col = Colaborador(nome="Ana Souza", cpf="52998224725")
    conta = ContaPrograma(colaborador=col, programa="Smiles")
    db.add_all([ag, col, conta])
    db.flush()
    lancar(db, conta, "compra", 100_000, valor=1500)
    db.add_all([PrecoPrograma(programa="Smiles", tipo_negocio="B2C", preco_venda=27),
                PrecoPrograma(programa="Smiles", tipo_negocio="B2B", preco_venda=24),
                PrecoPrograma(programa="Smiles", agencia_id=ag.id, tipo_negocio="B2B", preco_venda=22),
                Markup(alvo="programa", nome="Smiles", taxa_pct=10)])
    sincronizar_programa(db, "Smiles")
    db.commit()

    r = site.post("/pedidos/novo", data={
        "agencia_id": ag.id, "requisitante_nome": "Carla", "contato": "(85) 99999-0000", "tipo_emissao": "ida_volta",
        "origem": "for", "destino": "gru", "data_ida": "2026-12-10", "data_volta": "2026-12-20", "programa": "Smiles",
        "passageiros": "Maria Lima; 123.456.789-09\nJoão Lima; 987.654.321-00", "preco_combinado": "1.800,00"})
    assert "Pedido nº 1 registrado" in r.text
    pedido = db.query(PedidoEmissao).one()
    assert (pedido.origem, pedido.qtd_passageiros, pedido.status) == ("FOR", 2, "aguardando")
    assert "Viaje Bem" in site.get("/pedidos").text and "Pedidos de emissão" in site.get("/manager").text
    assert '<span class="contador">1</span>' in site.get("/manager").text  # menu

    tela = site.get(f"/emissoes/nova?pedido={pedido.id}").text
    assert 'value="FOR"' in tela and 'value="Maria Lima"' in tela and 'value="987.654.321-00"' in tela
    assert f'<option value="{ag.id}" selected>' in tela and 'value="1.800"' in tela and "pedido nº 1" in tela

    # preço sugerido: especial da agência (22/mil) + 10% de markup
    sug = site.get("/emissoes/sugestao", params={"programa": "Smiles", "pontos": "50.000", "agencia": ag.id}).json()
    assert (sug["tipo"], sug["milheiro"], sug["total"]) == ("B2B", 22, 1210.0)
    sug = site.get("/emissoes/sugestao", params={"programa": "Smiles", "pontos": "50000"}).json()
    assert (sug["tipo"], sug["total"]) == ("B2C", 1485.0)

    r = site.post("/emissoes/nova", data={
        "forma": "programa_proprio", "conta_id": conta.id, "pontos": "50.000", "preco_venda": "1.800,00",
        "origem": "FOR", "destino": "GRU", "agencia_id": ag.id, "pedido_id": pedido.id,
        "passageiro_nome": ["Maria Lima", "João Lima"], "passageiro_cpf": ["12345678909", "98765432100"]})
    assert "Pedido nº 1 marcado como emitido" in r.text
    e = db.query(Emissao).one()
    db.refresh(pedido)
    assert (pedido.status, pedido.emissao_id, pedido.atendido_por, e.agencia_id) == ("emitido", e.id, "Administrador", ag.id)
    assert "Viaje Bem" in site.get(f"/emissoes/{e.id}").text and "nº 1" in site.get(f"/agencias/{ag.id}").text

    # reabrir e cancelar
    site.post(f"/pedidos/{pedido.id}/status", data={"status": "aguardando"})
    db.refresh(pedido)
    assert (pedido.status, pedido.emissao_id) == ("aguardando", None)
    site.post(f"/pedidos/{pedido.id}/status", data={"status": "cancelado"})
    db.refresh(pedido)
    assert pedido.status == "cancelado"
    assert "Informe quem pediu" in site.post("/pedidos/novo", data={"origem": "FOR", "destino": "REC"}).text
