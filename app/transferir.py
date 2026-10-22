"""Copia todos os dados de um banco para outro (ex.: do alertas.db do computador para o Supabase).

Uso:  python -m app.cli copiar-banco "postgresql://usuario:senha@host:5432/postgres"

- Cria as tabelas no banco de destino, se ainda não existirem.
- Recusa copiar para um banco que já tenha clientes ou promoções, para não misturar nem duplicar dados
  (use --substituir para apagar o destino e copiar de novo).
- O banco de origem (o alertas.db) não é alterado.
"""
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine

from .config import BASE_DIR, normalizar_url_banco
from .database import Base, adicionar_colunas_faltantes


def _motor(url: str) -> Engine:
    url = normalizar_url_banco(url)
    args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=args, pool_pre_ping=True)


def copiar(origem_url: str, destino_url: str, substituir: bool = False, avisar=print) -> dict[str, int]:
    from . import models, models_mvo  # noqa: F401  (registra as tabelas)

    origem, destino = _motor(origem_url), _motor(destino_url)
    # o banco de origem pode ser de uma versão antiga: completa as colunas antes de ler
    Base.metadata.create_all(origem)
    adicionar_colunas_faltantes(origem)

    Base.metadata.create_all(destino)
    adicionar_colunas_faltantes(destino)

    with destino.connect() as con:
        ocupado = any(
            con.execute(select(func.count()).select_from(Base.metadata.tables[t])).scalar()
            for t in ("clientes", "promocoes")
        )
    if ocupado and not substituir:
        raise RuntimeError("O banco de destino já tem clientes ou promoções. Para apagar tudo lá e copiar de novo, "
                           "rode o comando com --substituir.")

    tabelas = Base.metadata.sorted_tables  # ordem que respeita as ligações entre tabelas
    contagem: dict[str, int] = {}
    copiados: dict[tuple[str, str], set] = {}  # (tabela, coluna) -> valores copiados, para conferir ligações
    with origem.connect() as de, destino.begin() as para:
        for tabela in reversed(tabelas):  # limpa o destino (filhas antes das mães)
            para.execute(tabela.delete())
        for tabela in tabelas:
            linhas = [dict(r._mapping) for r in de.execute(select(tabela))]
            # O SQLite não confere as ligações entre tabelas; o PostgreSQL sim. Registro que aponta para algo
            # já excluído (ex.: envio de um trecho apagado) perde a ligação ou, se ela é obrigatória, fica de fora.
            validas, soltas, descartadas = [], 0, 0
            for linha in linhas:
                manter = True
                for fk in tabela.foreign_keys:
                    col, alvo = fk.parent, fk.column
                    valor = linha.get(col.name)
                    if valor is None or valor in copiados.get((alvo.table.name, alvo.name), set()):
                        continue
                    if col.nullable:
                        linha[col.name] = None
                        soltas += 1
                    else:
                        manter = False
                if manter:
                    validas.append(linha)
                else:
                    descartadas += 1
            if validas:
                para.execute(tabela.insert(), validas)
            for coluna in tabela.columns:
                copiados[(tabela.name, coluna.name)] = {l[coluna.name] for l in validas}
            contagem[tabela.name] = len(validas)
            extra = []
            if soltas:
                extra.append(f"{soltas} ligação(ões) a registros excluídos removida(s)")
            if descartadas:
                extra.append(f"{descartadas} registro(s) de itens excluídos deixado(s) de fora")
            avisar(f"  {tabela.name}: {len(validas)}" + (f"  ({'; '.join(extra)})" if extra else ""))

        if para.dialect.name == "postgresql":
            # os próximos cadastros continuam a numeração de onde parou
            for tabela in tabelas:
                pk = list(tabela.primary_key.columns)
                if len(pk) == 1 and pk[0].autoincrement and str(pk[0].type) == "INTEGER":
                    para.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{tabela.name}', '{pk[0].name}'), "
                        f"COALESCE((SELECT MAX({pk[0].name}) FROM {tabela.name}), 0) + 1, false)"
                    ))
    return contagem


def origem_padrao() -> str:
    return f"sqlite:///{BASE_DIR / 'alertas.db'}"
