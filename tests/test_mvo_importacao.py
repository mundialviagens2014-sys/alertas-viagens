"""Importação das planilhas do Manager: pessoas, contas com saldo e fornecedores."""
import io

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.cripto import decifrar
from app.main import app
from app.models import Cliente, ProgramaMilhas, Usuario
from app.models_mvo import Colaborador, ContaPrograma, Fornecedor, Lancamento
from app.mvo import importar_manager as imp
from app.servicos import gerar_hash_senha

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(linhas) -> bytes:
    wb = Workbook()
    for linha in linhas:
        wb.active.append(linha)
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


def _site():
    site = TestClient(app)
    site.__enter__()
    site.post("/login", data={"login": "admin", "senha": "admin123"})
    return site


def test_importa_pessoas_separando_colaboradores_e_clientes(db):
    planilha = _xlsx([
        ["Relatório de clientes — Manager", None, None],  # cabeçalho do relatório antes dos títulos
        [],
        ["Nome", "CPF", "Celular", "E-mail", "Perfil", "Status", "Observação"],
        ["ANA PAULA SOUZA", "529.982.247-25", "(85) 98888-7777", "ana@x.com", "FIDELIZADO", "ATIVO", ""],
        ["João Pereira", "", 88997123344, "", "CLIENTE", "ATIVO", "comprou FOR-GRU"],
        ["Sem Telefone", "", "", "", "CLIENTE", "", ""],
        ["ana paula souza", "52998224725", "", "", "FIDELIZADO", "", ""],
        ["Bruno Carvalho", "111.444.777-35", "", "", "FIDELIZADO", "INATIVO", "não usar"],
    ])
    site = _site()
    try:
        r = site.post("/importar-manager", data={"tipo": "pessoas"}, files={"arquivo": ("clientes.xlsx", planilha, XLSX)})
        assert r.status_code == 200
        assert "cliente sem telefone" in r.text and "repetida (igual à linha 4)" in r.text
        assert "Confirmar importação de 3 linhas" in r.text
        token = r.text.split('name="token" value="')[1].split('"')[0]
        assert db.query(Colaborador).count() == 0  # conferência não grava nada

        r = site.post("/importar-manager/confirmar", data={"token": token, "tipo": "pessoas", "perfil_padrao": "colaborador"})
        assert "Importação concluída: 2 colaborador(es), 1 cliente(s)" in r.text
    finally:
        site.__exit__(None, None, None)
    ana = db.query(Colaborador).filter_by(cpf="52998224725").one()
    assert (ana.nome, ana.telefone, ana.email, ana.ativo) == ("Ana Paula Souza", "5585988887777", "ana@x.com", True)
    assert db.query(Colaborador).filter_by(cpf="11144477735").one().ativo is False
    joao = db.query(Cliente).one()
    assert (joao.nome, joao.telefone, joao.consentimento) == ("João Pereira", "5588997123344", False)


