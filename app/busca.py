"""Busca das listas do site.

- Sem diferença entre maiúsculas, minúsculas e acentos: "joao" acha "João", "conceicao" acha "Conceição".
- Palavra por palavra: "maria silva" acha "Maria da Silva" (cada palavra precisa aparecer em algum campo).
- Números (telefone, CPF, CNPJ) valem só pelos dígitos: "(85) 99876-1234" acha 5585998761234.
"""
import re
import unicodedata
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta

from sqlalchemy import String, and_, or_
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.functions import FunctionElement

# letras acentuadas (minúsculas e maiúsculas) e a versão sem acento, para o translate do PostgreSQL
_COM = "áàâãäåéèêëíìîïóòôõöúùûüçñýÿÁÀÂÃÄÅÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑÝ"
_SEM = "aaaaaaeeeeiiiiooooouuuucnyyaaaaaaeeeeiiiiooooouuuucny"


def sem_acento(texto: str | None) -> str:
    """'São JOÃO' -> 'sao joao'."""
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


class sem_acento_sql(FunctionElement):
    """A coluna em minúsculas e sem acento, dentro do banco (PostgreSQL ou SQLite)."""

    type = String()
    name = "sem_acento"
    inherit_cache = True


@compiles(sem_acento_sql)
def _sem_acento_sqlite(elemento, compilador, **kw):  # função registrada na conexão (database.py)
    return f"sem_acento({compilador.process(elemento.clauses, **kw)})"


@compiles(sem_acento_sql, "postgresql")
def _sem_acento_postgres(elemento, compilador, **kw):
    return f"translate(lower({compilador.process(elemento.clauses, **kw)}), '{_COM}', '{_SEM}')"


Campo = ColumnElement | Callable[[str], ColumnElement]


def _texto(campo: Campo, termo: str) -> ColumnElement:
    if callable(campo) and not isinstance(campo, ColumnElement):
        return campo(termo)  # campo em outra tabela (ex.: passageiros da emissão)
    return sem_acento_sql(campo).contains(termo, autoescape=True)


def _numero(campo: Campo, numero: str) -> ColumnElement:
    if callable(campo) and not isinstance(campo, ColumnElement):
        return campo(numero)
    return campo.contains(numero, autoescape=True)


def condicao(q: str | None, textos: Iterable[Campo], digitos: Iterable[Campo] = ()) -> ColumnElement | None:
    """Condição para a busca digitada; None se a busca estiver vazia.

    textos: colunas comparadas sem acento (ou funções que recebem a palavra já sem acento);
    digitos: colunas guardadas só com números (telefone, CPF), comparadas pelos dígitos digitados."""
    q = (q or "").strip()
    if not q:
        return None
    textos, digitos = list(textos), list(digitos)
    if not re.search(r"[^\W\d_]", q):  # sem letras: número (telefone, CPF, CNPJ), data ou IP
        numero = re.sub(r"\D", "", q)
        bruto = sem_acento(q)
        alternativas = [_texto(c, bruto) for c in textos]  # como foi digitado ("200.1.2", "15/03")
        if numero:
            alternativas += [_numero(c, numero) for c in digitos]  # só os dígitos ("(85) 9987-1234")
            if numero != bruto:
                alternativas += [_texto(c, numero) for c in textos]
        return or_(*alternativas) if alternativas else None
    partes = []
    for palavra in q.split():
        termo = sem_acento(palavra)
        alternativas = [_texto(c, termo) for c in textos]
        numero = re.sub(r"\D", "", palavra)
        if numero and not re.search(r"[^\W\d_]", palavra):  # "(85)", "9987-1234": vale pelos dígitos
            alternativas += [_numero(c, numero) for c in digitos]
        partes.append(or_(*alternativas))
    return and_(*partes)


def aplicar(consulta, q: str | None, textos: Iterable[Campo], digitos: Iterable[Campo] = ()):
    """consulta.where(condição da busca), ou a própria consulta se a busca estiver vazia."""
    filtro = condicao(q, textos, digitos)
    return consulta if filtro is None else consulta.where(filtro)


# períodos prontos dos filtros ("Cadastrados nos últimos 7 dias"...)
PERIODOS = {"hoje": "Hoje", "7": "Últimos 7 dias", "30": "Últimos 30 dias", "90": "Últimos 90 dias", "365": "Último ano"}


def desde(periodo: str | None) -> datetime | None:
    """Início do período escolhido; None = qualquer data."""
    if periodo == "hoje":
        return datetime.combine(date.today(), datetime.min.time())
    if periodo and periodo.isdigit():
        return datetime.now() - timedelta(days=int(periodo))
    return None
