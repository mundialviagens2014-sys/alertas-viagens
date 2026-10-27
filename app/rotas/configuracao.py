"""Configuração do Manager: tipos de programa e cadastros simples (classificações, consolidadores, grupos de
produtos, lista de preços, markups e franquias), todos com a mesma tela de lista + inclusão + edição."""
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import busca
from ..database import get_db
from ..exportar import Coluna, exportar, quer_planilha, sim_nao
from ..models import ProgramaMilhas, Usuario
from ..models_mvo import (
    CORES,
    TIPOS_NEGOCIO,
    Agencia,
    Classificacao,
    Consolidador,
    ContaPrograma,
    Franquia,
    GrupoProduto,
    Markup,
    PrecoPrograma,
)
from ..mvo.contas import registrar, so_digitos
from ..mvo.precos import sincronizar_programa
from ..permissoes import pode
from ..servicos import limpar_cache_programas, normalizar_telefone, recalcular_matches
from ..web import avisar, checar_exportar, exigir, ip_de, ler_data, ler_inteiro, ler_numero, render, voltar

rotas = APIRouter()
pode_ver = exigir("milhas_ver")
pode_configurar = exigir("milhas_ver", "configurar")


@dataclass
class Campo:
    nome: str
    rotulo: str
    tipo: str = "texto"  # texto, textarea, bool, cor, opcoes, programa, agencia, dinheiro, pct, inteiro, data, doc, tel
    obrigatorio: bool = False
    opcoes: dict[str, str] | None = None
    lista: bool = True  # aparece como coluna da tabela
    ajuda: str = ""
    custos: bool = False  # só para quem vê custos
    padrao: object = None
    milheiro: bool = False  # preço do milheiro: valor menor que R$ 1 foi digitado por milha (0,027 → 27)
    coluna: str = ""  # título curto da coluna na tabela (se não, o rótulo)
    mostrar_chave: bool = False  # opções: a tabela mostra a chave (ex.: B2C) em vez do nome completo

    @property
    def titulo_coluna(self) -> str:
        return self.coluna or self.rotulo


@dataclass
class Cadastro:
    chave: str
    modelo: type
    titulo: str
    singular: str
    sub: str
    campos: list[Campo]
    ordem: tuple = ()
    filtro: dict = field(default_factory=dict)
    permissao_editar: str = "configurar"
    unico: tuple[str, ...] = ()  # campos que não podem repetir
    depois: Callable | None = None  # chamado depois de salvar/excluir (db, item)
    dica: str = ""
    feminino: bool = False

    @property
    def o(self) -> str:
        return "a" if self.feminino else "o"


def _depois_preco(db: Session, item: PrecoPrograma) -> None:
    sincronizar_programa(db, item.programa)
    recalcular_matches(db)


