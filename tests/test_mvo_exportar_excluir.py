"""Exportar listas em planilha (com filtros, sem senhas, registrado) e excluir cadastros sem quebrar o histórico."""
import io
import re
import zipfile

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import app
from app.models import Cliente, Usuario
from app.models_mvo import (
    Colaborador,
    CompraExterna,
    ContaPrograma,
    Contrato,
    Emissao,
    Fornecedor,
    Lancamento,
    PedidoEmissao,
    RegistroAcesso,
)
from app.cripto import cifrar
from app.mvo.contas import lancar, transferir
from app.mvo.emissoes import cancelar_emissao, criar_emissao
from app.servicos import gerar_hash_senha

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _entrar(login, senha="senha123"):
    site = TestClient(app)
    site.__enter__()
    assert site.post("/login", data={"login": login, "senha": senha}).status_code == 200
    return site


@pytest.fixture()
def equipe(db):
    for login, papel in (("gestora", "gestor"), ("emissor", "emissor"), ("atendente", "atendente")):
        db.add(Usuario(nome=login.title(), login=login, senha_hash=gerar_hash_senha("senha123"), papel=papel))
    db.commit()
    sites = {"admin": _entrar("admin", "admin123")}
    for login in ("gestora", "emissor", "atendente"):
        sites[login] = _entrar(login)
    yield sites
    for s in sites.values():
        s.__exit__(None, None, None)


def _planilha(resposta) -> list[list]:
    assert resposta.status_code == 200, resposta.text[:300]
    assert resposta.headers["content-type"].startswith(XLSX)
    assert "attachment" in resposta.headers["content-disposition"]
    ws = load_workbook(io.BytesIO(resposta.content)).active
    return [[c.value for c in linha] for linha in ws.iter_rows()]


def _conta(db, nome="Ana Souza", cpf="52998224725", programa="Smiles", pontos=0, valor=None) -> ContaPrograma:
    col = db.query(Colaborador).filter_by(nome=nome).first() or Colaborador(nome=nome, cpf=cpf)
    conta = ContaPrograma(colaborador=col, programa=programa)
    db.add(conta)
    db.flush()
    if pontos:
        lancar(db, conta, "compra", pontos, valor=valor)
    db.commit()
    return conta


def test_exportar_colaboradores_e_contas(db, equipe):
    adm, emissor = equipe["admin"], equipe["emissor"]
    conta = _conta(db, pontos=50_000, valor=850)
    conta.login_cripto, conta.senha_cripto = cifrar("ana.login"), cifrar("SenhaSecreta#9")
    db.add(Colaborador(nome="=1+1", cpf="11144477735", ativo=False))  # texto com "=" não vira fórmula
    db.commit()

    assert "Exportar planilha" in adm.get("/colaboradores").text
    linhas = _planilha(adm.get("/colaboradores?ver=todos&formato=xlsx"))
    assert linhas[0][:3] == ["Nome", "CPF", "RG"]
    nomes = [l[0] for l in linhas[1:]]
    assert nomes == ["=1+1", "Ana Souza"] and linhas[2][1] == "529.982.247-25"
    assert [l[0] for l in _planilha(adm.get("/colaboradores?ver=ativos&formato=xlsx"))[1:]] == ["Ana Souza"]  # filtro

    resposta = adm.get("/contas?formato=xlsx")
    linhas = _planilha(resposta)
    titulos = linhas[0]
    assert "Custo médio (R$ por milheiro)" in titulos and "Login salvo" in titulos
    assert linhas[1][titulos.index("Saldo")] == 50_000 and linhas[1][titulos.index("Login salvo")] == "Sim"
    conteudo = b"".join(zipfile.ZipFile(io.BytesIO(resposta.content)).read(n)
                        for n in zipfile.ZipFile(io.BytesIO(resposta.content)).namelist())
    assert b"SenhaSecreta" not in conteudo and b"ana.login" not in conteudo  # senhas nunca vão para a planilha

    registro = db.query(RegistroAcesso).filter_by(acao="exportacao").all()
    assert len(registro) == 3 and "Programas (contas): 1 linha(s)" in registro[-1].detalhe

    # emissor e atendente não exportam (nem veem o botão)
    assert "Exportar planilha" not in emissor.get("/colaboradores").text
    assert emissor.get("/colaboradores?formato=xlsx").status_code == 403
    assert equipe["atendente"].get("/clientes?formato=xlsx").status_code == 403
    assert _planilha(equipe["gestora"].get("/colaboradores?formato=xlsx"))[1][0] == "Ana Souza"


