"""Dados de exemplo para testar o sistema sem precisar das fontes reais."""
from datetime import date

from sqlalchemy import func, select

from .database import SessionLocal
from .models import Cliente, Interesse
from .servicos import normalizar_telefone, registrar_promocao

CLIENTES = [
    ("Maria Oliveira", "(85) 99876-1234", [("FOR", "LIS", (2027, 3, 1), (2027, 6, 30), 3000)]),
    ("João Pereira", "(88) 99712-3344", [("FOR", "MCO", None, None, 4500), ("FOR", "GRU", None, None, 600)]),
    ("Ana Costa", "(85) 98111-2222", [("FOR", None, (2026, 12, 15), (2027, 1, 31), 1500)]),
    ("Carlos Mendes", "(11) 97654-0001", [("GRU", "MIA", (2026, 11, 1), (2026, 12, 31), 2500)]),
    ("Fernanda Lima", "(85) 99600-7788", [("FOR", "LIS", None, None, None), ("FOR", "PAR", None, None, 4000)]),
]

PROMOCOES = [
    "🔥 FORTALEZA ➜ LISBOA por R$ 2.389 ida e volta com a TAP! Datas de março a junho/27. https://exemplo.com/for-lis",
    "GRU → MIA a partir de R$ 1.899 com a LATAM, saídas em novembro e dezembro. Compre até 25/10!",
    "Voos para Orlando saindo de Fortaleza por 3.950 reais (ida e volta) em fevereiro/2027",
    "Fortaleza x São Paulo por R$ 389 o trecho, datas de 10/12 a 20/01 – Azul",
    "Smiles: Fortaleza para Recife por 6.500 milhas o trecho em janeiro",
    "Paris saindo de Fortaleza por R$ 3.600 com Air France em maio/2027",
]


def carregar() -> str:
    with SessionLocal() as db:
        if db.scalar(select(func.count(Cliente.id))):
            return "O banco já tem clientes; dados de exemplo não foram carregados."
        for nome, tel, interesses in CLIENTES:
            c = Cliente(nome=nome, telefone=normalizar_telefone(tel), consentimento=True)
            for origem, destino, ini, fim, teto in interesses:
                c.interesses.append(Interesse(
                    origem=origem, destino=destino, preco_maximo=teto,
                    periodo_inicio=date(*ini) if ini else None, periodo_fim=date(*fim) if fim else None,
                ))
            db.add(c)
        db.commit()
        total = 0
        for texto in PROMOCOES:
            r = registrar_promocao(db, texto=texto, fonte="manual", fonte_detalhe="Dados de exemplo")
            total += r.matches
        return f"{len(CLIENTES)} clientes e {len(PROMOCOES)} promoções de exemplo criados ({total} matches)."