CADASTROS: dict[str, Cadastro] = {c.chave: c for c in [
    Cadastro(
        "precos", PrecoPrograma, "Lista de preços", "preço",
        "Preço do milheiro de cada programa por tipo de negócio. Sem agência é o preço geral; com agência, o preço "
        "especial dela.",
        [Campo("programa", "Programa", "programa", True), Campo("agencia_id", "Agência", "agencia"),
         Campo("tipo_negocio", "Tipo de negócio", "opcoes", True, TIPOS_NEGOCIO, padrao="B2C", coluna="Tipo",
               mostrar_chave=True),
         Campo("limite_pontos", "Limite de pontos", "inteiro", ajuda="0 ou vazio = sem limite"),
         Campo("preco_venda", "Preço de venda (R$ o milheiro)", "dinheiro", True, milheiro=True, coluna="Venda / mil"),
         Campo("preco_compra", "Preço de compra (R$ o milheiro)", "dinheiro", custos=True, milheiro=True,
               coluna="Compra / mil"),
         Campo("ativo", "Ativo", "bool", padrao=True)],
        ordem=(PrecoPrograma.programa, PrecoPrograma.agencia_id.is_not(None), PrecoPrograma.tipo_negocio),
        unico=("programa", "agencia_id", "tipo_negocio"), depois=_depois_preco,
        dica="O preço B2C geral converte as promoções em milhas dos alertas em reais (milhas × preço ÷ 1.000)."),
    Cadastro(
        "markups-programa", Markup, "Markups de programa", "markup de programa",
        "Acréscimo sobre o preço das emissões em milhas de cada programa: taxa (%) e/ou valor fixo. "
        "Sem agência vale para todas.",
        [Campo("nome", "Programa", "programa", True), Campo("agencia_id", "Agência", "agencia"),
         Campo("taxa_pct", "Taxa (%)", "pct"), Campo("valor", "Valor fixo (R$)", "dinheiro"),
         Campo("ativo", "Ativo", "bool", padrao=True)],
        ordem=(Markup.nome, Markup.agencia_id.is_not(None)), filtro={"alvo": "programa"},
        unico=("nome", "agencia_id")),
    Cadastro(
        "markups-companhia", Markup, "Markups de companhia aérea", "markup de companhia",
        "Acréscimo sobre tarifas pagas (sem milhas) de cada companhia aérea: taxa (%) e/ou valor fixo.",
        [Campo("nome", "Companhia", "texto", True, ajuda="ex.: LATAM, GOL, AZUL"),
         Campo("agencia_id", "Agência", "agencia"), Campo("taxa_pct", "Taxa (%)", "pct"),
         Campo("valor", "Valor fixo (R$)", "dinheiro"), Campo("ativo", "Ativo", "bool", padrao=True)],
        ordem=(Markup.nome, Markup.agencia_id.is_not(None)), filtro={"alvo": "companhia"},
        unico=("nome", "agencia_id")),
    Cadastro(
        "grupos-produto", GrupoProduto, "Grupos de produtos", "grupo de produto",
        "Agrupam os tipos de programa (ex.: Programa, Hotel, Seguro).",
        [Campo("nome", "Grupo de produto", "texto", True),
         Campo("requer_aerea", "Requer aérea?", "bool"),
         Campo("tipo", "Tipo", "opcoes", False, {"PROGRAMA": "Programa", "HOTEL": "Hotel", "SEGURO": "Seguro",
                                                  "OUTRO": "Outro"}, padrao="PROGRAMA"),
         Campo("ativo", "Ativo", "bool", padrao=True)],
        ordem=(GrupoProduto.nome,), unico=("nome",)),
    Cadastro(
        "classificacoes", Classificacao, "Classificações", "classificação",
        "Etiquetas coloridas para clientes e colaboradores (ex.: VIP, parceiro fixo, novo).",
        [Campo("nome", "Nome", "texto", True), Campo("cor", "Cor", "cor", padrao="azul"),
         Campo("ativo", "Ativa", "bool", padrao=True)],
        ordem=(Classificacao.nome,), unico=("nome",), feminino=True),
    Cadastro(
        "consolidadores", Consolidador, "Consolidadores", "consolidador",
        "Grupos de fidelizados: quem indica ou reúne colaboradores (aparece no cadastro do colaborador).",
        [Campo("nome", "Nome", "texto", True), Campo("ativo", "Ativo", "bool", padrao=True)],
        ordem=(Consolidador.nome,), unico=("nome",)),
    Cadastro(
        "franquias", Franquia, "Franquias", "franquia",
        "Franquias da agência. As agências ligadas a uma franquia compram pelo preço B2F.",
        [Campo("nome", "Franquia", "texto", True), Campo("razao_social", "Razão social", lista=False),
         Campo("documento", "CPF / CNPJ", "doc"), Campo("responsavel", "Responsável"),
         Campo("telefone", "Telefone", "tel"), Campo("email", "E-mail"), Campo("cidade", "Cidade"),
         Campo("estado", "UF", ajuda="ex.: CE"), Campo("observacoes", "Observações", "textarea", lista=False),
         Campo("ativo", "Ativa", "bool", padrao=True)],
        ordem=(Franquia.nome,), permissao_editar="milhas_operar", unico=("nome",), feminino=True),
]}


