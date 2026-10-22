"""Importação das planilhas exportadas do Manager (ou de qualquer planilha com os mesmos dados).

Quatro tipos de planilha:
- pessoas: colaboradores (no Manager, perfil FIDELIZADO) e clientes (perfil CLIENTE), com documentos e endereço;
- contas: contas dos colaboradores nos programas, com saldo, custo, login e senha;
- fornecedores: fornecedores de pontos;
- agencias: agências parceiras (com a franquia, que é cadastrada se ainda não existir).

As colunas são reconhecidas pelo título, em qualquer ordem, e a linha de títulos pode estar abaixo de
linhas de cabeçalho do relatório. Nada é gravado na conferência: só ao confirmar.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cripto import cifrar
from ..importacao import _contem, _data, _texto
from ..models import Cliente, ProgramaMilhas
from ..models_mvo import Agencia, Colaborador, ContaPrograma, Fornecedor, Franquia, Lancamento
from ..parser import _normalizar
from ..programas import nome_programa
from ..servicos import ajustar_nome, normalizar_telefone
from .contas import formatar_pontos, lancar, so_digitos

TIPOS = {
    "pessoas": "Colaboradores (fidelizados) e clientes",
    "contas": "Contas nos programas, com saldo",
    "fornecedores": "Fornecedores de pontos",
    "agencias": "Agências parceiras",
}

_ENDERECO = [
    ("cep", ("cep",)),
    ("endereco", ("endereco", "logradouro")),
    ("numero_endereco", ("numero",)),
    ("complemento", ("complemento",)),
    ("bairro", ("bairro",)),
    ("cidade", ("cidade", "municipio")),
    ("estado", ("estado", "uf")),
]

# Ordem importa: a primeira regra que bater define a coluna (ex.: "Senha do programa" é senha, não programa).
REGRAS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "pessoas": [
        ("perfil", ("perfil", "tipo de cliente", "tipo cliente", "categoria", "tipo")),
        ("cpf", ("cpf", "cnpj", "documento", "doc")),
        ("email", ("e-mail", "email")),
        ("telefone", ("telefone", "celular", "whatsapp", "whats", "fone", "contato")),
        ("pix", ("pix",)),
        ("classificacao", ("classifica", "nivel")),
        ("consolidador", ("consolidador", "indicad", "indicacao", "captador", "grupo")),
        ("ativo", ("ativo", "status", "situacao")),
        ("observacoes", ("observ", "obs", "anotac", "coment")),
        ("rg", ("rg", "identidade", "registro geral")),
        ("data_nascimento", ("nascimento", "aniversario", "data de nasc")),
        *_ENDERECO,
        ("nome", ("nome", "cliente", "fidelizado", "colaborador", "titular", "razao social")),
    ],
    "contas": [
        ("token", ("token",)),
        ("senha", ("senha", "password", "pass")),
        ("email_programa", ("e-mail", "email")),
        ("codigo_conta", ("codigo da conta", "codigo conta", "cod. conta")),
        ("login", ("login", "usuario", "acesso")),
        ("cpf", ("cpf", "documento", "doc")),
        ("custo_total", ("custo total", "valor total", "valor pago", "total pago", "investido")),
        ("custo_milheiro", ("custo", "milheiro", "preco medio", "valor medio", "cpm")),
        ("reservado", ("reservad", "bloquead")),
        ("saldo", ("saldo", "pontos", "milhas", "quantidade", "qtd")),
        ("numero", ("numero", "nº", "n°", "matricula", "cartao", "fidelidade n")),
        ("programa", ("programa", "companhia", "cia", "fidelidade", "plano")),
        ("observacoes", ("observ", "obs", "anotac", "coment")),
        ("nome", ("nome", "colaborador", "fidelizado", "titular", "cliente")),
    ],
    "fornecedores": [
        ("documento", ("cpf", "cnpj", "documento", "doc")),
        ("email", ("e-mail", "email")),
        ("telefone", ("telefone", "celular", "whatsapp", "whats", "fone", "contato")),
        ("pix", ("pix",)),
        ("dias_pagamento", ("prazo", "dias")),
        ("observacoes", ("observ", "obs", "anotac", "coment")),
        ("nome", ("nome", "fornecedor", "razao social", "empresa")),
    ],
    "agencias": [
        ("documento", ("cpf", "cnpj", "documento", "doc")),
        ("email_financeiro", ("e-mail financeiro", "email financeiro", "financeiro")),
        ("email_comercial", ("e-mail", "email")),
        ("telefone2", ("telefone 2", "telefone2", "fone 2", "celular 2")),
        ("telefone", ("telefone", "celular", "whatsapp", "whats", "fone", "contato")),
        ("franquia", ("franquia",)),
        ("tipo_negocio", ("tipo de negocio", "negocio")),
        ("atividades", ("atividade",)),
        ("ativo", ("ativo", "status", "situacao")),
        ("razao_social", ("razao social", "nome completo")),
        ("data_fundacao", ("fundacao", "nascimento")),
        *_ENDERECO,
        ("site", ("site",)),
        ("porte", ("porte",)),
        ("perfil", ("perfil",)),
        ("consolidadora", ("consolidadora",)),
        ("observacoes", ("observ", "obs", "anotac", "coment")),
        ("nome", ("agencia", "nome", "fantasia")),
    ],
}

OBRIGATORIAS = {"pessoas": ("nome",), "contas": ("programa",), "fornecedores": ("nome",), "agencias": ("nome",)}

NOMES_CAMPOS = {
    "perfil": "Perfil", "cpf": "CPF", "email": "E-mail", "telefone": "Telefone", "pix": "PIX",
    "classificacao": "Classificação", "consolidador": "Indicado por", "ativo": "Situação",
    "observacoes": "Observações", "nome": "Nome", "senha": "Senha", "login": "Login",
    "custo_total": "Custo total (R$)", "custo_milheiro": "Custo do milheiro (R$)", "reservado": "Reservado",
    "saldo": "Saldo", "numero": "Número", "programa": "Programa", "documento": "CPF/CNPJ",
    "dias_pagamento": "Prazo (dias)", "rg": "RG", "data_nascimento": "Nascimento", "cep": "CEP",
    "endereco": "Endereço", "numero_endereco": "Número", "complemento": "Complemento", "bairro": "Bairro",
    "cidade": "Cidade", "estado": "UF", "email_financeiro": "E-mail financeiro", "email_comercial": "E-mail comercial",
    "telefone2": "Telefone 2", "franquia": "Franquia", "tipo_negocio": "Tipo de negócio", "atividades": "Atividades",
    "razao_social": "Razão social", "data_fundacao": "Fundação", "site": "Site", "porte": "Porte",
    "consolidadora": "Consolidadora", "token": "Token", "email_programa": "E-mail no programa",
    "codigo_conta": "Código da conta",
}

# Colunas das planilhas modelo de cada tipo
MODELOS = {
    "pessoas": (["Nome", "CPF", "Telefone", "E-mail", "Chave PIX", "Perfil", "Classificação", "Indicado por",
                 "Situação", "Observações"],
                [["Ana Paula Souza", "529.982.247-25", "(85) 98888-7777", "ana@email.com", "52998224725",
                  "FIDELIZADO", "parceiro", "", "ATIVO", ""],
                 ["João Pereira", "", "(88) 99712-3344", "", "", "CLIENTE", "", "", "ATIVO", "comprou FOR-GRU"]]),
    "contas": (["Nome", "CPF", "Programa", "Número", "Login", "Senha", "Saldo", "Custo do milheiro", "Reservado",
                "Observações"],
               [["Ana Paula Souza", "529.982.247-25", "SMILES", "123456789", "52998224725", "senha-da-conta",
                 "45000", "17,50", "0", ""],
                ["Ana Paula Souza", "529.982.247-25", "LATAM_PASS", "", "", "", "12000", "", "", "sem custo informado"]]),
    "fornecedores": (["Nome", "CPF/CNPJ", "Telefone", "E-mail", "Chave PIX", "Prazo de pagamento (dias)",
                      "Observações"],
                     [["Balcão Milhas Nordeste", "12.345.678/0001-90", "(85) 99123-4567", "", "pix@balcao.com", "2", ""]]),
    "agencias": (["Agência", "Nome completo ou razão social", "CPF ou CNPJ", "Telefone 1", "Telefone 2",
                  "E-mail comercial", "E-mail financeiro", "Franquia", "Tipo de negócio", "Cidade", "Estado", "Status",
                  "Observação"],
                 [["Viaje Bem", "Viaje Bem Turismo LTDA", "11.222.333/0001-81", "(85) 3333-4444", "",
                   "contato@viajebem.com", "financeiro@viajebem.com", "", "B2B", "Fortaleza", "CE", "ATIVO", ""]]),
}

_NAO = {"nao", "n", "no", "false", "0", "inativo", "inativa", "bloqueado", "bloqueada", "desativado", "desativada",
        "excluido", "cancelado", "suspenso", "suspensa"}


@dataclass
class LinhaManager:
    numero: int
    destino: str = ""  # colaborador, cliente, conta, fornecedor
    acao: str = ""  # criar, atualizar, ignorar, erro
    dados: dict = field(default_factory=dict)
    mensagens: list[str] = field(default_factory=list)
    alvo_id: int | None = None  # registro já existente

    @property
    def valida(self) -> bool:
        return self.acao in ("criar", "atualizar")


# ---------------------------------------------------------------- leitura


def _mapear(cabecalhos: list[str], tipo: str) -> dict[str, int]:
    mapa: dict[str, int] = {}
    for indice, cab in enumerate(cabecalhos):
        norm = _normalizar(str(cab or ""))
        if not norm:
            continue
        for campo, chaves in REGRAS[tipo]:
            if any(_contem(norm, ch) for ch in chaves):
                mapa.setdefault(campo, indice)
                break
    return mapa


def _linhas_numeradas(conteudo: bytes, nome_arquivo: str) -> list[tuple[int, list]]:
    """Linhas não vazias com o número que têm no arquivo (para a conferência apontar a linha certa)."""
    nome = nome_arquivo.lower()
    if nome.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        brutas = [list(r) for r in wb.active.iter_rows(values_only=True)]
        wb.close()
    elif nome.endswith((".csv", ".txt")):
        texto = None
        for codificacao in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                texto = conteudo.decode(codificacao)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialeto = csv.Sniffer().sniff(texto[:5000], delimiters=",;\t")
        except csv.Error:
            dialeto = csv.excel
        brutas = list(csv.reader(io.StringIO(texto), dialeto))
    else:
        raise ValueError("Formato não suportado. Envie um arquivo .xlsx (Excel/Google Planilhas) ou .csv.")
    linhas = [(n, l) for n, l in enumerate(brutas, start=1) if any(_texto(c) for c in l)]
    if not linhas:
        raise ValueError("A planilha está vazia.")
    return linhas


def ler(conteudo: bytes, nome_arquivo: str, tipo: str) -> tuple[list[str], dict[str, int], list[tuple[int, list]]]:
    """Lê a planilha e acha a linha de títulos (a que tiver mais colunas reconhecidas entre as 10 primeiras)."""
    linhas = _linhas_numeradas(conteudo, nome_arquivo)
    melhor, melhor_mapa = 0, {}
    for i, (_, linha) in enumerate(linhas[:10]):
        mapa = _mapear([_texto(c) for c in linha], tipo)
        if len(mapa) > len(melhor_mapa):
            melhor, melhor_mapa = i, mapa
    titulos = [_texto(c) for c in linhas[melhor][1]]
    return titulos, melhor_mapa, linhas[melhor + 1:]


def _numero(valor) -> float | None:
    if valor is None or isinstance(valor, (datetime, date)):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = _texto(valor).lower().replace("r$", "").replace(" ", "")
    texto = re.sub(r"[^\d.,-]", "", texto)
    if not texto or texto in ("-", ".", ","):
        return None
    if "," in texto or re.fullmatch(r"-?\d{1,3}(\.\d{3})+", texto):
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _chave_nome(nome: str) -> str:
    return " ".join(_normalizar(nome or "").split())


def _mesmo_nome(achado, documento: str | None, campo: str):
    """Cadastro achado pelo nome só vale se não tiver outro CPF/CNPJ (homônimos são pessoas diferentes)."""
    if achado is None:
        return None
    doc_existente = getattr(achado, campo, None)
    if documento and doc_existente and doc_existente != documento:
        return None
    return achado


def _sim(valor) -> bool:
    return _normalizar(_texto(valor)) in ("sim", "s", "yes", "true", "1", "x", "verdadeiro")


def _cep(valor) -> str | None:
    d = so_digitos(_texto(valor))[:8]
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else (d or None)


def _ler_endereco(celula, bruta) -> dict:
    return {"cep": _cep(celula(bruta, "cep")), "endereco": _texto(celula(bruta, "endereco"))[:200] or None,
            "numero_endereco": _texto(celula(bruta, "numero_endereco"))[:20] or None,
            "complemento": _texto(celula(bruta, "complemento"))[:80] or None,
            "bairro": _texto(celula(bruta, "bairro"))[:80] or None, "cidade": _texto(celula(bruta, "cidade"))[:80] or None,
            "estado": _texto(celula(bruta, "estado")).upper()[:2] or None}


def _ativo(valor) -> bool:
    texto = _normalizar(_texto(valor))
    return not (texto in _NAO or texto.startswith(("inativ", "bloque", "desativ", "suspen")))


# ---------------------------------------------------------------- conferência


def analisar(db: Session, tipo: str, mapa: dict[str, int], corpo: list[tuple[int, list]],
             perfil_padrao: str = "colaborador") -> list[LinhaManager]:
    if tipo not in TIPOS:
        raise ValueError("Tipo de planilha desconhecido.")

    def celula(linha, campo):
        i = mapa.get(campo)
        return linha[i] if i is not None and i < len(linha) else None

    itens: list[LinhaManager] = []
    vistos: dict[tuple, LinhaManager] = {}
    colaboradores = db.scalars(select(Colaborador)).all()
    por_cpf = {c.cpf: c for c in colaboradores if c.cpf}
    por_nome = {_chave_nome(c.nome): c for c in colaboradores}
    programas = list(db.scalars(select(ProgramaMilhas.nome)))

    # cadastros que já existem, lidos uma vez só (a planilha pode ter milhares de linhas)
    def _indice(objetos, campo):
        indice = {}
        for o in objetos:
            chave = getattr(o, campo) if campo != "nome" else _chave_nome(o.nome)
            if chave:
                indice.setdefault(chave, o)
        return indice

    existentes: dict[str, dict] = {}
    if tipo == "pessoas":
        clientes = db.scalars(select(Cliente).order_by(Cliente.id)).all()
        existentes = {"cli_tel": _indice(clientes, "telefone"), "cli_doc": _indice(clientes, "documento")}
    elif tipo == "agencias":
        agencias = db.scalars(select(Agencia).order_by(Agencia.id)).all()
        existentes = {"doc": _indice(agencias, "documento"), "nome": _indice(agencias, "nome"),
                      "franquias": {_chave_nome(f.nome) for f in db.scalars(select(Franquia))}}
    elif tipo == "fornecedores":
        fornecedores = db.scalars(select(Fornecedor).order_by(Fornecedor.id)).all()
        existentes = {"doc": _indice(fornecedores, "documento"), "nome": _indice(fornecedores, "nome")}

    for numero, bruta in corpo:
        if not any(_texto(c) for c in bruta):
            continue
        item = LinhaManager(numero=numero)
        d = item.dados

        if tipo == "pessoas":
            d["nome"] = ajustar_nome(" ".join(_texto(celula(bruta, "nome")).split()))
            d["cpf"] = so_digitos(_texto(celula(bruta, "cpf"))) or None
            tel = so_digitos(_texto(celula(bruta, "telefone")))
            d["telefone"] = normalizar_telefone(tel) if len(tel) >= 10 else None
            d["email"] = _texto(celula(bruta, "email")) or None
            d["pix"] = _texto(celula(bruta, "pix")) or None
            d["classificacao"] = _texto(celula(bruta, "classificacao")) or None
            d["consolidador"] = _texto(celula(bruta, "consolidador")) or None
            d["observacoes"] = _texto(celula(bruta, "observacoes")) or None
            d["ativo"] = _ativo(celula(bruta, "ativo")) if "ativo" in mapa else True
            d["rg"] = _texto(celula(bruta, "rg"))[:20] or None
            d["data_nascimento"] = _data(celula(bruta, "data_nascimento"))
            d.update(_ler_endereco(celula, bruta))
            perfil = _normalizar(_texto(celula(bruta, "perfil")))
            item.destino = ("cliente" if "client" in perfil else "colaborador" if perfil else perfil_padrao)
            if not d["nome"]:
                item.acao = "erro"
                item.mensagens.append("sem nome")
            elif d["cpf"] and len(d["cpf"]) not in (11, 14):
                item.mensagens.append(f"CPF “{_texto(celula(bruta, 'cpf'))}” com tamanho errado: ignorado")
                d["cpf"] = None
            if item.acao != "erro" and item.destino == "colaborador":
                chaves = [("col", "cpf", d["cpf"]), ("col", "nome", _chave_nome(d["nome"]))]
                existente = por_cpf.get(d["cpf"]) if d["cpf"] else None
                existente = existente or _mesmo_nome(por_nome.get(_chave_nome(d["nome"])), d["cpf"], "cpf")
            elif item.acao != "erro":
                if not d["telefone"]:
                    item.acao = "erro"
                    item.mensagens.append("cliente sem telefone (o cadastro de clientes precisa do WhatsApp)")
                chaves = [("cli", "tel", d["telefone"]), ("cli", "doc", d["cpf"])]
                existente = existentes["cli_tel"].get(d["telefone"]) if d["telefone"] else None
                if not existente and d["cpf"]:
                    existente = existentes["cli_doc"].get(d["cpf"])

        elif tipo == "contas":
            d["nome"] = ajustar_nome(" ".join(_texto(celula(bruta, "nome")).split()))
            d["cpf"] = so_digitos(_texto(celula(bruta, "cpf"))) or None
            d["programa"] = nome_programa(_texto(celula(bruta, "programa")), programas)
            d["numero"] = _texto(celula(bruta, "numero")) or None
            d["login"] = _texto(celula(bruta, "login")) or None
            d["senha"] = _texto(celula(bruta, "senha")) or None
            saldo = _numero(celula(bruta, "saldo"))
            d["saldo"] = int(round(saldo)) if saldo else 0
            d["reservado"] = int(round(_numero(celula(bruta, "reservado")) or 0))
            total, milheiro = _numero(celula(bruta, "custo_total")), _numero(celula(bruta, "custo_milheiro"))
            if milheiro is not None and 0 < milheiro < 1:
                milheiro = round(milheiro * 1000, 4)  # veio o custo por milha
            d["custo_total"] = total if total and total > 0 else None
            d["custo_milheiro"] = milheiro if milheiro and milheiro > 0 else None
            d["observacoes"] = _texto(celula(bruta, "observacoes")) or None
            d["email_programa"] = _texto(celula(bruta, "email_programa"))[:160] or None
            d["codigo_conta"] = _texto(celula(bruta, "codigo_conta"))[:60] or None
            d["token"] = _texto(celula(bruta, "token")) or None
            item.destino = "conta"
            if not d["programa"]:
                item.acao = "erro"
                item.mensagens.append("sem programa")
            if not (d["nome"] or d["cpf"]):
                item.acao = "erro"
                item.mensagens.append("sem nome nem CPF do colaborador")
            if d["saldo"] < 0:
                item.mensagens.append("saldo negativo: entra como zero")
                d["saldo"] = 0
            col = (por_cpf.get(d["cpf"]) if d["cpf"] else None) or (
                _mesmo_nome(por_nome.get(_chave_nome(d["nome"])), d["cpf"], "cpf") if d["nome"] else None)
            d["colaborador_id"] = col.id if col else None
            if not col and item.acao != "erro":
                if not d["nome"]:
                    item.acao = "erro"
                    item.mensagens.append("colaborador não encontrado pelo CPF e a linha não tem nome para cadastrar")
                else:
                    item.mensagens.append("colaborador novo: será cadastrado")
            chaves = [("conta", "cpf", d["cpf"], d["programa"]),
                      ("conta", "nome", _chave_nome(d["nome"]), d["programa"]) if d["nome"] else None]
            existente = None
            if col and d["programa"]:
                existente = db.scalar(select(ContaPrograma).where(ContaPrograma.colaborador_id == col.id,
                                                                  ContaPrograma.programa == d["programa"]))
            if existente:
                tem_movimento = db.scalar(select(Lancamento.id).where(Lancamento.conta_id == existente.id,
                                                                      Lancamento.status == "ativo").limit(1))
                if d["saldo"] and tem_movimento:
                    d["saldo_ignorado"] = True
                    if existente.saldo != d["saldo"]:
                        item.mensagens.append(
                            f"conta já tem lançamentos: saldo do sistema ({formatar_pontos(existente.saldo)}) mantido, "
                            f"planilha diz {formatar_pontos(d['saldo'])}")
            if d["programa"] and d["programa"] not in programas and item.acao != "erro":
                item.mensagens.append(f"programa “{d['programa']}” novo: será incluído em Programas e preços")
            if d["saldo"] and not (d["custo_total"] or d["custo_milheiro"]) and item.acao != "erro":
                item.mensagens.append("sem custo: o saldo entra sem custo (custo médio fica em branco)")

        elif tipo == "agencias":
            d["nome"] = " ".join(_texto(celula(bruta, "nome")).split())[:160]
            d["razao_social"] = " ".join(_texto(celula(bruta, "razao_social")).split())[:200] or None
            d["documento"] = so_digitos(_texto(celula(bruta, "documento"))) or None
            for campo in ("telefone", "telefone2"):
                tel = so_digitos(_texto(celula(bruta, campo)))
                d[campo] = normalizar_telefone(tel) if len(tel) >= 10 else None
            for campo in ("email_comercial", "email_financeiro", "site", "porte", "perfil", "observacoes", "atividades"):
                d[campo] = _texto(celula(bruta, campo)) or None
            d["franquia"] = " ".join(_texto(celula(bruta, "franquia")).split()) or None
            negocio = _texto(celula(bruta, "tipo_negocio")).upper()
            d["tipo_negocio"] = next((t for t in ("B2F", "B2C", "B2B") if t in negocio), None)
            d["consolidadora"] = _sim(celula(bruta, "consolidadora")) if "consolidadora" in mapa else None
            d["data_fundacao"] = _data(celula(bruta, "data_fundacao"))
            d["ativo"] = _ativo(celula(bruta, "ativo")) if "ativo" in mapa else True
            d.update(_ler_endereco(celula, bruta))
            item.destino = "agencia"
            if not d["nome"]:
                item.acao = "erro"
                item.mensagens.append("sem nome da agência")
            elif d["documento"] and len(d["documento"]) not in (11, 14):
                item.mensagens.append(f"CPF/CNPJ “{_texto(celula(bruta, 'documento'))}” com tamanho errado: ignorado")
                d["documento"] = None
            chaves = [("ag", "doc", d["documento"]), ("ag", "nome", _chave_nome(d["nome"]))]
            existente = existentes["doc"].get(d["documento"]) if d["documento"] else None
            if not existente and d["nome"]:
                existente = _mesmo_nome(existentes["nome"].get(_chave_nome(d["nome"])), d["documento"], "documento")
            if d["franquia"] and item.acao != "erro" and _chave_nome(d["franquia"]) not in existentes["franquias"]:
                item.mensagens.append(f"franquia “{d['franquia']}” nova: será cadastrada")

        else:  # fornecedores
            d["nome"] = " ".join(_texto(celula(bruta, "nome")).split())
            d["documento"] = so_digitos(_texto(celula(bruta, "documento"))) or None
            tel = so_digitos(_texto(celula(bruta, "telefone")))
            d["telefone"] = normalizar_telefone(tel) if len(tel) >= 10 else None
            d["email"] = _texto(celula(bruta, "email")) or None
            d["pix"] = _texto(celula(bruta, "pix")) or None
            dias = _numero(celula(bruta, "dias_pagamento"))
            d["dias_pagamento"] = int(dias) if dias is not None and dias >= 0 else None
            d["observacoes"] = _texto(celula(bruta, "observacoes")) or None
            item.destino = "fornecedor"
            if not d["nome"]:
                item.acao = "erro"
                item.mensagens.append("sem nome")
            chaves = [("forn", "doc", d["documento"]), ("forn", "nome", _chave_nome(d["nome"]))]
            existente = existentes["doc"].get(d["documento"]) if d["documento"] else None
            if not existente and d["nome"]:
                existente = _mesmo_nome(existentes["nome"].get(_chave_nome(d["nome"])), d["documento"], "documento")

        if item.acao != "erro":
            # a mesma pessoa/conta pode aparecer com e sem CPF: qualquer chave repetida conta
            chaves = [c for c in chaves if c and c[2]]
            doc = d.get("cpf") or d.get("documento")
            anterior = None
            for c in chaves:
                outro = vistos.get(c)
                if outro is None:
                    continue
                doc_outro = outro.dados.get("cpf") or outro.dados.get("documento")
                if c[1] == "nome" and doc and doc_outro and doc != doc_outro:
                    continue  # homônimos com documentos diferentes são pessoas diferentes
                anterior = outro
                break
            if anterior:
                item.acao = "ignorar"
                item.mensagens.append(f"repetida (igual à linha {anterior.numero})")
            else:
                vistos.update({c: item for c in chaves})
                item.acao = "atualizar" if existente else "criar"
                item.alvo_id = existente.id if existente else None
        itens.append(item)
    return itens


# ---------------------------------------------------------------- gravação


def _preencher(obj, dados: dict, campos: tuple[str, ...], sobrescrever: bool) -> None:
    """Copia os campos; em registros existentes, só preenche o que está vazio (não apaga nada)."""
    for campo in campos:
        valor = dados.get(campo)
        if valor in (None, ""):
            continue
        if sobrescrever or getattr(obj, campo) in (None, ""):
            setattr(obj, campo, valor)


def aplicar(db: Session, tipo: str, itens: list[LinhaManager], usuario: str | None = None,
            com_senhas: bool = True) -> dict:
    resumo = {"criados": 0, "atualizados": 0, "ignorados": 0, "erros": 0, "colaboradores": 0, "clientes": 0,
              "contas": 0, "fornecedores": 0, "agencias": 0, "pontos": 0}
    programas = set(db.scalars(select(ProgramaMilhas.nome)))
    for item in itens:
        if item.acao == "erro":
            resumo["erros"] += 1
            continue
        if item.acao == "ignorar":
            resumo["ignorados"] += 1
            continue
        d = item.dados
        novo = item.acao == "criar"
        if item.destino == "colaborador":
            col = Colaborador() if novo else db.get(Colaborador, item.alvo_id)
            _preencher(col, dict(d, numero=d.get("numero_endereco")),
                       ("nome", "cpf", "telefone", "email", "pix", "classificacao", "consolidador", "observacoes", "rg",
                        "data_nascimento", "cep", "endereco", "numero", "complemento", "bairro", "cidade", "estado"),
                       sobrescrever=novo)
            if novo:
                col.ativo = d.get("ativo", True)
                db.add(col)
            resumo["colaboradores"] += 1
        elif item.destino == "cliente":
            cli = Cliente(consentimento=False) if novo else db.get(Cliente, item.alvo_id)
            dados_cli = dict(d, documento=d.get("cpf"))
            _preencher(cli, dados_cli, ("nome", "telefone", "email", "documento", "observacoes", "data_nascimento",
                                        "classificacao"), sobrescrever=novo)
            if novo:
                cli.ativo = d.get("ativo", True)
                db.add(cli)
            resumo["clientes"] += 1
        elif item.destino == "agencia":
            ag = Agencia() if novo else db.get(Agencia, item.alvo_id)
            dados_ag = dict(d, numero=d.get("numero_endereco"))
            if d.get("franquia"):
                franquia = next((f for f in db.scalars(select(Franquia))
                                 if _chave_nome(f.nome) == _chave_nome(d["franquia"])), None)
                if franquia is None:
                    franquia = Franquia(nome=d["franquia"])
                    db.add(franquia)
                    db.flush()
                dados_ag["franquia_id"] = franquia.id
                dados_ag["tipo_negocio"] = d.get("tipo_negocio") or "B2F"
            _preencher(ag, dados_ag, ("nome", "razao_social", "documento", "telefone", "telefone2", "email_comercial",
                                      "email_financeiro", "site", "porte", "perfil", "observacoes", "atividades",
                                      "franquia_id", "tipo_negocio", "consolidadora", "data_fundacao", "cep", "endereco",
                                      "numero", "complemento", "bairro", "cidade", "estado"), sobrescrever=novo)
            if novo:
                ag.ativo = d.get("ativo", True)
                ag.tipo_negocio = ag.tipo_negocio or "B2B"
                db.add(ag)
            resumo["agencias"] += 1
        elif item.destino == "fornecedor":
            forn = Fornecedor() if novo else db.get(Fornecedor, item.alvo_id)
            _preencher(forn, d, ("nome", "documento", "telefone", "email", "pix", "dias_pagamento", "observacoes"),
                       sobrescrever=novo)
            if novo:
                db.add(forn)
            resumo["fornecedores"] += 1
        else:  # conta
            col = db.get(Colaborador, d["colaborador_id"]) if d.get("colaborador_id") else None
            if col is None:
                col = db.scalar(select(Colaborador).where(Colaborador.cpf == d["cpf"])) if d.get("cpf") else None
                if col is None and d.get("nome"):
                    col = _mesmo_nome(next((c for c in db.scalars(select(Colaborador))
                                            if _chave_nome(c.nome) == _chave_nome(d["nome"])), None), d.get("cpf"), "cpf")
            if col is None:
                col = Colaborador(nome=d["nome"], cpf=d.get("cpf") if d.get("cpf") and len(d["cpf"]) == 11 else None)
                db.add(col)
                db.flush()
                resumo["colaboradores"] += 1
            if d["programa"] not in programas:
                db.add(ProgramaMilhas(nome=d["programa"], personalizado=True))
                programas.add(d["programa"])
            conta = None if novo else db.get(ContaPrograma, item.alvo_id)
            if conta is None:
                conta = db.scalar(select(ContaPrograma).where(ContaPrograma.colaborador_id == col.id,
                                                              ContaPrograma.programa == d["programa"]))
            if conta is None:
                conta = ContaPrograma(colaborador_id=col.id, programa=d["programa"])
                db.add(conta)
                db.flush()
            _preencher(conta, d, ("numero", "observacoes", "email_programa", "codigo_conta"), sobrescrever=novo)
            if com_senhas and d.get("token"):
                conta.token_cripto = cifrar(d["token"])
            if d.get("reservado"):
                conta.saldo_reservado = d["reservado"]
            if com_senhas and d.get("login"):
                conta.login_cripto = cifrar(d["login"])
            if com_senhas and d.get("senha"):
                conta.senha_cripto = cifrar(d["senha"])
            if d.get("saldo") and not d.get("saldo_ignorado"):
                valor = d.get("custo_total")
                lancar(db, conta, "saldo_inicial", d["saldo"],
                       valor=valor if valor is not None else (None if d.get("custo_milheiro") else 0.0),
                       preco_milheiro=d.get("custo_milheiro"), observacao="Saldo importado do Manager",
                       usuario=usuario)
                resumo["pontos"] += d["saldo"]
            resumo["contas"] += 1
        resumo["criados" if novo else "atualizados"] += 1
    db.flush()
    return resumo


def planilha_modelo(tipo: str) -> bytes:
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    titulos, exemplos = MODELOS[tipo]
    wb = Workbook()
    ws = wb.active
    ws.title = TIPOS[tipo][:30]
    ws.append(titulos)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1B468F")
    for linha in exemplos:
        ws.append(linha)
    for coluna in ws.columns:
        ws.column_dimensions[coluna[0].column_letter].width = max(14, min(32, max(len(str(c.value or "")) for c in coluna) + 2))
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()
