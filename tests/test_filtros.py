"""Busca e filtros das listas: sem acento, palavra por palavra, por dígitos, e os filtros novos de cada lista."""
import io
from datetime import date, datetime, timedelta

import re

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import app
from app.busca import casa
from app.models import CanalTelegram, Cliente, ConfirmacaoCadastro, Interesse, Match, ProgramaMilhas, Promocao, Usuario
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


RE_ARCO = re.compile(r'<span class="rota-arco[^"]*"><b>(\w+)</b><svg.*?</svg><b[^>]*>([^<]+)</b></span>', re.S)


def _lista(r) -> str:
    """O que vem depois do último formulário de filtros (a lista em si, sem as opções dos filtros).
    O trecho desenhado com o arco de voo volta a ser texto ("FOR → LIS") para as conferências."""
    assert r.status_code == 200, r.status_code
    html = r.text
    i = html.rfind('<form class="filtros"')
    corpo = html[html.index("</form>", i):] if i >= 0 else html
    return RE_ARCO.sub(lambda m: f"{m.group(1)} → {m.group(2)}", corpo)


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
    corpo = _lista(site.get("/clientes?ordem=recentes"))   # padrão: só ativos com autorização, mais recentes primeiro
    assert corpo.index("João Pereira") < corpo.index("Maria") and "Pedro Inativo" not in corpo
    corpo = _lista(site.get("/clientes?ordem=recentes&situacao=todos"))   # todos: os prontos primeiro, depois os demais
    assert corpo.index("João Pereira") < corpo.index("Maria") < corpo.index("Pedro Inativo") < corpo.index("Conceição")


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
                "/franquias?ver=x", "/equipe/registro?periodo=x", "/classificacoes?q=a", "/precos?q=smiles&programa=Smiles",
                "/?ver=x&tipo=x&fonte=x&ordem=x&origem=xyz", "/?ver=confirmacoes&q=a", "/telegram?situacao=x&ordem=x&q=a"):
        assert site.get(url).status_code == 200, url


def test_busca_na_memoria_segue_a_mesma_regra():
    assert casa("", ["qualquer"]) and casa("  ", [])
    assert casa("joao", ["João Pereira"]) and not casa("joao", ["Maria"])
    assert casa("maria oliveira", ["Maria da Silva Oliveira"]) and not casa("maria souza", ["Maria da Silva Oliveira"])
    assert casa("(85) 99876-1234", ["Maria"], ["5585998761234"])
    assert casa("maria 99876", ["Maria"], ["5585998761234"]) and not casa("maria 11111", ["Maria"], ["5585998761234"])
    assert casa("-1002079295206", ["-1002079295206"]) and casa("2079295206", ["@grupo", "-1002079295206"])
    assert casa("lisboa", [lambda termo: termo == "lisboa"])
    assert casa("sao paulo", [lambda termo: termo == "sao paulo"])  # a busca inteira também passa pelas funções
    assert not casa("joao", [None, ""], [None])


def test_busca_por_cidade_de_nome_composto(site, db):
    """"são paulo", "rio de janeiro": a busca inteira vale como cidade (não só cada palavra sozinha)."""
    _clientes(db)
    registrar_promocao(db, texto="GRU → MIA R$ 1.899 com a LATAM em novembro", fonte="manual")
    registrar_promocao(db, texto="FOR → LIS R$ 2.389 ida e volta TAP em março/27", fonte="manual")
    corpo = _lista(site.get("/promocoes?q=sao paulo"))
    assert "GRU → MIA" in corpo and "FOR → LIS" not in corpo
    corpo = _lista(site.get("/interesses?q=São Paulo"))
    assert "SAO → REC" in corpo and "FOR → LIS" not in corpo
    assert "GRU → MIA" in _lista(site.get("/promocoes?q=latam miami"))  # palavra por palavra continua valendo


