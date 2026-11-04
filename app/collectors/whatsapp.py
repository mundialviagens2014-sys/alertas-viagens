"""Leitor de grupos do WhatsApp, dentro do próprio site (biblioteca neonize / whatsmeow).

Um número da agência usado SÓ para isso (nunca o número que fala com os clientes) entra nos grupos de promoção.
A equipe liga o número pela tela "Grupos do WhatsApp" (QR code ou código de pareamento, como o WhatsApp Web),
escolhe os grupos, e cada mensagem ou imagem que chega vai para a leitura de promoções (regras + IA).

Importante: isto usa o WhatsApp como o WhatsApp Web, sem ser a API oficial. A Meta pode bloquear o número a
qualquer momento; por isso ele deve ser um chip separado, que só lê e nunca envia nada.

A sessão (chaves do aparelho) fica num arquivo SQLite; como o disco do Render é apagado a cada publicação, o
arquivo é guardado no banco (ArquivoSessao) e restaurado quando o site inicia.
"""
import hashlib
import io
import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from .. import leitura
from ..config import settings
from ..database import SessionLocal
from ..models import ArquivoSessao, GrupoWhatsApp

log = logging.getLogger(__name__)
NOME_SESSAO = "whatsapp"
IDADE_MAXIMA_MENSAGEM = timedelta(days=2)  # mensagens antigas (sincronização ao religar) não viram promoção
INTERVALO_GUARDAR_SESSAO = 10 * 60
MIMES_IMAGEM = ("image/jpeg", "image/png", "image/webp")
TAMANHO_MAX_IMAGEM = 8 * 1024 * 1024


@dataclass
class Estado:
    """Situação do leitor, mostrada na tela. desligado, conectando, aguardando_qr, conectado, desconectado, erro."""

    status: str = "desligado"
    qr: str | None = None
    qr_em: datetime | None = None
    codigo_pareamento: str | None = None
    numero: str | None = None
    erro: str | None = None
    conectado_em: datetime | None = None
    mensagens_hoje: int = 0
    dia_contagem: str = ""
    aviso: str | None = None
    historico: list = field(default_factory=list)  # últimas 20 ocorrências (para a tela)

    def registrar(self, texto: str) -> None:
        self.historico = ([f"{datetime.now():%d/%m %H:%M} · {texto}"] + self.historico)[:20]
        log.info("WhatsApp: %s", texto)

    def contar_mensagem(self) -> None:
        hoje = datetime.now().strftime("%Y-%m-%d")
        if self.dia_contagem != hoje:
            self.dia_contagem, self.mensagens_hoje = hoje, 0
        self.mensagens_hoje += 1


estado = Estado()
_trava = threading.Lock()
_cliente = None
_fio: threading.Thread | None = None
_fila: "queue.Queue[dict]" = queue.Queue()
_fio_fila: threading.Thread | None = None
_parar = threading.Event()
_ultimo_hash_sessao: str | None = None
_ultimo_guardado = 0.0


def caminho_sessao() -> Path:
    return Path(settings.whatsapp_sessao)


# ---------------------------------------------------------------- sessão guardada no banco


def sessao_guardada() -> bool:
    with SessionLocal() as db:
        return db.get(ArquivoSessao, NOME_SESSAO) is not None


def restaurar_sessao() -> bool:
    """Escreve o arquivo da sessão a partir do banco (depois de uma publicação no Render). True se havia sessão."""
    with SessionLocal() as db:
        item = db.get(ArquivoSessao, NOME_SESSAO)
        if item is None:
            return False
        dados = item.dados
    caminho = caminho_sessao()
    if not caminho.exists() or caminho.stat().st_size == 0:
        caminho.write_bytes(dados)
    global _ultimo_hash_sessao
    _ultimo_hash_sessao = hashlib.sha256(dados).hexdigest()
    return True


def _snapshot_sessao() -> bytes | None:
    """Cópia consistente do SQLite da sessão (mesmo com o cliente escrevendo nele), como um arquivo .db."""
    caminho = caminho_sessao()
    if not caminho.exists():
        return None
    copia = Path(str(caminho) + ".copia")
    origem = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
    try:
        destino = sqlite3.connect(copia)
        try:
            origem.backup(destino)
        finally:
            destino.close()
        return copia.read_bytes()
    finally:
        origem.close()
        copia.unlink(missing_ok=True)


