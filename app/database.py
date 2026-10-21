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
            with motor.begin() as conexao:
                conexao.execute(text(f'ALTER TABLE {tabela.name} ADD COLUMN {coluna.name} {tipo}'))
            adicionadas.append(f"{tabela.name}.{coluna.name}")
    return adicionadas


def init_db() -> None:
    from . import models  # noqa: F401  (registra as tabelas)

    Base.metadata.create_all(engine)
    adicionar_colunas_faltantes(engine)
