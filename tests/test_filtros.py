"""Busca e filtros das listas: sem acento, palavra por palavra, por dígitos, e os filtros novos de cada lista."""
import io
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import app
from app.models import Cliente, Interesse, Match, ProgramaMilhas, Promocao, Usuario
from app.models_mvo import (
    Agencia,
    Colaborador,
    ContaPrograma,
    Emissao,
    EmissaoPassageiro,
    Fornecedor,
    Franquia,
    PedidoEmissao,
    RegistroAcesso,
)
from app.mvo.contas import lancar
from app.servicos import gerar_hash_senha, registrar_promocao

FETCH = {"X-Requested-With": "fetch"}


@pytest.fixture()
def site(db):
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "admin", "senha": "admin123"})
        yield cliente


def _lista(r) -> str:
    """O que vem depois do último formulário de filtros (a lista em si, sem as opções dos filtros)."""
    assert r.status_code == 200, r.status_code
    html = r.text
    i = html.rfind('<form class="filtros"')
    return html[html.index("</form>", i):] if i >= 0 else html


def _clientes(db):
    agora = datetime.now()
    joao = Cliente(nome="João Pereira", telefone="5588997123344", consentimento=True, criado_em=agora)
    joao.interesses.append(Interesse(origem="FOR", destino="MCO"))
    maria = Cliente(nome="Maria da Silva Oliveira", telefone="5585998761234", consentimento=True,
                    criado_em=agora - timedelta(days=60))
    maria.interesses.append(Interesse(origem="FOR", destino="LIS", periodo_inicio=date(2027, 3, 1),
                                      periodo_fim=date(2027, 4, 30)))
    conceicao = Cliente(nome="Conceição Alves", telefone="5511987654321", consentimento=False,
                        criado_em=agora - timedelta(days=5))
    conceicao.interesses.append(Interesse(origem="GRU", destino="REC"))
    pedro = Cliente(nome="Pedro Inativo", telefone="5585911112222", ativo=False, criado_em=agora - timedelta(days=1))
    db.add_all([joao, maria, conceicao, pedro])
    db.commit()
    return joao, maria, conceicao, pedro


def test_busca_sem_acento_por_palavras_e_digitos(site, db):
    _clientes(db)
    assert "João Pereira" in _lista(site.get("/clientes?q=joao")) and "Maria" not in _lista(site.get("/clientes?q=joao"))
    corpo = _lista(site.get("/clientes?q=maria oliveira"))  # palavras fora de ordem e não vizinhas
    assert "Maria da Silva Oliveira" in corpo and "João" not in corpo
    assert "Conceição Alves" in _lista(site.get("/clientes?q=CONCEICAO"))
    assert "Maria da Silva Oliveira" in _lista(site.get("/clientes?q=(85) 99876-1234"))
    assert "João Pereira" in _lista(site.get("/clientes?q=joão 88"))  # palavra + parte do telefone
    vazio = _lista(site.get("/clientes?q=100%25"))  # % não vira curinga
    assert "Nenhum cliente encontrado" in vazio


def test_filtros_dos_clientes(site, db):
    _clientes(db)
    corpo = _lista(site.get("/clientes?destino=Lisboa"))
    assert "Maria da Silva Oliveira" in corpo and "João" not in corpo
    corpo = _lista(site.get("/clientes?origem=São Paulo"))  # cidade com vários aeroportos (GRU)
    assert "Conceição Alves" in corpo and "Maria" not in corpo
    r = site.get("/clientes?destino=xyzabc")
    assert "não reconhecida" in r.text and "João Pereira" in _lista(r)
    corpo = _lista(site.get("/clientes?situacao=sem_consentimento"))
    assert "Conceição Alves" in corpo and "João" not in corpo
    corpo = _lista(site.get("/clientes?situacao=inativos"))
    assert "Pedro Inativo" in corpo and "João" not in corpo
    assert "Pedro Inativo" in _lista(site.get("/clientes?situacao=sem_trecho"))
    corpo = _lista(site.get("/clientes?cadastro=30"))
    assert "João Pereira" in corpo and "Maria" not in corpo
    corpo = _lista(site.get("/clientes?ordem=recentes"))
    assert corpo.index("João Pereira") < corpo.index("Pedro Inativo") < corpo.index("Conceição") < corpo.index("Maria")


