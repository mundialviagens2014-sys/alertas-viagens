from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.collectors import google_flights
from app.config import settings
from app.main import app
from app.models import Cliente, Interesse, Match, Promocao
from app.servicos import (
    formatar_preco,
    gerar_matches_para_interesse,
    link_whatsapp,
    montar_mensagem,
    normalizar_telefone,
    registrar_promocao,
)


def _cliente(db, nome="Maria Oliveira", tel="85999990000", **interesse):
    c = Cliente(nome=nome, telefone=normalizar_telefone(tel), consentimento=True)
    c.interesses.append(Interesse(**interesse))
    db.add(c)
    db.commit()
    return c


def test_telefone_e_preco():
    assert normalizar_telefone("(85) 99999-1234") == "5585999991234"
    assert normalizar_telefone("+351 912 345 678") == "351912345678"
    assert formatar_preco(2389) == "R$ 2.389"
    assert formatar_preco(1899.9) == "R$ 1.899,90"


def test_match_respeita_trecho_preco_e_periodo(db):
    _cliente(db, "Dentro", "85911111111", origem="FOR", destino="LIS", preco_maximo=3000,
             periodo_inicio=date(2027, 3, 1), periodo_fim=date(2027, 4, 30))
    _cliente(db, "Caro demais", "85922222222", origem="FOR", destino="LIS", preco_maximo=2000)
    _cliente(db, "Outra data", "85933333333", origem="FOR", destino="LIS",
             periodo_inicio=date(2027, 8, 1), periodo_fim=date(2027, 8, 31))
    _cliente(db, "Qualquer destino", "85944444444", origem="FOR")
    _cliente(db, "Outra origem", "85955555555", origem="REC", destino="LIS")

    r = registrar_promocao(db, texto="FORTALEZA ➜ LISBOA R$ 2.389 de março a junho/27", fonte="manual")
    nomes = {m.cliente.nome for m in r.promocao.matches}
    assert nomes == {"Dentro", "Qualquer destino"}


def test_aeroportos_da_mesma_cidade(db):
    _cliente(db, origem="SAO", destino="MIA")
    r = registrar_promocao(db, texto="GRU → MIA R$ 1.899", fonte="manual")
    assert r.matches == 1


def test_duplicada_e_incompleta(db):
    assert registrar_promocao(db, texto="FOR → LIS R$ 2.389", fonte="telegram").motivo == "criada"
    assert registrar_promocao(db, texto="Fortaleza x Lisboa por R$ 2.389!!", fonte="telegram").motivo == "duplicada"
    assert registrar_promocao(db, texto="Bom dia, grupo!", fonte="telegram").motivo == "incompleta"


def test_interesse_novo_encontra_promocao_vigente(db):
    registrar_promocao(db, texto="FOR → MCO R$ 3.950", fonte="manual")
    c = _cliente(db, origem="FOR", destino="MCO")
    db.query(Match).delete()
    db.commit()
    assert gerar_matches_para_interesse(db, c.interesses[0]) == 1


def test_preco_suspeito_vai_para_revisao(db):
    for i, preco in enumerate((3000, 3100, 2900)):
        registrar_promocao(db, texto=f"FOR → LIS R$ {preco} em 0{i + 3}/2027", fonte="manual")
    r = registrar_promocao(db, texto="FOR → LIS R$ 990 em 09/2027", fonte="manual")
    assert r.promocao.alerta and r.promocao.precisa_revisao


def test_mensagem_e_link_whatsapp(db):
    c = _cliente(db, origem="FOR", destino="LIS")
    p = registrar_promocao(db, texto="FOR → LIS R$ 2.389 com a TAP em março/27", fonte="manual").promocao
    msg = montar_mensagem("Olá, {primeiro_nome}! {origem} → {destino} por {valor}, {datas}. {inexistente}", c, p)
    assert msg == "Olá, Maria! Fortaleza → Lisboa por R$ 2.389, março/2027."
    assert link_whatsapp(c.telefone, "oi tudo") == "https://wa.me/5585999990000?text=oi%20tudo"


def _resposta_voo(preco, nivel, faixa=None):
    insights = {"price_level": nivel} if nivel else {}
    if faixa:
        insights["typical_price_range"] = faixa
    return {"best_flights": [{"price": preco, "flights": [{"airline": "TAP"}]}], "price_insights": insights,
            "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights"}}


