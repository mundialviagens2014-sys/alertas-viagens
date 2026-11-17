"""Página do cliente: endereço secreto com as promoções vigentes dele, que vai junto das mensagens.

O mais importante aqui é a segurança: a página abre sem login, então ela não pode mostrar nada além do primeiro
nome e das promoções, nem abrir com link errado, trocado ou de cliente inativo."""
import re
from datetime import datetime, timedelta
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Cliente, ConfirmacaoCadastro, Interesse, Match, Promocao
from app.servicos import preparar_banco, registrar_promocao

FETCH = {"X-Requested-With": "fetch"}


@pytest.fixture()
def site(db):
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "admin", "senha": "admin123"})
        yield cliente


@pytest.fixture()
def visitante(db):
    """Quem abre o link no celular: sem login."""
    with TestClient(app) as cliente:
        yield cliente


def _amanda(db, **campos):
    amanda = Cliente(nome="Amanda Ferreira Souza", telefone="5585991112233", email="amanda.souza@exemplo.com",
                     documento="12345678909", observacoes="cliente VIP, prefere Azul", consentimento=True, **campos)
    amanda.interesses.append(Interesse(origem="FOR", destino="RIO", preco_maximo=900))
    maria = Cliente(nome="Maria Oliveira", telefone="5585998761234", consentimento=True)
    maria.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add_all([amanda, maria])
    db.commit()
    return amanda, maria


def _promos(db):
    registrar_promocao(db, texto="FOR → GIG R$ 450 ida e volta Azul em março/2027", fonte="telegram",
                       fonte_detalhe="Grupo Secreto de Promos", link_fonte="https://t.me/gruposecreto/123")
    registrar_promocao(db, texto="FOR → SDU R$ 389 ida e volta GOL em abril/2027 https://oferta.exemplo.com/abc",
                       fonte="telegram", fonte_detalhe="Grupo Secreto de Promos")
    registrar_promocao(db, texto="FOR → LIS R$ 2.389 ida e volta TAP em março/2027", fonte="manual")


def test_cada_cliente_tem_um_link_secreto(db):
    amanda, maria = _amanda(db)
    for c in (amanda, maria):
        assert re.fullmatch(r"[A-Za-z0-9_-]{24}", c.token_pagina)  # 144 bits aleatórios
    assert amanda.token_pagina != maria.token_pagina
    # clientes de antes da página ganham o link ao iniciar o sistema
    maria.token_pagina = None
    db.commit()
    preparar_banco(db)
    db.commit()
    assert re.fullmatch(r"[A-Za-z0-9_-]{24}", maria.token_pagina)


def test_pagina_mostra_so_o_primeiro_nome_e_as_promocoes_do_cliente(db, visitante):
    amanda, maria = _amanda(db)
    _promos(db)
    r = visitante.get(f"/para-voce/{amanda.token_pagina}")
    assert r.status_code == 200
    html = r.text
    assert "Olá, Amanda!" in html and "Fortaleza → Rio de Janeiro" in html
    assert "R$ 389" in html and "R$ 450" in html
    assert html.index("R$ 389") < html.index("R$ 450")  # a mais barata primeiro
    assert "2 promoções" in html
    # nada de outro cliente nem dados pessoais ou internos
    for proibido in ("Lisboa", "R$ 2.389", "Maria", "Souza", "Ferreira", "91112233", "99111", "amanda.souza",
                     "12345678909", "VIP", "Grupo Secreto", "t.me", "oferta.exemplo", "gruposecreto",
                     "Fila de envio", "Manager", "Sair", "<script", "Imagem com a logo", "Texto original"):
        assert proibido not in html, proibido
    # cabeçalhos: sem script, sem cache, sem buscador, sem abrir em outro site, sem passar o endereço adiante
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "script-src" not in r.headers["content-security-policy"]  # default-src 'none' já proíbe scripts
    assert r.headers["x-robots-tag"].startswith("noindex")
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-frame-options"] == "DENY"
    assert "set-cookie" not in r.headers  # quem abre a página não ganha sessão do sistema
    assert 'name="robots" content="noindex' in html


