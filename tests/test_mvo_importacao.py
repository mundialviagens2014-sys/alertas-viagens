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


def test_situacao_do_manager_antigo():
    # o Manager antigo marca como SUSPENSO quem não deve mais ser usado
    assert [imp._ativo(v) for v in ("ATIVO", "", "SUSPENSO", "Suspensa", "INATIVO", "bloqueado")] == [
        True, True, False, False, False, False]


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


def test_importa_agencias_com_franquia_e_pessoas_com_endereco(db):
    from datetime import date

    from app.models_mvo import Agencia, Franquia

    agencias = _xlsx([
        ["Agência", "CPF ou CNPJ", "Telefone 1", "E-mail Comercial", "E-mail Financeiro", "Franquia", "Status",
         "Nome Completo ou Razão social", "Cidade", "Estado"],
        ["Viaje Bem", "11.222.333/0001-81", "(85) 3333-4444", "contato@viajebem.com", "fin@viajebem.com", "", "ATIVO",
         "Viaje Bem Turismo LTDA", "Fortaleza", "ce"],
        ["Rota Sul", "", "(51) 99999-1234", "", "", "Rede Sol", "ATIVO", "", "", ""],
        ["", "123", "", "", "", "", "", "", "", ""],
    ])
    pessoas = _xlsx([
        ["Nome", "CPF", "RG", "Data Nascimento", "CEP", "Endereço", "Número", "Bairro", "Cidade", "Estado", "Perfil"],
        ["Ana Souza", "529.982.247-25", "2009123", "20/06/1988", "63700150", "Rua das Flores", "12", "Centro",
         "Crateús", "CE", "FIDELIZADO"],
    ])
    site = _site()
    try:
        r = site.post("/importar-manager", data={"tipo": "agencias"}, files={"arquivo": ("agencias.xlsx", agencias, XLSX)})
        assert "franquia “Rede Sol” nova: será cadastrada" in r.text and "sem nome da agência" in r.text
        token = r.text.split('name="token" value="')[1].split('"')[0]
        r = site.post("/importar-manager/confirmar", data={"token": token, "tipo": "agencias"})
        assert "2 agência(s)" in r.text
        r = site.post("/importar-manager", data={"tipo": "pessoas"}, files={"arquivo": ("pessoas.xlsx", pessoas, XLSX)})
        token = r.text.split('name="token" value="')[1].split('"')[0]
        site.post("/importar-manager/confirmar", data={"token": token, "tipo": "pessoas"})
    finally:
        site.__exit__(None, None, None)
    viaje = db.query(Agencia).filter_by(nome="Viaje Bem").one()
    assert (viaje.documento, viaje.email_financeiro, viaje.razao_social, viaje.estado, viaje.tipo_negocio) == (
        "11222333000181", "fin@viajebem.com", "Viaje Bem Turismo LTDA", "CE", "B2B")
    rota = db.query(Agencia).filter_by(nome="Rota Sul").one()
    assert (rota.franquia.nome, rota.tipo_negocio) == ("Rede Sol", "B2F")
    assert db.query(Franquia).count() == 1
    ana = db.query(Colaborador).one()
    assert (ana.rg, ana.data_nascimento, ana.cep, ana.numero, ana.cidade) == (
        "2009123", date(1988, 6, 20), "63700-150", "12", "Crateús")


def test_reimportar_reconhece_o_que_ja_existe(db):
    from app.models_mvo import Agencia, Franquia

    db.add_all([Agencia(nome="Viaje Bem", documento="11222333000181"), Agencia(nome="Rota Sul"),
                Franquia(nome="Rede Sol"),
                Cliente(nome="João Pereira", telefone="5588997123344", consentimento=False)])
    db.commit()
    titulos = ["Agência", "CPF ou CNPJ", "Telefone 1", "E-mail Comercial", "E-mail Financeiro", "Franquia", "Status"]
    corpo = [(2, ["Outro Nome", "11.222.333/0001-81", "", "", "", "", ""]),  # mesmo CNPJ
             (3, ["ROTA SUL", "", "", "", "", "Rede Sol", ""]),  # mesmo nome; franquia já cadastrada
             (4, ["Nova", "", "", "", "", "", ""])]
    itens = imp.analisar(db, "agencias", imp._mapear(titulos, "agencias"), corpo)
    assert [i.acao for i in itens] == ["atualizar", "atualizar", "criar"]
    assert not any("franquia" in m for i in itens for m in i.mensagens)
    itens = imp.analisar(db, "pessoas", imp._mapear(["Nome", "Telefone", "Perfil"], "pessoas"),
                         [(2, ["João P.", "(88) 99712-3344", "CLIENTE"])])
    assert itens[0].acao == "atualizar"


