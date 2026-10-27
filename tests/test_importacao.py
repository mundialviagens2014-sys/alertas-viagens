import io
from datetime import date, datetime

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.importacao import aplicar, interpretar, ler_planilha, mapear_colunas
from app.main import app
from app.models import Cliente, Interesse
from app.servicos import registrar_promocao

# Cabeçalhos como o Google Forms gera (texto das perguntas)
FORMS = [
    "Carimbo de data/hora", "Nome completo", "WhatsApp com DDD", "Endereço de e-mail",
    "Em qual cidade você está?", "Para onde você quer viajar?", "Quando pretende viajar?",
    "Até quanto quer pagar?", "Você autoriza receber promoções pelo WhatsApp?",
]


def _xlsx(linhas) -> bytes:
    wb = Workbook()
    for l in linhas:
        wb.active.append(l)
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


def test_reconhece_colunas_do_google_forms():
    mapa = mapear_colunas(FORMS)
    assert mapa == {"data_resposta": 0, "nome": 1, "telefone": 2, "email": 3, "origem": 4,
                    "destino": 5, "periodo": 6, "preco_maximo": 7, "consentimento": 8}


def test_le_formulario_xlsx_e_interpreta(db):
    conteudo = _xlsx([
        FORMS,
        [datetime(2026, 10, 19, 9), "Maria Oliveira", 85998761234, "m@x.com", "Fortaleza", "Lisboa, Paris",
         "março a junho de 2027", "R$ 3.500", "Sim, autorizo"],
        [datetime(2026, 10, 19, 10), "João", "(88) 99712-3344", "", "", "qualquer lugar", "julho", 4500, "Não"],
        [datetime(2026, 10, 19, 11), "Sem telefone", "", "", "FOR", "Lisboa", "", "", "Sim"],
        [datetime(2026, 10, 19, 12), "Maria O.", "(85) 99876-1234", "", "Fortaleza", "Roma", "", "", "Sim"],
    ])
    cab, linhas = ler_planilha(conteudo, "respostas.xlsx")
    _, itens = interpretar(db, cab, linhas, origem_padrao="FOR", consentimento_padrao=False)

    maria, joao, erro = itens
    assert maria.telefone == "5585998761234" and maria.consentimento
    assert [(i["origem"], i["destino"]) for i in maria.interesses] == [("FOR", "LIS"), ("FOR", "PAR"), ("FOR", "ROM")]
    assert maria.interesses[0]["periodo_inicio"] == date(2027, 3, 1)
    assert maria.interesses[0]["preco_maximo"] == 3500
    assert any("linha 5" in a for a in maria.avisos)  # mesma pessoa repetida: trechos somados

    assert joao.interesses[0]["origem"] == "FOR"  # origem padrão
    assert joao.interesses[0]["destino"] is None  # qualquer destino
    assert not joao.consentimento

    assert erro.situacao == "erro" and "sem telefone" in erro.erros


def test_csv_ponto_e_virgula_em_latin1(db):
    texto = "Nome;Celular;Origem;Destino\nJosé Araújo;85 99999-0000;Recife;Orlando\n"
    cab, linhas = ler_planilha(texto.encode("cp1252"), "clientes.csv")
    _, itens = interpretar(db, cab, linhas, None, True)
    assert itens[0].nome == "José Araújo"
    assert itens[0].interesses[0]["origem"] == "REC" and itens[0].interesses[0]["destino"] == "MCO"
    assert itens[0].consentimento  # sem coluna de autorização: vale a opção marcada


def test_aplicar_cria_atualiza_e_encontra_promocoes(db):
    existente = Cliente(nome="Ana", telefone="5585911112222", consentimento=False)
    existente.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add(existente)
    db.commit()
    registrar_promocao(db, texto="FOR → MCO R$ 3.900", fonte="manual")

    conteudo = _xlsx([
        ["Nome", "WhatsApp", "Origem", "Destino", "Autoriza?"],
        ["Ana Costa", "85 91111-2222", "FOR", "Lisboa, Orlando", "sim"],
        ["Pedro", "85 93333-4444", "FOR", "Orlando", "sim"],
    ])
    cab, linhas = ler_planilha(conteudo, "x.xlsx")
    _, itens = interpretar(db, cab, linhas, None, False)
    assert [i.situacao for i in itens] == ["atualizar", "novo"]

    r = aplicar(db, itens)
    assert r == {"novos": 1, "atualizados": 1, "interesses": 2, "erros": 0, "matches": 2, "confirmacoes": 0}
    db.refresh(existente)
    assert existente.consentimento and len(existente.interesses) == 2  # Lisboa não duplicou


