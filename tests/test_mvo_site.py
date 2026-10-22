"""MVO pelo site: níveis de acesso, colaboradores e contas, senhas guardadas, lançamentos, emissões e fila."""
import pytest
from fastapi.testclient import TestClient

from app.cripto import decifrar
from app.main import app
from app.models import Cliente, Interesse, Match, ProgramaMilhas, Usuario
from app.models_mvo import Colaborador, ContaPrograma, Emissao, Lancamento, RegistroAcesso
from app.mvo.contas import lancar
from app.servicos import gerar_hash_senha, limpar_cache_programas, preparar_banco, registrar_promocao

FETCH = {"X-Requested-With": "fetch"}


def _entrar(login, senha="senha123"):
    site = TestClient(app)
    site.__enter__()
    r = site.post("/login", data={"login": login, "senha": senha})
    assert r.status_code == 200
    return site


@pytest.fixture()
def equipe(db):
    """Admin (padrão) + um usuário de cada nível."""
    for login, papel in (("gestora", "gestor"), ("emissor", "emissor"), ("atendente", "atendente")):
        db.add(Usuario(nome=login.title(), login=login, senha_hash=gerar_hash_senha("senha123"), papel=papel))
    db.commit()
    sites = {"admin": _entrar("admin", "admin123")}
    for login in ("gestora", "emissor", "atendente"):
        sites[login] = _entrar(login)
    yield sites
    for s in sites.values():
        s.__exit__(None, None, None)


def _conta(db, nome="Ana Souza", programa="Smiles", saldo=0, custo=None, **extra) -> ContaPrograma:
    col = db.query(Colaborador).filter_by(nome=nome).first() or Colaborador(nome=nome, cpf="52998224725")
    conta = ContaPrograma(colaborador=col, programa=programa, **extra)
    db.add(conta)
    db.flush()
    if saldo:
        lancar(db, conta, "compra", saldo, preco_milheiro=custo)
    db.commit()
    return conta


def test_niveis_de_acesso(db, equipe):
    adm, emissor, atendente = equipe["admin"], equipe["emissor"], equipe["atendente"]

    # atendente: só alertas
    assert atendente.get("/").status_code == 200
    r = atendente.get("/colaboradores")
    assert r.status_code == 403 and "Sem acesso" in r.text
    assert "Disponibilidade" not in atendente.get("/").text
    assert atendente.post("/milhas", data={}).status_code == 403
    assert atendente.post("/telegram/adicionar", data={"identificador": "@grupo"}).status_code == 403

    # emissor: milhas sim, configurações e equipe não
    assert emissor.get("/disponibilidade").status_code == 200
    assert emissor.get("/milhas").status_code == 200  # vê os preços
    assert emissor.post("/milhas", data={}).status_code == 403  # mas não altera
    assert emissor.get("/equipe").status_code == 403
    assert equipe["gestora"].get("/equipe").status_code == 403

    # administrador cria acesso com nível e não consegue ficar sem nenhum administrador
    r = adm.post("/equipe/novo", data={"nome": "Nova", "login": "nova", "senha": "123456", "papel": "emissor"})
    assert "como Emissor" in r.text
    admin = db.query(Usuario).filter_by(login="admin").one()
    r = adm.post(f"/equipe/{admin.id}", data={"nome": "Administrador", "papel": "gestor", "ativo": "true"})
    assert "pelo menos um Administrador" in r.text
    assert "Entrou no sistema" in adm.get("/equipe/registro").text


def test_usuarios_antigos_ganham_nivel(db):
    db.query(Usuario).update({"papel": None})
    db.add(Usuario(nome="Funcionária antiga", login="antiga", senha_hash=gerar_hash_senha("x" * 6)))
    db.commit()
    preparar_banco(db)
    papeis = {u.login: u.papel for u in db.query(Usuario)}
    assert papeis == {"admin": "admin", "antiga": "atendente"}