def test_telefone_repetido_so_junta_o_mesmo_cliente(db):
    # no Manager antigo o telefone da agência aparece em vários clientes diferentes
    titulos = ["Nome", "CPF", "Telefone", "Perfil"]
    corpo = [(2, ["Aline Ferreira", "", "(88) 98887-0800", "CLIENTE"]),
             (3, ["Ana Cláudia", "", "(88) 98887-0800", "CLIENTE"]),  # outra pessoa, mesmo telefone
             (4, ["Aline F.", "", "(88) 98887-0800", "CLIENTE"]),  # a mesma Aline
             (5, ["Bruno Souza", "529.982.247-25", "(88) 98887-0800", "CLIENTE"]),
             (6, ["Bruno Souza", "111.444.777-35", "(88) 98887-0800", "CLIENTE"])]  # homônimo com outro CPF
    itens = imp.analisar(db, "pessoas", imp._mapear(titulos, "pessoas"), corpo)
    assert [i.acao for i in itens] == ["criar", "criar", "ignorar", "criar", "criar"]
    imp.aplicar(db, "pessoas", itens)
    db.commit()
    assert db.query(Cliente).count() == 4
    # importar de novo: cada um acha o próprio cadastro
    itens = imp.analisar(db, "pessoas", imp._mapear(titulos, "pessoas"), corpo)
    assert [i.acao for i in itens] == ["atualizar", "atualizar", "ignorar", "atualizar", "atualizar"]


def _xlsx_abas(abas, ativa=0) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for nome, linhas in abas:
        ws = wb.create_sheet(nome)
        for linha in linhas:
            ws.append(linha)
    wb.active = ativa
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


def test_planilha_de_agencias_com_tipo_errado_e_outra_aba_aberta(db):
    from app.models_mvo import Agencia

    # salva com a aba “Leia-me” aberta e enviada com “pessoas” marcado (o padrão da tela)
    planilha = _xlsx_abas([
        ("Agências", [["Agência", "CPF ou CNPJ", "Telefone 1", "E-mail Comercial", "Status", "Posição no Manager"],
                      ["Viaje Bem", "11.222.333/0001-81", "(85) 3333-4444", "a@b.com", "ATIVO", 21],
                      ["Rota Sul", "", "+55 (51) 9 9999-1234", "", "ATIVO", 22]]),
        ("Leia-me", [["Agências do Manager antigo"], ["Como importar: ..."]]),
    ], ativa=1)
    site = _site()
    try:
        r = site.post("/importar-manager", data={"tipo": "pessoas"},
                      files={"arquivo": ("agencias.xlsx", planilha, XLSX)})
        assert r.status_code == 200
        assert "Esta planilha é de “Agências parceiras”" in r.text
        assert "Confirmar importação de 2 linhas" in r.text
        token = r.text.split('name="token" value="')[1].split('"')[0]
        tipo = r.text.split('name="tipo" value="')[1].split('"')[0]
        assert tipo == "agencias"
        r = site.post("/importar-manager/confirmar", data={"token": token, "tipo": tipo})
        assert "2 agência(s)" in r.text
    finally:
        site.__exit__(None, None, None)
    assert {a.nome for a in db.query(Agencia)} == {"Viaje Bem", "Rota Sul"}


def test_lista_do_manager_enviada_na_importacao_dos_alertas(db):
    planilha = _xlsx([
        ["Nome", "CPF/CNPJ", "Telefone", "E-mail Pessoal", "Perfil", "Status"],
        ["ANA PAULA SOUZA PG 16/05", "529.982.247-25", "+55 (85) 9 8888-7777", "", "FIDELIZADO", "ATIVO"],
        ["João Pereira", "", "+5588997123344", "", "CLIENTE", "SUSPENSO"],
    ])
    site = _site()
    try:
        r = site.post("/clientes/importar", files={"arquivo": ("clientes.xlsx", planilha, XLSX)})
        assert r.status_code == 200
        assert "Esta planilha é do Manager" in r.text and "Confirmar importação de 2 linhas" in r.text
        token = r.text.split('name="token" value="')[1].split('"')[0]
        r = site.post("/importar-manager/confirmar", data={"token": token, "tipo": "pessoas"})
        assert "1 colaborador(es), 1 cliente(s)" in r.text
    finally:
        site.__exit__(None, None, None)
    assert db.query(Colaborador).one().cpf == "52998224725"
    joao = db.query(Cliente).one()
    assert (joao.telefone, joao.ativo) == ("5588997123344", False)


def test_botoes_de_importar_nas_listas(db):
    site = _site()
    try:
        assert 'href="/importar-manager?tipo=agencias"' in site.get("/agencias").text
        assert 'href="/importar-manager?tipo=pessoas"' in site.get("/manager/clientes").text
        assert 'href="/importar-manager?tipo=pessoas"' in site.get("/colaboradores").text
        tela = site.get("/importar-manager?tipo=agencias").text
        assert 'value="agencias" checked' in tela and 'href="/agencias">← Agências' in tela
    finally:
        site.__exit__(None, None, None)


def test_conferencia_de_planilha_grande_mostra_amostra(db):
    linhas = [["Nome", "CPF", "Telefone", "Perfil"]]
    linhas += [[f"Pessoa {n:04d}", "", f"(85) 9{n:04d}-0000", "FIDELIZADO"] for n in range(1, 451)]
    linhas.append(["Cliente Sem Fone", "", "", "CLIENTE"])
    site = _site()
    try:
        r = site.post("/importar-manager", data={"tipo": "pessoas"},
                      files={"arquivo": ("grande.xlsx", _xlsx(linhas), XLSX)})
        assert "Confirmar importação de 450 linhas" in r.text
        assert "cliente sem telefone" in r.text  # linha com erro aparece mesmo no fim da planilha
        assert "e mais 151 linhas sem observação" in r.text
    finally:
        site.__exit__(None, None, None)
