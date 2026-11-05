"""Manager: agências, contratos, compras externas, lista de programas (contas), dados extras e o menu completo."""
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.cripto import decifrar
from app.config import settings
from app.main import app
from app.models import ProgramaMilhas, Usuario
from app.models_mvo import (
    Agencia,
    Colaborador,
    CompraExterna,
    ContaPrograma,
    Contrato,
    Emissao,
    Fornecedor,
    Franquia,
    Lancamento,
)
from app.mvo.contas import lancar
from app.servicos import gerar_hash_senha

FETCH = {"X-Requested-With": "fetch"}


def _entrar(login, senha="senha123"):
    site = TestClient(app)
    site.__enter__()
    assert site.post("/login", data={"login": login, "senha": senha}).status_code == 200
    return site


@pytest.fixture()
def equipe(db):
    for login, papel in (("gestora", "gestor"), ("emissor", "emissor")):
        db.add(Usuario(nome=login.title(), login=login, senha_hash=gerar_hash_senha("senha123"), papel=papel))
    db.commit()
    sites = {"admin": _entrar("admin", "admin123"), "gestora": _entrar("gestora"), "emissor": _entrar("emissor")}
    yield sites
    for s in sites.values():
        s.__exit__(None, None, None)


def _colaborador(db, nome="Ana Souza", cpf="52998224725") -> Colaborador:
    col = Colaborador(nome=nome, cpf=cpf)
    db.add(col)
    db.commit()
    return col


def test_agencias(db, equipe):
    adm = equipe["admin"]
    db.add(Franquia(nome="Rede Sol"))
    db.commit()
    franquia = db.query(Franquia).one()
    r = adm.post("/agencias/nova", data={
        "nome": "Viaje Bem", "razao_social": "Viaje Bem Turismo LTDA", "documento": "11.222.333/0001-81",
        "telefone": "(85) 3333-4444", "email_comercial": "contato@viajebem.com", "tipo_negocio": "B2B",
        "franquia_id": str(franquia.id), "cep": "60000000", "cidade": "Fortaleza", "estado": "ce",
    })
    assert "Agência Viaje Bem cadastrada" in r.text
    ag = db.query(Agencia).one()
    assert (ag.documento, ag.cep, ag.estado, ag.franquia_id) == ("11222333000181", "60000-000", "CE", franquia.id)
    assert "11.222.333/0001-81" in adm.get("/agencias").text
    assert "Já existe uma agência com esse CPF/CNPJ" in adm.post("/agencias/nova", data={"nome": "Outra",
                                                                                     "documento": "11222333000181"}).text
    assert "Fortaleza" in adm.get(f"/agencias/{ag.id}").text

    r = adm.post(f"/agencias/{ag.id}/editar", data={"nome": "Viaje Bem", "tipo_negocio": "B2F", "ativo": "true",
                                                    "franquia_id": str(franquia.id)})
    assert "Agência salva" in r.text
    db.expire_all()
    assert db.get(Agencia, ag.id).tipo_negocio == "B2F"

    # com emissão não exclui (desativa); sem nada, exclui
    db.add(Emissao(forma="pagante", agencia_id=ag.id, preco_venda=900))
    db.commit()
    assert "desmarque" in adm.post(f"/agencias/{ag.id}/excluir").text
    assert equipe["emissor"].post(f"/agencias/{ag.id}/editar", data={"nome": "X"}).status_code == 200  # emissor opera
    vazia = Agencia(nome="Sem uso")
    db.add(vazia)
    db.commit()
    assert "excluída" in adm.post(f"/agencias/{vazia.id}/excluir").text


def test_contratos(db, equipe):
    adm, emissor = equipe["admin"], equipe["emissor"]
    col = _colaborador(db)
    vence = (date.today() + timedelta(days=10)).isoformat()
    r = adm.post("/contratos/novo", data={"colaborador_id": col.id, "tipo": "diamante", "programa": "LATAM Pass",
                                          "data_inicio": date.today().isoformat(), "data_vencimento": vence,
                                          "valor_resgate": "750,00", "status": "ativo",
                                          "voltar_para": f"/colaboradores/{col.id}#contratos"})
    assert "Contrato de Ana Souza incluído" in r.text and "DIAMANTE" in r.text and "vence logo" in r.text
    k = db.query(Contrato).one()
    assert (k.tipo, k.valor_resgate) == ("DIAMANTE", 750.0)

    lista = adm.get("/contratos?status=ativo&vencimento=30dias").text
    assert "Ana Souza" in lista and "R$ 750,00" in lista
    assert "R$ 750,00" not in emissor.get("/contratos").text  # emissor não vê valores

    # emissor edita sem apagar o valor que não vê
    emissor.post(f"/contratos/{k.id}", data={"tipo": "OURO", "status": "ativo", "data_vencimento": vence})
    db.expire_all()
    k = db.get(Contrato, k.id)
    assert (k.tipo, k.valor_resgate) == ("OURO", 750.0)
    adm.post(f"/contratos/{k.id}", data={"tipo": "OURO", "status": "encerrado",
                                         "data_vencimento": (date.today() - timedelta(days=1)).isoformat()})
    link = f'href="/colaboradores/{col.id}#contratos"'
    assert link not in adm.get("/contratos").text  # padrão: só ativos
    assert link in adm.get("/contratos?status=todos").text
    adm.post(f"/contratos/{k.id}/excluir")
    assert db.query(Contrato).count() == 0