def guardar_sessao(forcar: bool = False) -> bool:
    """Guarda a sessão no banco se ela mudou (chaves novas chegam com o uso). True se guardou."""
    global _ultimo_hash_sessao, _ultimo_guardado
    caminho = caminho_sessao()
    if not caminho.exists():
        return False
    if not forcar and time.monotonic() - _ultimo_guardado < INTERVALO_GUARDAR_SESSAO:
        return False
    try:
        dados = _snapshot_sessao()
    except (OSError, sqlite3.Error) as erro:
        log.warning("Não li a sessão do WhatsApp: %s", erro)
        return False
    if not dados:
        return False
    _ultimo_guardado = time.monotonic()
    resumo = hashlib.sha256(dados).hexdigest()
    if resumo == _ultimo_hash_sessao:
        return False
    with SessionLocal() as db:
        item = db.get(ArquivoSessao, NOME_SESSAO)
        if item:
            item.dados = dados
        else:
            db.add(ArquivoSessao(nome=NOME_SESSAO, dados=dados))
        db.commit()
    _ultimo_hash_sessao = resumo
    return True


def esquecer_sessao() -> None:
    global _ultimo_hash_sessao
    with SessionLocal() as db:
        item = db.get(ArquivoSessao, NOME_SESSAO)
        if item:
            db.delete(item)
            db.commit()
    caminho = caminho_sessao()
    for sufixo in ("", "-wal", "-shm", "-journal"):
        Path(str(caminho) + sufixo).unlink(missing_ok=True)
    _ultimo_hash_sessao = None


# ---------------------------------------------------------------- mensagens


def _jid_texto(jid) -> str:
    return f"{jid.User}@{jid.Server}" if jid is not None and jid.User else ""


def extrair_mensagem(evento) -> dict | None:
    """O que interessa numa mensagem de grupo: texto e/ou imagem. None se não for de grupo ou não tiver nada."""
    fonte = evento.Info.MessageSource
    if not fonte.IsGroup or fonte.IsFromMe:
        return None
    m = evento.Message
    texto, imagem_msg = "", None
    if m.HasField("imageMessage"):
        imagem_msg = m.imageMessage
        texto = imagem_msg.caption or ""
    elif m.HasField("documentMessage") and m.documentMessage.mimetype in MIMES_IMAGEM \
            and (m.documentMessage.fileLength or 0) <= TAMANHO_MAX_IMAGEM:
        imagem_msg = m.documentMessage
        texto = m.documentMessage.caption or ""
    elif m.HasField("extendedTextMessage"):
        texto = m.extendedTextMessage.text or ""
    elif m.HasField("conversation"):
        texto = m.conversation or ""
    if getattr(evento, "IsViewOnce", False) or getattr(evento, "IsViewOnceV2", False):
        imagem_msg = None
    if not texto.strip() and imagem_msg is None:
        return None
    quando = datetime.fromtimestamp(evento.Info.Timestamp) if evento.Info.Timestamp else datetime.now()
    return {"jid": _jid_texto(fonte.Chat), "texto": texto, "tem_imagem": imagem_msg is not None,
            "mime": (imagem_msg.mimetype if imagem_msg is not None else None) or "image/jpeg",
            "quando": quando, "id": evento.Info.ID}


def _ao_receber(cliente, evento) -> None:
    """Chamado pela biblioteca a cada mensagem: filtra, baixa a imagem e põe na fila de leitura."""
    try:
        dados = extrair_mensagem(evento)
        if not dados:
            return
        if datetime.now() - dados["quando"] > IDADE_MAXIMA_MENSAGEM:
            return
        with SessionLocal() as db:
            grupo = db.scalar(select(GrupoWhatsApp).where(GrupoWhatsApp.jid == dados["jid"]))
            if grupo is None:  # grupo que a conta entrou depois: aparece na lista, desligado
                grupo = GrupoWhatsApp(jid=dados["jid"], nome=None, ativo=False)
                db.add(grupo)
                db.commit()
                return
            if not grupo.ativo:
                return
            nome = grupo.nome or dados["jid"]
        imagem = None
        if dados["tem_imagem"]:
            try:
                imagem = cliente.download_any(evento.Message)
            except Exception as erro:
                log.warning("Não baixou a imagem do grupo %s: %s", nome, erro)
        if not (dados["texto"] or "").strip() and not imagem:
            return
        _fila.put({"jid": dados["jid"], "nome": nome, "texto": dados["texto"], "imagem": imagem,
                   "mime": dados["mime"] if imagem else None})
    except Exception:
        log.exception("Falha ao tratar mensagem do WhatsApp")