def test_filtros_das_promocoes_interesses_e_historico(site, db):
    joao, maria, _, _ = _clientes(db)
    registrar_promocao(db, texto="FOR → LIS R$ 2.389 ida e volta TAP em março/27", fonte="manual")
    registrar_promocao(db, texto="Smiles: Fortaleza para Recife por 6.500 milhas o trecho em janeiro", fonte="manual")
    registrar_promocao(db, texto="GRU → MIA R$ 1.899 com a LATAM em novembro", fonte="manual")
    registrar_promocao(db, texto="FOR → MCO R$ 3.950 em fevereiro/2027", fonte="manual")
    mia = db.query(Promocao).filter_by(destino="MIA").one()
    mia.coletada_em = datetime.now() - timedelta(days=40)
    mia.alerta = "possível erro tarifário"
    db.commit()

    def trechos(url):
        corpo = _lista(site.get(url))
        return {t for t in ("FOR → LIS", "FOR → REC", "GRU → MIA", "FOR → MCO") if t in corpo}

    assert trechos("/promocoes?q=lisboa") == {"FOR → LIS"}
    assert trechos("/promocoes?q=latam") == {"GRU → MIA"}
    assert trechos("/promocoes?destino=Miami") == {"GRU → MIA"}
    assert trechos("/promocoes?origem=São Paulo") == {"GRU → MIA"}
    assert trechos("/promocoes?tipo=milhas") == {"FOR → REC"}
    assert "FOR → REC" not in trechos("/promocoes?tipo=reais")
    assert trechos("/promocoes?interessados=1") == {"FOR → LIS", "FOR → MCO"}
    assert "GRU → MIA" not in trechos("/promocoes?lidas=30")
    assert trechos("/promocoes?revisar=1") == {"GRU → MIA"}
    # os botões de fonte e status guardam a busca
    assert 'href="/promocoes?q=lisboa&amp;fonte=telegram"' in site.get("/promocoes?q=lisboa").text

    # por interesse
    corpo = _lista(site.get("/interesses?destino=lisboa"))
    assert "FOR → LIS" in corpo and "FOR → MCO" not in corpo
    corpo = _lista(site.get("/interesses?q=joao"))
    assert "FOR → MCO" in corpo and "FOR → LIS" not in corpo
    corpo = _lista(site.get("/interesses?promos=sem"))
    assert "SAO → REC" in corpo and "FOR → LIS" not in corpo  # GRU entra como São Paulo (todos os aeroportos)

    # histórico: quem enviou e busca
    db.add(Usuario(nome="Gestora", login="gestora", senha_hash=gerar_hash_senha("senha123"), papel="gestor"))
    db.commit()
    m_maria = db.query(Match).filter_by(cliente_id=maria.id).one()
    m_joao = db.query(Match).filter_by(cliente_id=joao.id).one()
    site.post(f"/matches/{m_maria.id}/enviar", headers=FETCH)
    with TestClient(app) as gestora:
        gestora.post("/login", data={"login": "gestora", "senha": "senha123"})
        gestora.post(f"/matches/{m_joao.id}/enviar", headers=FETCH)
    corpo = _lista(site.get("/historico?quem=Gestora"))
    assert "João Pereira" in corpo and "Maria" not in corpo
    corpo = _lista(site.get("/historico?q=orlando"))
    assert "João Pereira" in corpo and "Maria" not in corpo
    assert "Maria da Silva Oliveira" in _lista(site.get("/historico?q=99876"))


