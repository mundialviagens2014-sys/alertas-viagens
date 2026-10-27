"""Milhas: colaboradores, contas nos programas, lançamentos de pontos, transferências e fornecedores."""
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import busca
from .. import colunas as col_
from ..cripto import SenhaIlegivel, cifrar, decifrar
from ..database import get_db
from ..exportar import Coluna, exportar, quer_planilha
from ..models import ProgramaMilhas, Usuario
from ..models_mvo import (
    STATUS_CONTRATO,
    TIPOS_LANCAMENTO,
    TIPOS_MANUAIS,
    Colaborador,
    ContaPrograma,
    Contrato,
    Emissao,
    Fornecedor,
    Lancamento,
)
from ..mvo.contas import (
    ErroMVO,
    cancelar_lancamento,
    chave_passageiro,
    formatar_cpf,
    formatar_pontos,
    inicio_janela,
    lancar,
    recalcular_conta,
    registrar,
    situacao,
    situacoes,
    so_digitos,
    transferir,
)
from ..mvo.exclusoes import excluir_colaborador, excluir_conta, excluir_fornecedor
from ..permissoes import pode
from ..servicos import formatar_telefone, normalizar_telefone
from ..web import (
    avisar,
    checar_exportar,
    destino_seguro,
    exigir,
    ip_de,
    ler_data,
    ler_endereco,
    ler_inteiro,
    ler_numero,
    ler_texto,
    render,
    voltar,
)

rotas = APIRouter()
pode_ver = exigir("milhas_ver")
pode_operar = exigir("milhas_ver", "milhas_operar")
pode_ver_senha = exigir("milhas_ver", "senhas")

TIPOS_COM_CUSTO = [t for t in TIPOS_MANUAIS if TIPOS_LANCAMENTO[t].get("custo_entrada")]


def _texto(valor: str | None) -> str | None:
    valor = (valor or "").strip()
    return valor or None


def programas_cadastrados(db: Session) -> list[str]:
    return sorted(db.scalars(select(ProgramaMilhas.nome)))


def opcoes_contas(db: Session, so_ativas: bool = True) -> list[tuple[str, list[ContaPrograma]]]:
    """Contas agrupadas por programa (para os campos de escolha)."""
    consulta = select(ContaPrograma).join(Colaborador).options(selectinload(ContaPrograma.colaborador))
    if so_ativas:
        consulta = consulta.where(ContaPrograma.ativa.is_(True), Colaborador.ativo.is_(True))
    grupos: dict[str, list[ContaPrograma]] = defaultdict(list)
    for c in db.scalars(consulta.order_by(ContaPrograma.programa, Colaborador.nome)):
        grupos[c.programa].append(c)
    return sorted(grupos.items())


def tipos_permitidos(usuario: Usuario) -> list[str]:
    """Quem não vê custos não lança entradas com custo (compra, clube, saldo inicial)."""
    if pode(usuario, "custos"):
        return TIPOS_MANUAIS
    return [t for t in TIPOS_MANUAIS if t not in TIPOS_COM_CUSTO]


def _conta_ou_404(db: Session, conta_id: int) -> ContaPrograma:
    conta = db.get(ContaPrograma, conta_id)
    if not conta:
        raise HTTPException(404, "Conta não encontrada.")
    return conta


def _descricao(conta: ContaPrograma) -> str:
    return f"{conta.programa} de {conta.colaborador.nome}"


# ---------------------------------------------------------------- colaboradores