def _ler(campo: Campo, form) -> object:
    bruto = form.get(campo.nome)
    t = campo.tipo
    if t == "bool":
        return bruto == "true"
    texto = (bruto or "").strip() if isinstance(bruto, str) else ""
    if t in ("texto", "textarea", "programa"):
        if campo.nome == "estado":
            return texto.upper()[:2] or None
        return texto or None
    if t == "cor":
        return texto if texto in CORES else None
    if t == "opcoes":
        return texto if texto in (campo.opcoes or {}) else None
    if t == "agencia":
        return ler_inteiro(texto) or None
    if t in ("dinheiro", "pct"):
        numero = ler_numero(texto)
        if campo.milheiro and numero is not None and 0 < numero < 1:
            numero = round(numero * 1000, 4)
        return numero
    if t == "inteiro":
        return ler_inteiro(texto)
    if t == "data":
        return ler_data(texto)
    if t == "doc":
        return so_digitos(texto) or None
    if t == "tel":
        return normalizar_telefone(texto) if so_digitos(texto) else None
    return texto or None


def _coluna_planilha(c: Campo, agencias_por_id: dict) -> Coluna:
    """Coluna da planilha com o mesmo conteúdo que a tela mostra."""
    from .. import colunas as col_

    def valor(item):
        v = getattr(item, c.nome)
        if c.tipo == "bool":
            return sim_nao(v)
        if c.tipo == "opcoes":
            return (c.opcoes or {}).get(v, v) if not c.mostrar_chave else v
        if c.tipo == "agencia":
            return agencias_por_id[v].nome if v in agencias_por_id else "Todas (geral)"
        if c.tipo == "doc":
            return col_.documento(v)
        if c.tipo == "tel":
            return col_.telefone(v)
        return v

    formato = {"dinheiro": "dinheiro", "pct": "pct", "inteiro": "pontos", "data": "data"}.get(c.tipo, "")
    return Coluna(c.rotulo, valor, formato)


def _validar(db: Session, cad: Cadastro, dados: dict, exceto: int | None = None) -> str | None:
    for c in cad.campos:
        if c.obrigatorio and dados.get(c.nome) in (None, ""):
            return f"Preencha “{c.rotulo}”."
    if cad.unico:
        consulta = select(cad.modelo)
        for chave, valor in cad.filtro.items():
            consulta = consulta.where(getattr(cad.modelo, chave) == valor)
        for nome in cad.unico:
            coluna = getattr(cad.modelo, nome)
            valor = dados.get(nome)
            if valor is None:
                consulta = consulta.where(coluna.is_(None))
            elif isinstance(valor, str):
                consulta = consulta.where(func.lower(coluna) == valor.lower())
            else:
                consulta = consulta.where(coluna == valor)
        if exceto:
            consulta = consulta.where(cad.modelo.id != exceto)
        if db.scalar(consulta.limit(1)):
            return f"Já existe um{'a' if cad.feminino else ''} {cad.singular} igual cadastrad{cad.o}."
    return None


def _cadastro(chave: str) -> Cadastro:
    cad = CADASTROS.get(chave)
    if not cad:
        raise HTTPException(404)
    return cad


def _filtrar(consulta, cad: Cadastro, q: str, ver: str, programa: str):
    """Busca e filtros das listas de configuração (os campos de texto de cada cadastro)."""
    textos, digitos = [], []
    for c in cad.campos:
        coluna = getattr(cad.modelo, c.nome)
        if c.tipo in ("texto", "textarea", "programa", "opcoes"):
            textos.append(coluna)
        elif c.tipo in ("doc", "tel"):
            digitos.append(coluna)
        elif c.tipo == "agencia":
            textos.append(lambda termo, coluna=coluna: coluna.in_(
                select(Agencia.id).where(busca.sem_acento_sql(Agencia.nome).contains(termo, autoescape=True))))
    if textos or digitos:
        consulta = busca.aplicar(consulta, q, textos, digitos)
    if hasattr(cad.modelo, "ativo") and ver in ("ativos", "inativos"):
        consulta = consulta.where(cad.modelo.ativo.is_(False) if ver == "inativos" else cad.modelo.ativo.is_not(False))
    campo_programa = next((c.nome for c in cad.campos if c.tipo == "programa"), None)
    if programa and campo_programa:
        consulta = consulta.where(getattr(cad.modelo, campo_programa) == programa)
    return consulta


