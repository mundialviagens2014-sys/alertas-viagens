"""Envio de e-mail do MVO (por enquanto, só o link de "Esqueci minha senha").

O Render gratuito bloqueia as portas de e-mail comuns (SMTP 25/465/587), então o envio é pela API HTTP da Brevo,
que tem plano gratuito (300 e-mails por dia). A chave da API e o remetente são preenchidos em Configurações; a chave
fica cifrada no banco, como o token da Meta, e nunca volta para a tela.
"""
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from . import cripto
from .config import settings
from .servicos import gravar_estado, ler_estado

log = logging.getLogger(__name__)

URL_BREVO = "https://api.brevo.com/v3/smtp/email"
PREFIXO = "email:"
CHAVE_API, REMETENTE, NOME_REMETENTE = PREFIXO + "brevo_chave", PREFIXO + "remetente", PREFIXO + "nome_remetente"


class EmailNaoEnviado(Exception):
    """Falhou: sem configuração, chave errada, remetente não confirmado na Brevo, sem internet..."""


@dataclass
class ConfigEmail:
    chave: str
    remetente: str
    nome: str

    @property
    def pronto(self) -> bool:
        return bool(self.chave and self.remetente)


def configuracao(db: Session) -> ConfigEmail:
    chave = ler_estado(db, CHAVE_API) or ""
    if chave:
        try:
            chave = cripto.decifrar(chave) or ""
        except cripto.SenhaIlegivel:
            chave = ""
    return ConfigEmail(chave=chave, remetente=(ler_estado(db, REMETENTE) or "").strip(),
                       nome=(ler_estado(db, NOME_REMETENTE) or "").strip() or f"MVO · {settings.agencia_nome}")


def configurado(db: Session) -> bool:
    """Sem decifrar a chave: serve para mostrar o estado em Configurações."""
    return bool(ler_estado(db, CHAVE_API)) and bool((ler_estado(db, REMETENTE) or "").strip())


def salvar(db: Session, chave: str, remetente: str, nome: str) -> None:
    if chave.strip():  # em branco: mantém a chave que já estava (ela nunca aparece na tela)
        gravar_estado(db, CHAVE_API, cripto.cifrar(chave.strip()) or "")
    gravar_estado(db, REMETENTE, remetente.strip().lower())
    gravar_estado(db, NOME_REMETENTE, nome.strip())


def apagar_chave(db: Session) -> None:
    gravar_estado(db, CHAVE_API, "")


def enviar(db: Session, para: str, nome_para: str, assunto: str, html: str, texto: str) -> None:
    conf = configuracao(db)
    if not conf.pronto:
        raise EmailNaoEnviado("O envio de e-mail não está configurado (Configurações → E-mail).")
    try:
        r = httpx.post(URL_BREVO, timeout=15, headers={"api-key": conf.chave, "accept": "application/json"}, json={
            "sender": {"name": conf.nome, "email": conf.remetente},
            "to": [{"email": para, "name": nome_para or para}],
            "subject": assunto, "htmlContent": html, "textContent": texto,
            "tags": ["mvo-senha"],
        })
    except httpx.HTTPError as e:
        raise EmailNaoEnviado(f"Sem resposta da Brevo ({e.__class__.__name__}).") from e
    if r.status_code >= 300:
        try:
            detalhe = r.json().get("message") or r.text[:200]
        except ValueError:
            detalhe = r.text[:200]
        if r.status_code == 401:
            detalhe = "chave da API recusada pela Brevo"
        raise EmailNaoEnviado(f"A Brevo recusou o envio ({r.status_code}): {detalhe}")
