from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.database import adicionar_colunas_faltantes
from app.main import app
from app.models import Cliente, Interesse, Match, ProgramaMilhas, Promocao
from app.parser import extrair
from app.programas import detectar_programa
from app.servicos import (
    formatar_valor,
    limpar_cache_programas,
    montar_mensagem,
    preco_reais,
    recalcular_matches,
    registrar_promocao,
)

ALERTA = """✅ {programa}
📍 Origem: NATAL
📍 Destino: RECIFE
✈️ Companhia Aérea: {cia}
📆 Data da Cotação: 17/10/2026

👉🏻 Trecho ida e volta {milhas} milhas.

Data de Ida:
Março 2027: 21, 22, 23"""


def _definir(db, nome, valor, taxa=None):
    p = db.query(ProgramaMilhas).filter_by(nome=nome).one()
    p.valor_milha, p.taxa_fixa = valor, taxa
    db.commit()
    limpar_cache_programas()


def test_detecta_programa_pelo_texto_ou_pela_companhia():
    assert extrair(ALERTA.format(programa="Azul Fidelidade", cia="Azul", milhas="24.000")).programa == "Azul Fidelidade"
    assert extrair(ALERTA.format(programa="Smiles", cia="Gol", milhas="10.000")).programa == "Smiles"
    assert extrair(ALERTA.format(programa="Oferta", cia="LATAM", milhas="10.000")).programa == "LATAM Pass"
    assert detectar_programa("Resgate pela TudoAzul") == "Azul Fidelidade"
    assert detectar_programa("Clube XPTO 5.000 pontos", extras=["Clube XPTO"]) == "Clube XPTO"
    assert extrair("FOR → LIS R$ 2.389").programa is None  # promoção em dinheiro não tem programa


def test_converte_milhas_em_reais(db):
    promo = registrar_promocao(db, texto=ALERTA.format(programa="Smiles", cia="Gol", milhas="1.000"),
                               fonte="telegram").promocao
    assert preco_reais(promo) is None and formatar_valor(promo) == "1.000 milhas"

    _definir(db, "Smiles", 0.27)  # exemplo: 1.000 milhas × R$ 0,27
    assert preco_reais(promo) == 270
    assert formatar_valor(promo) == "R$ 270"

    _definir(db, "Smiles", 0.027, taxa=80)
    assert preco_reais(promo) == 107  # 1.000 × 0,027 + 80


def test_mensagem_usa_valor_em_reais(db):
    _definir(db, "Azul Fidelidade", 0.025)
    cliente = Cliente(nome="Maria Oliveira", telefone="5584999990000", consentimento=True)
    promo = registrar_promocao(db, texto=ALERTA.format(programa="Azul Fidelidade", cia="Azul", milhas="24.000"),
                               fonte="telegram").promocao
    msg = montar_mensagem("{valor} ({milhas} {programa})", cliente, promo)
    assert msg == "R$ 600 (24.000 milhas Azul Fidelidade)"


def test_orcamento_do_cliente_usa_valor_convertido_e_recalcula(db):
    c = Cliente(nome="Ana", telefone="5584988887777", consentimento=True)
    c.interesses.append(Interesse(origem="NAT", destino="REC", preco_maximo=500))
    db.add(c)
    db.commit()

    _definir(db, "Azul Fidelidade", 0.03)  # 24.000 × 0,03 = R$ 720, acima de R$ 500
    r = registrar_promocao(db, texto=ALERTA.format(programa="Azul Fidelidade", cia="Azul", milhas="24.000"),
                           fonte="telegram")
    assert r.matches == 0

    _definir(db, "Azul Fidelidade", 0.02)  # agora R$ 480: cabe no orçamento
    assert recalcular_matches(db) == {"removidos": 0, "criados": 1}

    _definir(db, "Azul Fidelidade", 0.05)  # R$ 1.200: sai da fila
    assert recalcular_matches(db) == {"removidos": 1, "criados": 0}
    assert db.query(Match).count() == 0


def test_programa_sem_valor_continua_chegando_para_a_equipe_avaliar(db):
    c = Cliente(nome="Ana", telefone="5584988887777", consentimento=True)
    c.interesses.append(Interesse(origem="NAT", destino="REC", preco_maximo=500))
    db.add(c)
    db.commit()
    r = registrar_promocao(db, texto=ALERTA.format(programa="LATAM Pass", cia="LATAM", milhas="9.000"),
                           fonte="telegram")
    assert r.matches == 1