def _contexto(db: Session, cad: Cadastro, q: str = "", ver: str = "", programa: str = "") -> dict:
    consulta = select(cad.modelo)
    for chave, valor in cad.filtro.items():
        consulta = consulta.where(getattr(cad.modelo, chave) == valor)
    consulta = _filtrar(consulta, cad, q, ver, programa)
    if cad.ordem:
        consulta = consulta.order_by(*cad.ordem)
    agencias = db.scalars(select(Agencia).order_by(Agencia.nome)).all()
    sem_preco = []
    if cad.chave == "precos":
        sem_preco = db.scalars(select(ProgramaMilhas.nome).where(
            ProgramaMilhas.ativo.is_not(False), ProgramaMilhas.preco_b2c.is_(None)).order_by(ProgramaMilhas.nome)).all()
    return {
        "cad": cad, "itens": db.scalars(consulta).all(),
        "programas": sorted(db.scalars(select(ProgramaMilhas.nome)), key=str.lower),
        "agencias": agencias, "agencias_por_id": {a.id: a for a in agencias}, "cores": CORES, "sem_preco": sem_preco,
        "q": q, "ver": ver, "programa": programa, "filtrando": bool(q.strip() or ver or programa),
        "tem_ativo": hasattr(cad.modelo, "ativo"), "tem_programa": any(c.tipo == "programa" for c in cad.campos),
    }


def _registrar_rotas(cad: Cadastro) -> None:
    caminho = "/" + cad.chave

    def pode_editar_dep(request: Request, db: Session = Depends(get_db)):
        return exigir("milhas_ver", cad.permissao_editar)(request, db)

    @rotas.get(caminho, response_class=HTMLResponse, name=f"lista_{cad.chave}")
    def lista(request: Request, q: str = "", ver: str = "", programa: str = "", formato: str = "",
              db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
        contexto = _contexto(db, cad, q, ver, programa)
        if quer_planilha(formato):
            checar_exportar(usuario)
            campos = [c for c in cad.campos if not (c.custos and not pode(usuario, "custos"))]
            colunas = [_coluna_planilha(c, contexto["agencias_por_id"]) for c in campos]
            return exportar(request, db, usuario, cad.titulo, colunas, contexto["itens"])
        return render(request, "mvo/cadastro.html", usuario, pagina=cad.chave,
                      pode_editar=pode(usuario, "milhas_ver", cad.permissao_editar), **contexto)

    @rotas.post(caminho + "/novo", name=f"novo_{cad.chave}")
    async def novo(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_editar_dep)):
        form = await request.form()
        dados = {c.nome: _ler(c, form) for c in cad.campos if not (c.custos and not pode(usuario, "custos"))}
        erro = _validar(db, cad, dados)
        if erro:
            avisar(request, erro, "erro")
            return voltar(caminho)
        item = cad.modelo(**dados, **cad.filtro)
        db.add(item)
        db.flush()
        if cad.depois:
            cad.depois(db, item)
        registrar(db, usuario, "configuracao", f"{cad.chave}:{item.id}", f"{cad.titulo}: incluiu {_descrever(cad, item)}",
                  ip_de(request))
        db.commit()
        avisar(request, f"{cad.singular.capitalize()} incluíd{cad.o}.")
        return voltar(caminho)

    @rotas.post(caminho + "/{item_id}", name=f"editar_{cad.chave}")
    async def editar(item_id: int, request: Request, db: Session = Depends(get_db),
                     usuario: Usuario = Depends(pode_editar_dep)):
        item = db.get(cad.modelo, item_id)
        if not item or any(getattr(item, k) != v for k, v in cad.filtro.items()):
            raise HTTPException(404)
        form = await request.form()
        dados = {c.nome: _ler(c, form) for c in cad.campos if not (c.custos and not pode(usuario, "custos"))}
        erro = _validar(db, cad, dados, exceto=item.id)
        if erro:
            avisar(request, erro, "erro")
            return voltar(caminho)
        programa_antes = getattr(item, "programa", None)
        for chave, valor in dados.items():
            setattr(item, chave, valor)
        db.flush()
        if cad.depois:
            cad.depois(db, item)
            if programa_antes and programa_antes != getattr(item, "programa", None):
                sincronizar_programa(db, programa_antes)
        registrar(db, usuario, "configuracao", f"{cad.chave}:{item.id}", f"{cad.titulo}: alterou {_descrever(cad, item)}",
                  ip_de(request))
        db.commit()
        avisar(request, "Alteração salva.")
        return voltar(caminho)

    @rotas.post(caminho + "/{item_id}/excluir", name=f"excluir_{cad.chave}")
    def excluir(item_id: int, request: Request, db: Session = Depends(get_db),
                usuario: Usuario = Depends(pode_editar_dep)):
        item = db.get(cad.modelo, item_id)
        if not item or any(getattr(item, k) != v for k, v in cad.filtro.items()):
            raise HTTPException(404)
        descricao = _descrever(cad, item)
        programa = getattr(item, "programa", None)
        db.delete(item)
        db.flush()
        if cad.chave == "precos" and programa:
            sincronizar_programa(db, programa)
            recalcular_matches(db)
        registrar(db, usuario, "configuracao", f"{cad.chave}:{item_id}", f"{cad.titulo}: excluiu {descricao}", ip_de(request))
        db.commit()
        avisar(request, f"{cad.singular.capitalize()} excluíd{cad.o}.")
        return voltar(caminho)


