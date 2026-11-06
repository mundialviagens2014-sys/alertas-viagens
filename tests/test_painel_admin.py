"""Resumo do administrador (coluna da direita nas telas largas)."""
from fastapi.testclient import TestClient

from app import painel_admin
from app.main import app
from app.models import Cliente, Interesse, Usuario
from app.servicos import gerar_hash_senha, registrar_promocao


def _entrar(login, senha):
    site = TestClient(app)
    site.__enter__()
    assert site.post("/login", data={"login": login, "senha": senha}).status_code == 200
    return site


def test_so_o_administrador_ve_o_resumo(db):
    painel_admin.limpar()
    db.add(Usuario(nome="Emissor", login="emissor", senha_hash=gerar_hash_senha("senha123"), papel="emissor"))
    ana = Cliente(nome="Ana Lima", telefone="5585981112222", consentimento=True)
    ana.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add(ana)
    db.commit()
    registrar_promocao(db, texto="FOR → LIS R$ 2.389 ida e volta em março/27", fonte="manual")

    admin, emissor = _entrar("admin", "admin123"), _entrar("emissor", "senha123")
    try:
        # a página do administrador traz o lugar do resumo; a do emissor não (o conteúdo só fica centralizado)
        assert 'id="painel-admin"' in admin.get("/").text
        assert 'id="painel-admin"' not in emissor.get("/").text
        assert 'id="painel-admin"' not in admin.get("/crm").text   # o quadro do CRM precisa da largura

        r = admin.get("/painel-admin")
        assert r.status_code == 200
        assert "Resumo" in r.text and "promoções lidas" in r.text and "<svg" in r.text
        assert "<html" not in r.text   # é um pedaço de página, entra por fetch
        assert "Lisboa" in r.text      # destinos mais pedidos

        assert emissor.get("/painel-admin", follow_redirects=False).status_code in (302, 303, 403)
    finally:
        admin.__exit__(None, None, None)
        emissor.__exit__(None, None, None)