def test_pagina_esconde_puladas_descartadas_para_revisar_e_vencidas(db, visitante):
    amanda, _ = _amanda(db)
    for preco in (401, 402, 403, 404, 405):
        registrar_promocao(db, texto=f"FOR → GIG R$ {preco} ida e volta em maio/2027", fonte="manual")
    promo = {p.preco: p for p in db.query(Promocao).filter(Promocao.destino == "GIG")}
    db.query(Match).filter_by(cliente_id=amanda.id, promocao_id=promo[401].id).one().status = "descartada"  # pulada
    promo[402].status = "descartada"
    promo[403].alerta = "possível erro tarifário"
    promo[404].coletada_em = datetime.now() - timedelta(days=30)
    db.commit()
    html = visitante.get(f"/para-voce/{amanda.token_pagina}").text
    assert "R$ 405" in html
    for escondida in ("R$ 401", "R$ 402", "R$ 403", "R$ 404"):
        assert escondida not in html, escondida


def test_trecho_pausado_ou_excluido_tira_as_promocoes_da_pagina(db, site, visitante):
    amanda, _ = _amanda(db)
    _promos(db)
    trecho = amanda.interesses[0]
    site.post(f"/interesses/{trecho.id}/alternar")  # pausa o trecho FOR → RIO
    html = visitante.get(f"/para-voce/{amanda.token_pagina}").text
    assert "R$ 389" not in html and "Ainda não apareceu promoção" in html
    # outro trecho ativo que também combina (qualquer destino saindo de Fortaleza) traz as promoções de volta
    site.post(f"/clientes/{amanda.id}/interesses", data={"origem": "FOR", "destino": ""})
    html = visitante.get(f"/para-voce/{amanda.token_pagina}").text
    assert "R$ 389" in html and "R$ 450" in html
    assert "R$ 2.389" in html  # e as que combinam com o trecho novo


def test_pagina_sem_promocao_ainda(db, visitante):
    amanda, _ = _amanda(db)
    html = visitante.get(f"/para-voce/{amanda.token_pagina}").text
    assert "Ainda não apareceu promoção" in html and "Fortaleza → Rio de Janeiro" in html


def test_link_errado_trocado_ou_de_cliente_inativo_nao_abre(db, site, visitante):
    amanda, maria = _amanda(db)
    _promos(db)
    antigo = amanda.token_pagina
    for token in ("abc", "x" * 24, antigo[:-1], antigo + "a", "%00" + antigo, antigo.lower() + "@", "' or '1'='1"):
        r = visitante.get(f"/para-voce/{token}")
        assert r.status_code == 404 and "Link indisponível" in r.text and "Amanda" not in r.text, token
    # link novo: o antigo para de funcionar
    site.post(f"/clientes/{amanda.id}/pagina/novo-link")
    db.refresh(amanda)
    assert amanda.token_pagina != antigo
    assert visitante.get(f"/para-voce/{antigo}").status_code == 404
    assert visitante.get(f"/para-voce/{amanda.token_pagina}").status_code == 200
    # inativo ou sem autorização: mesma resposta de link errado
    maria.ativo = False
    amanda.consentimento = False
    db.commit()
    for c in (maria, amanda):
        r = visitante.get(f"/para-voce/{c.token_pagina}")
        assert r.status_code == 404 and "Link indisponível" in r.text and c.nome.split()[0] not in r.text
    # só a equipe troca o link
    assert visitante.post(f"/clientes/{maria.id}/pagina/novo-link", follow_redirects=False).status_code in (303, 401, 403)


def test_muitas_tentativas_de_link_errado_bloqueiam(db, visitante):
    amanda, _ = _amanda(db)
    for i in range(30):
        assert visitante.get(f"/para-voce/tentativa{i:020d}").status_code == 404
    r = visitante.get("/para-voce/tentativa99999999999999999999")
    assert r.status_code == 429 and "Muitas tentativas" in r.text
    # no mesmo endereço (ex.: o de uma operadora de celular), o link certo continua abrindo
    assert visitante.get(f"/para-voce/{amanda.token_pagina}").status_code == 200


def test_mensagens_levam_o_link_da_pagina(db, site):
    amanda, maria = _amanda(db)
    _promos(db)
    db.add(ConfirmacaoCadastro(cliente=maria))
    db.commit()
    link = f"http://testserver/para-voce/{amanda.token_pagina}"
    fila = site.get("/").text
    assert "Veja todas as promoções que separamos para você: " + link in fila  # botão Copiar
    assert quote(link, safe="") in fila or quote(link) in fila  # link do WhatsApp
    assert f"vão aparecer nesta página: http://testserver/para-voce/{maria.token_pagina}" in fila  # confirmação
    # modelo com {pagina} no meio: o link não se repete no fim
    site.post("/configuracoes/mensagem", data={"texto": "Oi {primeiro_nome}! {origem} → {destino} {valor}. Página: {pagina}"})
    fila = site.get("/").text
    assert f"Página: {link}" in fila and "Veja todas as promoções que separamos" not in fila