def test_compra_externa_entra_na_conta_quando_recebida(db, equipe):
    adm = equipe["admin"]
    col = _colaborador(db)
    conta = ContaPrograma(colaborador=col, programa="Smiles")
    db.add(conta)
    db.add(Fornecedor(nome="Balcão do Zé"))
    db.commit()
    forn = db.query(Fornecedor).one()

    r = adm.post("/compras-externas/nova", data={"programa": "Smiles", "fornecedor_id": forn.id, "pontos": "100.000",
                                                 "preco_milheiro": "17,50", "forma_pagamento": "PIX", "status": "pendente",
                                                 "vencimento": (date.today() - timedelta(days=2)).isoformat()})
    assert "Compra registrada" in r.text and "com vencimento passado" in r.text
    compra = db.query(CompraExterna).one()
    assert (compra.valor, compra.lancamento_id) == (1750.0, None)

    # sem conta não marca recebida
    assert "Escolha a conta" in adm.post(f"/compras-externas/{compra.id}/status", data={"status": "recebida"}).text
    adm.post(f"/compras-externas/{compra.id}/status", data={"status": "paga"})
    dados = {"programa": "Smiles", "fornecedor_id": forn.id, "pontos": "100.000", "valor": "1.750,00",
             "status": "paga", "conta_id": conta.id, "forma_pagamento": "PIX"}
    adm.post(f"/compras-externas/{compra.id}", data=dados)
    r = adm.post(f"/compras-externas/{compra.id}/status", data={"status": "recebida"})
    assert "Pontos recebidos" in r.text
    db.expire_all()
    conta = db.get(ContaPrograma, conta.id)
    assert (conta.saldo, conta.custo_medio) == (100_000, 17.5)
    lanc = db.query(Lancamento).filter_by(conta_id=conta.id, status="ativo").one()
    assert (lanc.tipo, lanc.valor, lanc.fornecedor_id) == ("compra", 1750.0, forn.id)

    # corrigir os pontos refaz o lançamento; cancelar a compra tira os pontos
    adm.post(f"/compras-externas/{compra.id}", data={**dados, "status": "recebida", "pontos": "80.000"})
    db.expire_all()
    assert db.get(ContaPrograma, conta.id).saldo == 80_000
    assert db.query(Lancamento).filter_by(status="cancelado").count() == 1
    adm.post(f"/compras-externas/{compra.id}/status", data={"status": "cancelada"})
    db.expire_all()
    assert (db.get(ContaPrograma, conta.id).saldo, db.get(CompraExterna, compra.id).lancamento_id) == (0, None)

    # conta de outro programa: recusa
    r = adm.post("/compras-externas/nova", data={"programa": "LATAM Pass", "pontos": "1000", "valor": "30",
                                                 "conta_id": conta.id, "status": "pendente"})
    assert r.status_code == 400 and "é do Smiles" in r.text
    # emissor (sem custos) não registra compra
    assert equipe["emissor"].get("/compras-externas/nova").status_code == 403
    assert equipe["emissor"].get("/compras-externas").status_code == 200


