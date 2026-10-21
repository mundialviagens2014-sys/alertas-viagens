"""Leitura de grupos e canais do Telegram com uma conta da agência (Telethon).

Antes do primeiro uso:
  1. Crie as credenciais em https://my.telegram.org -> "API development tools".
  2. Preencha TELEGRAM_API_ID e TELEGRAM_API_HASH no .env.
  3. Rode `python -m app.cli telegram-login` uma vez e informe o código recebido no Telegram.

Os grupos lidos são cadastrados no site, em "Grupos do Telegram". A conta precisa
participar de cada grupo.
"""
import asyncio
import logging
import os
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import CanalTelegram
from ..servicos import gravar_estado, ler_estado, registrar_promocao

log = logging.getLogger(__name__)

PRIMEIRA_LEITURA = 50  # mensagens lidas de um grupo na primeira vez
MAXIMO_POR_RODADA = 300


class TelegramNaoConfigurado(RuntimeError):
    pass


CHAVE_SESSAO = "telegram:sessao"


def sessao_salva() -> str | None:
    """Login do Telegram guardado no banco (funciona no servidor, onde não há arquivo de sessão)."""
    from ..database import SessionLocal

    with SessionLocal() as db:
        return ler_estado(db, CHAVE_SESSAO) or None


def salvar_sessao(texto: str) -> None:
    from ..database import SessionLocal

    with SessionLocal() as db:
        gravar_estado(db, CHAVE_SESSAO, texto)


def _arquivo_sessao() -> str | None:
    caminho = settings.telegram_sessao
    caminho = caminho if caminho.endswith(".session") else caminho + ".session"
    return caminho if os.path.exists(caminho) else None


def _cliente(usar_arquivo: bool = False):
    if not (settings.telegram_api_id and settings.telegram_api_hash):
        raise TelegramNaoConfigurado("Preencha TELEGRAM_API_ID e TELEGRAM_API_HASH no .env.")
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as erro:
        raise TelegramNaoConfigurado("Instale a dependência: pip install telethon") from erro
    api_id, api_hash = int(settings.telegram_api_id), settings.telegram_api_hash
    texto = None if usar_arquivo else sessao_salva()
    if texto:
        return TelegramClient(StringSession(texto), api_id, api_hash)
    if not usar_arquivo and _arquivo_sessao():
        # login feito antes desta versão: passa o arquivo para o banco e segue
        migrar_arquivo_para_banco()
        return TelegramClient(StringSession(sessao_salva()), api_id, api_hash)
    return TelegramClient(settings.telegram_sessao, api_id, api_hash)


def migrar_arquivo_para_banco() -> bool:
    """Copia o login do arquivo telegram_agencia.session para o banco de dados."""
    from telethon.sessions import SQLiteSession, StringSession

    arquivo = _arquivo_sessao()
    if not arquivo:
        return False
    sessao = SQLiteSession(arquivo[: -len(".session")])
    try:
        if not sessao.auth_key:
            return False
        salvar_sessao(StringSession.save(sessao))
        return True
    finally:
        sessao.close()


async def _conectar():
    client = _cliente()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise TelegramNaoConfigurado("Sessão do Telegram não autorizada. Rode no terminal: python -m app.cli telegram-login")
    return client


def _entidade(canal: str):
    canal = canal.strip()
    return int(canal) if canal.lstrip("-").isdigit() else canal


def _link_mensagem(canal: str, msg_id: int) -> str | None:
    if canal.lstrip("-").isdigit():
        if canal.startswith("-100"):  # grupo privado: link funciona para quem participa
            return f"https://t.me/c/{canal[4:]}/{msg_id}"
        return None
    return f"https://t.me/{canal.lstrip('@')}/{msg_id}"


def _identificador(dialogo) -> str:
    usuario = getattr(dialogo.entity, "username", None)
    return f"@{usuario}" if usuario else str(dialogo.id)


