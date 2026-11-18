"""WhatsApp automático (API oficial da Meta): configuração em tela própria, envio de promoção e confirmação com
modelo aprovado, limites por dia, webhook assinado com status e respostas, SAIR tira a autorização, tela de
respostas. A Meta é substituída por um transporte de mentira (não há rede nos testes)."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import coleta, whatsapp_api as api
from app.config import settings
from app.main import app
from app.models import Cliente, ConfirmacaoCadastro, EnvioWhatsApp, ImagemPromocao, Interesse, Match, Promocao, RespostaWhatsApp
from app.servicos import registrar_promocao

TOKEN = "EAAB-token-de-teste"
SEGREDO = "segredo-do-app"


@pytest.fixture()
def meta(monkeypatch):
    """Grava o que o sistema mandou para a Meta e responde como ela."""
    estado = {"mensagens": [], "midias": [], "falhar": None, "contador": 0}

    def transporte(pedido: httpx.Request):
        assert pedido.headers.get("Authorization") == f"Bearer {TOKEN}"
        if pedido.url.path.endswith("/media"):
            estado["midias"].append(pedido)
            return httpx.Response(200, json={"id": "MEDIA123"})
        corpo = json.loads(pedido.content)
        estado["mensagens"].append(corpo)
        if estado["falhar"]:
            return httpx.Response(400, json={"error": {"message": estado["falhar"], "code": 131026}})
        estado["contador"] += 1
        return httpx.Response(200, json={"messaging_product": "whatsapp", "contacts": [{"wa_id": corpo["to"]}],
                                         "messages": [{"id": f"wamid.{estado['contador']}"}]})

    class ClienteFingido(httpx.Client):
        def close(self):  # o sistema fecha o cliente que abriu; aqui o mesmo serve para o teste todo
            pass

    cliente = ClienteFingido(transport=httpx.MockTransport(transporte))
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: cliente)
    yield estado
    monkeypatch.undo()
    httpx.Client.close(cliente)


@pytest.fixture()
def site(db):
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "admin", "senha": "admin123"})
        yield cliente


def _configurar(db, **extra):
    dados = {"token": TOKEN, "phone_id": "111222333", "waba_id": "999", "verify_token": "verifica-123",
             "app_secret": SEGREDO, "modelo_promocao": "promocao_passagem", "modelo_confirmacao": "confirmacao_cadastro",
             "automatico": "1"}
    dados.update(extra)
    api.salvar_configuracao(db, dados)


def _ana(db):
    ana = Cliente(nome="Ana Costa Lima", telefone="5585981112222", consentimento=True)
    ana.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add(ana)
    db.commit()
    return ana


def _promocao_com_match(db):
    registrar_promocao(db, texto="FOR → LIS R$ 2.389 ida e volta TAP em março/27", fonte="manual")
    return db.query(Match).one()


# ---------------------------------------------------------------- configuração


def test_configuracao_fica_cifrada_e_token_nunca_volta_para_a_tela(db, site):
    r = site.post("/configuracoes/whatsapp", data={"token": TOKEN, "phone_id": "111", "waba_id": "9", "verify_token": "v",
                                                  "app_secret": SEGREDO, "modelo_promocao": "promo", "idioma": "pt_BR",
                                                  "variaveis_promocao": "nome, trecho, valor", "max_por_dia": "50",
                                                  "max_por_cliente_dia": "2"}, follow_redirects=False)
    assert r.status_code == 303
    from app.models import EstadoColetor

    guardado = db.get(EstadoColetor, "whatsapp_api:token").valor
    assert TOKEN not in guardado  # cifrado no banco
    conf = api.configuracao(db)
    assert conf["token"] == TOKEN and conf["app_secret"] == SEGREDO and conf["max_por_dia"] == "50"
    assert api.configurada(db) and not api.automatico_ligado(db)

    tela = site.get("/configuracoes/whatsapp").text
    assert TOKEN not in tela and SEGREDO not in tela
    assert "guardado · deixe em branco para manter" in tela
    assert "configurada" in tela

    # salvar de novo com o token em branco mantém o token
    site.post("/configuracoes/whatsapp", data={"token": "", "phone_id": "111", "automatico": "1", "max_por_dia": "50",
                                              "max_por_cliente_dia": "2"})
    assert api.configuracao(db)["token"] == TOKEN
    assert api.automatico_ligado(db)


def test_variavel_desconhecida_e_recusada(db, site):
    r = site.post("/configuracoes/whatsapp", data={"phone_id": "111", "variaveis_promocao": "nome, cpf"}, follow_redirects=True)
    assert "Variável desconhecida: cpf" in r.text
    assert api.configuracao(db)["phone_id"] == ""


def test_configuracoes_tem_atalho_e_atendente_nao_entra(db, site):
    assert "/configuracoes/whatsapp" in site.get("/configuracoes").text
    from app.models import Usuario
    from app.servicos import gerar_hash_senha

    db.add(Usuario(nome="Bia", login="bia", senha_hash=gerar_hash_senha("bia12345"), papel="atendente"))
    db.commit()
    with TestClient(app) as outro:
        outro.post("/login", data={"login": "bia", "senha": "bia12345"})
        assert outro.get("/configuracoes/whatsapp", follow_redirects=False).status_code in (302, 303, 403)


# ---------------------------------------------------------------- envios


def test_envia_promocao_com_modelo_imagem_e_variaveis(db, meta, monkeypatch):
    _configurar(db, variaveis_promocao="nome, trecho, valor, datas, pagina, contato", contato="85 3333-4444",
                imagem_cabecalho="1")
    ana = _ana(db)
    m = _promocao_com_match(db)
    m.promocao.imagem = ImagemPromocao(mime="image/jpeg", dados=b"\xff\xd8imagem")
    db.commit()

    r = api.enviar_promocao(db, m, "https://buscas.mundialviagens.com", "Equipe")
    assert r.ok, r.motivo
    assert len(meta["midias"]) == 1  # a imagem subiu uma vez
    corpo = meta["mensagens"][0]
    assert corpo["to"] == "5585981112222" and corpo["type"] == "template"
    assert corpo["template"]["name"] == "promocao_passagem" and corpo["template"]["language"]["code"] == "pt_BR"
    cabecalho, body = corpo["template"]["components"]
    assert cabecalho["parameters"][0]["image"]["id"] == "MEDIA123"
    textos = [p["text"] for p in body["parameters"]]
    assert textos[0] == "Ana Lima" and "Lisboa" in textos[1] and "2.389" in textos[2]
    assert textos[4].startswith("https://buscas.mundialviagens.com/para-voce/")
    assert textos[5] == "https://wa.me/558533334444"

    db.refresh(m)
    assert m.status == "enviada" and m.envios == 1 and m.enviado_por == "Equipe"
    envio = db.query(EnvioWhatsApp).one()
    assert envio.wamid == "wamid.1" and envio.status == "enviada" and envio.tipo == "promocao"
    assert m.promocao.imagem.meta_media_id == "MEDIA123"

    # segundo envio reaproveita o media id
    api.enviar_promocao(db, m, "https://buscas.mundialviagens.com", "Equipe")
    assert len(meta["midias"]) == 1


def test_falha_da_meta_fica_registrada_e_nao_marca_enviada(db, meta):
    _configurar(db)
    _ana(db)
    m = _promocao_com_match(db)
    meta["falhar"] = "Template name does not exist"
    r = api.enviar_promocao(db, m, "https://x.test", "Equipe")
    assert not r.ok and "Template name does not exist" in r.motivo
    db.refresh(m)
    assert m.status == "nova"
    envio = db.query(EnvioWhatsApp).one()
    assert envio.status == "falhou" and "131026" in envio.erro


def test_nao_envia_sem_consentimento_nem_acima_dos_limites(db, meta):
    _configurar(db, max_por_dia="2", max_por_cliente_dia="1")
    ana = _ana(db)
    m = _promocao_com_match(db)
    ana.consentimento = False
    db.commit()
    assert api.enviar_promocao(db, m, "https://x.test", "Equipe").motivo == "cliente sem autorização"
    ana.consentimento = True
    db.commit()
    assert api.enviar_promocao(db, m, "https://x.test", "Equipe").ok
    r = api.enviar_promocao(db, m, "https://x.test", "Equipe")
    assert r.motivo == "este cliente já recebeu o máximo de hoje"
    # limite do dia (geral)
    bia = Cliente(nome="Bia", telefone="5585980000001", consentimento=True)
    bia.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add(bia)
    db.commit()
    from app.servicos import gerar_matches_para_interesse

    gerar_matches_para_interesse(db, bia.interesses[0])
    m2 = db.query(Match).filter(Match.cliente_id == bia.id).one()
    assert api.enviar_promocao(db, m2, "https://x.test", "Equipe").ok
    carla = Cliente(nome="Carla", telefone="5585980000002", consentimento=True)
    carla.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add(carla)
    db.commit()
    gerar_matches_para_interesse(db, carla.interesses[0])
    m3 = db.query(Match).filter(Match.cliente_id == carla.id).one()
    assert api.enviar_promocao(db, m3, "https://x.test", "Equipe").motivo == "limite diário de envios atingido"
    assert len(meta["mensagens"]) == 2


def test_confirmacao_de_cadastro_pela_api(db, meta):
    _configurar(db, variaveis_confirmacao="nome, trechos, pagina")
    ana = _ana(db)
    conf = ConfirmacaoCadastro(cliente=ana)
    db.add(conf)
    db.commit()
    r = api.enviar_confirmacao(db, conf, "https://x.test", "Equipe")
    assert r.ok, r.motivo
    corpo = meta["mensagens"][0]
    assert corpo["template"]["name"] == "confirmacao_cadastro"
    textos = [p["text"] for p in corpo["template"]["components"][0]["parameters"]]
    assert textos[0] == "Ana Lima" and "Lisboa" in textos[1] and "\n" not in textos[1]
    db.refresh(conf)
    assert conf.status == "enviada" and conf.envios == 1
    assert not any(c["type"] == "button" for c in corpo["template"]["components"])

    # com o botão "página do cliente": o final do link é o código da página da Ana
    api.salvar_configuracao(db, {"botao_pagina_confirmacao": "1"})
    conf2 = ConfirmacaoCadastro(cliente=ana)
    db.add(conf2)
    db.commit()
    assert api.enviar_confirmacao(db, conf2, "https://x.test", "Equipe").ok
    botao = [c for c in meta["mensagens"][-1]["template"]["components"] if c["type"] == "button"][0]
    assert botao["sub_type"] == "url" and botao["parameters"][0]["text"] == ana.token_pagina


def test_pagina_do_cliente_mostra_saida_destino_ida_e_volta(db):
    """Pacote (ex.: do site): a página mostra Saindo de, Destino, Ida, Volta, Viagem e o valor parcelado."""
    from datetime import date as d
    from app.rotas.para_voce import _item

    p = Promocao(tipo="pacote", origem="FOR", destino="LIS", lugar="Lisboa e Porto", preco=500, parcelas=12,
                 data_inicio=d(2027, 3, 10), data_fim=d(2027, 3, 19), tipo_viagem="ida_volta", fonte="site")
    p.coletada_em = datetime.now()
    item = _item(p, "Ana", "", "tok")
    assert (item["saindo"], item["destino_nome"], item["ida"], item["volta"]) == ("Fortaleza", "Lisboa e Porto", "10/03/2027", "19/03/2027")
    assert item["valor"] == "R$ 500,00 de 12x" and item["viagem"]


def test_envio_automatico_manda_a_fila_e_pula_o_que_precisa_de_revisao(db, meta, monkeypatch):
    _configurar(db)
    ana = _ana(db)
    db.add(ConfirmacaoCadastro(cliente=ana))
    db.commit()
    m = _promocao_com_match(db)
    registrar_promocao(db, texto="FOR → LIS R$ 189 ida e volta em abril/27", fonte="manual")
    suspeita = db.query(Promocao).filter(Promocao.preco == 189).one()
    suspeita.alerta = "Preço 90% abaixo da média do trecho: possível erro tarifário"  # fica "para conferir"
    db.commit()
    assert suspeita.precisa_revisao

    monkeypatch.setattr(settings, "site_url", "https://buscas.mundialviagens.com")
    monkeypatch.setattr(settings, "coleta_automatica", True)
    coleta._enviar_whatsapp_automatico()
    resumo = coleta.ultima_execucao["whatsapp_api"]
    assert resumo["promocoes"] == 1 and resumo["confirmacoes"] == 1
    db.expire_all()
    assert m.status == "enviada" and m.enviado_por == "Automático (API)"
    assert db.query(Match).filter(Match.promocao_id == suspeita.id).one().status == "nova"
    assert all(e.automatico for e in db.query(EnvioWhatsApp))

    # desligado em Configurações: não manda nada
    api.salvar_configuracao(db, {"automatico": "0"})
    registrar_promocao(db, texto="FOR → LIS R$ 2.500 ida e volta em maio/27", fonte="manual")
    coleta._enviar_whatsapp_automatico()
    assert len(meta["mensagens"]) == 2


def test_sem_modelo_de_confirmacao_ela_nao_vai_pela_api(db, meta, monkeypatch, site):
    """Confirmação de cadastro sem modelo em Configurações: o automático não manda (nem conta falha) e a fila
    não mostra "Pela API" nela; a promoção segue normal."""
    _configurar(db, modelo_confirmacao="")
    ana = _ana(db)
    db.add(ConfirmacaoCadastro(cliente=ana))
    db.commit()
    _promocao_com_match(db)
    monkeypatch.setattr(settings, "site_url", "https://mvo.mundialviagens.com")
    monkeypatch.setattr(settings, "coleta_automatica", True)
    coleta._enviar_whatsapp_automatico()
    resumo = coleta.ultima_execucao["whatsapp_api"]
    assert resumo["promocoes"] == 1 and resumo["confirmacoes"] == 0 and resumo["falhas"] == 0
    db.expire_all()
    assert db.query(ConfirmacaoCadastro).one().status == "nova"   # continua na fila para o envio manual
    nova = db.query(ConfirmacaoCadastro).one()
    html = site.get("/?ver=confirmacoes").text
    assert f"/confirmacoes/{nova.id}/enviar-api" not in html
    _configurar(db, modelo_confirmacao="confirmacao_cadastro")   # com o modelo, o botão volta
    assert f"/confirmacoes/{nova.id}/enviar-api" in site.get("/?ver=confirmacoes").text


def test_sem_site_url_o_automatico_nao_roda(db, meta, monkeypatch):
    _configurar(db)
    _ana(db)
    _promocao_com_match(db)
    monkeypatch.setattr(settings, "site_url", "")
    coleta.ultima_execucao.pop("whatsapp_api", None)
    coleta._enviar_whatsapp_automatico()
    assert "whatsapp_api" not in coleta.ultima_execucao and not meta["mensagens"]


def test_botoes_pela_api_na_fila_e_envio_por_fetch(db, meta, site):
    _ana(db)
    m = _promocao_com_match(db)
    assert "Pela API" not in site.get("/").text  # sem configuração, o botão não aparece
    _configurar(db)
    fila = site.get("/").text
    assert f'data-enviar-api="/matches/{m.id}/enviar-api"' in fila
    r = site.post(f"/matches/{m.id}/enviar-api", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and r.json() == {"ok": True, "motivo": "", "envios": 1}
    meta["falhar"] = "Recipient not in allowed list"
    r = site.post(f"/matches/{m.id}/enviar-api", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 400 and "allowed list" in r.json()["motivo"]
    assert site.post("/matches/9999/enviar-api").status_code == 404


def test_mensagem_de_teste(db, meta, site):
    _configurar(db)
    r = site.post("/configuracoes/whatsapp/teste", data={"telefone": "(85) 98111-2222"}, follow_redirects=True)
    assert "Mensagem de teste enviada" in r.text
    corpo = meta["mensagens"][0]
    assert corpo["template"]["name"] == "hello_world" and corpo["template"]["language"]["code"] == "en_US"
    assert corpo["to"] == "5585981112222"


# ---------------------------------------------------------------- webhook


def _assinar(corpo: bytes) -> str:
    return "sha256=" + hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()


def _webhook(statuses=None, messages=None, contacts=None):
    valor = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": "111222333"}}
    if statuses:
        valor["statuses"] = statuses
    if messages:
        valor["messages"] = messages
    if contacts:
        valor["contacts"] = contacts
    return {"object": "whatsapp_business_account", "entry": [{"id": "999", "changes": [{"field": "messages", "value": valor}]}]}


def test_verificacao_do_webhook(db, site):
    _configurar(db)
    r = site.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verifica-123", "hub.challenge": "4242"})
    assert r.status_code == 200 and r.text == "4242"
    assert site.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "1"}).status_code == 403
    api.salvar_configuracao(db, {"verify_token": ""})
    assert site.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}).status_code == 403


def test_webhook_exige_assinatura_e_atualiza_status(db, meta, site):
    _configurar(db)
    _ana(db)
    m = _promocao_com_match(db)
    assert api.enviar_promocao(db, m, "https://x.test", "Equipe").ok
    corpo = json.dumps(_webhook(statuses=[{"id": "wamid.1", "status": "delivered", "timestamp": "1700000000"}])).encode()
    assert site.post("/webhooks/whatsapp", content=corpo, headers={"content-type": "application/json"}).status_code == 403
    assert site.post("/webhooks/whatsapp", content=corpo, headers={"content-type": "application/json",
                                                                  "X-Hub-Signature-256": "sha256=errada"}).status_code == 403
    r = site.post("/webhooks/whatsapp", content=corpo, headers={"content-type": "application/json", "X-Hub-Signature-256": _assinar(corpo)})
    assert r.status_code == 200
    db.expire_all()
    envio = db.query(EnvioWhatsApp).one()
    assert envio.status == "entregue"
    # "read" depois de "delivered" avança; um "sent" atrasado não volta atrás
    for st in ("read", "sent"):
        corpo = json.dumps(_webhook(statuses=[{"id": "wamid.1", "status": st}])).encode()
        site.post("/webhooks/whatsapp", content=corpo, headers={"content-type": "application/json", "X-Hub-Signature-256": _assinar(corpo)})
    db.expire_all()
    assert envio.status == "lida"
    corpo = json.dumps(_webhook(statuses=[{"id": "wamid.1", "status": "failed",
                                           "errors": [{"code": 131047, "title": "Re-engagement message", "message": "fora da janela"}]}])).encode()
    site.post("/webhooks/whatsapp", content=corpo, headers={"content-type": "application/json", "X-Hub-Signature-256": _assinar(corpo)})
    db.expire_all()
    assert envio.status == "falhou" and "Re-engagement" in envio.erro


def test_webhook_sem_api_configurada_e_404_e_corpo_invalido_400(db, site):
    assert site.post("/webhooks/whatsapp", content=b"{}", headers={"content-type": "application/json"}).status_code == 404
    _configurar(db, app_secret="")  # sem segredo do app: aceita sem assinatura
    assert site.post("/webhooks/whatsapp", content=b"isso nao e json", headers={"content-type": "application/json"}).status_code == 400
    assert site.post("/webhooks/whatsapp", content=b"[]", headers={"content-type": "application/json"}).status_code == 400
    assert site.post("/webhooks/whatsapp", content=b"{}", headers={"content-type": "application/json"}).status_code == 200


def test_resposta_do_cliente_vira_conversa_e_sair_tira_a_autorizacao(db, meta, site):
    _configurar(db)
    ana = _ana(db)
    mensagens = [
        {"from": "5585981112222", "id": "wamid.r1", "timestamp": str(int(datetime.now().timestamp())), "type": "text",
         "text": {"body": "Oi! Tem para abril?"}},
        {"from": "558598765432", "id": "wamid.r2", "timestamp": str(int(datetime.now().timestamp())), "type": "image",
         "image": {"id": "img", "caption": "esse aqui"}},
    ]
    contatos = [{"wa_id": "5585981112222", "profile": {"name": "Ana"}}, {"wa_id": "558598765432", "profile": {"name": "Zé"}}]
    corpo = json.dumps(_webhook(messages=mensagens, contacts=contatos)).encode()
    cabecalhos = {"content-type": "application/json", "X-Hub-Signature-256": _assinar(corpo)}
    assert site.post("/webhooks/whatsapp", content=corpo, headers=cabecalhos).status_code == 200
    site.post("/webhooks/whatsapp", content=corpo, headers=cabecalhos)  # a Meta reenvia: não duplica
    respostas = db.query(RespostaWhatsApp).order_by(RespostaWhatsApp.id).all()
    assert len(respostas) == 2
    assert respostas[0].cliente_id == ana.id and respostas[0].texto == "Oi! Tem para abril?" and not respostas[0].lida
    assert respostas[1].cliente_id is None and respostas[1].texto == "[image] esse aqui" and respostas[1].nome == "Zé"
    assert api.respostas_nao_lidas(db) == 2

    tela = site.get("/whatsapp/respostas").text
    assert "Oi! Tem para abril?" in tela and "Ana Costa Lima" in tela and "Zé" in tela
    assert "Respostas do WhatsApp" in site.get("/").text  # entrada no menu, com contador
    assert '<span class="contador">2</span>' in site.get("/").text

    # a equipe responde (texto livre, dentro das 24 h) e a conversa fica lida
    r = site.post("/whatsapp/respostas/responder", data={"telefone": "5585981112222", "texto": "Temos sim! Veja a página."}, follow_redirects=True)
    assert "Resposta enviada" in r.text
    corpo_envio = meta["mensagens"][-1]
    assert corpo_envio["type"] == "text" and corpo_envio["text"]["body"] == "Temos sim! Veja a página."
    db.expire_all()
    assert respostas[0].lida and not respostas[1].lida
    assert db.query(EnvioWhatsApp).filter(EnvioWhatsApp.tipo == "resposta").one().cliente_id == ana.id
    # a tela em formato de conversa: lista com as duas pessoas, a resposta da equipe aparece no balão,
    # e ?c= abre a conversa escolhida (a de Zé, ainda com 1 não lida)
    assert r.url.query.decode() == "c=5585981112222"
    tela = site.get("/whatsapp/respostas?c=558598765432").text
    assert tela.count("data-alvo=") == 2 and "Temos sim! Veja a página." in tela
    assert 'data-conversa="5585981112222" hidden' in tela and 'data-conversa="558598765432" hidden' not in tela
    # abrir a conversa marca como lida sem recarregar a página
    assert site.post("/whatsapp/respostas/lidas", data={"telefone": "558598765432"},
                     headers={"x-requested-with": "fetch"}).text == "ok"
    db.expire_all()
    assert respostas[1].lida

    # SAIR: o cadastro perde a autorização
    corpo = json.dumps(_webhook(messages=[{"from": "5585981112222", "id": "wamid.r3", "type": "text",
                                           "timestamp": str(int(datetime.now().timestamp())), "text": {"body": "SAIR"}}])).encode()
    site.post("/webhooks/whatsapp", content=corpo, headers={"content-type": "application/json", "X-Hub-Signature-256": _assinar(corpo)})
    db.expire_all()
    assert ana.consentimento is False and "não receber mensagens" in ana.observacoes
    assert db.query(RespostaWhatsApp).filter(RespostaWhatsApp.wamid == "wamid.r3").one().descadastrou
    assert "pediu para sair" in site.get("/whatsapp/respostas").text

    site.post("/whatsapp/respostas/lidas", data={})
    assert api.respostas_nao_lidas(db) == 0


def test_cliente_e_achado_sem_o_nono_digito(db):
    ana = _ana(db)  # 5585 9 8111 2222
    assert api.cliente_pelo_telefone(db, "558581112222") is ana
    assert api.cliente_pelo_telefone(db, "5585981112222") is ana
    assert api.cliente_pelo_telefone(db, "5511999990000") is None
    assert api.variantes_telefone("558581112222") == ["558581112222", "5585981112222"]


def test_assinatura_valida():
    conf = {"app_secret": SEGREDO}
    corpo = b'{"a":1}'
    assert api.assinatura_valida(conf, corpo, _assinar(corpo))
    assert not api.assinatura_valida(conf, corpo, "sha256=00")
    assert not api.assinatura_valida(conf, corpo, None)
    assert api.assinatura_valida({"app_secret": ""}, corpo, None)  # sem segredo configurado, aceita


def test_corpo_do_modelo_limpa_quebras_de_linha():
    corpo = api.corpo_modelo("5585981112222", "promo", "pt_BR", ["nome", "resumo"], {"nome": "Ana", "resumo": "linha 1\nlinha   2"}, None)
    assert corpo["template"]["components"] == [{"type": "body", "parameters": [{"type": "text", "text": "Ana"},
                                                                                 {"type": "text", "text": "linha 1 linha 2"}]}]
    assert api.corpo_modelo("55", "m", "pt_BR", [], {}, "MEDIA")["template"]["components"][0]["type"] == "header"


def test_apagar_dados_da_api_desliga_tudo(db, site):
    _configurar(db)
    assert api.configurada(db) and api.automatico_ligado(db)
    r = site.post("/configuracoes/whatsapp/apagar", follow_redirects=True)
    assert "apagados" in r.text
    conf = api.configuracao(db)
    assert conf["token"] == "" and conf["app_secret"] == "" and conf["automatico"] == "0"
    assert not api.configurada(db)
    assert conf["phone_id"] == "111222333"  # os IDs ficam: é só colar um token novo


def test_webhook_de_outro_numero_e_ignorado(db, meta, site):
    _configurar(db)
    _ana(db)
    dados = _webhook(messages=[{"from": "5585981112222", "id": "wamid.x", "type": "text", "timestamp": "1700000000",
                                "text": {"body": "SAIR"}}])
    dados["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "outro-numero"
    corpo = json.dumps(dados).encode()
    assert site.post("/webhooks/whatsapp", content=corpo, headers={"content-type": "application/json",
                                                                  "X-Hub-Signature-256": _assinar(corpo)}).status_code == 200
    assert db.query(RespostaWhatsApp).count() == 0
    assert db.query(Cliente).one().consentimento is True


def test_teste_com_numero_real_e_aceito(db, meta, site):
    _configurar(db)
    meta["falhar"] = "(#131058) Hello World templates can only be sent from the Public Test Numbers"
    r = site.post("/configuracoes/whatsapp/teste", data={"telefone": "(85) 98111-2222"}, follow_redirects=True)
    assert "Token e número conferidos" in r.text


@pytest.mark.parametrize("codigo", [132012, 132000, 132018])
@pytest.mark.parametrize("modelo_tem_imagem", [True, False])
def test_cabecalho_de_imagem_se_ajusta_ao_modelo(db, monkeypatch, modelo_tem_imagem, codigo):
    """Erro 132012 (formato não bate): promoção sem imagem própria vai com a "Imagem com a logo" gerada quando o modelo
    tem cabeçalho de imagem, e sem cabeçalho quando não tem; o sistema guarda o que funcionou para os próximos envios."""
    mensagens, midias = [], []

    def transporte(pedido: httpx.Request):
        if pedido.url.path.endswith("/media"):
            midias.append(pedido.content)
            return httpx.Response(200, json={"id": f"MEDIA{len(midias)}"})
        corpo = json.loads(pedido.content)
        mensagens.append(corpo)
        tem_cabecalho = any(c["type"] == "header" for c in corpo["template"]["components"])
        if tem_cabecalho != modelo_tem_imagem:
            detalhe = ("header: Template does not contain title component, no parameters allowed" if tem_cabecalho
                       else "header: Format mismatch, expected IMAGE, received UNKNOWN")
            return httpx.Response(400, json={"error": {"message": "Parameter format does not match format in the created template",
                                                       "code": codigo, "error_data": {"details": detalhe}}})
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(mensagens)}"}]})

    _configurar(db, imagem_cabecalho="1")
    _ana(db)
    m = _promocao_com_match(db)   # promoção de texto: sem imagem própria
    with httpx.Client(transport=httpx.MockTransport(transporte)) as http:
        r = api.enviar_promocao(db, m, "https://mvo.mundialviagens.com", "Equipe", cliente_http=http)
        assert r.ok, r.motivo
        assert len(mensagens) == (1 if modelo_tem_imagem else 2)   # tenta com imagem primeiro
        if modelo_tem_imagem:
            assert b"JFIF" in midias[0][:2000] or len(midias[0]) > 10_000   # a imagem gerada (JPEG) subiu
        # o próximo envio já sai do jeito certo, sem errar antes
        mensagens.clear()
        m.status = "pendente"
        db.commit()
        assert api.enviar_promocao(db, m, "https://mvo.mundialviagens.com", "Equipe", cliente_http=http).ok
        assert len(mensagens) == 1


def test_botao_falar_com_atendente_leva_ao_0400_e_avisa_o_crm(db, site, meta):
    """Modelo com botão de URL: o envio leva o código do cliente no botão; o toque abre o 0400 e o CRM marca."""
    from app import crm
    from app.models import ConversaAtendimento
    _configurar(db, botao_atendimento="1", contato="85 3333-4444")
    ana = _ana(db)
    m = _promocao_com_match(db)
    assert api.enviar_promocao(db, m, "https://mvo.mundialviagens.com", "Equipe").ok
    botao = [c for c in meta["mensagens"][-1]["template"]["components"] if c["type"] == "button"][0]
    sufixo = botao["parameters"][0]["text"]
    assert botao["sub_type"] == "url" and sufixo == f"{ana.token_pagina}.{m.promocao_id}"

    r = site.get(f"/falar/{sufixo}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://api.whatsapp.com/send?phone=558533334444&text=")
    site.get(f"/falar/{sufixo}", follow_redirects=False)   # tocar de novo não duplica
    assert db.query(ConversaAtendimento).filter_by(cliente_id=ana.id, origem="cliente").count() == 1
    cartao = {c.cliente.id: c for c in crm.cartoes(db)}[ana.id]
    assert cartao.etapa == "respondeu" and cartao.canal == "0400" and "botão" in cartao.evento

    assert site.get("/falar/codigo-errado-que-nao-existe-123", follow_redirects=False).status_code == 404


def test_variaveis_origem_e_destino_em_codigo(db, meta):
    _configurar(db, variaveis_promocao="nome, origem, destino, valor")
    _ana(db)
    m = _promocao_com_match(db)
    assert api.enviar_promocao(db, m, "https://mvo.mundialviagens.com", "Equipe").ok
    corpo = [c for c in meta["mensagens"][-1]["template"]["components"] if c["type"] == "body"][0]
    assert [p["text"] for p in corpo["parameters"]][1:3] == ["FOR", "LIS"]


def test_modelo_com_variaveis_com_nome_na_meta(db, meta, site):
    """Modelo criado com {{nome_cliente}}, {{origem}}... : a configuração "nome_cliente=nome, ..." manda parameter_name."""
    _configurar(db, variaveis_promocao="nome_cliente=nome, origem=origem, destino=destino, valor_pedido=valor_numero")
    _ana(db)
    m = _promocao_com_match(db)
    assert api.enviar_promocao(db, m, "https://mvo.mundialviagens.com", "Equipe").ok
    corpo = [c for c in meta["mensagens"][-1]["template"]["components"] if c["type"] == "body"][0]
    assert corpo["parameters"] == [
        {"type": "text", "text": "Ana Lima", "parameter_name": "nome_cliente"},
        {"type": "text", "text": "FOR", "parameter_name": "origem"},
        {"type": "text", "text": "LIS", "parameter_name": "destino"},
        {"type": "text", "text": "2.389", "parameter_name": "valor_pedido"},
    ]
    # com os nomes das cidades no lugar dos códigos
    _configurar(db, variaveis_promocao="nome_cliente=nome, origem=cidade_origem, destino=cidade_destino, valor_pedido=valor_numero")
    m2 = _promocao_com_match(db)
    assert api.enviar_promocao(db, m2, "https://mvo.mundialviagens.com", "Equipe").ok
    corpo = [c for c in meta["mensagens"][-1]["template"]["components"] if c["type"] == "body"][0]
    assert [p["text"] for p in corpo["parameters"]][1:3] == ["Fortaleza", "Lisboa"]
    # a tela de Configurações aceita esse formato
    r = site.post("/configuracoes/whatsapp", data={"phone_id": "111222333", "waba_id": "999", "verify_token": "v",
                                                  "modelo_promocao": "envio_pedido", "idioma": "pt_BR",
                                                  "variaveis_promocao": "nome_cliente=nome, valor_pedido=valor_numero"})
    assert "Variável desconhecida" not in site.get("/configuracoes/whatsapp").text


def test_132000_que_nao_e_o_cabecalho_explica_o_que_conferir(db, meta):
    """Quantidade de variáveis errada: tenta com e sem imagem, e a mensagem diz o que conferir em Configurações."""
    _configurar(db, variaveis_promocao="nome, trecho, valor, datas, pagina")
    _ana(db)
    m = _promocao_com_match(db)
    meta["falhar"] = "(#132000) Number of parameters does not match the expected number of params"
    r = api.enviar_promocao(db, m, "https://mvo.mundialviagens.com", "Equipe")
    assert not r.ok
    assert "5 configurada(s)" in r.motivo and "Falar com um atendente" in r.motivo


def test_erro_da_meta_mostra_o_detalhe(db):
    """A Meta põe o motivo exato em error_data.details: ele aparece na mensagem de erro."""
    def transporte(pedido):
        return httpx.Response(400, json={"error": {"message": "(#132018) There's an issue with the parameters in your template",
                                                   "code": 132018, "error_data": {
                                                       "details": "Button at index 0 of type Url does not require parameters"}}})
    _configurar(db, botao_atendimento="1")
    _ana(db)
    m = _promocao_com_match(db)
    with httpx.Client(transport=httpx.MockTransport(transporte)) as http:
        r = api.enviar_promocao(db, m, "https://mvo.mundialviagens.com", "Equipe", cliente_http=http)
    assert not r.ok
    assert "Button at index 0" in r.motivo and "link fixo" in r.motivo


def test_sem_imagem_no_topo_por_padrao(db, meta):
    """Com a opção "imagem no topo" desmarcada (padrão), a mensagem sai só com texto: nenhuma imagem sobe."""
    _configurar(db)
    _ana(db)
    m = _promocao_com_match(db)
    assert api.enviar_promocao(db, m, "https://mvo.mundialviagens.com", "Equipe").ok
    assert len(meta["mensagens"]) == 1 and not meta["midias"]
    assert not any(c["type"] == "header" for c in meta["mensagens"][-1]["template"]["components"])


def test_respostas_mostra_quem_foi_para_o_0400_pelo_botao(db, site):
    """Quem tocou em "Falar com um atendente" aparece na tela de respostas, com o aviso de que a conversa está no 0400."""
    from app.models import Cliente, ConversaAtendimento

    junior = Cliente(nome="Junior Vale", telefone="5588981112222", consentimento=True)
    db.add(junior)
    db.flush()
    db.add(ConversaAtendimento(cliente_id=junior.id, origem="cliente", aberta_por="cliente"))
    db.commit()
    tela = site.get("/whatsapp/respostas").text
    assert "Junior Vale" in tela and "a conversa continua no WhatsApp do 0400" in tela


def test_confirmacao_pelo_0500_na_pagina_do_cliente(db, meta, site):
    """Botão "Pelo 0500 (API)" do cartão de confirmação: cria a confirmação se não houver, envia e volta à ficha."""
    _configurar(db, variaveis_confirmacao="primeiro_nome, trechos, pagina")
    ana = _ana(db)
    tela = site.get(f"/clientes/{ana.id}").text
    assert 'id="confirmacao-cadastro"' in tela and f'action="/clientes/{ana.id}/confirmacao-api"' in tela
    r = site.post(f"/clientes/{ana.id}/confirmacao-api", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"/clientes/{ana.id}")
    corpo = meta["mensagens"][-1]
    assert corpo["template"]["name"] == "confirmacao_cadastro"
    textos = [p["text"] for p in corpo["template"]["components"][0]["parameters"]]
    assert textos[0] == "Ana" and "/para-voce/" in textos[2]
    db.expire_all()
    assert ana.confirmacoes[-1].status == "enviada"


def test_confirmacao_com_origem_e_destino_separados(db, meta):
    """Modelo "{{1}} … {{2}} para {{3}} … {{4}}": nome, cidade de saída, destinos e link da página."""
    _configurar(db, variaveis_confirmacao="primeiro_nome, cidade_origem, cidade_destino, pagina")
    ana = _ana(db)   # acompanha Fortaleza → Lisboa
    conf = ConfirmacaoCadastro(cliente=ana)
    db.add(conf)
    db.commit()
    assert api.enviar_confirmacao(db, conf, "https://mvo.mundialviagens.com", "Equipe").ok
    textos = [p["text"] for p in meta["mensagens"][-1]["template"]["components"][0]["parameters"]]
    assert textos[:3] == ["Ana", "Fortaleza", "Lisboa"] and textos[3].startswith("https://mvo.mundialviagens.com/para-voce/")