def test_colaborador_conta_e_senha_guardada(db, equipe):
    adm, emissor, atendente = equipe["admin"], equipe["emissor"], equipe["atendente"]
    r = adm.post("/colaboradores/novo", data={"nome": "Ana  Souza", "cpf": "529.982.247-25", "telefone": "(85) 98888-7777"})
    assert "Agora adicione as contas" in r.text
    col = db.query(Colaborador).one()
    assert (col.nome, col.cpf, col.telefone) == ("Ana Souza", "52998224725", "5585988887777")
    assert "Já existe um colaborador com esse CPF" in adm.post("/colaboradores/novo", data={"nome": "Outra", "cpf": "52998224725"}).text

    r = adm.post(f"/colaboradores/{col.id}/contas", data={"programa": "Smiles", "login": "ana@email.com",
                                                          "senha": "S3nh@Smiles", "saldo": "50.000", "custo_milheiro": "18"})
    assert "Conta Smiles criada" in r.text
    conta = db.query(ContaPrograma).one()
    db.refresh(conta)
    assert (conta.saldo, conta.custo_medio) == (50_000, 18.0)
    assert "S3nh@Smiles" not in conta.senha_cripto and decifrar(conta.senha_cripto) == "S3nh@Smiles"

    # quem tem permissão vê (e fica registrado); atendente não
    revelado = emissor.post(f"/contas/{conta.id}/credenciais", headers=FETCH).json()
    assert revelado == {"login": "ana@email.com", "senha": "S3nh@Smiles"}
    visto = db.query(RegistroAcesso).filter_by(acao="ver_credenciais").one()
    assert visto.usuario_nome == "Emissor" and visto.alvo == f"conta:{conta.id}"
    assert atendente.post(f"/contas/{conta.id}/credenciais", headers=FETCH).status_code == 403

    # emissor não vê custo; gestor vê
    assert "custo médio do milheiro" not in emissor.get(f"/contas/{conta.id}").text
    assert "custo médio do milheiro" in equipe["gestora"].get(f"/contas/{conta.id}").text

    # trocar a senha mantém o login; apagar remove os dois
    adm.post(f"/contas/{conta.id}/editar", data={"senha": "nova-senha", "ativa": "true"})
    db.refresh(conta)
    assert decifrar(conta.login_cripto) == "ana@email.com" and decifrar(conta.senha_cripto) == "nova-senha"
    adm.post(f"/contas/{conta.id}/editar", data={"apagar_credenciais": "true", "ativa": "true"})
    db.refresh(conta)
    assert conta.login_cripto is None and conta.senha_cripto is None


