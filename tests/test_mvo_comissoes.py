"""Emissor de cada emissão, comissões do mês, clientes no Manager e página inicial do Manager."""
import io
from datetime import date, datetime

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import app
from app.models import Cliente, Usuario
from app.models_mvo import Colaborador, ContaPrograma, Emissao
from app.mvo.comissoes import comissoes_do_mes, ler_mes, mes_vizinho
from app.mvo.contas import lancar
from app.mvo.emissoes import criar_emissao
from app.mvo.migracoes import preparar_mvo
from app.servicos import gerar_hash_senha


def _usuario(db, nome, papel="emissor", **extra):
    u = Usuario(nome=nome, login=nome.split()[0].lower(), senha_hash=gerar_hash_senha("senha123"), papel=papel, **extra)
    db.add(u)
    db.commit()
    return u


def _conta(db, saldo=100_000, custo=20.0):
    conta = ContaPrograma(colaborador=Colaborador(nome="Ana Souza", cpf="52998224725"), programa="Smiles")
    db.add(conta)
    db.flush()
    lancar(db, conta, "compra", saldo, preco_milheiro=custo, data_efetivacao=date(2026, 1, 2))
    db.commit()
    return conta


def _site(login, senha="senha123"):
    site = TestClient(app)
    site.__enter__()
    site.post("/login", data={"login": login, "senha": senha})
    return site


def test_mes_e_vizinhos():
    assert ler_mes("2026-10") == (2026, 10)
    assert ler_mes("bobagem", hoje=datetime(2026, 3, 5).date()) == (2026, 3)
    assert mes_vizinho(2026, 1, -1) == "2025-12" and mes_vizinho(2026, 12, 1) == "2027-01"


def test_comissao_do_mes_por_emissor(db):
    rafa = _usuario(db, "Rafael Emissor", comissao_pct=10, comissao_base="lucro")
    julia = _usuario(db, "Julia Vendas", comissao_pct=2, comissao_base="venda")
    conta = _conta(db)
    out = datetime(2026, 10, 10, 12)

    def emitir(emissor, pontos, venda, quando=out, **extra):
        return criar_emissao(db, conta=conta, pontos=pontos, preco_venda=venda, data_emissao=quando, forcar=True,
                             emissor_id=emissor.id, emissor_nome=emissor.nome, usuario="Administrador", **extra)

    emitir(rafa, 10_000, 500)                       # custo 200, lucro 300
    emitir(rafa, 5_000, 300, taxa_cartao=10)        # custo 100 + 10, lucro 190
    emitir(julia, 20_000, 1000)                     # custo 400, lucro 600
    cancelada = emitir(julia, 1_000, 900)
    cancelada.status = "cancelada"
    emitir(julia, 1_000, 999, quando=datetime(2026, 9, 30, 23, 59))  # outro mês
    db.commit()

    linhas = {l.emissor_nome: l for l in comissoes_do_mes(db, 2026, 10)}
    assert set(linhas) == {"Rafael Emissor", "Julia Vendas"}
    r, j = linhas["Rafael Emissor"], linhas["Julia Vendas"]
    assert (r.quantidade, r.vendido, r.lucro, r.comissao) == (2, 800.0, 490.0, 49.0)  # 10% do lucro
    assert (j.quantidade, j.vendido, j.lucro, j.comissao) == (1, 1000.0, 600.0, 20.0)  # 2% da venda

    adm = _site("admin", "admin123")
    try:
        tela = adm.get("/comissoes?mes=2026-10")
        assert tela.status_code == 200 and "outubro de 2026" in tela.text and "R$ 49,00" in tela.text
        # muda o percentual do Rafael para 15% sobre a venda
        r2 = adm.post("/comissoes/percentuais", data={"mes": "2026-10", f"pct_{rafa.id}": "15",
                                                      f"base_{rafa.id}": "venda", f"pct_{julia.id}": "2",
                                                      f"base_{julia.id}": "venda"})
        assert "Percentuais de comissão salvos" in r2.text and "R$ 120,00" in r2.text  # 15% de 800
        planilha = adm.get("/comissoes/2026-10.xlsx")
        assert planilha.status_code == 200
        wb = load_workbook(io.BytesIO(planilha.content))
        assert wb.sheetnames == ["Comissões", "Emissões"] and wb["Emissões"].max_row == 4  # cabeçalho + 3 emissões
        # lista de emissões filtrada pelo mês e pelo emissor
        lista = adm.get(f"/emissoes?mes=2026-10&emissor={julia.id}")
        assert "Julia Vendas" in lista.text and lista.text.count('class="codigo"') == 1
    finally:
        adm.__exit__(None, None, None)

    emissor = _site("rafael")
    try:
        assert emissor.get("/comissoes").status_code == 403  # sem acesso a custos
    finally:
        emissor.__exit__(None, None, None)