def test_link_desligado_nao_vai_nas_mensagens(db, site):
    amanda, _ = _amanda(db)
    _promos(db)
    site.post("/configuracoes/pagina", data={"texto": "", "whatsapp": ""})  # caixa desmarcada
    fila = site.get("/").text
    assert "Veja todas as promoções que separamos" not in fila
    assert f"/para-voce/{amanda.token_pagina}" not in fila
    site.post("/configuracoes/pagina", data={"texto": "", "whatsapp": "", "link_nas_mensagens": "1"})
    assert "Veja todas as promoções que separamos" in site.get("/").text


def test_por_cliente_manda_o_link_e_registra_as_promocoes(db, site):
    amanda, maria = _amanda(db)
    _promos(db)
    registrar_promocao(db, texto="FOR → GIG R$ 199 ida e volta em junho/2027", fonte="manual")
    revisar = db.query(Promocao).filter_by(preco=199).one()
    revisar.alerta = "possível erro tarifário"
    db.commit()
    html = site.get("/?ver=clientes").text
    assert "Por cliente · link da página (2)" in html
    corpo = html.split('<form class="filtros"')[-1]
    assert "Amanda Ferreira Souza" in corpo and "2 novas" in corpo and "Maria Oliveira" in corpo
    assert "A Mundial Viagens separou 2 promoções para você" in corpo or "separou 2 promoções para você" in corpo
    assert f"http://testserver/para-voce/{amanda.token_pagina}" in corpo
    assert "R$ 199" not in corpo  # promoção para revisar não entra na página nem na mensagem
    assert "Amanda" not in site.get("/?ver=clientes&q=maria").text.split('<form class="filtros"')[-1]

    r = site.post(f"/clientes/{amanda.id}/pagina/enviada", headers=FETCH)
    assert r.json() == {"ok": True, "envios": 1, "promocoes": 2}
    db.expire_all()
    status = {m.promocao.preco: m.status for m in db.query(Match).filter_by(cliente_id=amanda.id)}
    assert status == {450: "enviada", 389: "enviada", 199: "nova"}  # a de revisar continua na fila
    corpo = site.get("/?ver=clientes").text.split('<form class="filtros"')[-1]
    assert "Amanda Ferreira Souza" not in corpo and "Maria Oliveira" in corpo
    # sem autorização: não registra
    maria.consentimento = False
    db.commit()
    assert site.post(f"/clientes/{maria.id}/pagina/enviada", headers=FETCH).status_code == 400


def test_cliente_mostra_mensagem_de_retorno(db, site):
    """O cartão "Página do cliente" saiu; no lugar da confirmação fica a mensagem de retorno, com o link da página."""
    amanda, _ = _amanda(db)
    _promos(db)
    html = site.get(f"/clientes/{amanda.id}").text
    assert "Gerar link novo" not in html and "Enviar link no WhatsApp" not in html
    assert 'id="link-pagina"' in html and f'data-copiar="http://testserver/para-voce/{amanda.token_pagina}"' in html
    assert "Mensagem de retorno" in html and "30 dias sem comprar" in html
    assert f"http://testserver/para-voce/{amanda.token_pagina}" in html     # o link da página vai dentro da mensagem
    assert f'action="/crm/reengajar/{amanda.id}/whatsapp"' in html
    # enviar daqui funciona mesmo fora da lista e registra a tentativa
    r = site.post(f"/crm/reengajar/{amanda.id}/whatsapp", data={"de": "cliente"}, follow_redirects=False)
    assert r.status_code == 303 and "api.whatsapp.com" in r.headers["location"]
    assert "Enviada pelo WhatsApp" in site.get(f"/clientes/{amanda.id}").text


