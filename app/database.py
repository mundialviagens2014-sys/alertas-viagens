from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _ligacoes_sqlite(conexao, _registro):
        # faz o SQLite respeitar as ligações entre tabelas, como o PostgreSQL
        cursor = conexao.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        # busca sem acento (no PostgreSQL é feita com translate; ver app/busca.py)
        from .busca import sem_acento
        conexao.create_function("sem_acento", 1, lambda texto: None if texto is None else sem_acento(texto),
                                deterministic=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def adicionar_colunas_faltantes(motor) -> list[str]:
    """Atualiza bancos criados por versões anteriores: acrescenta colunas novas sem apagar dados."""
    inspetor = inspect(motor)
    adicionadas = []
    for tabela in Base.metadata.sorted_tables:
        if not inspetor.has_table(tabela.name):
            continue
        existentes = {c["name"] for c in inspetor.get_columns(tabela.name)}
        for coluna in tabela.columns:
            if coluna.name in existentes:
                continue
            tipo = coluna.type.compile(motor.dialect)
            padrao = _valor_padrao_sql(coluna)
            with motor.begin() as conexao:
                conexao.execute(text(
                    f'ALTER TABLE {tabela.name} ADD COLUMN {coluna.name} {tipo}' + (f" DEFAULT {padrao}" if padrao else "")
                ))
            adicionadas.append(f"{tabela.name}.{coluna.name}")
    return adicionadas


def _valor_padrao_sql(coluna) -> str | None:
    """Valor padrão simples (sim/não, número, texto) da coluna, para os registros que já existem."""
    padrao = coluna.default
    if padrao is None or not getattr(padrao, "is_scalar", False):
        return None
    valor = padrao.arg
    if isinstance(valor, bool):
        return "TRUE" if valor else "FALSE"
    if isinstance(valor, (int, float)):
        return str(valor)
    if isinstance(valor, str):
        return "'" + valor.replace("'", "''") + "'"
    return None


def init_db() -> None:
    from . import models, models_mvo  # noqa: F401  (registra as tabelas)

    Base.metadata.create_all(engine)
    adicionar_colunas_faltantes(engine)
    # a coluna nova entra sem índice nos bancos que já existiam; o código da página do cliente precisa ser único
    with engine.begin() as conexao:
        conexao.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_clientes_token_pagina ON clientes (token_pagina)"))