def test_lista_de_programas_e_login_com_dados_do_titular(db, equipe):
    adm, gestora = equipe["admin"], equipe["gestora"]
    col = _colaborador(db)
    r = adm.post(f"/colaboradores/{col.id}", data={
        "nome": "Ana Souza", "cpf": "529.982.247-25", "rg": "2009 123", "data_nascimento": "1988-06-20",
        "cep": "63700150", "endereco": "Rua das Flores", "numero": "12", "cidade": "Crateús", "estado": "CE",
        "classificacao": "VIP", "consolidador": "Grupo Norte", "ativo": "true"})
    assert "Cadastro salvo" in r.text
    db.expire_all()
    col = db.get(Colaborador, col.id)
    assert (col.rg, col.data_nascimento, col.cep, col.cidade) == ("2009 123", date(1988, 6, 20), "63700-150", "Crateús")

    r = adm.post("/contas/nova", data={"colaborador_id": col.id, "programa": "LATAM Pass", "login": "ana.latam",
                                       "senha": "Segredo#1", "token": "tok-123", "email_programa": "ana@latam.com",
                                       "codigo_conta": "LA-77", "saldo": "50.000", "custo_milheiro": "20",
                                       "saldo_reservado": "5.000", "observacoes": "só clientes finais"})
    assert "Conta LATAM Pass criada para Ana Souza" in r.text
    conta = db.query(ContaPrograma).filter_by(programa="LATAM Pass").one()
    assert decifrar(conta.token_cripto) == "tok-123" and "tok-123" not in conta.token_cripto
    pessoal = ContaPrograma(colaborador=col, programa="Smiles", uso_pessoal=True)
    db.add(pessoal)
    db.flush()
    lancar(db, pessoal, "compra", 7_000, valor=140)
    db.commit()

    tela = gestora.get("/contas").text
    assert "LATAM Pass" in tela and "45.000" in tela and "R$ 900,00" in tela  # 45 mil × R$ 20
    assert '<span class="etiqueta">uso pessoal</span>' not in tela
    assert '<span class="etiqueta">uso pessoal</span>' in gestora.get("/contas?pessoal=1").text
    assert "Comprar" not in equipe["emissor"].get("/contas").text

    dados = gestora.post(f"/contas/{conta.id}/credenciais", headers=FETCH).json()
    assert (dados["login"], dados["senha"], dados["token"]) == ("ana.latam", "Segredo#1", "tok-123")
    assert (dados["rg"], dados["nascimento"], dados["email_programa"], dados["codigo_conta"]) == (
        "2009 123", "20/06/1988", "ana@latam.com", "LA-77")
    assert "Crateús/CE" in dados["endereco"]

    # programa sem login: o formulário esconde login e senha
    latam = db.query(ProgramaMilhas).filter_by(nome="LATAM Pass").one()
    latam.necessita_login = False
    db.commit()
    assert 'data-login="0">LATAM Pass' in adm.get("/contas/nova").text


def test_programa_que_exige_descricao(db, equipe):
    adm = equipe["admin"]
    smiles = db.query(ProgramaMilhas).filter_by(nome="Smiles").one()
    smiles.exige_descricao = True
    col = _colaborador(db)
    conta = ContaPrograma(colaborador=col, programa="Smiles")
    db.add(conta)
    db.flush()
    lancar(db, conta, "compra", 50_000, valor=900)
    db.commit()
    dados = {"forma": "programa_proprio", "conta_id": conta.id, "pontos": "10.000", "passageiro_nome": ["Maria"],
             "passageiro_cpf": [""]}
    r = adm.post("/emissoes/nova", data=dados)
    assert r.status_code == 400 and "exige descrição" in r.text and "Registrar mesmo assim" not in r.text
    assert "Emissão registrada" in adm.post("/emissoes/nova", data={**dados, "observacao": "cliente final"}).text


def test_fornecedor_com_endereco(db, equipe):
    adm = equipe["admin"]
    adm.post("/fornecedores/novo", data={"nome": "Balcão Norte", "cep": "60.000-000", "cidade": "Fortaleza"})
    f = db.query(Fornecedor).one()
    assert (f.cep, f.cidade) == ("60000-000", "Fortaleza")
    adm.post(f"/fornecedores/{f.id}", data={"nome": "Balcão Norte", "bairro": "Centro", "ativo": "true"})
    db.expire_all()
    assert (db.get(Fornecedor, f.id).bairro, db.get(Fornecedor, f.id).cep) == ("Centro", None)


def test_todos_os_links_do_menu_abrem(db, equipe):
    """Cada item do menu dos dois sistemas abre (administrador, gestor e emissor)."""
    for quem, site in equipe.items():
        for inicio in ("/", "/manager"):
            html = site.get(inicio).text
            inicio_menu = html.index('<nav class="menu"')  # tem id="menu-principal" para o botão Menu do celular
            menu = html[inicio_menu:html.index("</nav>", inicio_menu)]
            links = set(re.findall(r'href="(/[^"#?]*)"', menu))
            assert links, quem
            for link in sorted(links):
                if link == "/sair":
                    continue
                r = site.get(link)
                assert r.status_code == 200, (quem, link, r.status_code)
                assert f"MVO {settings.agencia_nome}</title>" in r.text, (quem, link)  # nada esconde o nome da agência