def test_quero_esta_promocao_abre_o_whatsapp_da_agencia(db, site, visitante):
    amanda, _ = _amanda(db)
    _promos(db)
    html = visitante.get(f"/para-voce/{amanda.token_pagina}").text
    assert "wa.me" not in html and "Responda a mensagem que você recebeu" in html
    r = site.post("/configuracoes/pagina", data={"texto": "", "whatsapp": "123", "link_nas_mensagens": "1"},
                  follow_redirects=True)
    assert "WhatsApp da agência inválido" in r.text
    site.post("/configuracoes/pagina", data={"texto": "", "whatsapp": "(85) 99999-0000", "link_nas_mensagens": "1"})
    html = visitante.get(f"/para-voce/{amanda.token_pagina}").text
    assert html.count("https://api.whatsapp.com/send?phone=5585999990000&amp;text=") == 3  # duas promoções e o botão do rodapé
    assert quote("Olá! Sou Amanda e tenho interesse nesta promoção: Fortaleza → Rio de Janeiro · R$ 389") in html
    assert 'rel="noopener noreferrer"' in html
    assert "(85) 99999-0000" in site.get("/configuracoes").text


def test_configuracoes_da_pagina(db, site):
    html = site.get("/configuracoes").text
    assert "Página do cliente" in html and "separou 3 promoções para você" in html  # prévia
    assert "/para-voce/" in html
    site.post("/configuracoes/pagina", data={"texto": "Oi {primeiro_nome}, veja: {pagina}", "whatsapp": "",
                                               "link_nas_mensagens": "1"})
    assert "Oi Maria, veja: http://testserver/para-voce/" in site.get("/configuracoes").text
    site.post("/configuracoes/pagina", data={"texto": "x", "whatsapp": "", "link_nas_mensagens": "1", "restaurar": "1"})
    assert "separou 3 promoções para você" in site.get("/configuracoes").text


