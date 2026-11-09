import httpx
import pytest
from fastapi.testclient import TestClient

from app import formulario
from app.main import app
from app.models import Cliente, Interesse
from app.servicos import gravar_estado, registrar_promocao

URL = "https://script.google.com/macros/s/AKfycbExemplo123/exec"
CAB = ('"Carimbo de data/hora","Nome completo","WhatsApp com DDD","De onde você vai sair?",'
       '"Para onde você quer viajar?  ( 1 ou mais destinos)","Já possui uma Data em mente?",'
       '"Você autoriza receber promoções pelo WhatsApp?","Observações","Insira a data de IDA","Insira a data de VOLTA"')
MARIA = '"20/10/2026 09:12:40","MARIA DA SILVA OLIVEIRA","(85) 99876-1234","Fortaleza","Lisboa, Paris","Sim","Sim","","março","abril"'
JOAO = '"20/10/2026 10:05:02","João Pereira","88997123344","Fortaleza","Orlando","Não","Sim","","",""'
ANA = '"20/10/2026 15:30:00","Ana Costa","85 98111-2222","Natal","Recife","Sim","Sim","","15/03/2027",""'


def _google(linhas: list[str], chave_certa: str | None = None):
    """Simula o Apps Script: /exec redireciona para googleusercontent e devolve o CSV."""
    def responder(req: httpx.Request):
        if req.url.host == "script.google.com":
            if chave_certa and req.url.params.get("chave") != chave_certa:
                return httpx.Response(302, headers={"location": "https://script.googleusercontent.com/negado"})
            return httpx.Response(302, headers={"location": "https://script.googleusercontent.com/echo?x=1"})
        if req.url.path == "/negado":
            return httpx.Response(200, text="acesso negado")
        return httpx.Response(200, text="\n".join(linhas), headers={"content-type": "text/csv"})
    return httpx.Client(transport=httpx.MockTransport(responder), follow_redirects=True)


def test_endereco_e_validacao():
    assert formulario.endereco_download(URL, "abc") == URL + "?chave=abc"
    assert formulario.endereco_download(
        "https://docs.google.com/spreadsheets/d/1AbC_x-9/edit#gid=12345", "abc"
    ) == "https://docs.google.com/spreadsheets/d/1AbC_x-9/export?format=csv&gid=12345"
    assert formulario.validar_url(URL) is None
    assert "termina em /dev" in formulario.validar_url("https://script.google.com/macros/s/AKfy/dev")
    assert "link do formulário" in formulario.validar_url("https://docs.google.com/forms/d/e/1FAIp/viewform")


def test_importa_so_respostas_novas(db):
    chave = formulario.chave_secreta(db)
    formulario.configurar(db, URL)

    with _google([CAB, MARIA, JOAO], chave) as http:
        r = formulario.sincronizar(db, http)
    assert (r["novas"], r["novos"]) == (2, 2)
    maria = db.query(Cliente).filter_by(telefone="5585998761234").one()
    assert maria.nome == "Maria da Silva Oliveira" and maria.consentimento
    assert {(i.destino, str(i.periodo_inicio), str(i.periodo_fim)) for i in maria.interesses} == {
        ("LIS", "2027-03-01", "2027-04-30"), ("PAR", "2027-03-01", "2027-04-30")}

    # a equipe pausa um trecho da Maria; uma nova leitura não pode desfazer isso
    maria.interesses[0].ativo = False
    db.commit()
    with _google([CAB, MARIA, JOAO], chave) as http:
        r = formulario.sincronizar(db, http)
    assert r["novas"] == 0
    db.refresh(maria)
    assert maria.interesses[0].ativo is False

    # chega uma resposta nova, com promoção já existente para o trecho
    registrar_promocao(db, texto="NAT → REC R$ 389 em março/27", fonte="manual")
    with _google([CAB, MARIA, JOAO, ANA], chave) as http:
        r = formulario.sincronizar(db, http)
    assert (r["novas"], r["novos"], r["matches"]) == (1, 1, 1)
    assert db.query(Cliente).count() == 3


def test_erros_explicados(db):
    chave = formulario.chave_secreta(db)
    formulario.configurar(db, URL)
    with _google([CAB, MARIA], "outra-chave") as http, pytest.raises(RuntimeError, match="recusou a chave"):
        formulario.sincronizar(db, http)

    login = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text="<html><body>Fazer login</body></html>")))
    with pytest.raises(RuntimeError, match="Qualquer pessoa"):
        formulario.sincronizar(db, login)

    gravar_estado(db, formulario.CHAVE_URL, "")
    with pytest.raises(formulario.FormularioNaoConfigurado):
        formulario.sincronizar(db, _google([CAB], chave))


def test_trocar_de_planilha_relê_tudo(db):
    chave = formulario.chave_secreta(db)
    formulario.configurar(db, URL)
    with _google([CAB, MARIA], chave) as http:
        formulario.sincronizar(db, http)
    formulario.configurar(db, "https://script.google.com/macros/s/OutroScript/exec")
    with _google([CAB, MARIA, JOAO], chave) as http:
        r = formulario.sincronizar(db, http)
    assert r["novas"] == 2 and r["novos"] == 1 and r["atualizados"] == 1


