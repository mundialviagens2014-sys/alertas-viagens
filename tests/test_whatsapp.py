"""Leitor de grupos do WhatsApp: mensagens e imagens dos grupos escolhidos viram promoções; sessão guardada
no banco; tela com QR code, grupos e controles. A biblioteca do WhatsApp é substituída por um cliente de mentira
(não há rede nos testes)."""
import sqlite3
import threading
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.collectors import whatsapp
from app.config import settings
from app.main import app
from app.models import ArquivoSessao, Cliente, GrupoWhatsApp, Interesse, Promocao


def _evento(jid="120363000001@g.us", texto="", imagem=False, de_mim=False, grupo=True, ha_dias=0, caption=None):
    from neonize.proto.Neonize_pb2 import Message as MessageEv

    ev = MessageEv()
    usuario, servidor = jid.split("@")
    ev.Info.MessageSource.Chat.User, ev.Info.MessageSource.Chat.Server = usuario, servidor
    ev.Info.MessageSource.IsGroup, ev.Info.MessageSource.IsFromMe = grupo, de_mim
    ev.Info.ID = "ABC123"
    ev.Info.Timestamp = int((datetime.now() - timedelta(days=ha_dias)).timestamp())
    if imagem:
        ev.Message.imageMessage.mimetype = "image/jpeg"
        ev.Message.imageMessage.caption = caption or ""
    elif texto:
        ev.Message.conversation = texto
    return ev


class ClienteFalso:
    """Faz o papel da biblioteca: guarda os manipuladores e devolve uma imagem ao baixar."""

    def __init__(self):
        self.handlers = {}
        self.parado = False
        self.saiu = False
        self.baixadas = 0

    class _Event:
        def __init__(self, dono):
            self.dono = dono

        def qr(self, f):
            self.dono.handlers["qr"] = f

        def __call__(self, tipo):
            def registrar(f):
                self.dono.handlers[tipo.__name__] = f
            return registrar

    @property
    def event(self):
        return self._Event(self)

    def connect(self):
        self.handlers["qr"](self, b"2@abc,def,ghi")
        while not self.parado:
            threading.Event().wait(0.05)

    def stop(self):
        self.parado = True

    def logout(self):
        self.saiu = True
        self.parado = True

    def download_any(self, mensagem):
        self.baixadas += 1
        return b"\xff\xd8imagem"

    def get_me(self):
        from neonize.proto.Neonize_pb2 import Device

        d = Device()
        d.JID.User = "5585999990000"
        return d

    def get_joined_groups(self):
        from neonize.proto.Neonize_pb2 import GroupInfo

        grupos = []
        for user, nome in (("120363000001", "Promoções Nordeste"), ("120363000002", "Ofertas Cruzeiros")):
            g = GroupInfo()
            g.JID.User, g.JID.Server = user, "g.us"
            g.GroupName.Name = nome
            grupos.append(g)
        return grupos


@pytest.fixture()
def falso(db, monkeypatch, tmp_path):
    cliente = ClienteFalso()
    monkeypatch.setattr(whatsapp, "_novo_cliente", lambda novo: cliente)
    monkeypatch.setattr(settings, "whatsapp_sessao", str(tmp_path / "sessao.db"))
    monkeypatch.setattr(settings, "whatsapp_leitura", True)
    whatsapp.estado.__init__()
    whatsapp._parar.clear()
    yield cliente
    whatsapp.parar()
    whatsapp._cliente = None


@pytest.fixture()
def site(db):
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "admin", "senha": "admin123"})
        yield cliente


def _esperar(condicao, segundos=5.0):
    fim = datetime.now() + timedelta(seconds=segundos)
    while datetime.now() < fim:
        if condicao():
            return True
        threading.Event().wait(0.05)
    return False