def test_banco_antigo_ganha_coluna_nova_sem_perder_dados(tmp_path):
    motor = create_engine(f"sqlite:///{tmp_path}/antigo.db")
    with motor.begin() as con:  # tabela como era antes desta versão (sem a coluna "programa")
        con.execute(text("CREATE TABLE promocoes (id INTEGER PRIMARY KEY, fonte VARCHAR(30), origem VARCHAR(3), "
                         "destino VARCHAR(3), milhas INTEGER, companhia VARCHAR(80))"))
        con.execute(text("INSERT INTO promocoes (fonte, origem, destino, milhas) VALUES ('telegram','NAT','REC',24000)"))
    adicionadas = adicionar_colunas_faltantes(motor)
    assert "promocoes.programa" in adicionadas
    assert "programa" in {c["name"] for c in inspect(motor).get_columns("promocoes")}
    with motor.connect() as con:
        assert con.execute(text("SELECT milhas FROM promocoes")).scalar() == 24000


def test_tela_de_milhas(db):
    registrar_promocao(db, texto=ALERTA.format(programa="Smiles", cia="Gol", milhas="10.000"), fonte="telegram")
    with TestClient(app) as site:
        site.post("/login", data={"login": "admin", "senha": "admin123"})
        tela = site.get("/milhas")
        assert "Smiles" in tela.text and "sem valor definido" in tela.text

        smiles = db.query(ProgramaMilhas).filter_by(nome="Smiles").one()
        r = site.post("/milhas", data={f"b2c_{smiles.id}": "27,00", f"b2b_{smiles.id}": "24", f"taxa_{smiles.id}": "",
                                       f"cpfs_{smiles.id}": "25", f"janela_{smiles.id}": "ano",
                                       "novo_nome": "Clube Exemplo", "novo_b2c": "0,02"})  # valor por milha: vira 20
        assert "Preços salvos" in r.text
        db.expire_all()
        smiles = db.get(ProgramaMilhas, smiles.id)
        assert (smiles.preco_b2c, smiles.valor_milha, smiles.preco_b2b, smiles.limite_cpfs) == (27, 0.027, 24, 25)
        clube = db.query(ProgramaMilhas).filter_by(nome="Clube Exemplo", personalizado=True).one()
        assert (clube.preco_b2c, clube.valor_milha) == (20, 0.02)
        assert "R$ 270" in site.get("/promocoes").text


def test_copia_ignora_registros_de_itens_excluidos(tmp_path):
    """Bancos antigos do computador (SQLite sem conferência de ligações) podem ter envios de trechos apagados."""
    import sqlite3

    from app.transferir import copiar

    origem = tmp_path / "antigo.db"
    destino = tmp_path / "novo.db"
    copiar(f"sqlite:///{tmp_path / 'vazio.db'}", f"sqlite:///{origem}", avisar=lambda *_: None)  # cria as tabelas
    con = sqlite3.connect(origem)
    con.execute("INSERT INTO clientes (id, nome, telefone, consentimento, ativo, criado_em) VALUES (1, 'Ana', '5585900000001', 1, 1, '2026-10-20')")
    con.execute("INSERT INTO promocoes (id, fonte, origem, destino, confianca, status, coletada_em) VALUES (1, 'manual', 'FOR', 'RIO', 1, 'ativa', '2026-10-20')")
    con.execute("INSERT INTO matches (id, promocao_id, cliente_id, interesse_id, status, criado_em) VALUES (1, 1, 1, 8, 'enviada', '2026-10-20')")
    con.execute("INSERT INTO matches (id, promocao_id, cliente_id, status, criado_em) VALUES (2, 1, 999, 'nova', '2026-10-20')")
    con.commit()
    con.close()

    r = copiar(f"sqlite:///{origem}", f"sqlite:///{destino}", avisar=lambda *_: None)
    assert r["matches"] == 1
    con = sqlite3.connect(destino)
    assert con.execute("SELECT id, interesse_id, status FROM matches").fetchall() == [(1, None, "enviada")]