def _descrever(cad: Cadastro, item) -> str:
    partes = [str(getattr(item, c.nome)) for c in cad.campos[:3]
              if c.tipo not in ("bool", "agencia") and getattr(item, c.nome) not in (None, "")]
    return " · ".join(partes)[:150]


for _cad in CADASTROS.values():
    _registrar_rotas(_cad)


# ---------------------------------------------------------------- tipos de programa


OPCOES_PROGRAMA = {
    "tipo_uso": ["Programa", "Hotel", "Locadora", "Outro"],
    "numeracao": ["Informado", "Automático"],
    "tipo_parceiro": ["Fidelizado", "Banco / cartão", "Fornecedor", "Agência"],
    "unidade": ["Milhas", "Pontos"],
    "moeda": ["Real (BRL)", "Dólar (USD)", "Euro (EUR)"],
}
FLAGS_PROGRAMA = [
    ("necessita_login", "Programa necessita de login?", "As contas pedem login e senha (guardados com criptografia)."),
    ("controla_saldo", "Controla saldo?", "A emissão confere se a conta tem pontos suficientes."),
    ("controla_emissoes", "Programa controla emissões?", "Conta o limite de passageiros (CPFs) por período."),
    ("cancelamento_devolve", "Cancelamento de emissão devolve passageiros?",
     "Se não, o passageiro de uma emissão cancelada continua ocupando o CPF no período."),
    ("exige_descricao", "Programa exige descrição?", "A emissão só é registrada com a observação preenchida."),
    ("exige_senha_resgate", "Programa exige senha de resgate?", ""),
    ("precisa_token", "Precisa de token?", ""),
    ("saldo_implantacao", "Programa tem saldo na implantação?", ""),
    ("transferencia_cruzada", "Permite transferência cruzada?", ""),
    ("aceita_interline", "Aceita emissão interline?", ""),
    ("pode_ofertar", "Programa pode ser ofertado?", ""),
    ("conciliacao", "Tem conciliação habilitada?", ""),
]


_FLAGS_LIGADAS = {"necessita_login", "controla_saldo", "controla_emissoes", "cancelamento_devolve"}  # vazio = sim


def col_situacao(ativo) -> str:
    return "Inativo" if ativo is False else "Ativo"


