"""Esqueci minha senha: pedido do link, e-mail pela Brevo, criação da senha nova e configuração."""
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import email_envio
from app.limite import zerar_todos
from app.main import app
from app.models import RedefinicaoSenha, Usuario
from app.servicos import conferir_senha


@pytest.fixture()
def site(db):
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "admin", "senha": "admin123"})
        yield cliente


@pytest.fixture()
def enviados(monkeypatch):
    """Troca o envio real pela Brevo por uma lista."""
    lista = []

    class Resposta:
        status_code = 201

        def json(self):
            return {"messageId": "x"}

    def falso_post(url, timeout, headers, json):
        lista.append({"url": url, "chave": headers["api-key"], **json})
        return Resposta()

    monkeypatch.setattr(email_envio.httpx, "post", falso_post)
    return lista


def _configurar(db, site):
    r = site.post("/configuracoes/email", data={"chave": "xkeysib-teste", "remetente": "Contato@Agencia.com",
                                                "nome": "MVO Teste"})
    assert r.status_code == 200
    assert email_envio.configurado(db)


def _admin(db):
    admin = db.query(Usuario).filter_by(login="admin").one()
    admin.email = "admin@agencia.com"
    db.commit()
    return admin


def _link(enviados) -> str:
    return re.search(r"/redefinir-senha/[\w-]+", enviados[-1]["textContent"]).group(0)


def test_link_por_email_e_senha_nova(db, site, enviados):
    _configurar(db, site)
    _admin(db)
    assert "Esqueci minha senha" in TestClient(app).get("/login").text
    anonimo = TestClient(app)
    r = anonimo.post("/esqueci-senha", data={"login": "ADMIN@agencia.com"})
    assert "enviamos um link" in r.text
    assert len(enviados) == 1
    email = enviados[0]
    assert email["to"][0]["email"] == "admin@agencia.com" and email["sender"]["email"] == "contato@agencia.com"
    assert email["chave"] == "xkeysib-teste"
    caminho = _link(enviados)
    codigo = caminho.rsplit("/", 1)[1]
    # o banco guarda só o hash do código
    assert db.query(RedefinicaoSenha).one().codigo_hash != codigo

    assert "Criar senha nova" in anonimo.get(caminho).text
    r = anonimo.post(caminho, data={"nova": "curta", "confirmar": "curta"})
    assert "pelo menos 8" in r.text
    r = anonimo.post(caminho, data={"nova": "Viagem2027", "confirmar": "Viagem2028"})
    assert "não são iguais" in r.text
    r = anonimo.post(caminho, data={"nova": "Viagem2027", "confirmar": "Viagem2027"})
    assert "Senha nova criada" in r.text
    db.expire_all()
    assert conferir_senha("Viagem2027", db.query(Usuario).filter_by(login="admin").one().senha_hash)
    # o link vale uma vez só
    assert "Link vencido" in anonimo.get(caminho).text
    r = anonimo.post(caminho, data={"nova": "OutraSenha9", "confirmar": "OutraSenha9"})
    assert "Link vencido" in r.text
    db.expire_all()
    assert conferir_senha("Viagem2027", db.query(Usuario).filter_by(login="admin").one().senha_hash)


def test_resposta_igual_para_quem_nao_existe(db, site, enviados):
    _configurar(db, site)
    anonimo = TestClient(app)
    existe_sem_email = anonimo.post("/esqueci-senha", data={"login": "admin"}).text
    nao_existe = anonimo.post("/esqueci-senha", data={"login": "ninguem"}).text
    assert "enviamos um link" in existe_sem_email and "enviamos um link" in nao_existe
    assert enviados == []


def test_link_vencido_e_novo_pedido_anula_o_anterior(db, site, enviados):
    _configurar(db, site)
    _admin(db)
    anonimo = TestClient(app)
    anonimo.post("/esqueci-senha", data={"login": "admin"})
    primeiro = _link(enviados)
    zerar_todos()
    anonimo.post("/esqueci-senha", data={"login": "admin"})
    segundo = _link(enviados)
    assert "Link vencido" in anonimo.get(primeiro).text
    assert "Criar senha nova" in anonimo.get(segundo).text
    pedido = db.query(RedefinicaoSenha).filter(RedefinicaoSenha.usado_em.is_(None)).one()
    pedido.expira_em = datetime.now() - timedelta(minutes=1)
    db.commit()
    assert "Link vencido" in anonimo.get(segundo).text


def test_limite_de_pedidos(db, site, enviados):
    _configurar(db, site)
    _admin(db)
    anonimo = TestClient(app)
    for _ in range(3):
        anonimo.post("/esqueci-senha", data={"login": "admin"})
    r = anonimo.post("/esqueci-senha", data={"login": "admin"})
    assert "Muitos pedidos" in r.text and len(enviados) == 3


def test_sem_configuracao_nao_quebra(db, site, monkeypatch):
    _admin(db)
    chamadas = []
    monkeypatch.setattr(email_envio.httpx, "post", lambda *a, **k: chamadas.append(1))
    r = TestClient(app).post("/esqueci-senha", data={"login": "admin"})
    assert r.status_code == 200 and "enviamos um link" in r.text and chamadas == []
    assert "Pediu link" in site.get("/equipe/registro").text


def test_configuracao_email_teste_e_meu_email(db, site, enviados):
    tela = site.get("/configuracoes").text
    assert 'id="email"' in tela and "desligado" in tela
    _configurar(db, site)
    tela = site.get("/configuracoes").text
    assert "xkeysib-teste" not in tela and "ligado" in tela and "contato@agencia.com" in tela
    r = site.post("/configuracoes/email", data={"chave": "", "remetente": "contato@agencia.com", "nome": "MVO",
                                                "teste": "eu@agencia.com"})
    assert "E-mail de teste enviado" in r.text and enviados[-1]["to"][0]["email"] == "eu@agencia.com"
    assert enviados[-1]["chave"] == "xkeysib-teste"  # em branco manteve a chave
    assert "E-mail salvo" in site.post("/configuracoes/meu-email", data={"email": " Eu@Agencia.com "}).text
    db.expire_all()
    assert db.query(Usuario).filter_by(login="admin").one().email == "eu@agencia.com"
    assert "não parece certo" in site.post("/configuracoes/meu-email", data={"email": "sem-arroba"}).text
    site.post("/configuracoes/email", data={"apagar": "1"})
    assert not email_envio.configurado(db)


def test_email_na_equipe(db, site):
    site.post("/equipe/novo", data={"nome": "Ana", "login": "ana", "senha": "segredo123", "papel": "atendente",
                                    "email": "ana@agencia.com"})
    ana = db.query(Usuario).filter_by(login="ana").one()
    assert ana.email == "ana@agencia.com"
    site.post(f"/equipe/{ana.id}", data={"nome": "Ana", "papel": "atendente", "ativo": "true",
                                         "email": "ana.nova@agencia.com"})
    db.expire_all()
    assert db.get(Usuario, ana.id).email == "ana.nova@agencia.com"
    assert "ana.nova@agencia.com" in site.get("/equipe").text