def test_google_flights_explora_e_confirma_so_o_que_bate_com_os_clientes(db, monkeypatch):
    """Uma busca "Explorar" por origem; confirma no Google Flights só o destino de cliente com preço até o teto dele;
    entra no sistema só o que o Google marca como preço baixo."""
    monkeypatch.setattr(settings, "serpapi_key", "teste")
    monkeypatch.setattr(settings, "intervalo_google_horas", 24)   # uma rodada por dia: a cota do dia vai toda nela
    _cliente(db, origem="FOR", destino="LIS", preco_maximo=2600)
    _cliente(db, nome="Ana Lima", tel="85988887777", origem="FOR", destino="MIA", preco_maximo=1500)
    pedidos = []

    def resposta(request: httpx.Request):
        motor = request.url.params["engine"]
        pedidos.append(motor)
        if motor == "google_travel_explore":
            assert request.url.params["departure_id"] == "FOR" and request.url.params["type"] == "2"
            return httpx.Response(200, json={"destinations": [
                {"name": "Lisboa", "destination_airport": {"code": "LIS"}, "flight_price": 2400, "start_date": "2026-11-20"},
                {"name": "Miami", "destination_airport": {"code": "MIA"}, "flight_price": 3100, "start_date": "2026-11-25"},
                {"name": "Paris", "destination_airport": {"code": "CDG"}, "flight_price": 2100, "start_date": "2026-11-21"},
            ]})
        assert request.url.params["arrival_id"] == "LIS" and request.url.params["outbound_date"] == "2026-11-20"
        return httpx.Response(200, json=_resposta_voo(2400, "low", [3100, 3900]))

    with httpx.Client(transport=httpx.MockTransport(resposta)) as http:
        r = google_flights.coletar(db, hoje=date(2026, 10, 1), cliente_http=http)
    # 1 explorar + 1 confirmação (Miami passou do teto da cliente; Paris ninguém pediu)
    assert pedidos == ["google_travel_explore", "google_flights"] and r["buscas"] == 2
    assert r["acima_do_cliente"] == 1 and r["criadas"] == 1
    promo = db.query(Promocao).filter_by(fonte="google_flights").one()
    assert (promo.destino, promo.preco) == ("LIS", 2400)
    assert promo.fonte_detalhe == "Google Flights · preço baixo (normal: R$ 3100 a R$ 3900)"
    assert r["orcamento"]["usadas_mes"] == 2

    # na rodada seguinte, Lisboa foi conferida há pouco: só o explorar gasta busca
    pedidos.clear()
    with httpx.Client(transport=httpx.MockTransport(resposta)) as http:
        r = google_flights.coletar(db, hoje=date(2026, 10, 1), cliente_http=http)
    assert pedidos == ["google_travel_explore"] and r["confirmacoes"] == 0


def test_google_flights_preco_tipico_nao_entra_e_trecho_fora_do_explorar_tem_busca_direta(db, monkeypatch):
    monkeypatch.setattr(settings, "serpapi_key", "teste")
    monkeypatch.setattr(settings, "intervalo_google_horas", 24)
    _cliente(db, origem="FOR", destino="LIS", preco_maximo=3000)

    def resposta(request: httpx.Request):
        if request.url.params["engine"] == "google_travel_explore":
            return httpx.Response(200, json={"destinations": []})   # Lisboa não apareceu no explorar
        return httpx.Response(200, json=_resposta_voo(1900, "typical"))

    with httpx.Client(transport=httpx.MockTransport(resposta)) as http:
        r = google_flights.coletar(db, hoje=date(2026, 10, 1), cliente_http=http)
    assert r["exploradas"] == 1 and r["diretas"] == 1 and r["acima_do_preco"] == 1
    assert db.query(Promocao).count() == 0