def test_quem_ataca_nao_trava_a_equipe_no_aparelho_dela(db):
    with TestClient(app) as equipe, TestClient(app) as atacante:
        assert equipe.post("/login", data={"login": "admin", "senha": "admin123"}, follow_redirects=False).status_code == 303
        equipe.get("/sair")
        for _ in range(15):  # outro aparelho erra a senha do admin várias vezes
            atacante.post("/login", data={"login": "admin", "senha": "chute"})
        r = atacante.post("/login", data={"login": "admin", "senha": "admin123"}, follow_redirects=True)
        assert "Muitas tentativas" in r.text  # aparelho novo: travado
        r = equipe.post("/login", data={"login": "admin", "senha": "admin123"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"  # aparelho conhecido entra normalmente
        # cookie de aparelho falsificado não vale
        from app.main import _nome_cookie_aparelho

        atacante.cookies.set(_nome_cookie_aparelho("admin"), "eyJsIjoiYWRtaW4iLCJhIjoieCJ9.falso")
        r = atacante.post("/login", data={"login": "admin", "senha": "admin123"}, follow_redirects=True)
        assert "Muitas tentativas" in r.text


def test_navegador_com_varios_logins_guarda_um_aparelho_para_cada(db):
    from app.models import Usuario
    from app.servicos import gerar_hash_senha

    db.add(Usuario(nome="Gestora", login="gestora", senha_hash=gerar_hash_senha("Gestora-2026!"), papel="gestor"))
    db.commit()
    with TestClient(app) as balcao, TestClient(app) as atacante:
        balcao.post("/login", data={"login": "admin", "senha": "admin123"})
        balcao.get("/sair")
        balcao.post("/login", data={"login": "gestora", "senha": "Gestora-2026!"})
        balcao.get("/sair")
        for _ in range(12):
            atacante.post("/login", data={"login": "admin", "senha": "chute"})
        r = balcao.post("/login", data={"login": "admin", "senha": "admin123"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"


def test_cookie_de_aparelho_vence(db, monkeypatch):
    import time as relogio

    from itsdangerous.timed import TimestampSigner

    from app.main import VALIDADE_APARELHO, _assinatura_aparelho, _nome_cookie_aparelho

    agora = int(relogio.time())
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: agora - VALIDADE_APARELHO - 60)
    vencido = _assinatura_aparelho.dumps({"l": "admin", "a": "aparelho-antigo"})
    monkeypatch.undo()
    with TestClient(app) as atacante:
        for _ in range(12):
            atacante.post("/login", data={"login": "admin", "senha": "chute"})
        atacante.cookies.set(_nome_cookie_aparelho("admin"), vencido)
        r = atacante.post("/login", data={"login": "admin", "senha": "admin123"}, follow_redirects=True)
        assert "Muitas tentativas" in r.text


def test_tentativas_simultaneas_nao_passam_do_limite():
    import threading

    from app.limite import Limitador

    limite = Limitador(maximo=10, janela=60)
    liberadas = []
    largada = threading.Barrier(40)

    def tentar():
        largada.wait()
        liberadas.append(limite.tentar("admin"))

    fios = [threading.Thread(target=tentar) for _ in range(40)]
    for f in fios:
        f.start()
    for f in fios:
        f.join()
    assert liberadas.count(True) == 10


def test_chaves_inventadas_nao_apagam_um_bloqueio():
    from app.limite import Limitador

    limite = Limitador(maximo=3, janela=60, max_chaves=100)
    for _ in range(3):
        assert limite.tentar("admin")
    assert not limite.tentar("admin")
    for i in range(1000):
        limite.tentar(f"lixo-{i}")
    assert not limite.tentar("admin") and len(limite) <= 100


def test_limite_nao_deixa_a_memoria_crescer():
    from app.limite import Limitador

    limite = Limitador(maximo=3, janela=60, max_chaves=500)
    for i in range(5000):
        limite.tentar(f"ip-inventado-{i}" + "x" * 500)
    assert len(limite) == 500
    assert max(len(k) for k in limite._tentativas) <= 100


def test_ip_real_vem_da_cloudflare(db):
    from app.models_mvo import RegistroAcesso

    with TestClient(app) as navegador:
        navegador.post("/login", data={"login": "admin", "senha": "errada"}, headers={"CF-Connecting-IP": "203.0.113.9"})
        navegador.post("/login", data={"login": "admin", "senha": "errada"}, headers={"CF-Connecting-IP": "<script>"})
    ips = [r.ip for r in db.query(RegistroAcesso).filter_by(acao="login_recusado").order_by(RegistroAcesso.id)]
    assert ips[0] == "203.0.113.9" and ips[1] != "<script>"


def test_link_do_cliente_nao_fica_no_registro_do_servidor():
    import logging

    from app.main import _EsconderLinkDoCliente

    registro = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', (
        "1.2.3.4:5", "GET", "/para-voce/AbCdEf123456789012345678?x=1", "1.1", 200), None)
    _EsconderLinkDoCliente().filter(registro)
    assert "AbCdEf" not in registro.getMessage() and "/para-voce/…?x=1" in registro.getMessage()


def test_aviso_para_trocar_senha_fraca(db, site):
    assert "Troque a sua senha" in site.get("/").text  # o admin dos testes usa a senha padrão
    r = site.post("/configuracoes/senha", data={"atual": "admin123", "nova": "abc123"}, follow_redirects=True)
    assert "ainda é fraca" in r.text and "Troque a sua senha" in site.get("/").text
    site.post("/configuracoes/senha", data={"atual": "abc123", "nova": "Viagem-Forte-2026"})
    assert "Troque a sua senha" not in site.get("/").text


def test_login_bloqueia_depois_de_muitas_tentativas(db):
    with TestClient(app) as navegador:
        for _ in range(10):
            r = navegador.post("/login", data={"login": "admin", "senha": "errada"}, follow_redirects=True)
            assert "Login ou senha incorretos" in r.text
        r = navegador.post("/login", data={"login": "admin", "senha": "admin123"}, follow_redirects=True)
        assert "Muitas tentativas" in r.text
        assert navegador.get("/", follow_redirects=False).status_code == 303  # continua fora


def test_login_certo_zera_as_tentativas(db):
    with TestClient(app) as navegador:
        for _ in range(9):
            navegador.post("/login", data={"login": "admin", "senha": "errada"})
        assert navegador.post("/login", data={"login": "admin", "senha": "admin123"}, follow_redirects=False).status_code == 303
        assert navegador.get("/", follow_redirects=False).status_code == 200
        navegador.get("/sair")
        for _ in range(9):
            navegador.post("/login", data={"login": "admin", "senha": "errada"})
        r = navegador.post("/login", data={"login": "admin", "senha": "admin123"}, follow_redirects=True)
        assert "Muitas tentativas" not in r.text and "Fila de envio" in r.text


def test_cabecalhos_de_seguranca_em_todo_o_site(db, site):
    r = site.get("/login")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["referrer-policy"] == "same-origin"
    assert "strict-transport-security" not in r.headers  # só no https
    r = site.get("/static/para-voce.css")
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff"