def test_toda_lista_com_botao_exporta(db, equipe):
    """Cada página com o botão "Exportar planilha" (Alertas e Manager) entrega uma planilha válida."""
    adm = equipe["admin"]
    conta = _conta(db, pontos=40_000, valor=700)
    e = criar_emissao(db, conta=conta, pontos=10_000, preco_venda=500, passageiros=[("Maria Lima", "12345678909")],
                      localizador="ABC123")
    db.add_all([Fornecedor(nome="Balcão"), Contrato(colaborador_id=conta.colaborador_id, tipo="OURO"),
                PedidoEmissao(requisitante_nome="Paulo", origem="FOR", destino="LIS"),
                CompraExterna(programa="Smiles", pontos=1000, valor=20)])
    db.commit()
    paginas = set()
    for inicio in ("/", "/manager"):
        html = adm.get(inicio).text
        inicio_menu = html.index('<nav class="menu"')  # tem id="menu-principal" para o botão Menu do celular
        menu = html[inicio_menu:html.index("</nav>", inicio_menu)]
        paginas |= {l for l in re.findall(r'href="(/[^"#?]*)"', menu) if l != "/sair"}
    paginas |= {"/equipe/registro", "/emissoes?de=2020-01-01"}
    exportadas = 0
    for pagina in sorted(paginas):
        html = adm.get(pagina).text
        achado = re.search(r'href="([^"]*formato=xlsx[^"]*)"', html)
        if not achado:
            continue
        linhas = _planilha(adm.get(achado.group(1).replace("&amp;", "&")))
        assert linhas and linhas[0][0], pagina
        exportadas += 1
    assert exportadas >= 20, exportadas
    linhas = _planilha(adm.get("/emissoes?de=2020-01-01&formato=xlsx"))
    assert linhas[1][linhas[0].index("Localizador")] == "ABC123"
    assert linhas[1][linhas[0].index("CPFs dos passageiros")] == "123.456.789-09"
    assert e.id


def test_excluir_colaborador_conta_e_o_que_depende(db, equipe):
    adm = equipe["admin"]
    # cadastrado por engano: sai com contas, lançamentos e contratos
    teste = _conta(db, "Teste Errado", "39053344705", pontos=5_000, valor=90)
    db.add(Contrato(colaborador_id=teste.colaborador_id, tipo="OURO"))
    db.commit()
    col_id = teste.colaborador_id
    r = adm.post(f"/colaboradores/{col_id}/excluir")
    assert "Colaborador Teste Errado excluído, com 1 conta(s)" in r.text
    db.expunge_all()  # o site apagou em outra sessão
    assert db.query(Colaborador).filter_by(id=col_id).count() == 0
    assert db.query(Lancamento).count() == 0 and db.query(Contrato).count() == 0

    # com emissão ativa não exclui; cancelada a emissão, exclui (a emissão fica, sem a conta)
    conta = _conta(db, pontos=50_000, valor=850)
    e = criar_emissao(db, conta=conta, pontos=10_000, preco_venda=500)
    db.commit()
    r = adm.post(f"/colaboradores/{conta.colaborador_id}/excluir")
    assert "1 emissão(ões) ativa(s)" in r.text and db.get(Colaborador, conta.colaborador_id)
    cancelar_emissao(db, e)
    db.commit()
    emissao_id = e.id
    assert "excluída" in adm.post(f"/contas/{conta.id}/excluir").text
    db.expunge_all()
    assert db.get(Emissao, emissao_id).conta_id is None and db.get(Emissao, emissao_id).status == "cancelada"

    # transferência com outra conta: cancela antes
    livelo = _conta(db, "Bia Lima", "11144477735", "Livelo", pontos=20_000, valor=700)
    smiles = _conta(db, "Bia Lima", "11144477735", "Smiles")
    outro = _conta(db, "Caio Melo", "15350946056", "Smiles")
    _, entrada = transferir(db, livelo, outro, 10_000)
    db.commit()
    r = adm.post(f"/contas/{livelo.id}/excluir")
    assert "transferência(s) de pontos com outras contas" in r.text
    assert "excluída" in adm.post(f"/contas/{smiles.id}/excluir").text  # a outra conta dela não tem nada
    compra = CompraExterna(programa="Livelo", pontos=1000, valor=30, status="recebida", conta_id=livelo.id)
    db.add(compra)
    db.commit()
    lanc = db.query(Lancamento).filter_by(id=entrada.par_id).one()
    adm.post(f"/lancamentos/{lanc.id}/cancelar")
    assert "Colaborador Bia Lima excluído" in adm.post(f"/colaboradores/{livelo.colaborador_id}/excluir").text
    db.expire_all()
    compra = db.get(CompraExterna, compra.id)
    assert (compra.conta_id, compra.lancamento_id, compra.status) == (None, None, "paga")
    assert db.get(ContaPrograma, outro.id).saldo == 0  # a transferência cancelada devolveu os pontos