@rotas.get("/programas", response_class=HTMLResponse)
def lista_tipos(request: Request, q: str = "", ver: str = "", tipo: str = "", formato: str = "",
                db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    consulta = select(ProgramaMilhas).order_by(ProgramaMilhas.nome)
    consulta = busca.aplicar(consulta, q, [ProgramaMilhas.nome, ProgramaMilhas.nome_manager, ProgramaMilhas.cia_aerea,
                                           ProgramaMilhas.observacoes])
    if ver == "ativos":
        consulta = consulta.where(ProgramaMilhas.ativo.is_not(False))
    elif ver == "inativos":
        consulta = consulta.where(ProgramaMilhas.ativo.is_(False))
    elif ver == "sem_preco":
        consulta = consulta.where(ProgramaMilhas.ativo.is_not(False), ProgramaMilhas.preco_b2c.is_(None))
    if tipo:
        consulta = consulta.where(ProgramaMilhas.tipo_uso == tipo)
    programas = db.scalars(consulta).all()
    contas = dict(db.execute(select(ContaPrograma.programa, func.count(ContaPrograma.id))
                             .group_by(ContaPrograma.programa)).all())
    grupos = {g.id: g.nome for g in db.scalars(select(GrupoProduto))}
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [
            Coluna("Programa", lambda p: p.nome), Coluna("Tipo de uso", lambda p: p.tipo_uso or "Programa"),
            Coluna("Grupo", lambda p: grupos.get(p.grupo_produto_id)), Coluna("Cia aérea", lambda p: p.cia_aerea),
            Coluna("Numeração", lambda p: p.numeracao), Coluna("Tipo do parceiro", lambda p: p.tipo_parceiro),
            Coluna("Unidade", lambda p: p.unidade or "Milhas"), Coluna("Moeda", lambda p: p.moeda),
            Coluna("Máximo de CPFs", lambda p: p.limite_cpfs),
            Coluna("Período dos CPFs", lambda p: ("Últimos 12 meses" if p.janela_cpfs == "12m" else "Por ano")
                   if p.limite_cpfs else None),
            *[Coluna(rotulo, (lambda campo: lambda p: sim_nao(getattr(p, campo) is not False
                                                              if campo in _FLAGS_LIGADAS else getattr(p, campo)))(campo))
              for campo, rotulo, _ in FLAGS_PROGRAMA],
            Coluna("Preço B2C geral (milheiro)", lambda p: p.preco_b2c, "dinheiro"),
            Coluna("Preço B2B geral (milheiro)", lambda p: p.preco_b2b, "dinheiro"),
            Coluna("Preço B2F geral (milheiro)", lambda p: p.preco_b2f, "dinheiro"),
            Coluna("Acréscimo fixo nos alertas", lambda p: p.taxa_fixa, "dinheiro"),
            Coluna("Contas", lambda p: contas.get(p.nome, 0)), Coluna("Situação", lambda p: col_situacao(p.ativo)),
            Coluna("Observações", lambda p: p.observacoes),
        ]
        return exportar(request, db, usuario, "Tipos de programa", colunas, programas)
    tipos = sorted({t for t in db.scalars(select(ProgramaMilhas.tipo_uso).distinct()) if t})
    return render(request, "mvo/programas.html", usuario, programas=programas, contas=contas, grupos=grupos,
                  pode_editar=pode(usuario, "configurar"), q=q, ver=ver, tipo=tipo, tipos=tipos,
                  filtrando=bool(q.strip() or ver or tipo))


def _tela_tipo(request: Request, db: Session, usuario: Usuario, prog: ProgramaMilhas | None):
    grupos = db.scalars(select(GrupoProduto).order_by(GrupoProduto.nome)).all()
    contas = db.scalar(select(func.count(ContaPrograma.id)).where(ContaPrograma.programa == prog.nome)) if prog else 0
    precos = db.scalars(select(PrecoPrograma).where(PrecoPrograma.programa == prog.nome)
                        .order_by(PrecoPrograma.agencia_id.is_not(None), PrecoPrograma.tipo_negocio)).all() if prog else []
    return render(request, "mvo/programa_tipo.html", usuario, prog=prog, grupos=grupos, opcoes=OPCOES_PROGRAMA,
                  flags=FLAGS_PROGRAMA, contas=contas, precos=precos, pode_editar=pode(usuario, "configurar"))


@rotas.get("/programas/novo", response_class=HTMLResponse)
def novo_tipo(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    return _tela_tipo(request, db, usuario, None)


@rotas.get("/programas/{prog_id}", response_class=HTMLResponse)
def ver_tipo(prog_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    prog = db.get(ProgramaMilhas, prog_id)
    if not prog:
        raise HTTPException(404)
    return _tela_tipo(request, db, usuario, prog)


async def _salvar_tipo(request: Request, db: Session, prog: ProgramaMilhas) -> None:
    form = await request.form()
    for campo, opcoes in OPCOES_PROGRAMA.items():
        valor = (form.get(campo) or "").strip()
        setattr(prog, campo, valor if valor in opcoes else getattr(prog, campo))
    for campo, _, _ in FLAGS_PROGRAMA:
        setattr(prog, campo, form.get(campo) == "true")
    prog.grupo_produto_id = ler_inteiro(form.get("grupo_produto_id")) or None
    prog.cia_aerea = (form.get("cia_aerea") or "").strip() or None
    limite = ler_inteiro(form.get("limite_cpfs"))
    prog.limite_cpfs = limite if limite and limite > 0 else None
    prog.janela_cpfs = "12m" if form.get("janela_cpfs") == "12m" else "ano"
    prog.taxa_fixa = ler_numero(form.get("taxa_fixa"))
    prog.ativo = form.get("ativo") == "true"
    prog.observacoes = (form.get("observacoes") or "").strip() or None


@rotas.post("/programas/novo")
async def criar_tipo(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    form = await request.form()
    nome = " ".join((form.get("nome") or "").split())
    if not nome:
        avisar(request, "Informe o nome do programa.", "erro")
        return voltar("/programas/novo")
    if db.scalar(select(ProgramaMilhas).where(func.lower(ProgramaMilhas.nome) == nome.lower())):
        avisar(request, f"O programa {nome} já existe.", "erro")
        return voltar("/programas/novo")
    prog = ProgramaMilhas(nome=nome, personalizado=True)
    db.add(prog)
    await _salvar_tipo(request, db, prog)
    db.flush()
    registrar(db, usuario, "configuracao", f"programa:{prog.id}", f"Tipo de programa: incluiu {nome}", ip_de(request))
    db.commit()
    limpar_cache_programas()
    avisar(request, f"Programa {nome} cadastrado.")
    return voltar(f"/programas/{prog.id}")


@rotas.post("/programas/{prog_id}")
async def editar_tipo(prog_id: int, request: Request, db: Session = Depends(get_db),
                      usuario: Usuario = Depends(pode_configurar)):
    prog = db.get(ProgramaMilhas, prog_id)
    if not prog:
        raise HTTPException(404)
    await _salvar_tipo(request, db, prog)
    registrar(db, usuario, "configuracao", f"programa:{prog.id}", f"Tipo de programa: alterou {prog.nome}", ip_de(request))
    db.commit()
    limpar_cache_programas()
    recalcular_matches(db)
    avisar(request, "Programa salvo.")
    return voltar(f"/programas/{prog.id}")


@rotas.post("/programas/{prog_id}/excluir")
def excluir_tipo(prog_id: int, request: Request, db: Session = Depends(get_db),
                 usuario: Usuario = Depends(pode_configurar)):
    prog = db.get(ProgramaMilhas, prog_id)
    if not prog:
        raise HTTPException(404)
    if not prog.personalizado or db.scalar(select(func.count(ContaPrograma.id)).where(ContaPrograma.programa == prog.nome)):
        avisar(request, "Só dá para excluir programas incluídos pela equipe e sem contas. Para parar de usar, marque como inativo.",
               "erro")
        return voltar(f"/programas/{prog.id}")
    nome = prog.nome
    db.delete(prog)
    registrar(db, usuario, "configuracao", f"programa:{prog_id}", f"Tipo de programa: excluiu {nome}", ip_de(request))
    db.commit()
    limpar_cache_programas()
    avisar(request, f"Programa {nome} excluído.")
    return voltar("/programas")