async def _coletar(db: Session, reler: bool = False, so_canal: int | None = None) -> dict:
    resumo = {"lidas": 0, "criadas": 0, "duplicadas": 0, "ignoradas": 0, "matches": 0, "erros": []}
    consulta = select(CanalTelegram).where(CanalTelegram.ativo.is_(True))
    if so_canal:
        consulta = select(CanalTelegram).where(CanalTelegram.id == so_canal)
    canais = db.scalars(consulta).all()
    if not canais:
        raise TelegramNaoConfigurado("Nenhum grupo cadastrado. Adicione em “Grupos do Telegram”.")

    client = await _conectar()
    try:
        # o login guardado no banco não traz a lista de grupos: carrega antes (necessário para grupos privados)
        await client.get_dialogs()
        for canal in canais:
            ident = canal.identificador
            chave = f"telegram:{ident}:ultimo_id"
            ultimo = 0 if reler else int(ler_estado(db, chave) or 0)
            try:
                entidade = await client.get_entity(_entidade(ident))
                if not canal.nome:
                    canal.nome = getattr(entidade, "title", None) or getattr(entidade, "first_name", None)
                mensagens = [
                    m async for m in client.iter_messages(
                        entidade, min_id=ultimo, limit=MAXIMO_POR_RODADA if ultimo else PRIMEIRA_LEITURA
                    )
                ]
            except Exception as erro:  # grupo inexistente, conta fora do grupo etc.
                log.warning("Falha ao ler %s: %s", ident, erro)
                canal.ultimo_erro = str(erro)[:300]
                db.commit()
                resumo["erros"].append(f"{canal.nome or ident}: {erro}")
                continue

            for msg in reversed(mensagens):  # da mais antiga para a mais nova
                texto = msg.message or ""
                resumo["lidas"] += 1
                if not texto.strip():
                    resumo["ignoradas"] += 1
                    continue
                r = registrar_promocao(
                    db, texto=texto, fonte="telegram", fonte_detalhe=canal.nome or ident,
                    link_fonte=_link_mensagem(ident, msg.id),
                )
                if r.motivo == "criada":
                    resumo["criadas"] += 1
                    resumo["matches"] += r.matches
                elif r.motivo == "duplicada":
                    resumo["duplicadas"] += 1
                else:
                    resumo["ignoradas"] += 1

            if mensagens:
                gravar_estado(db, chave, str(max(m.id for m in mensagens)))
            canal.ultima_leitura, canal.ultimo_erro = datetime.now(), None
            db.commit()
    finally:
        await client.disconnect()
    return resumo


def coletar(db: Session, reler: bool = False, so_canal: int | None = None) -> dict:
    """Lê as mensagens novas dos grupos ativos.

    reler=True volta a ler as últimas mensagens (útil depois de melhorar a leitura);
    promoções já registradas não se repetem."""
    return asyncio.run(_coletar(db, reler, so_canal))


async def _listar_grupos() -> list[dict]:
    client = await _conectar()
    try:
        grupos = []
        async for d in client.iter_dialogs(limit=300):
            if d.is_channel or d.is_group:
                grupos.append({"identificador": _identificador(d), "nome": d.name,
                               "membros": getattr(d.entity, "participants_count", None)})
        return grupos
    finally:
        await client.disconnect()


def listar_grupos() -> list[dict]:
    """Grupos e canais de que a conta da agência participa."""
    return asyncio.run(_listar_grupos())


async def _login() -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(), int(settings.telegram_api_id), settings.telegram_api_hash)
    await client.start()  # pede telefone e código no terminal
    eu = await client.get_me()
    salvar_sessao(client.session.save())
    print(f"Sessão criada para {eu.first_name} ({eu.phone}) e guardada no banco de dados.")
    print("Agora cadastre os grupos no site, em “Grupos do Telegram”.")
    await client.disconnect()


def login() -> None:
    asyncio.run(_login())
