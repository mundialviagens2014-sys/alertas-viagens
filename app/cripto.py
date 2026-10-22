"""Criptografia dos logins e senhas das contas dos colaboradores.

A chave vem de CHAVE_SENHAS (recomendado; no Render é gerada automaticamente). Se ela não
existir, é derivada da SECRET_KEY. As duas chaves são aceitas na leitura, então dados gravados
com a chave derivada continuam legíveis depois que CHAVE_SENHAS for configurada.
Se as duas chaves mudarem, as senhas gravadas deixam de ser legíveis (precisam ser digitadas de novo).
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .config import settings


def _fernet_de(segredo: str) -> Fernet:
    chave = hashlib.sha256(f"mvo-senhas:{segredo}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(chave))


def _cofre() -> MultiFernet:
    segredos = [s for s in (settings.chave_senhas, settings.secret_key) if s]
    unicos = list(dict.fromkeys(segredos))  # sem repetir, mantendo a ordem (a primeira cifra)
    return MultiFernet([_fernet_de(s) for s in unicos])


def cifrar(texto: str | None) -> str | None:
    if texto is None or texto == "":
        return None
    return _cofre().encrypt(texto.encode()).decode()


class SenhaIlegivel(Exception):
    pass


def decifrar(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _cofre().decrypt(token.encode()).decode()
    except InvalidToken as erro:
        raise SenhaIlegivel("Não foi possível ler: a chave de criptografia mudou. Digite a senha de novo.") from erro


def mascarar(texto: str | None) -> str:
    """'12345678901' -> '123•••••901' (para mostrar sem revelar)."""
    if not texto:
        return ""
    if len(texto) <= 4:
        return "•" * len(texto)
    return texto[:2] + "•" * (len(texto) - 4) + texto[-2:]


def cifrar_bytes(conteudo: bytes) -> bytes:
    """Para arquivos temporários com dados sensíveis (ex.: planilha com senhas aguardando confirmação)."""
    return _cofre().encrypt(conteudo)


def decifrar_bytes(conteudo: bytes) -> bytes:
    try:
        return _cofre().decrypt(conteudo)
    except InvalidToken as erro:
        raise SenhaIlegivel("Arquivo temporário ilegível.") from erro