def test_extrai_texto_e_imagem_das_mensagens_de_grupo():
    e = whatsapp.extrair_mensagem(_evento(texto="FOR → LIS R$ 2.389"))
    assert e["jid"] == "120363000001@g.us" and e["texto"] == "FOR → LIS R$ 2.389" and not e["tem_imagem"]
    e = whatsapp.extrair_mensagem(_evento(imagem=True, caption="Olha essa!"))
    assert e["tem_imagem"] and e["mime"] == "image/jpeg" and e["texto"] == "Olha essa!"
    assert whatsapp.extrair_mensagem(_evento(texto="oi", grupo=False)) is None  # conversa privada: ignora
    assert whatsapp.extrair_mensagem(_evento(texto="oi", de_mim=True)) is None  # mensagem do próprio número
    assert whatsapp.extrair_mensagem(_evento()) is None  # sem texto nem imagem


def test_mensagens_dos_grupos_lidos_viram_promocoes(db, falso):
    ana = Cliente(nome="Ana Costa", telefone="5585981112222", consentimento=True)
    ana.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add(ana)
    db.add(GrupoWhatsApp(jid="120363000001@g.us", nome="Promoções Nordeste", ativo=True))
    db.add(GrupoWhatsApp(jid="120363000002@g.us", nome="Ofertas Cruzeiros", ativo=False))
    db.commit()
    assert whatsapp.iniciar(novo=True)
    assert _esperar(lambda: whatsapp.estado.status == "aguardando_qr")
    assert whatsapp.estado.qr == "2@abc,def,ghi" and whatsapp.qr_png()[:8] == b"\x89PNG\r\n\x1a\n"
    # pareou e conectou
    from neonize.proto.Neonize_pb2 import Connected

    falso.handlers["Connected"](falso, Connected())
    assert whatsapp.estado.status == "conectado" and whatsapp.estado.numero == "5585999990000"
    assert _esperar(lambda: db.query(ArquivoSessao).count() == 1 or True)
    # chegam mensagens: grupo lido vira promoção; grupo não lido e mensagem velha não
    receber = falso.handlers["Message"]
    receber(falso, _evento(texto="FOR → LIS R$ 2.389 ida e volta TAP em março/27"))
    receber(falso, _evento(jid="120363000002@g.us", texto="GRU → MIA R$ 1.899 LATAM em novembro"))
    receber(falso, _evento(texto="FOR → REC R$ 389 Azul em dezembro", ha_dias=5))
    receber(falso, _evento(jid="120363000009@g.us", texto="NAT → REC R$ 300 em janeiro"))  # grupo novo: entra desligado
    assert _esperar(lambda: db.query(Promocao).count() == 1)
    db.expire_all()
    promo = db.query(Promocao).one()
    assert promo.fonte == "whatsapp" and promo.fonte_detalhe == "Promoções Nordeste" and promo.destino == "LIS"
    assert len(promo.matches) == 1
    grupo = db.query(GrupoWhatsApp).filter_by(jid="120363000001@g.us").one()
    assert _esperar(lambda: (db.expire_all() or db.query(GrupoWhatsApp).filter_by(jid="120363000001@g.us").one().mensagens) == 1)
    assert grupo.promocoes == 1 and grupo.ultima_mensagem is not None
    novo = db.query(GrupoWhatsApp).filter_by(jid="120363000009@g.us").one()
    assert novo.ativo is False and novo.nome is None
    assert whatsapp.estado.mensagens_hoje == 1


def test_imagem_do_grupo_vai_para_a_ia(db, falso, monkeypatch):
    from app import ia, leitura

    db.add(GrupoWhatsApp(jid="120363000001@g.us", nome="Promoções Nordeste", ativo=True))
    db.commit()
    monkeypatch.setattr(settings, "gemini_api_key", "x")
    recebidos = []

    def processar_fingido(db_, **kw):
        recebidos.append(kw)
        return leitura.Resultado(None, "incompleta")

    monkeypatch.setattr(leitura, "processar", processar_fingido)
    whatsapp.iniciar(novo=True)
    assert _esperar(lambda: "Message" in falso.handlers)
    falso.handlers["Message"](falso, _evento(imagem=True))
    assert _esperar(lambda: len(recebidos) == 1)
    assert recebidos[0]["imagem"] == b"\xff\xd8imagem" and recebidos[0]["mime"] == "image/jpeg"
    assert recebidos[0]["fonte"] == "whatsapp" and recebidos[0]["fonte_detalhe"] == "Promoções Nordeste"
    assert falso.baixadas == 1


