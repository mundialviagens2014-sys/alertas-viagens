"""Listas do site em planilha do Excel, com os mesmos filtros da tela.

Cada lista define as colunas (título, como tirar o valor de cada item e o formato). Quem exporta fica no
registro de acessos. Logins e senhas das contas nunca vão para a planilha.
"""
import io
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LIMITE_LINHAS = 100_000

FORMATOS = {
    "dinheiro": '"R$" #,##0.00;[Red]-"R$" #,##0.00',
    "pontos": "#,##0;[Red]-#,##0",
    "numero": "#,##0.00",
    "pct": '0.0"%"',
    "data": "DD/MM/YYYY",
    "datahora": "DD/MM/YYYY HH:MM",
}


@dataclass
class Coluna:
    titulo: str
    valor: Callable  # recebe o item e devolve o conteúdo da célula
    formato: str = ""  # dinheiro, pontos, numero, pct, data, datahora (vazio = texto/automático)
    largura: int | None = None


def sim_nao(valor) -> str:
    return "Sim" if valor else "Não"


def _limpar_texto(texto: str) -> str:
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    return ILLEGAL_CHARACTERS_RE.sub("", texto)[:32000]


def _celula(valor):
    if valor is None:
        return None
    if isinstance(valor, bool):
        return sim_nao(valor)
    if isinstance(valor, (int, float, date, datetime)):
        return valor
    if isinstance(valor, (list, tuple, set)):
        valor = ", ".join(str(v) for v in valor if v not in (None, ""))
    return _limpar_texto(str(valor)) or None


def _largura(valor, formato: str) -> int:
    if valor is None:
        return 0
    if formato in ("data",):
        return 10
    if formato == "datahora":
        return 16
    if isinstance(valor, (int, float)):
        return len(f"{valor:,.2f}") + (3 if formato == "dinheiro" else 0)
    return max((len(parte) for parte in str(valor).splitlines()), default=0)


def _nome_aba(titulo: str) -> str:
    return re.sub(r"[\[\]:*?/\\]", " ", titulo)[:31] or "Planilha"


def gerar(titulo: str, colunas: list[Coluna], itens: Iterable) -> tuple[bytes, int]:
    """Planilha com a linha de títulos (azul, fixa e com filtro) e uma linha por item."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = _nome_aba(titulo)
    for i, coluna in enumerate(colunas, start=1):
        celula = ws.cell(row=1, column=i, value=coluna.titulo)
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor="1B468F")
        celula.alignment = Alignment(vertical="center")
    larguras = [len(c.titulo) for c in colunas]
    linha = 1
    for item in itens:
        linha += 1
        if linha > LIMITE_LINHAS + 1:
            break
        for i, coluna in enumerate(colunas, start=1):
            valor = _celula(coluna.valor(item))
            celula = ws.cell(row=linha, column=i, value=valor)
            if isinstance(valor, str) and valor.startswith("="):
                celula.data_type = "s"  # texto que começa com "=" não vira fórmula
            elif coluna.formato in FORMATOS and valor is not None and not isinstance(valor, str):
                celula.number_format = FORMATOS[coluna.formato]
            larguras[i - 1] = max(larguras[i - 1], _largura(valor, coluna.formato))
    for i, coluna in enumerate(colunas, start=1):
        ws.column_dimensions[get_column_letter(i)].width = coluna.largura or max(9, min(60, larguras[i - 1] + 2))
    ws.freeze_panes = "A2"
    if linha > 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(colunas))}{linha}"
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue(), linha - 1


def _arquivo(titulo: str) -> str:
    base = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "planilha"
    return f"mvo-{base}-{date.today():%Y-%m-%d}.xlsx"


def exportar(request: Request, db: Session, usuario, titulo: str, colunas: list[Coluna], itens: Iterable) -> Response:
    """Gera a planilha, registra quem exportou e devolve o arquivo para baixar."""
    from .mvo.contas import registrar

    conteudo, linhas = gerar(titulo, colunas, itens)
    filtros = "; ".join(f"{k}={v}" for k, v in request.query_params.multi_items() if k != "formato" and v)
    registrar(db, usuario, "exportacao", None, f"{titulo}: {linhas} linha(s)" + (f" ({filtros})" if filtros else ""),
              request.client.host if request.client else None)
    db.commit()
    return Response(conteudo, media_type=XLSX, headers={
        "Content-Disposition": f'attachment; filename="{_arquivo(titulo)}"', "Cache-Control": "no-store"})


def link_planilha(request: Request) -> str:
    """Endereço da mesma lista, com os mesmos filtros, em planilha."""
    parametros = [(k, v) for k, v in request.query_params.multi_items() if k not in ("formato", "limite")]
    return request.url.path + "?" + urlencode(parametros + [("formato", "xlsx")])


def quer_planilha(formato: str) -> bool:
    return (formato or "").lower() == "xlsx"