def processar_item(db, item: dict):
    """Registra a promoção de uma mensagem da fila e atualiza os contadores do grupo."""
    r = leitura.processar(db, fonte="whatsapp", fonte_detalhe=item["nome"], link_fonte=None, texto=item["texto"],
                          imagem=item["imagem"], mime=item["mime"], orcamento=leitura.Orcamento(restantes=1))
    grupo = db.scalar(select(GrupoWhatsApp).where(GrupoWhatsApp.jid == item["jid"]))
    if grupo:
        grupo.mensagens = (grupo.mensagens or 0) + 1
        grupo.ultima_mensagem = datetime.now()
        if r.motivo == "criada":
            grupo.promocoes = (grupo.promocoes or 0) + 1
        db.commit()
    estado.contar_mensagem()
    return r


def _rodar_fila() -> None:
    while not _parar.is_set():
        try:
            item = _fila.get(timeout=2)
        except queue.Empty:
            guardar_sessao()
            continue
        try:
            with SessionLocal() as db:
                processar_item(db, item)
        except Exception:
            log.exception("Falha ao registrar promoção do WhatsApp")
        finally:
            _fila.task_done()


# ---------------------------------------------------------------- ligar e desligar


def _novo_cliente(novo: bool):
    from neonize.client import NewClient

    return NewClient(str(caminho_sessao()), new_device=novo)


def _rodar(novo: bool) -> None:
    """Fio do cliente: conecta e fica recebendo; se cair por falha de rede, tenta de novo."""
    global _cliente
    from neonize.events import ConnectedEv, DisconnectedEv, LoggedOutEv, MessageEv, PairStatusEv

    tentativas = 0
    while not _parar.is_set():
        try:
            cliente = _novo_cliente(novo)
        except Exception as erro:  # biblioteca ausente ou incompatível
            estado.status, estado.erro = "erro", f"Leitor do WhatsApp indisponível: {erro}"
            estado.registrar(estado.erro)
            return
        _cliente = cliente

        @cliente.event.qr
        def _qr(c, dados: bytes):
            estado.qr, estado.qr_em, estado.status = dados.decode(errors="ignore"), datetime.now(), "aguardando_qr"
            estado.registrar("QR code gerado; escaneie com o celular do número leitor")

        @cliente.event(PairStatusEv)
        def _pareado(c, ev):
            estado.qr, estado.codigo_pareamento = None, None
            estado.registrar(f"Aparelho pareado ({_jid_texto(ev.ID) or 'número'})")

        @cliente.event(ConnectedEv)
        def _conectado(c, ev):
            estado.status, estado.erro, estado.qr, estado.codigo_pareamento = "conectado", None, None, None
            estado.conectado_em = datetime.now()
            try:
                eu = c.get_me()
                estado.numero = eu.JID.User if eu and eu.JID.User else estado.numero
            except Exception:
                pass
            estado.registrar(f"Conectado{' como +' + estado.numero if estado.numero else ''}")
            threading.Thread(target=_apos_conectar, args=(c,), name="whatsapp-grupos", daemon=True).start()

        @cliente.event(DisconnectedEv)
        def _desconectado(c, ev):
            if estado.status == "conectado":
                estado.status = "conectando"
                estado.registrar("Conexão caiu; a biblioteca tenta religar sozinha")

        @cliente.event(LoggedOutEv)
        def _saiu(c, ev):
            estado.status, estado.numero = "desconectado", None
            estado.registrar("O número foi desconectado pelo WhatsApp (sessão encerrada no celular ou bloqueio)")
            esquecer_sessao()
            _parar.set()

        cliente.event(MessageEv)(_ao_receber)

        estado.status, estado.erro = "conectando", None
        try:
            cliente.connect()  # bloqueia até stop()
            tentativas = 0
        except Exception as erro:
            tentativas += 1
            estado.status, estado.erro = "erro", str(erro)[:300]
            estado.registrar(f"Falha ao conectar: {str(erro)[:120]}")
        if _parar.is_set():
            break
        novo = False
        espera = min(60 * tentativas or 5, 600)
        _parar.wait(espera)
    guardar_sessao(forcar=True)
    if estado.status not in ("desconectado", "erro"):
        estado.status = "desligado"