def test_google_flights_respeita_o_teto_do_mes(db, monkeypatch):
    """Teto de 30 por mês em 30 dias: 1 busca por dia; acabou a cota do dia, a rodada não gasta nada."""
    from app.servicos import gravar_estado
    monkeypatch.setattr(settings, "serpapi_key", "teste")
    monkeypatch.setattr(settings, "intervalo_google_horas", 6)
    _cliente(db, origem="FOR", destino="LIS", preco_maximo=3000)
    gravar_estado(db, "google_flights:limite_mes", "30")
    chamadas = []

    def resposta(request: httpx.Request):
        chamadas.append(request.url.params["engine"])
        return httpx.Response(200, json={"destinations": []})

    with httpx.Client(transport=httpx.MockTransport(resposta)) as http:
        primeira = google_flights.coletar(db, hoje=date(2026, 11, 1), cliente_http=http)
        segunda = google_flights.coletar(db, hoje=date(2026, 11, 1), cliente_http=http)
    assert primeira["buscas"] == 1 and segunda["buscas"] == 0 and segunda.get("sem_cota")
    assert len(chamadas) == 1
    o = google_flights.orcamento(db, date(2026, 11, 1))
    assert (o["limite"], o["usadas_mes"], o["cota_hoje"], o["restantes_mes"]) == (30, 1, 1, 29)

    gravar_estado(db, "google_flights:limite_mes", "0")   # zero desliga as buscas
    with httpx.Client(transport=httpx.MockTransport(resposta)) as http:
        assert google_flights.coletar(db, hoje=date(2026, 11, 2), cliente_http=http)["buscas"] == 0


@pytest.fixture()
def site(db):
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "admin", "senha": "admin123"})
        yield cliente