@rotas.get("/colaboradores", response_class=HTMLResponse)
def lista_colaboradores(request: Request, q: str = "", programa: str = "", ver: str = "ativos", classificacao: str = "",
                        consolidador: str = "", contas: str = "", ordem: str = "nome", formato: str = "",
                        db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    consulta = select(Colaborador).options(selectinload(Colaborador.contas))
    consulta = consulta.order_by(Colaborador.criado_em.desc() if ordem == "recentes" else Colaborador.nome)
    if ver == "ativos":
        consulta = consulta.where(Colaborador.ativo.is_(True))
    elif ver == "inativos":
        consulta = consulta.where(Colaborador.ativo.is_(False))
    consulta = busca.aplicar(consulta, q, [Colaborador.nome, Colaborador.email, Colaborador.observacoes, Colaborador.pix,
                                           Colaborador.rg, Colaborador.classificacao, Colaborador.consolidador],
                             [Colaborador.cpf, Colaborador.telefone])
    if programa:
        consulta = consulta.where(Colaborador.contas.any(ContaPrograma.programa == programa))
    if classificacao:
        consulta = consulta.where(Colaborador.classificacao == classificacao)
    if consolidador:
        consulta = consulta.where(Colaborador.consolidador == consolidador)
    if contas == "com_saldo":
        consulta = consulta.where(Colaborador.contas.any(and_(ContaPrograma.ativa.is_(True), ContaPrograma.saldo > 0)))
    elif contas == "sem_contas":
        consulta = consulta.where(~Colaborador.contas.any())
    colaboradores = db.scalars(consulta).all()
    if ordem == "pontos":
        colaboradores = sorted(colaboradores, key=lambda c: -sum(max(ct.saldo or 0, 0) for ct in c.contas if ct.ativa))
    if quer_planilha(formato):
        checar_exportar(usuario)
        return exportar(request, db, usuario, "Colaboradores", COLUNAS_COLABORADORES, colaboradores)
    programas = sorted(set(db.scalars(select(ContaPrograma.programa).distinct())))
    totais = {
        "colaboradores": len(colaboradores),
        "contas": sum(len(c.contas) for c in colaboradores),
        "pontos": sum(max(ct.saldo or 0, 0) for c in colaboradores for ct in c.contas if ct.ativa),
    }
    filtrando = bool(q.strip() or programa or ver != "ativos" or classificacao or consolidador or contas or ordem != "nome")
    return render(request, "mvo/colaboradores.html", usuario, colaboradores=colaboradores, q=q, programa=programa,
                  ver=ver, programas=programas, totais=totais, classificacao=classificacao, consolidador=consolidador,
                  contas=contas, ordem=ordem, filtrando=filtrando)


COLUNAS_COLABORADORES = [
    Coluna("Nome", lambda c: c.nome), Coluna("CPF", lambda c: col_.documento(c.cpf)), Coluna("RG", lambda c: c.rg),
    Coluna("Nascimento", lambda c: c.data_nascimento, "data"), Coluna("Telefone", lambda c: col_.telefone(c.telefone)),
    Coluna("E-mail", lambda c: c.email), Coluna("Chave PIX", lambda c: c.pix),
    Coluna("Classificação", lambda c: c.classificacao), Coluna("Consolidador", lambda c: c.consolidador),
    *col_.endereco(),
    Coluna("Contas", lambda c: [f"{ct.programa} ({ct.saldo:,})".replace(",", ".") for ct in c.contas]),
    Coluna("Pontos nas contas ativas", lambda c: sum(max(ct.saldo or 0, 0) for ct in c.contas if ct.ativa), "pontos"),
    Coluna("Situação", lambda c: col_.situacao(c.ativo)), Coluna("Observações", lambda c: c.observacoes),
    Coluna("Cadastrado em", lambda c: c.criado_em, "data"),
]


def _dados_colaborador(form) -> dict:
    telefone = form.get("telefone") or ""
    return {
        "nome": " ".join((form.get("nome") or "").split()), "cpf": so_digitos(form.get("cpf")) or None,
        "telefone": normalizar_telefone(telefone) if so_digitos(telefone) else None,
        "email": ler_texto(form.get("email")), "pix": ler_texto(form.get("pix")),
        "classificacao": ler_texto(form.get("classificacao"), 60), "consolidador": ler_texto(form.get("consolidador"), 120),
        "observacoes": _texto(form.get("observacoes")), "rg": ler_texto(form.get("rg"), 20),
        "data_nascimento": ler_data(form.get("data_nascimento")), **ler_endereco(form),
    }


def _erro_colaborador(db: Session, dados: dict, exceto: int | None = None) -> str | None:
    if not dados["nome"]:
        return "Informe o nome."
    if dados["cpf"] and len(dados["cpf"]) != 11:
        return "O CPF precisa ter 11 números."
    if (outro := _cpf_repetido(db, dados["cpf"], exceto=exceto)):
        return f"Já existe um colaborador com esse CPF: {outro.nome}."
    return None


def _cpf_repetido(db: Session, cpf: str | None, exceto: int | None = None) -> Colaborador | None:
    if not cpf:
        return None
    consulta = select(Colaborador).where(Colaborador.cpf == cpf)
    if exceto:
        consulta = consulta.where(Colaborador.id != exceto)
    return db.scalar(consulta)


@rotas.get("/colaboradores/novo", response_class=HTMLResponse)
def novo_colaborador(request: Request, usuario: Usuario = Depends(pode_operar)):
    return render(request, "mvo/colaborador_form.html", usuario, dados={})


@rotas.post("/colaboradores/novo")
async def criar_colaborador(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    dados = _dados_colaborador(await request.form())
    erro = _erro_colaborador(db, dados)
    if erro:
        avisar(request, erro, "erro")
        return render(request, "mvo/colaborador_form.html", usuario, dados=dados)
    col = Colaborador(**dados)
    db.add(col)
    db.flush()
    registrar(db, usuario, "colaborador_criado", f"colaborador:{col.id}", col.nome, ip_de(request))
    db.commit()
    avisar(request, f"{col.nome} cadastrado. Agora adicione as contas nos programas.")
    return voltar(f"/colaboradores/{col.id}")


@rotas.get("/colaboradores/{col_id}", response_class=HTMLResponse)
def ver_colaborador(col_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    col = db.get(Colaborador, col_id)
    if not col:
        raise HTTPException(404)
    cfgs = {p.nome: p for p in db.scalars(select(ProgramaMilhas))}
    sits = situacoes(db, list(col.contas), cfgs)
    desde = datetime.now() - timedelta(days=365)
    emissoes = db.scalars(
        select(Emissao).where(Emissao.conta_id.in_([c.id for c in col.contas] or [0]), Emissao.data_emissao >= desde)
        .order_by(Emissao.data_emissao.desc()).limit(30)
    ).all()
    usados = {c.programa for c in col.contas}
    livres = [p for p in sorted(cfgs) if p not in usados and cfgs[p].ativo is not False]
    resumo = {
        "pontos": sum(max(c.saldo or 0, 0) for c in col.contas if c.ativa),
        "disponivel": sum(c.disponivel for c in col.contas if c.ativa and not c.uso_pessoal),
        "custo": round(sum(c.custo_total or 0 for c in col.contas if c.ativa), 2),
        "emissoes": sum(1 for e in emissoes if e.status == "ativa"),
    }
    contratos = sorted(col.contratos, key=lambda c: (c.status != "ativo", c.data_vencimento or date.max))
    return render(request, "mvo/colaborador.html", usuario, col=col, sits=sits, emissoes=emissoes,
                  programas_livres=livres, resumo=resumo, contratos=contratos, status_contrato=STATUS_CONTRATO,
                  tipos_contrato=tipos_de_contrato(db),
                  login_programa={p: cfg.necessita_login is not False for p, cfg in cfgs.items()})


def tipos_de_contrato(db: Session) -> list[str]:
    return sorted(set(db.scalars(select(Contrato.tipo).where(Contrato.tipo.is_not(None)).distinct())), key=str.lower)


@rotas.post("/colaboradores/{col_id}")
async def editar_colaborador(col_id: int, request: Request, db: Session = Depends(get_db),
                             usuario: Usuario = Depends(pode_operar)):
    col = db.get(Colaborador, col_id)
    if not col:
        raise HTTPException(404)
    form = await request.form()
    dados = _dados_colaborador(form)
    erro = _erro_colaborador(db, dados, exceto=col.id)
    if erro:
        avisar(request, erro, "erro")
    else:
        for chave, valor in dados.items():
            setattr(col, chave, valor)
        col.ativo = form.get("ativo") == "true"
        registrar(db, usuario, "colaborador_editado", f"colaborador:{col.id}", col.nome, ip_de(request))
        db.commit()
        avisar(request, "Cadastro salvo.")
    return voltar(f"/colaboradores/{col_id}")


@rotas.post("/colaboradores/{col_id}/excluir")
def apagar_colaborador(col_id: int, request: Request, db: Session = Depends(get_db),
                       usuario: Usuario = Depends(pode_operar)):
    col = db.get(Colaborador, col_id)
    if not col:
        raise HTTPException(404)
    nome, contas = col.nome, len(col.contas)
    try:
        excluir_colaborador(db, col)
    except ErroMVO as erro:
        db.rollback()
        avisar(request, str(erro), "erro")
        return voltar(f"/colaboradores/{col_id}")
    registrar(db, usuario, "exclusao", f"colaborador:{col_id}", f"Colaborador {nome} ({contas} conta(s))", ip_de(request))
    db.commit()
    avisar(request, f"Colaborador {nome} excluído" + (f", com {contas} conta(s)." if contas else "."))
    return voltar("/colaboradores")


# ---------------------------------------------------------------- contas


def criar_conta_do_formulario(request: Request, db: Session, usuario: Usuario, col: Colaborador, form) -> ContaPrograma | str:
    """Cria a conta com o que veio do formulário (com o saldo atual como "saldo inicial"). Devolve a conta ou o erro."""
    programa = (form.get("programa") or "").strip()
    if not programa:
        return "Escolha o programa."
    if any(c.programa == programa for c in col.contas):
        return f"{col.nome} já tem conta {programa}."
    if not db.scalar(select(ProgramaMilhas).where(ProgramaMilhas.nome == programa)):
        db.add(ProgramaMilhas(nome=programa, personalizado=True))
    conta = ContaPrograma(colaborador=col, programa=programa, numero=ler_texto(form.get("numero"), 40),
                          uso_pessoal=form.get("uso_pessoal") == "true",
                          saldo_reservado=max(ler_inteiro(form.get("saldo_reservado")) or 0, 0),
                          observacoes=_texto(form.get("observacoes")),
                          email_programa=ler_texto(form.get("email_programa"), 160),
                          codigo_conta=ler_texto(form.get("codigo_conta"), 60))
    if pode(usuario, "senhas"):
        conta.login_cripto = cifrar(ler_texto(form.get("login")))
        conta.senha_cripto = cifrar((form.get("senha") or "").strip() or None)
        conta.token_cripto = cifrar((form.get("token") or "").strip() or None)
    db.add(conta)
    db.flush()
    pontos = ler_inteiro(form.get("saldo"))
    if pontos and pontos > 0:
        custo = ler_numero(form.get("custo_milheiro")) if pode(usuario, "custos") else None
        lancar(db, conta, "saldo_inicial", pontos, preco_milheiro=custo,
               valor=None if custo is not None else 0.0, observacao="Saldo ao cadastrar a conta", usuario=usuario.nome)
    registrar(db, usuario, "conta_criada", f"conta:{conta.id}", _descricao(conta), ip_de(request))
    return conta


@rotas.post("/colaboradores/{col_id}/contas")
async def criar_conta(col_id: int, request: Request, db: Session = Depends(get_db),
                      usuario: Usuario = Depends(pode_operar)):
    col = db.get(Colaborador, col_id)
    if not col:
        raise HTTPException(404)
    conta = criar_conta_do_formulario(request, db, usuario, col, await request.form())
    if isinstance(conta, str):
        db.rollback()
        avisar(request, conta, "erro")
        return voltar(f"/colaboradores/{col_id}")
    db.commit()
    avisar(request, f"Conta {conta.programa} criada para {col.nome}.")
    return voltar(f"/contas/{conta.id}")


@rotas.get("/contas/{conta_id}", response_class=HTMLResponse)
def ver_conta(conta_id: int, request: Request, todos: int = 0, db: Session = Depends(get_db),
              usuario: Usuario = Depends(pode_ver)):
    conta = _conta_ou_404(db, conta_id)
    consulta = (select(Lancamento).where(Lancamento.conta_id == conta.id)
                .options(selectinload(Lancamento.fornecedor))
                .order_by(Lancamento.data_efetivacao.desc(), Lancamento.id.desc()))
    total = db.scalar(select(func.count(Lancamento.id)).where(Lancamento.conta_id == conta.id)) or 0
    lancamentos = db.scalars(consulta if todos else consulta.limit(100)).all()
    cfg = db.scalar(select(ProgramaMilhas).where(ProgramaMilhas.nome == conta.programa))
    sit = situacao(db, conta, cfg)
    desde = inicio_janela(cfg)
    passageiros = []
    if desde:
        vistos = set()
        for e in db.scalars(
            select(Emissao).where(Emissao.conta_id == conta.id, Emissao.status == "ativa",
                                  Emissao.data_emissao >= datetime.combine(desde, datetime.min.time()))
            .options(selectinload(Emissao.passageiros)).order_by(Emissao.data_emissao)
        ):
            for p in e.passageiros:
                chave = chave_passageiro(p.nome, p.cpf)
                if chave not in vistos:
                    vistos.add(chave)
                    passageiros.append({"nome": p.nome, "cpf": p.cpf, "data": e.data_emissao, "emissao": e.id})
    fornecedores = db.scalars(select(Fornecedor).where(Fornecedor.ativo.is_(True)).order_by(Fornecedor.nome)).all()
    return render(request, "mvo/conta.html", usuario, conta=conta, lancamentos=lancamentos, total=total,
                  todos=todos, sit=sit, cfg=cfg, desde=desde, passageiros=passageiros, fornecedores=fornecedores,
                  tipos=tipos_permitidos(usuario), hoje_iso=date.today().isoformat())


@rotas.post("/contas/{conta_id}/editar")
async def editar_conta(conta_id: int, request: Request, db: Session = Depends(get_db),
                       usuario: Usuario = Depends(pode_operar)):
    conta = _conta_ou_404(db, conta_id)
    form = await request.form()
    conta.numero = ler_texto(form.get("numero"), 40)
    conta.saldo_reservado = max(ler_inteiro(form.get("saldo_reservado")) or 0, 0)
    conta.uso_pessoal, conta.ativa = form.get("uso_pessoal") == "true", form.get("ativa") == "true"
    conta.observacoes = _texto(form.get("observacoes"))
    conta.data_conciliacao = ler_data(form.get("data_conciliacao"))
    conta.email_programa = ler_texto(form.get("email_programa"), 160)
    conta.codigo_conta = ler_texto(form.get("codigo_conta"), 60)
    detalhes = [_descricao(conta)]
    if pode(usuario, "senhas"):
        if form.get("apagar_credenciais") == "true":
            conta.login_cripto = conta.senha_cripto = conta.token_cripto = None
            detalhes.append("login, senha e token apagados")
        else:
            for campo, nome in (("login", "login"), ("senha", "senha"), ("token", "token")):
                valor = (form.get(campo) or "").strip()
                if valor:
                    setattr(conta, f"{campo}_cripto", cifrar(valor))
                    detalhes.append(f"{nome} alterado")
    acao = "credenciais_alteradas" if len(detalhes) > 1 else "conta_editada"
    registrar(db, usuario, acao, f"conta:{conta.id}", "; ".join(detalhes), ip_de(request))
    db.commit()
    avisar(request, "Conta salva.")
    return voltar(destino_seguro(form.get("voltar_para"), f"/contas/{conta_id}"))


@rotas.post("/contas/{conta_id}/excluir")
def apagar_conta(conta_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    conta = _conta_ou_404(db, conta_id)
    descricao, col_id = _descricao(conta), conta.colaborador_id
    try:
        excluir_conta(db, conta)
    except ErroMVO as erro:
        db.rollback()
        avisar(request, str(erro), "erro")
        return voltar(f"/contas/{conta_id}")
    registrar(db, usuario, "exclusao", f"conta:{conta_id}", f"Conta {descricao}", ip_de(request))
    db.commit()
    avisar(request, f"Conta {descricao} excluída.")
    return voltar(f"/colaboradores/{col_id}")


@rotas.post("/contas/{conta_id}/credenciais")
def mostrar_credenciais(conta_id: int, request: Request, db: Session = Depends(get_db),
                        usuario: Usuario = Depends(pode_ver_senha)):
    """Login, senha e token (e os dados do titular, para entrar no site do programa). Fica registrado."""
    conta = _conta_ou_404(db, conta_id)
    try:
        login, senha, token = (decifrar(conta.login_cripto), decifrar(conta.senha_cripto),
                               decifrar(conta.token_cripto))
    except SenhaIlegivel as erro:
        return JSONResponse({"erro": str(erro)}, status_code=409)
    registrar(db, usuario, "ver_credenciais", f"conta:{conta.id}", _descricao(conta), ip_de(request))
    db.commit()
    col = conta.colaborador
    titular = {
        "programa": conta.programa, "colaborador": col.nome, "cpf": formatar_cpf(col.cpf) if col.cpf else "",
        "rg": col.rg or "", "nascimento": col.data_nascimento.strftime("%d/%m/%Y") if col.data_nascimento else "",
        "telefone": formatar_telefone(col.telefone) if col.telefone else "", "email": col.email or "",
        "email_programa": conta.email_programa or "", "codigo_conta": conta.codigo_conta or "",
        "numero": conta.numero or "", "observacoes": conta.observacoes or "",
        "endereco": ", ".join(p for p in (col.endereco, col.numero, col.complemento, col.bairro,
                                          f"{col.cidade}/{col.estado}" if col.cidade and col.estado else col.cidade,
                                          f"CEP {col.cep}" if col.cep else None) if p),
    }
    return JSONResponse({"login": login or "", "senha": senha or "", "token": token or "", **titular},
                        headers={"Cache-Control": "no-store"})


@rotas.post("/contas/{conta_id}/recalcular")
def recalcular(conta_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    conta = _conta_ou_404(db, conta_id)
    recalcular_conta(db, conta)
    db.commit()
    avisar(request, "Saldo e custo médio recalculados a partir dos lançamentos.")
    return voltar(f"/contas/{conta_id}")


# ---------------------------------------------------------------- lançamentos


@rotas.get("/lancamentos", response_class=HTMLResponse)
def lista_lancamentos(
    request: Request, programa: str = "", tipo: str = "", q: str = "", de: str = "", ate: str = "",
    ver: str = "ativos", limite: int = 300, formato: str = "", db: Session = Depends(get_db),
    usuario: Usuario = Depends(pode_ver),
):
    consulta = (select(Lancamento).join(ContaPrograma, Lancamento.conta_id == ContaPrograma.id).join(Colaborador)
                .options(selectinload(Lancamento.conta).selectinload(ContaPrograma.colaborador),
                         selectinload(Lancamento.fornecedor)))
    if programa:
        consulta = consulta.where(ContaPrograma.programa == programa)
    if tipo:
        consulta = consulta.where(Lancamento.tipo == tipo)
    consulta = busca.aplicar(consulta, q, [Colaborador.nome, Lancamento.observacao, ContaPrograma.numero],
                             [Colaborador.cpf])
    inicio, fim = ler_data(de), ler_data(ate)
    if inicio:
        consulta = consulta.where(Lancamento.data_efetivacao >= inicio)
    if fim:
        consulta = consulta.where(Lancamento.data_efetivacao <= fim)
    if ver == "ativos":
        consulta = consulta.where(Lancamento.status == "ativo")
    elif ver == "futuros":
        consulta = consulta.where(Lancamento.status == "ativo", Lancamento.data_efetivacao > date.today())
    elif ver == "cancelados":
        consulta = consulta.where(Lancamento.status == "cancelado")
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [
            Coluna("Efetivação", lambda l: l.data_efetivacao, "data"),
            Coluna("Lançado em", lambda l: l.data_lancamento, "datahora"),
            Coluna("Tipo", lambda l: l.nome_tipo), Coluna("Programa", lambda l: l.conta.programa),
            Coluna("Colaborador", lambda l: l.conta.colaborador.nome),
            Coluna("CPF", lambda l: col_.documento(l.conta.colaborador.cpf)),
            Coluna("Pontos", lambda l: l.pontos, "pontos"),
        ]
        if pode(usuario, "custos"):
            colunas += [Coluna("Valor", lambda l: l.valor, "dinheiro"),
                        Coluna("R$ por milheiro", lambda l: l.preco_milheiro, "dinheiro")]
        colunas += [Coluna("Fornecedor", lambda l: l.fornecedor.nome if l.fornecedor else None),
                    Coluna("Emissão", lambda l: (l.emissao.localizador or f"nº {l.emissao.id}") if l.emissao else None),
                    Coluna("Status", lambda l: "Cancelado" if l.status == "cancelado" else "Ativo"),
                    Coluna("Observação", lambda l: l.observacao), Coluna("Lançado por", lambda l: l.usuario)]
        itens = db.scalars(consulta.options(selectinload(Lancamento.emissao))
                           .order_by(Lancamento.data_efetivacao.desc(), Lancamento.id.desc())).all()
        return exportar(request, db, usuario, "Lançamentos", colunas, itens)
    limite = max(50, min(limite, 3000))
    lancs = db.scalars(consulta.order_by(Lancamento.data_efetivacao.desc(), Lancamento.id.desc()).limit(limite + 1)).all()
    mais = len(lancs) > limite
    lancs = lancs[:limite]
    ativos = [l for l in lancs if l.status == "ativo"]
    resumo = {
        "entradas": sum(l.pontos for l in ativos if l.pontos > 0),
        "saidas": -sum(l.pontos for l in ativos if l.pontos < 0),
        "compras": round(sum(l.valor or 0 for l in ativos if l.tipo in ("compra", "clube")), 2),
    }
    return render(request, "mvo/lancamentos.html", usuario, lancamentos=lancs, mais=mais, limite=limite,
                  programa=programa, tipo=tipo, q=q, de=de, ate=ate, ver=ver, resumo=resumo,
                  programas=programas_cadastrados(db))


@rotas.get("/lancamentos/novo", response_class=HTMLResponse)
def novo_lancamento(request: Request, conta: int = 0, tipo: str = "compra", db: Session = Depends(get_db),
                    usuario: Usuario = Depends(pode_operar)):
    fornecedores = db.scalars(select(Fornecedor).where(Fornecedor.ativo.is_(True)).order_by(Fornecedor.nome)).all()
    tipos = tipos_permitidos(usuario)
    return render(request, "mvo/lancamento_form.html", usuario, grupos=opcoes_contas(db), conta_id=conta,
                  tipos=tipos, tipo=tipo if tipo in tipos else tipos[0], fornecedores=fornecedores,
                  hoje_iso=date.today().isoformat(), tipos_com_custo=TIPOS_COM_CUSTO)


@rotas.post("/lancamentos/novo")
def criar_lancamento(
    request: Request, conta_id: int = Form(...), tipo: str = Form(...), pontos: str = Form(...),
    valor: str = Form(""), preco_milheiro: str = Form(""), data_efetivacao: str = Form(""),
    fornecedor_id: str = Form(""), observacao: str = Form(""), voltar_para: str = Form(""),
    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar),
):
    conta = _conta_ou_404(db, conta_id)
    destino = destino_seguro(voltar_para, f"/contas/{conta.id}")
    if tipo not in tipos_permitidos(usuario):
        avisar(request, "Você não pode lançar esse tipo de movimento.", "erro")
        return voltar(destino)
    qtd = ler_inteiro(pontos)
    total, milheiro = ler_numero(valor), ler_numero(preco_milheiro)
    if tipo in TIPOS_COM_CUSTO and tipo != "saldo_inicial" and total is None and milheiro is None:
        avisar(request, "Informe quanto foi pago (valor total ou preço do milheiro).", "erro")
        return voltar(destino)
    try:
        l = lancar(db, conta, tipo, qtd or 0, valor=total, preco_milheiro=milheiro,
                   data_efetivacao=ler_data(data_efetivacao), fornecedor_id=ler_inteiro(fornecedor_id) or None,
                   observacao=observacao, usuario=usuario.nome)
    except ErroMVO as erro:
        avisar(request, str(erro), "erro")
        return voltar(destino)
    registrar(db, usuario, "lancamento", f"conta:{conta.id}",
              f"{l.nome_tipo} de {formatar_pontos(abs(l.pontos))} pontos em {_descricao(conta)}", ip_de(request))
    db.commit()
    quando = " (entra no saldo em " + l.data_efetivacao.strftime("%d/%m/%Y") + ")" if l.futuro else ""
    avisar(request, f"{l.nome_tipo}: lançamento registrado{quando}. Saldo atual: {formatar_pontos(conta.saldo)} pontos.")
    return voltar(destino)


@rotas.post("/lancamentos/{lanc_id}/cancelar")
def cancelar(lanc_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    l = db.get(Lancamento, lanc_id)
    if not l:
        raise HTTPException(404)
    if l.emissao_id and l.tipo == "emissao":
        avisar(request, "Este lançamento é de uma emissão: cancele a emissão (os pontos voltam junto).", "erro")
        return voltar(request.headers.get("referer") or f"/contas/{l.conta_id}")
    cancelar_lancamento(db, l)
    registrar(db, usuario, "lancamento_cancelado", f"conta:{l.conta_id}",
              f"{l.nome_tipo} de {formatar_pontos(abs(l.pontos))} pontos em {_descricao(l.conta)}", ip_de(request))
    db.commit()
    avisar(request, "Lançamento cancelado. Saldo e custo médio recalculados.")
    return voltar(request.headers.get("referer") or f"/contas/{l.conta_id}")


# ---------------------------------------------------------------- transferências


@rotas.get("/transferencias/nova", response_class=HTMLResponse)
def nova_transferencia(request: Request, origem: int = 0, destino: int = 0, db: Session = Depends(get_db),
                       usuario: Usuario = Depends(pode_operar)):
    return render(request, "mvo/transferencia.html", usuario, grupos=opcoes_contas(db), origem=origem,
                  destino=destino, hoje_iso=date.today().isoformat())


@rotas.post("/transferencias/nova")
def criar_transferencia(
    request: Request, origem_id: int = Form(...), destino_id: int = Form(...), pontos: str = Form(...),
    bonus: str = Form(""), recebidos: str = Form(""), data_transferencia: str = Form(""),
    data_credito: str = Form(""), observacao: str = Form(""),
    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar),
):
    origem, destino = _conta_ou_404(db, origem_id), _conta_ou_404(db, destino_id)
    qtd = ler_inteiro(pontos) or 0
    if qtd > origem.saldo:
        avisar(request, f"A conta de origem tem {formatar_pontos(origem.saldo)} pontos.", "erro")
        return voltar(f"/transferencias/nova?origem={origem.id}&destino={destino.id}")
    try:
        saida, entrada = transferir(db, origem, destino, qtd, bonus_pct=ler_numero(bonus) or 0.0,
                                    pontos_recebidos=ler_inteiro(recebidos) or None,
                                    data_efetivacao=ler_data(data_transferencia), data_credito=ler_data(data_credito),
                                    observacao=_texto(observacao), usuario=usuario.nome)
    except ErroMVO as erro:
        avisar(request, str(erro), "erro")
        return voltar(f"/transferencias/nova?origem={origem.id}&destino={destino.id}")
    registrar(db, usuario, "transferencia", f"conta:{origem.id}",
              f"{formatar_pontos(qtd)} de {_descricao(origem)} → {formatar_pontos(entrada.pontos)} em "
              f"{_descricao(destino)}", ip_de(request))
    db.commit()
    avisar(request, f"Transferência registrada: {formatar_pontos(entrada.pontos)} pontos entram em {_descricao(destino)}.")
    return voltar(f"/contas/{destino.id}")


# ---------------------------------------------------------------- fornecedores


def _dados_fornecedor(form) -> dict:
    telefone = form.get("telefone") or ""
    return {
        "nome": " ".join((form.get("nome") or "").split()), "documento": so_digitos(form.get("documento")) or None,
        "telefone": normalizar_telefone(telefone) if so_digitos(telefone) else None, "email": ler_texto(form.get("email")),
        "pix": ler_texto(form.get("pix")), "dias_pagamento": ler_inteiro(form.get("dias_pagamento")),
        "observacoes": _texto(form.get("observacoes")), **ler_endereco(form),
    }


@rotas.get("/fornecedores", response_class=HTMLResponse)
def lista_fornecedores(request: Request, q: str = "", ver: str = "", formato: str = "", db: Session = Depends(get_db),
                       usuario: Usuario = Depends(pode_ver)):
    consulta = select(Fornecedor).order_by(Fornecedor.ativo.desc(), Fornecedor.nome)
    consulta = busca.aplicar(consulta, q, [Fornecedor.nome, Fornecedor.email, Fornecedor.pix, Fornecedor.cidade,
                                           Fornecedor.observacoes], [Fornecedor.documento, Fornecedor.telefone])
    if ver == "ativos":
        consulta = consulta.where(Fornecedor.ativo.is_not(False))
    elif ver == "inativos":
        consulta = consulta.where(Fornecedor.ativo.is_(False))
    fornecedores = db.scalars(consulta).all()
    compras = dict(db.execute(
        select(Lancamento.fornecedor_id, func.sum(Lancamento.pontos))
        .where(Lancamento.status == "ativo", Lancamento.pontos > 0, Lancamento.fornecedor_id.is_not(None))
        .group_by(Lancamento.fornecedor_id)
    ).all())
    valores = dict(db.execute(
        select(Lancamento.fornecedor_id, func.sum(Lancamento.valor))
        .where(Lancamento.status == "ativo", Lancamento.pontos > 0, Lancamento.fornecedor_id.is_not(None))
        .group_by(Lancamento.fornecedor_id)
    ).all())
    emissoes = dict(db.execute(
        select(Emissao.fornecedor_id, func.count(Emissao.id))
        .where(Emissao.status == "ativa", Emissao.fornecedor_id.is_not(None)).group_by(Emissao.fornecedor_id)
    ).all())
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [
            Coluna("Nome", lambda f: f.nome), Coluna("CPF / CNPJ", lambda f: col_.documento(f.documento)),
            Coluna("Telefone", lambda f: col_.telefone(f.telefone)), Coluna("E-mail", lambda f: f.email),
            Coluna("Chave PIX", lambda f: f.pix), Coluna("Prazo de pagamento (dias)", lambda f: f.dias_pagamento),
            *col_.endereco(), Coluna("Pontos comprados", lambda f: compras.get(f.id, 0), "pontos"),
        ]
        if pode(usuario, "custos"):
            colunas.append(Coluna("Valor pago", lambda f: valores.get(f.id), "dinheiro"))
        colunas += [Coluna("Emissões", lambda f: emissoes.get(f.id, 0)), Coluna("Situação", lambda f: col_.situacao(f.ativo)),
                    Coluna("Observações", lambda f: f.observacoes)]
        return exportar(request, db, usuario, "Fornecedores", colunas, fornecedores)
    return render(request, "mvo/fornecedores.html", usuario, fornecedores=fornecedores, compras=compras,
                  valores=valores, emissoes=emissoes, q=q, ver=ver, filtrando=bool(q.strip() or ver))


@rotas.post("/fornecedores/novo")
async def criar_fornecedor(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    dados = _dados_fornecedor(await request.form())
    if not dados["nome"]:
        avisar(request, "Informe o nome do fornecedor.", "erro")
        return voltar("/fornecedores")
    f = Fornecedor(**dados)
    db.add(f)
    db.flush()
    registrar(db, usuario, "fornecedor_salvo", f"fornecedor:{f.id}", f.nome, ip_de(request))
    db.commit()
    avisar(request, f"Fornecedor {f.nome} cadastrado.")
    return voltar("/fornecedores")


@rotas.get("/fornecedores/{forn_id}", response_class=HTMLResponse)
def ver_fornecedor(forn_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    f = db.get(Fornecedor, forn_id)
    if not f:
        raise HTTPException(404)
    lancamentos = db.scalars(
        select(Lancamento).where(Lancamento.fornecedor_id == f.id)
        .options(selectinload(Lancamento.conta).selectinload(ContaPrograma.colaborador))
        .order_by(Lancamento.data_efetivacao.desc()).limit(200)
    ).all()
    emissoes = db.scalars(
        select(Emissao).where(Emissao.fornecedor_id == f.id).order_by(Emissao.data_emissao.desc()).limit(200)
    ).all()
    return render(request, "mvo/fornecedor.html", usuario, f=f, lancamentos=lancamentos, emissoes=emissoes)


@rotas.post("/fornecedores/{forn_id}/excluir")
def apagar_fornecedor(forn_id: int, request: Request, db: Session = Depends(get_db),
                      usuario: Usuario = Depends(pode_operar)):
    f = db.get(Fornecedor, forn_id)
    if not f:
        raise HTTPException(404)
    nome = f.nome
    try:
        excluir_fornecedor(db, f)
    except ErroMVO as erro:
        db.rollback()
        avisar(request, str(erro), "erro")
        return voltar(f"/fornecedores/{forn_id}")
    registrar(db, usuario, "exclusao", f"fornecedor:{forn_id}", f"Fornecedor {nome}", ip_de(request))
    db.commit()
    avisar(request, f"Fornecedor {nome} excluído.")
    return voltar("/fornecedores")


@rotas.post("/fornecedores/{forn_id}")
async def editar_fornecedor(forn_id: int, request: Request, db: Session = Depends(get_db),
                            usuario: Usuario = Depends(pode_operar)):
    f = db.get(Fornecedor, forn_id)
    if not f:
        raise HTTPException(404)
    form = await request.form()
    dados = _dados_fornecedor(form)
    if not dados["nome"]:
        avisar(request, "Informe o nome do fornecedor.", "erro")
        return voltar(f"/fornecedores/{forn_id}")
    for chave, valor in dados.items():
        setattr(f, chave, valor)
    f.ativo = form.get("ativo") == "true"
    registrar(db, usuario, "fornecedor_salvo", f"fornecedor:{f.id}", f.nome, ip_de(request))
    db.commit()
    avisar(request, "Fornecedor salvo.")
    return voltar(f"/fornecedores/{forn_id}")
