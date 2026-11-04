"""Planilha com os dados brutos do sistema: uma aba por tabela, todas as colunas, todas as linhas.

Só o administrador baixa, e fica no registro de acessos. Ficam de fora o que não é dado de análise e o que é
segredo: senhas (hash), logins/senhas/tokens cifrados das contas, token da API da Meta, código secreto da página
do cliente, a sessão do WhatsApp e o conteúdo das imagens (as abas trazem só os campos de texto e números).
"""
import io
import re
from datetime import date, datetime

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from .database import Base

COLUNAS_SECRETAS = re.compile(r"(senha|_cripto$|^token|token_pagina|segredo|^dados$|^hash)", re.I)
TABELAS_FORA = {"arquivos_sessao", "estado_coletor"}
LIMITE_LINHAS = 200_000
DESCRICOES = {
    "clientes": "Clientes dos alertas (telefone, autorização, classificação)",
    "interesses": "Trechos de interesse de cada cliente",
    "promocoes": "Promoções lidas (fonte, tipo, trecho, preço, datas)",
    "imagens_promocao": "Imagens das promoções (só os campos; a imagem em si não vai)",
    "matches": "Promoção × cliente: o que entrou na fila e o que foi enviado",
    "confirmacoes_cadastro": "Confirmações de cadastro pelo formulário",
    "envios_whatsapp": "Mensagens enviadas pela API da Meta (status, modelo, quem enviou)",
    "respostas_whatsapp": "Respostas dos clientes pela API (texto, intenção, SAIR)",
    "etapas_crm": "Etapa do CRM definida à mão",
    "grupos_whatsapp": "Grupos lidos pelo número leitor",
    "leituras_pendentes": "Mensagens esperando a IA",
    "colaboradores": "Quem cede as contas de milhas",
    "contas_programa": "Contas nos programas (saldo, custo; sem login/senha)",
    "lancamentos": "Lançamentos de pontos (compras, bônus, emissões, transferências)",
    "emissoes": "Emissões (venda, custo, lucro, passageiros, emissor)",
    "emissao_passageiros": "Passageiros de cada emissão",
    "pedidos_emissao": "Pedidos de emissão",
    "agencias": "Agências parceiras", "franquias": "Franquias", "contratos": "Contratos",
    "compras_externas": "Compras externas de pontos", "fornecedores": "Fornecedores",
    "programas_milhas": "Programas e lista de preços", "usuarios": "Equipe (sem senhas)",
    "registro_acessos": "Registro de acessos e ações da equipe",
}


def _celula(valor):
    if valor is None or isinstance(valor, (int, float, bool, date, datetime)):
        return valor
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return f"[{len(valor)} bytes]"
    texto = str(valor)
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    texto = ILLEGAL_CHARACTERS_RE.sub("", texto)[:32000]
    return "'" + texto if texto.startswith(("=", "+", "-", "@")) else texto


def tabelas() -> list[Table]:
    return [t for t in Base.metadata.sorted_tables if t.name not in TABELAS_FORA]


def colunas_de(tabela: Table) -> list[str]:
    return [c.name for c in tabela.columns if not COLUNAS_SECRETAS.search(c.name)]


def gerar(db: Session) -> tuple[bytes, list[tuple[str, int]]]:
    """Devolve o arquivo e [(tabela, linhas), ...]."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from sqlalchemy import func

    selecionadas = [(t, colunas_de(t)) for t in tabelas()]
    selecionadas = [(t, c) for t, c in selecionadas if c]
    contagens = {t.name: min(db.scalar(select(func.count()).select_from(t)) or 0, LIMITE_LINHAS) for t, _ in selecionadas}

    wb = Workbook(write_only=True)
    leia_me = wb.create_sheet("Leia-me")
    leia_me.column_dimensions["A"].width = 28
    leia_me.column_dimensions["B"].width = 10
    leia_me.column_dimensions["C"].width = 70
    leia_me.append(["Dados brutos do MVO", datetime.now().strftime("%d/%m/%Y %H:%M")])
    leia_me.append([])
    leia_me.append(["Aba (tabela)", "Linhas", "O que é"])
    for t, _ in selecionadas:
        leia_me.append([t.name, contagens[t.name], DESCRICOES.get(t.name, "")])
    leia_me.append([])
    leia_me.append(["Fora da planilha: senhas, logins/senhas/tokens cifrados das contas, token da API da Meta, "
                    "código da página do cliente, sessão do WhatsApp e o conteúdo das imagens."])

    resumo: list[tuple[str, int]] = []
    for tabela, colunas in selecionadas:
        ws = wb.create_sheet(tabela.name[:31])
        for i, nome in enumerate(colunas, start=1):
            ws.column_dimensions[get_column_letter(i)].width = max(10, min(40, len(nome) + 4))
        ws.append(colunas)
        linhas = 0
        consulta = select(*[tabela.c[nome] for nome in colunas]).limit(LIMITE_LINHAS)
        chaves = list(tabela.primary_key.columns)
        if chaves:
            consulta = consulta.order_by(*chaves)
        for linha in db.execute(consulta):
            ws.append([_celula(v) for v in linha])
            linhas += 1
        resumo.append((tabela.name, linhas))
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue(), resumo