def test_filtros_da_fila_de_envio(site, db):
    joao, maria, conceicao, _ = _clientes(db)
    registrar_promocao(db, texto="FOR → LIS R$ 2.389 ida e volta TAP em março/27", fonte="manual")
    registrar_promocao(db, texto="FOR → MCO R$ 3.950 em fevereiro/2027", fonte="telegram",
                       fonte_detalhe="Alertas Vale Voos")
    registrar_promocao(db, texto="Smiles: São Paulo para Recife por 6.500 milhas o trecho em janeiro", fonte="manual")
    db.add(ConfirmacaoCadastro(cliente=joao))  # João se cadastrou pelo formulário (FOR → MCO)
    db.commit()
    confirmacao = "Novos cadastros pelo formulário"

    def nomes(url):
        corpo = _lista(site.get(url))
        return {n for n in ("João Pereira", "Maria da Silva Oliveira", "Conceição Alves") if n in corpo}

    tudo = site.get("/").text
    assert nomes("/") == {"João Pereira", "Maria da Silva Oliveira", "Conceição Alves"} and confirmacao in tudo
    assert nomes("/?q=joao") == {"João Pereira"}
    assert nomes("/?q=lisboa") == {"Maria da Silva Oliveira"}
    assert nomes("/?q=sao paulo") == {"Conceição Alves"}  # cidade de nome composto
    assert nomes("/?q=99876") == {"Maria da Silva Oliveira"}  # parte do telefone
    r = site.get("/?q=vale voos")  # nome do grupo do Telegram de onde veio a promoção
    assert nomes("/?q=vale voos") == {"João Pereira"} and confirmacao not in _lista(r)
    assert nomes("/?destino=Lisboa") == {"Maria da Silva Oliveira"}
    assert nomes("/?origem=São Paulo") == {"Conceição Alves"}
    assert nomes("/?tipo=reais") == {"João Pereira", "Maria da Silva Oliveira"}
    assert nomes("/?fonte=telegram") == {"João Pereira"}
    r = site.get("/?tipo=milhas")  # filtro de promoção: as confirmações saem de "Para enviar"
    assert nomes("/?tipo=milhas") == {"Conceição Alves"} and confirmacao not in _lista(r)
    corpo = _lista(site.get("/?tipo=reais&ordem=preco"))
    assert corpo.index("Maria da Silva Oliveira") < corpo.index("João Pereira")  # R$ 2.389 antes de R$ 3.950
    assert "1 envio em 1 promoção" in site.get("/?q=lisboa").text
    # os botões de cima guardam a busca
    assert 'href="/?q=joao&amp;ver=enviadas"' in site.get("/?q=joao").text
    r = site.get("/?destino=xyzabc")
    assert "não reconhecida" in r.text and len(nomes("/?destino=xyzabc")) == 3

    # só as confirmações de cadastro
    r = site.get("/?ver=confirmacoes")
    assert confirmacao in r.text and nomes("/?ver=confirmacoes") == {"João Pereira"}
    assert 'name="tipo"' not in r.text  # reais/milhas e fonte não valem para as confirmações
    assert nomes("/?ver=confirmacoes&destino=Orlando") == {"João Pereira"}
    assert "Nada na fila com esses filtros" in site.get("/?ver=confirmacoes&destino=Lisboa").text

    # já enviadas
    m_maria = db.query(Match).filter_by(cliente_id=maria.id).one()
    site.post(f"/matches/{m_maria.id}/enviar", headers=FETCH)
    assert nomes("/?ver=enviadas") == {"Maria da Silva Oliveira"}
    assert "Nada na fila com esses filtros" in site.get("/?ver=enviadas&q=joao").text