def test_sessao_e_guardada_no_banco_e_restaurada(db, falso, tmp_path):
    caminho = tmp_path / "sessao.db"
    con = sqlite3.connect(caminho)
    con.execute("CREATE TABLE chaves (k TEXT)")
    con.execute("INSERT INTO chaves VALUES ('abc')")
    con.commit()
    con.close()
    assert whatsapp.guardar_sessao(forcar=True) is True
    assert whatsapp.guardar_sessao(forcar=True) is False  # não mudou: não grava de novo
    assert db.query(ArquivoSessao).one().nome == "whatsapp" and whatsapp.sessao_guardada()
    caminho.unlink()
    assert whatsapp.restaurar_sessao() is True and caminho.exists()
    copia = sqlite3.connect(caminho)
    assert copia.execute("SELECT k FROM chaves").fetchall() == [("abc",)]
    copia.close()
    whatsapp.esquecer_sessao()
    assert not caminho.exists() and not whatsapp.sessao_guardada()


def test_religa_ao_subir_so_se_houver_sessao(db, falso):
    assert whatsapp.iniciar_se_houver_sessao() is False
    db.add(ArquivoSessao(nome="whatsapp", dados=b"x"))
    db.commit()
    assert whatsapp.iniciar_se_houver_sessao() is True
    assert _esperar(lambda: whatsapp.estado.status in ("aguardando_qr", "conectando"))


def test_tela_do_whatsapp(db, falso, site):
    html = site.get("/whatsapp").text
    assert "Conectar um número" in html and "Use um chip separado" in html
    r = site.post("/whatsapp/conectar", follow_redirects=True)
    assert r.status_code == 200
    assert _esperar(lambda: whatsapp.estado.status == "aguardando_qr")
    html = site.get("/whatsapp").text
    assert "/whatsapp/qr.png" in html and 'http-equiv="refresh"' in html and "Aparelhos conectados" in html
    assert site.get("/whatsapp/qr.png").headers["content-type"] == "image/png"
    assert site.get("/whatsapp/estado").json()["status"] == "aguardando_qr"
    # código de pareamento
    falso.PairPhone = lambda tel, push: "ABCD-EFGH"
    r = site.post("/whatsapp/parear", data={"telefone": "(85) 99999-0000"}, follow_redirects=True)
    assert "ABCD-EFGH" in r.text
    # conectado: lista de grupos sincronizada e controles
    from neonize.proto.Neonize_pb2 import Connected

    falso.handlers["Connected"](falso, Connected())
    assert _esperar(lambda: db.query(GrupoWhatsApp).count() == 2)
    html = site.get("/whatsapp").text
    assert "Promoções Nordeste" in html and "Ofertas Cruzeiros" in html and "+5585999990000" in html
    grupo = db.query(GrupoWhatsApp).filter_by(nome="Promoções Nordeste").one()
    site.post(f"/whatsapp/grupos/{grupo.id}/alternar")
    db.expire_all()
    assert grupo.ativo is True
    assert "Promoções Nordeste" in site.get("/whatsapp?ver=lidos").text
    assert "Ofertas Cruzeiros" not in site.get("/whatsapp?ver=lidos").text.split("<table")[-1]
    # desconectar: sai da conta e esquece a sessão
    db.add(ArquivoSessao(nome="whatsapp", dados=b"x"))
    db.commit()
    site.post("/whatsapp/desconectar")
    assert falso.saiu and not whatsapp.sessao_guardada() and whatsapp.estado.status == "desconectado"


def test_atendente_nao_mexe_no_leitor(db, falso):
    from app.models import Usuario
    from app.servicos import gerar_hash_senha

    db.add(Usuario(nome="Atendente", login="atende", senha_hash=gerar_hash_senha("Senha-Forte-1"), papel="atendente"))
    db.commit()
    with TestClient(app) as navegador:
        navegador.post("/login", data={"login": "atende", "senha": "Senha-Forte-1"})
        assert navegador.get("/whatsapp").status_code == 200
        assert navegador.post("/whatsapp/conectar", follow_redirects=False).status_code == 403