def test_importa_contas_com_saldo_custo_e_senha(db):
    db.add(Colaborador(nome="Ana Paula Souza", cpf="52998224725"))
    db.commit()
    csv = ("Fidelizado;CPF;Programa;Saldo;Custo médio do milheiro;Login;Senha;Observação\n"
           "Ana Paula Souza;529.982.247-25;SMILES;45.000;17,50;52998224725;s3nh@;\n"
           "Ana Paula Souza;529.982.247-25;LATAM_PASS;12000;;;;sem custo\n"
           "Carlos Novo;;TUDO_AZUL;20.000;0,015;;;\n"
           ";;SMILES;1000;;;;\n").encode("utf-8")
    site = _site()
    try:
        r = site.post("/importar-manager", data={"tipo": "contas"}, files={"arquivo": ("contas.csv", csv, "text/csv")})
        assert "sem nome nem CPF" in r.text and "colaborador novo: será cadastrado" in r.text
        assert "77.000" in r.text  # pontos de saldo inicial
        token = r.text.split('name="token" value="')[1].split('"')[0]
        r = site.post("/importar-manager/confirmar", data={"token": token, "tipo": "contas"})
        assert "3 conta(s) com 77.000 pontos de saldo inicial" in r.text

        smiles = db.query(ContaPrograma).filter_by(programa="Smiles").one()
        assert (smiles.saldo, smiles.custo_medio) == (45_000, 17.5)
        assert decifrar(smiles.login_cripto) == "52998224725" and decifrar(smiles.senha_cripto) == "s3nh@"
        latam = db.query(ContaPrograma).filter_by(programa="LATAM Pass").one()
        assert (latam.saldo, latam.custo_medio, latam.observacoes) == (12_000, None, "sem custo")
        azul = db.query(ContaPrograma).filter_by(programa="Azul Fidelidade").one()
        assert (azul.colaborador.nome, azul.saldo, azul.custo_medio) == ("Carlos Novo", 20_000, 15.0)

        # importar de novo não duplica: contas com lançamentos mantêm o saldo do sistema
        csv2 = csv.replace(b"45.000", b"50.000")
        r = site.post("/importar-manager", data={"tipo": "contas"}, files={"arquivo": ("contas.csv", csv2, "text/csv")})
        assert "saldo do sistema (45.000) mantido, planilha diz 50.000" in r.text
        token = r.text.split('name="token" value="')[1].split('"')[0]
        site.post("/importar-manager/confirmar", data={"token": token, "tipo": "contas"})
        assert db.query(Lancamento).count() == 3 and db.query(ContaPrograma).count() == 3
        db.expire_all()
        assert db.get(ContaPrograma, smiles.id).saldo == 45_000
        assert "Saldo importado do Manager" in site.get(f"/contas/{smiles.id}").text
    finally:
        site.__exit__(None, None, None)


def test_importa_fornecedores_e_reconhece_programas(db):
    titulos, mapa, corpo = imp.ler(_xlsx([["Fornecedor", "CNPJ", "WhatsApp", "Prazo (dias)"],
                                          ["Balcão Nordeste", "12.345.678/0001-90", "85991234567", 2],
                                          ["balcão nordeste", "", "", ""]]), "f.xlsx", "fornecedores")
    itens = imp.analisar(db, "fornecedores", mapa, corpo)
    assert [i.acao for i in itens] == ["criar", "ignorar"]
    imp.aplicar(db, "fornecedores", itens)
    f = db.query(Fornecedor).one()
    assert (f.documento, f.telefone, f.dias_pagamento) == ("12345678000190", "5585991234567", 2)

    from app.programas import nome_programa
    assert [nome_programa(n) for n in ("SMILES", "LATAM_PASS", "TUDO_AZUL", "AMERICAN_AIRLINES", "TAP", "livelo")] == [
        "Smiles", "LATAM Pass", "Azul Fidelidade", "AAdvantage", "TAP Miles&Go", "Livelo"]
    assert db.query(ProgramaMilhas).filter_by(nome="Esfera").count() == 1


def test_so_quem_cuida_dos_custos_importa(db):
    db.add(Usuario(nome="Emissor", login="emissor", senha_hash=gerar_hash_senha("senha123"), papel="emissor"))
    db.commit()
    with TestClient(app) as site:
        site.post("/login", data={"login": "emissor", "senha": "senha123"})
        assert site.get("/importar-manager").status_code == 403
    with TestClient(app) as site:
        site.post("/login", data={"login": "admin", "senha": "admin123"})
        modelo = site.get("/importar-manager/modelo/contas.xlsx")
        assert modelo.status_code == 200 and modelo.content[:2] == b"PK"


def test_homonimos_com_cpfs_diferentes_sao_pessoas_diferentes(db):
    db.add(Colaborador(nome="João Silva", cpf="11144477735"))
    db.commit()
    _, mapa, corpo = imp.ler(_xlsx([["Nome", "CPF", "Perfil"],
                                    ["João Silva", "529.982.247-25", "FIDELIZADO"],   # outro João: novo
                                    ["João Silva", "111.444.777-35", "FIDELIZADO"],   # o já cadastrado
                                    ["Maria Souza", "390.533.447-05", "FIDELIZADO"],
                                    ["Maria Souza", "", "FIDELIZADO"]]), "p.xlsx", "pessoas")  # mesma Maria, sem CPF
    acoes = [(i.numero, i.acao) for i in imp.analisar(db, "pessoas", mapa, corpo)]
    assert acoes == [(2, "criar"), (3, "atualizar"), (4, "criar"), (5, "ignorar")]