def test_filtros_dos_grupos_do_telegram(site, db):
    agora = datetime.now()
    db.add_all([
        CanalTelegram(identificador="@valevoos", nome="Voos Baratos BR", ultima_leitura=agora),
        CanalTelegram(identificador="-1002079295206", nome="Passagens Imperdíveis", ativo=False),
        CanalTelegram(identificador="@promoerro", nome="Promo com Erro", ultima_leitura=agora - timedelta(hours=1),
                      ultimo_erro="sem acesso ao grupo"),
        CanalTelegram(identificador="@grupo_novo"),  # aguardando a 1ª leitura
    ])
    db.commit()
    registrar_promocao(db, texto="FOR → LIS R$ 2.389 ida e volta TAP em março/27", fonte="telegram",
                       fonte_detalhe="Voos Baratos BR")

    def grupos(url):
        corpo = _lista(site.get(url))
        return {n for n in ("Voos Baratos BR", "Passagens Imperdíveis", "Promo com Erro", "@grupo_novo") if n in corpo}

    assert len(grupos("/telegram")) == 4
    assert grupos("/telegram?q=imperdiveis") == {"Passagens Imperdíveis"}
    assert grupos("/telegram?q=2079295206") == {"Passagens Imperdíveis"}
    assert grupos("/telegram?situacao=pausados") == {"Passagens Imperdíveis"}
    assert grupos("/telegram?situacao=erro") == {"Promo com Erro"}
    assert grupos("/telegram?situacao=aguardando") == {"@grupo_novo"}
    assert grupos("/telegram?situacao=lendo") == {"Voos Baratos BR"}
    assert grupos("/telegram?situacao=parados") == {"Promo com Erro", "@grupo_novo"}  # ativos sem promoção em 7 dias
    corpo = _lista(site.get("/telegram?ordem=promocoes"))
    assert corpo.index("Voos Baratos BR") < corpo.index("Promo com Erro")
    r = site.get("/telegram?q=xyz")
    assert "Nenhum grupo com esses filtros" in r.text and "1 de 4" not in r.text and "0 de 4 grupos lidos" in r.text


def test_historico_e_interesses_com_trecho_tipo_e_ordem(site, db):
    joao, maria, conceicao, _ = _clientes(db)
    registrar_promocao(db, texto="FOR → LIS R$ 2.389 ida e volta TAP em março/27", fonte="manual")
    registrar_promocao(db, texto="FOR → MCO R$ 3.950 em fevereiro/2027", fonte="manual")
    registrar_promocao(db, texto="Smiles: São Paulo para Recife por 6.500 milhas o trecho em janeiro", fonte="manual")
    conceicao.consentimento = True  # sem autorização o envio não é registrado
    db.commit()
    for cliente in (joao, maria, conceicao):
        m = db.query(Match).filter_by(cliente_id=cliente.id).one()
        site.post(f"/matches/{m.id}/enviar", headers=FETCH)

    def nomes(url):
        corpo = _lista(site.get(url))
        return {n for n in ("João Pereira", "Maria da Silva Oliveira", "Conceição Alves") if n in corpo}

    assert len(nomes("/historico")) == 3
    assert nomes("/historico?destino=Lisboa") == {"Maria da Silva Oliveira"}
    assert nomes("/historico?origem=Fortaleza") == {"João Pereira", "Maria da Silva Oliveira"}
    assert nomes("/historico?tipo=milhas") == {"Conceição Alves"}
    assert nomes("/historico?q=sao paulo") == {"Conceição Alves"}
    r = site.get("/historico?destino=Lisboa&tipo=milhas")
    assert "Nenhum envio com esses filtros" in r.text

    corpo = _lista(site.get("/interesses?ordem=nome"))
    assert corpo.index("FOR → LIS") < corpo.index("FOR → MCO") < corpo.index("SAO → REC")
    assert site.get("/interesses?ordem=clientes").status_code == 200


def test_clientes_por_padrao_so_ativos_com_autorizacao(db, site):
    from app.models import Cliente
    db.add_all([Cliente(nome="Pronto Ativo", telefone="5585900000001", consentimento=True),
                Cliente(nome="Inativo Importado", telefone="5585900000002", consentimento=True, ativo=False),
                Cliente(nome="Sem Autorizacao", telefone="5585900000003", consentimento=False)])
    db.commit()
    padrao = _lista(site.get("/clientes"))
    assert "Pronto Ativo" in padrao and "Inativo Importado" not in padrao and "Sem Autorizacao" not in padrao
    todos = _lista(site.get("/clientes?situacao=todos"))
    assert todos.index("Pronto Ativo") < todos.index("Inativo Importado")   # ativos e autorizados primeiro
    assert "Sem Autorizacao" in todos
    assert "Inativo Importado" in _lista(site.get("/clientes?situacao=inativos"))