def test_lancamento_transferencia_e_emissao_pelo_site(db, equipe):
    adm = equipe["admin"]
    smiles = db.query(ProgramaMilhas).filter_by(nome="Smiles").one()
    smiles.limite_cpfs, smiles.janela_cpfs = 25, "ano"
    db.commit()
    livelo = _conta(db, programa="Livelo")
    conta = _conta(db, programa="Smiles")

    r = adm.post("/lancamentos/novo", data={"conta_id": livelo.id, "tipo": "compra", "pontos": "20.000", "valor": "700,00"})
    assert "Saldo atual: 20.000 pontos" in r.text
    r = adm.post("/transferencias/nova", data={"origem_id": livelo.id, "destino_id": conta.id, "pontos": "20.000", "bonus": "100"})
    assert "40.000 pontos entram" in r.text
    db.expire_all()
    assert (db.get(ContaPrograma, livelo.id).saldo, db.get(ContaPrograma, conta.id).custo_medio) == (0, 17.5)

    # emissão sem saldo suficiente: avisa e oferece registrar mesmo assim
    dados = {"forma": "programa_proprio", "conta_id": conta.id, "localizador": "abc123", "origem": "FOR", "destino": "GRU",
             "tipo_viagem": "ida_volta", "pontos": "50.000", "preco_venda": "1.500,00", "taxa_embarque": "80",
             "passageiro_nome": ["Maria Lima", "João Lima"], "passageiro_cpf": ["123.456.789-09", ""]}
    r = adm.post("/emissoes/nova", data=dados)
    assert r.status_code == 400 and "Registrar mesmo assim" in r.text and 'value="Maria Lima"' in r.text

    dados["pontos"] = "30.000"
    r = adm.post("/emissoes/nova", data=dados)
    assert "30.000 pontos descontados" in r.text
    e = db.query(Emissao).one()
    assert (e.localizador, e.qtd_passageiros, e.custo_milhas, e.lucro) == ("ABC123", 2, 525.0, 975.0)
    db.expire_all()
    assert db.get(ContaPrograma, conta.id).saldo == 10_000
    assert "ABC123" in adm.get("/emissoes").text and "Maria Lima" in adm.get(f"/contas/{conta.id}").text

    # o lançamento de emissão só se cancela pela emissão
    lanc = db.query(Lancamento).filter_by(tipo="emissao").one()
    assert "cancele a emissão" in adm.post(f"/lancamentos/{lanc.id}/cancelar").text
    r = adm.post(f"/emissoes/{e.id}/cancelar")
    assert "Os pontos voltaram" in r.text
    db.expire_all()
    assert db.get(ContaPrograma, conta.id).saldo == 40_000

    # emissor não lança compra (é entrada com custo)
    r = equipe["emissor"].post("/lancamentos/novo", data={"conta_id": conta.id, "tipo": "compra", "pontos": "1000", "valor": "10"})
    assert "não pode lançar" in r.text
    for pagina in ("/lancamentos", "/lancamentos/novo", "/transferencias/nova", "/fornecedores", "/colaboradores",
                   f"/colaboradores/{conta.colaborador_id}", "/disponibilidade?programa=Smiles&pontos=10000&passageiros=2",
                   "/emissoes/nova", f"/emissoes/{e.id}", "/equipe/registro"):
        assert adm.get(pagina).status_code == 200, pagina


def test_fila_mostra_quem_pode_emitir_e_leva_para_a_emissao(db, equipe):
    smiles = db.query(ProgramaMilhas).filter_by(nome="Smiles").one()
    smiles.preco_b2c, smiles.valor_milha = 27.0, 0.027
    db.commit()
    limpar_cache_programas()
    _conta(db, "Bia Barata", saldo=40_000, custo=15)
    _conta(db, "Caio Caro", saldo=40_000, custo=22)
    _conta(db, "Paulo Pouco", saldo=5_000, custo=10)
    cliente = Cliente(nome="Maria Oliveira", telefone="5585999990000", consentimento=True)
    cliente.interesses.append(Interesse(origem="NAT", destino="REC"))
    db.add(cliente)
    db.commit()
    texto = "✅ Smiles\n📍 Origem: NATAL\n📍 Destino: RECIFE\n✈️ Companhia Aérea: Gol\n\n👉🏻 Trecho ida e volta 10.000 milhas."
    registrar_promocao(db, texto=texto, fonte="telegram")

    fila = equipe["gestora"].get("/").text
    assert "Quem pode emitir · 2 contas" in fila
    assert fila.index("Bia Barata") < fila.index("Caio Caro") and "Paulo Pouco" not in fila
    assert "Margem com Bia" in fila and "R$ 120,00" in fila  # (27 - 15) × 10 milheiros
    assert "Margem com" not in equipe["emissor"].get("/").text  # sem custos
    assert "Quem pode emitir" not in equipe["atendente"].get("/").text

    m = db.query(Match).one()
    tela = equipe["emissor"].get(f"/emissoes/nova?match={m.id}").text
    assert 'value="NAT"' in tela and 'value="REC"' in tela and 'value="Maria Oliveira"' in tela and 'value="10.000"' in tela