def test_login_obrigatorio(db):
    with TestClient(app) as anonimo:
        r = anonimo.get("/clientes", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"


def test_fluxo_completo_pelo_site(site, db):
    r = site.post("/clientes/novo", data={"nome": "João Pereira", "telefone": "(88) 99712-3344",
                                          "consentimento": "true", "origem": "Fortaleza", "destino": "Orlando",
                                          "preco_maximo": "4.500"})
    assert r.status_code == 200 and "João Pereira" in r.text

    r = site.post("/promocoes/analisar", data={"texto": "Voos para Orlando saindo de Fortaleza por 3.950 reais"})
    assert 'value="FOR"' in r.text and 'value="MCO"' in r.text

    site.post("/promocoes/nova", data={"origem": "FOR", "destino": "MCO", "preco": "3950"})
    fila = site.get("/")
    assert "João Pereira" in fila.text and "wa.me/5588997123344" in fila.text

    m = db.query(Match).one()
    assert site.post(f"/matches/{m.id}/enviar", headers={"X-Requested-With": "fetch"}).json() == {"ok": True, "envios": 1}
    db.expire_all()
    assert db.get(Match, m.id).status == "enviada"
    assert "João Pereira" in site.get("/historico").text
    for pagina in ("/interesses", "/promocoes", "/clientes", "/configuracoes"):
        assert site.get(pagina).status_code == 200


def test_api_de_entrada(site):
    sem_chave = site.post("/api/promocoes", json={"texto": "FOR → LIS R$ 2.389"})
    assert sem_chave.status_code == 401
    ok = site.post("/api/promocoes", json={"texto": "FOR → LIS R$ 2.389"}, headers={"X-API-Key": "chave-teste"})
    assert ok.json()["resultado"] == "criada"


def test_nova_promocao_em_blocos(site, db):
    tela = site.get("/promocoes/nova")
    for bloco in ("1. Trecho", "2. Valor", "3. Datas", "Voo", "5. Origem da promoção", "PRÉVIA", "1. Oferta"):
        assert bloco in tela.text

    # sem valor: recusa e mantém o que foi digitado
    r = site.post("/promocoes/nova", data={"origem": "Fortaleza", "destino": "Lisboa", "companhia": "TAP",
                                           "fonte_detalhe": "Grupo X"})
    assert "Informe o valor" in r.text
    assert 'value="FOR"' in r.text and 'value="LIS"' in r.text and 'value="TAP"' in r.text and 'value="Grupo X"' in r.text

    r = site.post("/promocoes/nova", data={"origem": "GRU", "destino": "CGH", "preco": "300"})
    assert "mesma cidade" in r.text
    r = site.post("/promocoes/nova", data={"origem": "FOR", "destino": "LIS", "preco": "2.389",
                                           "inicio": "2027-05-10", "fim": "2027-05-01"})
    assert "volta está antes" in r.text

    # cadastro direto, sem colar texto, em milhas
    site.post("/promocoes/nova", data={"origem": "Natal", "destino": "Recife", "milhas": "24.000",
                                       "programa": "Azul Fidelidade", "companhia": "Azul",
                                       "inicio": "2027-03-21", "fim": "2027-04-10"})
    p = db.query(Promocao).one()
    assert (p.origem, p.destino, p.milhas, p.programa, p.preco) == ("NAT", "REC", 24000, "Azul Fidelidade", None)



def test_reenvio_pela_fila_e_pelo_cliente(site, db):
    c = Cliente(nome="Eduardo Lima", telefone="5588998613236", consentimento=True)
    c.interesses.append(Interesse(origem="FOR", destino="RIO"))
    db.add(c)
    db.commit()
    registrar_promocao(db, texto="FOR → RIO R$ 927 ida e volta em dezembro", fonte="manual")
    m = db.query(Match).one()

    assert "Enviar no WhatsApp" in site.get("/").text
    r = site.post(f"/matches/{m.id}/enviar", headers={"X-Requested-With": "fetch"}).json()
    assert r["envios"] == 1

    # some de "Para enviar", mas aparece em "Já enviadas" com o botão de reenviar
    assert "Eduardo Lima" not in site.get("/").text
    enviadas = site.get("/?ver=enviadas").text
    assert "Eduardo Lima" in enviadas and "Enviar de novo" in enviadas and "Já enviadas (1)" in enviadas
    assert site.post(f"/matches/{m.id}/enviar", headers={"X-Requested-With": "fetch"}).json()["envios"] == 2

    # no cadastro do cliente também dá para enviar, inclusive promoção pulada
    tela = site.get(f"/clientes/{c.id}").text
    assert "enviada 2x" in tela and "Enviar de novo" in tela and "wa.me/5588998613236" in tela
    site.post(f"/matches/{m.id}/descartar")
    assert "pulada" in site.get(f"/clientes/{c.id}").text
    assert site.post(f"/matches/{m.id}/enviar", headers={"X-Requested-With": "fetch"}).json()["envios"] == 3
    db.refresh(m)
    assert m.status == "enviada"


def test_ida_e_volta_na_mensagem(db):
    from app.servicos import TEMPLATE_PADRAO

    c = Cliente(nome="Eduardo Lima", telefone="5588998613236")
    ida_volta = registrar_promocao(db, texto="FOR → RIO R$ 927 ida e volta", fonte="manual").promocao
    so_ida = registrar_promocao(db, texto="FOR → REC por 6.500 milhas o trecho", fonte="manual").promocao
    sem_info = registrar_promocao(db, texto="FOR → LIS R$ 2.389", fonte="manual").promocao
    assert (ida_volta.tipo_viagem, so_ida.tipo_viagem, sem_info.tipo_viagem) == ("ida_volta", "ida", None)

    assert "*Fortaleza → Rio de Janeiro*\n🔁 Ida e volta\n💰 *R$ 927*" in montar_mensagem(TEMPLATE_PADRAO, c, ida_volta)
    assert "➡️ Somente ida" in montar_mensagem(TEMPLATE_PADRAO, c, so_ida)
    msg = montar_mensagem(TEMPLATE_PADRAO, c, sem_info)
    assert "Ida e volta" not in msg and "Somente ida" not in msg and "\n\n💰" not in msg


def test_tipo_de_viagem_no_cadastro_manual(site, db):
    tela = site.post("/promocoes/analisar", data={"texto": "Natal x Recife 24.000 milhas trecho ida e volta Azul Fidelidade"}).text
    assert 'value="ida_volta" checked' in tela
    site.post("/promocoes/nova", data={"origem": "FOR", "destino": "GRU", "preco": "389", "tipo_viagem": "ida"})
    assert db.query(Promocao).one().tipo_viagem == "ida"


def test_monitor_de_disponibilidade_usa_head(db):
    """O UptimeRobot testa com HEAD; antes o sistema respondia 405 e parecia fora do ar."""
    with TestClient(app) as cliente:
        assert cliente.head("/saude").status_code == 200
        assert cliente.head("/saude").content == b""
        r = cliente.head("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert cliente.head("/login").status_code == 200


def test_configuracoes_mostra_e_salva_o_limite_do_google(db, site, monkeypatch):
    monkeypatch.setattr(settings, "serpapi_key", "teste")
    tela = site.get("/configuracoes").text
    assert 'id="google-flights"' in tela and "buscas usadas no mês" in tela
    site.post("/configuracoes/google-flights", data={"limite_mes": "120"})
    assert google_flights.limite_mes(db) == 120
    assert 'value="120"' in site.get("/configuracoes").text