def test_formulario_grava_quem_emitiu_e_pagamento(db):
    julia = _usuario(db, "Julia Vendas")
    conta = _conta(db)
    adm = _site("admin", "admin123")
    try:
        tela = adm.get(f"/emissoes/nova?conta={conta.id}")
        assert "6. Emissor e pagamento" in tela.text and "Julia Vendas" in tela.text
        r = adm.post("/emissoes/nova", data={
            "forma": "programa_proprio", "conta_id": conta.id, "pontos": "10.000", "preco_venda": "500",
            "localizador": "ZZT123", "emissor_id": julia.id, "metodo_pagamento": "PIX", "conta_recebedora": "Stone",
            "taxa_cartao": "12,50", "conexoes": "1", "passageiro_nome": "Maria", "passageiro_cpf": "",
        })
        assert "Emissão registrada" in r.text and "emitida por <b>Julia Vendas</b>" in r.text
        e = db.query(Emissao).one()
        db.refresh(e)
        assert (e.emissor_id, e.emissor_nome, e.metodo_pagamento, e.taxa_cartao, e.conexoes) == (julia.id, "Julia Vendas", "PIX", 12.5, 1)
        assert e.lucro == 500 - 200 - 12.5
        # troca o emissor depois
        adm.post(f"/emissoes/{e.id}", data={"emissor_id": str(db.query(Usuario).filter_by(login="admin").one().id),
                                            "metodo_pagamento": "Boleto", "localizador": "ZZT123"})
        db.refresh(e)
        assert (e.emissor_nome, e.metodo_pagamento, e.taxa_cartao) == ("Administrador", "Boleto", 12.5)
    finally:
        adm.__exit__(None, None, None)


def test_emissoes_antigas_ganham_emissor(db):
    rafa = _usuario(db, "Rafael Emissor")
    conta = _conta(db)
    e = criar_emissao(db, conta=conta, pontos=1000, usuario="Rafael Emissor", forcar=True)
    e.emissor_id = e.emissor_nome = None
    db.commit()
    preparar_mvo(db)
    db.refresh(e)
    assert (e.emissor_id, e.emissor_nome) == (rafa.id, "Rafael Emissor")


def test_clientes_no_manager_e_inicio(db):
    conta = _conta(db)
    adm = _site("admin", "admin123")
    try:
        r = adm.post("/manager/clientes/novo", data={"nome": "MARIA OLIVEIRA", "telefone": "(85) 99999-0000",
                                                     "documento": "529.982.247-25", "data_nascimento": "1990-05-02",
                                                     "classificacao": "VIP"})
        assert "Cliente Maria Oliveira cadastrado" in r.text
        cliente = db.query(Cliente).one()
        assert (cliente.documento, str(cliente.data_nascimento), cliente.classificacao) == ("52998224725", "1990-05-02", "VIP")
        assert "já é de Maria Oliveira" in adm.post("/manager/clientes/novo", data={"nome": "Outra", "telefone": "85999990000"}).text

        tela = adm.get(f"/emissoes/nova?cliente={cliente.id}")
        assert 'value="Maria Oliveira"' in tela.text
        criar_emissao(db, conta=conta, pontos=10_000, preco_venda=700, cliente_id=cliente.id, cliente_nome=cliente.nome,
                      localizador="CLI001", usuario="Administrador", passageiros=[("Maria Oliveira", "52998224725")])
        db.commit()
        ficha = adm.get(f"/manager/clientes/{cliente.id}")
        assert "CLI001" in ficha.text and "Quem já viajou" in ficha.text and "R$ 700" in ficha.text
        lista = adm.get("/manager/clientes?ordem=vendido&ver=compraram")
        assert "Maria Oliveira" in lista.text
        inicio = adm.get("/manager")
        assert inicio.status_code == 200 and "Smiles" in inicio.text and "90.000 pts" in inicio.text
        assert 'href="/manager/clientes"' in inicio.text  # menu do Manager com a área de clientes
    finally:
        adm.__exit__(None, None, None)