def test_filtros_das_agencias_e_exportacao(site, db):
    sol = Agencia(nome="Viagens Sol Nascente", documento="11222333000181", email_comercial="contato@sol.com",
                  telefone="5585988880000", estado="CE", criado_em=datetime.now() - timedelta(days=30))
    rota = Agencia(nome="Rota Azul Turismo", email_financeiro="fin@rotaazul.com", telefone2="5581988887777",
                   estado="PE", criado_em=datetime.now() - timedelta(days=20))
    mar = Agencia(nome="Mar e Céu Viagens", criado_em=datetime.now())
    db.add_all([sol, rota, mar])
    db.commit()
    db.add(Emissao(forma="pagante", agencia_id=sol.id, preco_venda=900))
    db.commit()

    def nomes(url):
        corpo = _lista(site.get(url))
        return {n for n in ("Viagens Sol Nascente", "Rota Azul Turismo", "Mar e Céu Viagens") if n in corpo}

    assert nomes("/agencias?q=ceu") == {"Mar e Céu Viagens"}
    assert nomes("/agencias?q=fin@rotaazul") == {"Rota Azul Turismo"}
    assert nomes("/agencias?q=8888-7777") == {"Rota Azul Turismo"}
    assert nomes("/agencias?q=11.222.333") == {"Viagens Sol Nascente"}
    assert nomes("/agencias?emissoes=com") == {"Viagens Sol Nascente"}
    assert nomes("/agencias?emissoes=sem") == {"Rota Azul Turismo", "Mar e Céu Viagens"}
    assert nomes("/agencias?dados=sem_documento") == {"Rota Azul Turismo", "Mar e Céu Viagens"}
    assert nomes("/agencias?dados=sem_email") == {"Mar e Céu Viagens"}
    assert nomes("/agencias?dados=sem_telefone") == {"Mar e Céu Viagens"}
    assert nomes("/agencias?estado=PE") == {"Rota Azul Turismo"}
    corpo = _lista(site.get("/agencias?ordem=vendas"))
    assert corpo.index("Viagens Sol Nascente") < corpo.index("Mar e Céu") and corpo.index("Viagens Sol") < corpo.index("Rota Azul")
    corpo = _lista(site.get("/agencias?ordem=recentes"))
    assert corpo.index("Mar e Céu") < corpo.index("Rota Azul") < corpo.index("Viagens Sol")

    # a planilha exportada leva os mesmos filtros
    r = site.get("/agencias?dados=sem_documento&formato=xlsx")
    ws = load_workbook(io.BytesIO(r.content)).active
    linhas = [l for l in ws.iter_rows(values_only=True) if l and l[0]]
    assert sorted(l[0] for l in linhas if l[0] != "Agência") == ["Mar e Céu Viagens", "Rota Azul Turismo"]


def test_filtros_dos_clientes_do_manager_e_colaboradores(site, db):
    joao, maria, _, _ = _clientes(db)
    hoje = date.today()
    maria.classificacao = "VIP"
    maria.data_nascimento = date(1990, hoje.month, 10)
    joao.data_nascimento = date(1985, hoje.month % 12 + 1, 5)
    db.commit()
    corpo = _lista(site.get("/manager/clientes?classificacao=VIP"))
    assert "Maria da Silva Oliveira" in corpo and "João" not in corpo
    corpo = _lista(site.get("/manager/clientes?aniversario=mes"))
    assert "Maria da Silva Oliveira" in corpo and "João" not in corpo
    corpo = _lista(site.get("/manager/clientes?aniversario=proximo"))
    assert "João Pereira" in corpo and "Maria" not in corpo
    assert "João Pereira" in _lista(site.get("/manager/clientes?q=pereira joao"))

    ana = Colaborador(nome="Ana Souza", cpf="52998224725", classificacao="Ouro", consolidador="Rede X")
    bruno = Colaborador(nome="Bruno Lima", cpf="11144477735")
    carla = Colaborador(nome="Carla Dias")
    db.add_all([ana, bruno, carla])
    db.flush()
    for col, programa, pontos in ((ana, "Smiles", 100_000), (carla, "LATAM Pass", 5_000)):
        conta = ContaPrograma(colaborador=col, programa=programa)
        db.add(conta)
        db.flush()
        lancar(db, conta, "compra", pontos, preco_milheiro=15)
    db.commit()

    def nomes(url):
        corpo = _lista(site.get(url))
        return {n for n in ("Ana Souza", "Bruno Lima", "Carla Dias") if n in corpo}

    assert nomes("/colaboradores?classificacao=Ouro") == {"Ana Souza"}
    assert nomes("/colaboradores?consolidador=Rede X") == {"Ana Souza"}
    assert nomes("/colaboradores?contas=sem_contas") == {"Bruno Lima"}
    assert nomes("/colaboradores?contas=com_saldo") == {"Ana Souza", "Carla Dias"}
    assert nomes("/colaboradores?q=529.982") == {"Ana Souza"}
    corpo = _lista(site.get("/colaboradores?ordem=pontos"))
    assert corpo.index("Ana Souza") < corpo.index("Carla Dias") < corpo.index("Bruno Lima")
    corpo = _lista(site.get("/contas?ordem=saldo"))
    assert corpo.index("Ana Souza") < corpo.index("Carla Dias")