def _apos_conectar(cliente) -> None:
    """Fora do fio de eventos: lista os grupos e guarda a sessão."""
    try:
        sincronizar_grupos(cliente)
    except Exception as erro:
        log.warning("Não listei os grupos do WhatsApp: %s", erro)
    guardar_sessao(forcar=True)


def iniciar(novo: bool = False) -> bool:
    """Liga o leitor (num fio próprio). novo=True começa um pareamento do zero (QR code)."""
    global _fio, _fio_fila
    if not settings.whatsapp_leitura:
        return False
    with _trava:
        if _fio and _fio.is_alive():
            return False
        _parar.clear()
        if novo:
            esquecer_sessao()
            estado.numero, estado.qr, estado.erro = None, None, None
        else:
            restaurar_sessao()
        _fio = threading.Thread(target=_rodar, args=(novo,), name="whatsapp-leitor", daemon=True)
        _fio.start()
        if not (_fio_fila and _fio_fila.is_alive()):
            _fio_fila = threading.Thread(target=_rodar_fila, name="whatsapp-fila", daemon=True)
            _fio_fila.start()
    return True


def iniciar_se_houver_sessao() -> bool:
    """Ao subir o site: religa o número se ele já foi pareado antes."""
    if not settings.whatsapp_leitura:
        return False
    try:
        if not sessao_guardada():
            return False
    except Exception as erro:
        log.warning("Não consultei a sessão do WhatsApp: %s", erro)
        return False
    return iniciar(novo=False)


def parar() -> None:
    """Desliga o leitor (a sessão continua guardada; religa ao subir de novo)."""
    _parar.set()
    cliente = _cliente
    if cliente is not None:
        try:
            cliente.stop()
        except Exception:
            pass
    if _fio and _fio.is_alive():
        _fio.join(timeout=10)
    guardar_sessao(forcar=True)
    if estado.status not in ("desconectado", "erro"):
        estado.status = "desligado"


def desconectar() -> None:
    """Sai da conta (como "desconectar aparelho" no celular) e apaga a sessão guardada."""
    cliente = _cliente
    if cliente is not None:
        try:
            cliente.logout()
        except Exception as erro:
            log.warning("Logout do WhatsApp falhou: %s", erro)
    parar()
    esquecer_sessao()
    estado.status, estado.numero, estado.qr, estado.codigo_pareamento = "desconectado", None, None, None
    estado.registrar("Número desconectado pela equipe")


def pedir_codigo_pareamento(telefone: str) -> str:
    """Código de 8 letras para parear sem QR (Aparelhos conectados → Conectar com número de telefone)."""
    cliente = _cliente
    if cliente is None or estado.status not in ("aguardando_qr", "conectando"):
        raise RuntimeError("Clique em “Conectar” e espere o QR code aparecer antes de pedir o código.")
    codigo = cliente.PairPhone(telefone, True)
    estado.codigo_pareamento = codigo
    estado.registrar("Código de pareamento gerado")
    return codigo


def sincronizar_grupos(cliente=None) -> int:
    """Traz os grupos em que o número está (nome e id) para a lista da tela; os novos entram desligados."""
    cliente = cliente or _cliente
    if cliente is None or estado.status != "conectado":
        raise RuntimeError("O número não está conectado.")
    grupos = cliente.get_joined_groups()
    with SessionLocal() as db:
        existentes = {g.jid: g for g in db.scalars(select(GrupoWhatsApp))}
        novos = 0
        for g in grupos:
            jid = _jid_texto(g.JID)
            if not jid:
                continue
            if jid in existentes:
                existentes[jid].nome = g.GroupName.Name or existentes[jid].nome
            else:
                db.add(GrupoWhatsApp(jid=jid, nome=g.GroupName.Name or None, ativo=False))
                novos += 1
        db.commit()
    return novos


def qr_png() -> bytes | None:
    """O QR code atual como imagem PNG (para a tela)."""
    if not estado.qr:
        return None
    import segno

    saida = io.BytesIO()
    segno.make(estado.qr, error="m").save(saida, kind="png", scale=6, border=2)
    return saida.getvalue()