def test_tela_do_formulario(db, monkeypatch):
    with TestClient(app) as site:
        site.post("/login", data={"login": "admin", "senha": "admin123"})
        tela = site.get("/clientes/formulario")
        chave = formulario.chave_secreta(db)
        assert "Como conectar" in tela.text and chave in tela.text and "doGet" in tela.text

        assert "link do formulário" in site.post(
            "/clientes/formulario", data={"url": "https://docs.google.com/forms/d/e/x/viewform"}).text

        original = formulario.baixar
        monkeypatch.setattr(formulario, "baixar", lambda url, ch, http=None: original(url, ch, _google([CAB, MARIA, JOAO], chave)))
        r = site.post("/clientes/formulario", data={"url": URL})
        assert "2 resposta(s) nova(s)" in r.text and "Formulários ligados (1)" in r.text
        assert "Nenhuma resposta nova" in site.post("/clientes/formulario/sincronizar").text


def test_planilha_da_mundial(db):
    """Cabeçalhos reais da planilha da agência (aba Form_Responses), com uma resposta antiga
    em que as datas caíram nas colunas de “data em mente” e de preço."""
    cab = ('"Carimbo de data/hora","Como podemos te chamar?","WhatsApp","De onde você vai sair?",'
           '"Para onde você quer viajar?  ( 1 ou mais destinos)","Já possui uma Data em mente?",'
           '"Até quanto quer pagar?","Você autoriza receber promoções pelo WhatsApp?","Observações",'
           '"Insira a data de IDA","Insira a data de VOLTA"')
    resposta = ('"19/10/2026 15:13:27","Eduardo Soares Albuquerque","88 99861-3236","Fortaleza","São Paulo",'
                '"19/10/2026","20/10/2026","Sim","N/S","",""')
    chave = formulario.chave_secreta(db)
    formulario.configurar(db, URL)
    with _google([cab, resposta], chave) as http:
        r = formulario.sincronizar(db, http)
    assert (r["novas"], r["novos"]) == (1, 1)
    c = db.query(Cliente).one()
    assert c.nome == "Eduardo Soares Albuquerque" and c.telefone == "5588998613236" and c.consentimento
    i = c.interesses[0]
    assert (i.origem, i.destino, i.preco_maximo) == ("FOR", "SAO", None)  # data na coluna de preço é ignorada


def test_erro_mostra_colunas_recebidas(db):
    chave = formulario.chave_secreta(db)
    formulario.configurar(db, URL)
    with _google(['"Carimbo de data/hora","Qual seu apelido?","Celular"', '"20/10/2026 10:00:00","Ana","85 99999-0000"'], chave) as http:
        with pytest.raises(RuntimeError, match="coluna de nome.*“Qual seu apelido\\?”"):
            formulario.sincronizar(db, http)


URL2 = "https://script.google.com/macros/s/OutroFormulario456/exec"
CAB2 = ('"Timestamp","Seu nome","Celular","Cidade de saída","Destino desejado",'
        '"Autoriza receber ofertas no WhatsApp?"')
PEDRO = '"21/10/2026 11:00:00","Pedro Lima","85 98222-3333","Fortaleza","Natal","Sim"'


def test_varios_formularios(db):
    """Dois formulários (colunas diferentes): cada um com o próprio controle de respostas lidas; erro em um não
    impede o outro; o mesmo telefone nos dois vira um cliente só."""
    chave = formulario.chave_secreta(db)
    formulario.configurar(db, URL, 1, "Cadastro do site")
    assert formulario.proximo_numero(db) == 2
    formulario.configurar(db, URL2, 2, "Instagram")
    assert [f["nome"] for f in formulario.formularios(db)] == ["Cadastro do site", "Instagram"]

    def planilhas(req: httpx.Request):
        if req.url.host == "script.google.com":
            destino = "um" if "AKfycbExemplo123" in str(req.url) else "dois"
            return httpx.Response(302, headers={"location": f"https://script.googleusercontent.com/{destino}"})
        if req.url.path == "/um":
            return httpx.Response(200, text="\n".join([CAB, MARIA, JOAO]))
        return httpx.Response(200, text="\n".join([CAB2, PEDRO, '"21/10/2026 12:00:00","Maria da Silva Oliveira","(85) 99876-1234","Fortaleza","Natal","Sim"']))

    with httpx.Client(transport=httpx.MockTransport(planilhas), follow_redirects=True) as http:
        r = formulario.sincronizar(db, http)
    assert r["novas"] == 4 and r["novos"] == 3 and len(r["formularios"]) == 2   # Maria respondeu os dois: 1 cadastro
    maria = db.query(Cliente).filter(Cliente.telefone.like("%998761234")).one()
    assert {i.destino for i in maria.interesses} >= {"LIS", "NAT"}
    assert formulario.ultimo_resumo(db, 2)["novas"] == 2

    # o segundo fora do ar: o primeiro continua sendo lido
    def so_um(req: httpx.Request):
        if "OutroFormulario456" in str(req.url):
            return httpx.Response(500)
        return planilhas(req)
    with httpx.Client(transport=httpx.MockTransport(so_um), follow_redirects=True) as http:
        r = formulario.sincronizar(db, http)
    assert r["novas"] == 0 and r["erros_formularios"] and "Instagram" in r["erros_formularios"][0]
    assert "erro" in formulario.ultimo_resumo(db, 2)

    formulario.configurar(db, "", 2)   # desconectar
    assert [f["n"] for f in formulario.formularios(db)] == [1] and formulario.proximo_numero(db) == 2