def test_filtros_de_fornecedores_emissoes_pedidos_e_configuracao(site, db):
    db.add_all([Fornecedor(nome="Balcão Milhas"), Fornecedor(nome="Banco Parceiro", ativo=False)])
    sol = Agencia(nome="Viagens Sol Nascente")
    rota = Agencia(nome="Rota Azul Turismo")
    db.add_all([sol, rota])
    db.commit()
    e1 = Emissao(forma="pagante", agencia_id=sol.id, localizador="ABC123", origem="FOR", destino="LIS", preco_venda=900)
    e1.passageiros.append(EmissaoPassageiro(nome="Carlos Dias", cpf="12345678909"))
    db.add_all([e1, Emissao(forma="pagante", agencia_id=rota.id, localizador="XYZ789", origem="NAT", destino="REC")])
    db.add_all([PedidoEmissao(requisitante_nome="Paula", origem="FOR", destino="LIS", programa="Smiles"),
                PedidoEmissao(requisitante_nome="Rafael", origem="NAT", destino="GRU", programa="LATAM Pass")])
    db.add_all([Franquia(nome="Rede Sol"), Franquia(nome="Rede Lua", ativo=False)])
    db.commit()

    corpo = _lista(site.get("/fornecedores?q=balcao"))
    assert "Balcão Milhas" in corpo and "Banco Parceiro" not in corpo
    corpo = _lista(site.get("/fornecedores?ver=inativos"))
    assert "Banco Parceiro" in corpo and "Balcão" not in corpo

    corpo = _lista(site.get(f"/emissoes?agencia={sol.id}"))
    assert "ABC123" in corpo and "XYZ789" not in corpo
    corpo = _lista(site.get("/emissoes?q=123.456.789-09"))  # CPF do passageiro
    assert "ABC123" in corpo and "XYZ789" not in corpo
    corpo = _lista(site.get("/emissoes?q=lisboa"))
    assert "ABC123" in corpo and "XYZ789" not in corpo
    assert "ABC123" in _lista(site.get("/emissoes?q=carlos"))
    assert "Viagens Sol Nascente" in site.get("/emissoes").text  # a agência aparece no filtro

    corpo = _lista(site.get("/pedidos?status=todos&programa=Smiles"))
    assert "Paula" in corpo and "Rafael" not in corpo
    corpo = _lista(site.get("/pedidos?status=todos&q=lisboa"))
    assert "Paula" in corpo and "Rafael" not in corpo
    assert 'href="/pedidos?q=lisboa&amp;status=todos"' in site.get("/pedidos?q=lisboa").text

    corpo = _lista(site.get("/franquias?ver=inativos"))
    assert "Rede Lua" in corpo and "Rede Sol" not in corpo
    corpo = _lista(site.get("/franquias?q=sol"))
    assert "Rede Sol" in corpo and "Rede Lua" not in corpo

    smiles = db.query(ProgramaMilhas).filter_by(nome="Smiles").one()
    smiles.preco_b2c = 20
    db.commit()
    corpo = _lista(site.get("/programas?q=smiles"))
    assert ">Smiles<" in corpo and ">LATAM Pass<" not in corpo
    corpo = _lista(site.get("/programas?ver=sem_preco"))
    assert ">Smiles<" not in corpo and ">LATAM Pass<" in corpo

    db.add(RegistroAcesso(usuario_nome="Administrador", acao="login", detalhe="acesso bem antigo",
                          criado_em=datetime.now() - timedelta(days=100)))
    db.add(RegistroAcesso(usuario_nome="Administrador", acao="login", detalhe="acesso de hoje", ip="200.1.2.3"))
    db.commit()
    corpo = _lista(site.get("/equipe/registro?periodo=30"))
    assert "acesso de hoje" in corpo and "bem antigo" not in corpo
    corpo = _lista(site.get("/equipe/registro?q=200.1.2"))
    assert "acesso de hoje" in corpo and "bem antigo" not in corpo


def test_listas_abrem_com_todos_os_filtros_vazios_ou_estranhos(site, db):
    _clientes(db)
    for url in ("/clientes?situacao=x&cadastro=abc&ordem=x", "/promocoes?tipo=x&lidas=x", "/interesses?promos=x",
                "/historico?quem=ninguem", "/agencias?emissoes=x&dados=x&ordem=x", "/manager/clientes?aniversario=x",
                "/colaboradores?contas=x&ordem=x", "/contas?ordem=x", "/fornecedores?ver=x", "/programas?ver=x",
                "/franquias?ver=x", "/equipe/registro?periodo=x", "/classificacoes?q=a", "/precos?q=smiles&programa=Smiles"):
        assert site.get(url).status_code == 200, url