def test_excluir_fornecedor_cliente_pedido_emissao_e_acesso(db, equipe):
    adm = equipe["admin"]
    forn = Fornecedor(nome="Balcão Usado")
    vazio = Fornecedor(nome="Balcão Novo")
    db.add_all([forn, vazio])
    db.commit()
    conta = _conta(db)
    lancar(db, conta, "compra", 10_000, valor=170, fornecedor_id=forn.id)
    db.commit()
    assert "aparece em 1 lançamento(s) de compra" in adm.post(f"/fornecedores/{forn.id}/excluir").text
    assert "Fornecedor Balcão Novo excluído" in adm.post(f"/fornecedores/{vazio.id}/excluir").text

    cliente = Cliente(nome="Maria Cliente", telefone="5585999990000")
    db.add(cliente)
    db.commit()
    pedido = PedidoEmissao(requisitante_nome="Maria", cliente_id=cliente.id, origem="FOR", destino="GRU")
    db.add(pedido)
    db.flush()
    e = criar_emissao(db, conta=conta, pontos=5_000, preco_venda=300, cliente_id=cliente.id, cliente_nome=cliente.nome,
                      pedido_id=pedido.id)
    pedido.emissao_id, pedido.status = e.id, "emitido"
    db.commit()
    r = adm.post(f"/manager/clientes/{cliente.id}/excluir")
    assert "As emissões dele continuam registradas com o nome" in r.text
    db.expire_all()
    assert (db.get(Emissao, e.id).cliente_id, db.get(Emissao, e.id).cliente_nome) == (None, "Maria Cliente")

    emissao_id, pedido_id = e.id, pedido.id
    assert "Cancele a emissão antes" in adm.post(f"/emissoes/{emissao_id}/excluir").text
    adm.post(f"/emissoes/{emissao_id}/cancelar")
    assert "excluída" in adm.post(f"/emissoes/{emissao_id}/excluir").text
    db.expunge_all()
    assert db.get(Emissao, emissao_id) is None and db.get(PedidoEmissao, pedido_id).emissao_id is None
    assert db.query(Lancamento).filter_by(tipo="emissao").count() == 0
    assert db.query(ContaPrograma).one().saldo == 10_000  # os pontos da emissão voltaram
    assert "excluído" in adm.post(f"/pedidos/{pedido_id}/excluir").text
    db.expunge_all()
    assert db.get(PedidoEmissao, pedido_id) is None

    # acessos: não exclui a si mesmo nem o último administrador; os outros sim
    admin = db.query(Usuario).filter_by(login="admin").one()
    assert "seu próprio acesso" in adm.post(f"/equipe/{admin.id}/excluir").text
    emissor_id = db.query(Usuario).filter_by(login="emissor").one().id
    assert "Acesso de Emissor excluído" in adm.post(f"/equipe/{emissor_id}/excluir").text
    db.expunge_all()
    assert db.query(Usuario).filter_by(login="emissor").count() == 0
    assert equipe["gestora"].post(f"/equipe/{admin.id}/excluir").status_code == 403
    assert {r.acao for r in db.query(RegistroAcesso)} >= {"exclusao"}