def test_importacao_pelo_site(db):
    with TestClient(app) as site:
        site.post("/login", data={"login": "admin", "senha": "admin123"})
        modelo = site.get("/clientes/importar/modelo.xlsx")
        assert modelo.status_code == 200

        r = site.post("/clientes/importar", files={"arquivo": ("modelo.xlsx", modelo.content)},
                      data={"origem_padrao": "FOR"})
        assert r.status_code == 200 and "Confirmar importação de 3 clientes" in r.text
        token = r.text.split('name="token" value="')[1].split('"')[0]

        r = site.post("/clientes/importar/confirmar", data={"token": token, "origem_padrao": "FOR"})
        assert "3 clientes novos" in r.text
        assert db.query(Cliente).count() == 3

        ruim = site.post("/clientes/importar", files={"arquivo": ("x.xlsx", _xlsx([["Coluna A", "Coluna B"], [1, 2]]))})
        assert "Não encontrei as colunas de nome e telefone" in ruim.text


def test_celula_de_preco_com_data_nao_vira_preco(db):
    from app.importacao import _preco
    from app.models import Interesse
    from app.servicos import preparar_banco

    assert _preco(datetime(2026, 10, 20)) is None
    assert _preco("20/10/2026") is None
    assert _preco("R$ 3.500") == 3500 and _preco("3 mil") == 3000 and _preco(4500) == 4500
    assert _preco(20261020000000) is None

    conteudo = _xlsx([["Nome", "WhatsApp", "Origem", "Destino", "Até quanto quer pagar?"],
                      ["Eduardo", "88 99861-3236", "FOR", "SAO", datetime(2026, 10, 20)]])
    cab, linhas = ler_planilha(conteudo, "x.xlsx")
    _, itens = interpretar(db, cab, linhas, None, True)
    assert itens[0].interesses[0]["preco_maximo"] is None
    assert any("não reconhecido" in a for a in itens[0].avisos)

    # cliente importado antes da correção: o limite absurdo é removido ao iniciar
    c = Cliente(nome="Eduardo", telefone="5588998613236")
    c.interesses.append(Interesse(origem="FOR", destino="SAO", preco_maximo=20261020000000))
    db.add(c)
    db.commit()
    preparar_banco(db)
    db.refresh(c.interesses[0])
    assert c.interesses[0].preco_maximo is None


FORMS_NOVO = [
    "Carimbo de data/hora", "Nome completo", "WhatsApp com DDD", "De onde você vai sair?",
    "Para onde você quer viajar?  ( 1 ou mais destinos)", "Já possui uma Data em mente?",
    "Você autoriza receber promoções pelo WhatsApp?", "Observações",
    "Insira a data de IDA", "Insira a data de VOLTA",
]


def test_formulario_novo_com_ida_e_volta(db):
    mapa = mapear_colunas(FORMS_NOVO)
    assert mapa["inicio"] == 8 and mapa["fim"] == 9
    assert "periodo" not in mapa and "preco_maximo" not in mapa  # “Já possui uma data em mente?” é ignorada

    agora = datetime(2026, 10, 20, 14, 0)
    conteudo = _xlsx([
        FORMS_NOVO,
        [agora, "Maria", "85 99876-1234", "Fortaleza", "Lisboa", "Sim", "Sim", "", "março", "abril"],
        [agora, "João", "88 99712-3344", "Fortaleza", "Orlando", "Sim", "Sim", "", "15/03/2027", "28/03/2027"],
        [agora, "Ana", "85 98111-2222", "Fortaleza", "Recife", "Sim", "Sim", "", "dezembro", ""],
        [agora, "Pedro", "85 93333-4444", "Fortaleza", "Natal", "Sim", "Sim", "", datetime(2027, 1, 10), ""],
        [agora, "Bia", "85 97777-3333", "Fortaleza", "Paris", "Não", "Sim", "", "", ""],
        [agora, "Caio", "85 96666-5555", "Fortaleza", "Roma", "Sim", "Sim", "", "assim que der", ""],
    ])
    cab, linhas = ler_planilha(conteudo, "respostas.xlsx")
    _, itens = interpretar(db, cab, linhas, None, False)
    periodo = {i.nome: (i.interesses[0]["periodo_inicio"], i.interesses[0]["periodo_fim"]) for i in itens}

    assert periodo["Maria"] == (date(2027, 3, 1), date(2027, 4, 30))  # mês da ida ao fim do mês da volta
    assert periodo["João"] == (date(2027, 3, 15), date(2027, 3, 28))
    assert periodo["Ana"] == (date(2026, 12, 1), date(2026, 12, 31))  # só a ida, só o mês
    assert periodo["Pedro"] == (date(2027, 1, 10), date(2027, 1, 10))
    assert periodo["Bia"] == (None, None)  # sem data em mente: qualquer data
    assert periodo["Caio"] == (None, None)
    assert any("não reconhecida" in a for a in itens[-1].avisos)
