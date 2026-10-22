"""Cadastros do Manager: agências, contratos, compras externas e a lista de programas (as contas dos colaboradores)."""
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import colunas as col_
from ..database import get_db
from ..exportar import Coluna, exportar, quer_planilha, sim_nao
from ..models import ProgramaMilhas, Usuario
from ..models_mvo import (
    METODOS_PAGAMENTO,
    STATUS_COMPRA,
    STATUS_CONTRATO,
    TIPOS_NEGOCIO,
    Agencia,
    Colaborador,
    CompraExterna,
    ContaPrograma,
    Contrato,
    Emissao,
    Fornecedor,
    Franquia,
    Lancamento,
    Markup,
    PedidoEmissao,
    PrecoPrograma,
)
from ..mvo.contas import ErroMVO, cancelar_lancamento, formatar_pontos, lancar, registrar, so_digitos
from ..permissoes import pode
from ..servicos import normalizar_telefone
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
from .milhas import criar_conta_do_formulario, opcoes_contas, tipos_de_contrato

rotas = APIRouter()
pode_ver = exigir("milhas_ver")
pode_operar = exigir("milhas_ver", "milhas_operar")
pode_comprar = exigir("milhas_ver", "milhas_operar", "custos")


def _destino(form, padrao: str) -> str:
    """Endereço para voltar depois de salvar (só caminhos do próprio site)."""
    return destino_seguro(form.get("voltar_para"), padrao)


def _telefone(valor) -> str | None:
    valor = valor or ""
    return normalizar_telefone(valor) if so_digitos(valor) else None


def _texto_longo(valor) -> str | None:
    return (valor or "").strip() or None if isinstance(valor, str) else None


# ---------------------------------------------------------------- agências


def _dados_agencia(form) -> dict:
    tipo = form.get("tipo_negocio")
    return {
        "nome": ler_texto(form.get("nome"), 160), "razao_social": ler_texto(form.get("razao_social"), 200),
        "consolidadora": form.get("consolidadora") == "true", "documento": so_digitos(form.get("documento")) or None,
        "data_fundacao": ler_data(form.get("data_fundacao")),
        "telefone": _telefone(form.get("telefone")), "telefone2": _telefone(form.get("telefone2")),
        "email_comercial": ler_texto(form.get("email_comercial"), 160),
        "email_financeiro": ler_texto(form.get("email_financeiro"), 160),
        "site": ler_texto(form.get("site"), 160), "porte": ler_texto(form.get("porte"), 40),
        "perfil": ler_texto(form.get("perfil"), 60), "tipo_negocio": tipo if tipo in TIPOS_NEGOCIO else "B2B",
        "limite_consultas": ler_inteiro(form.get("limite_consultas")),
        "atividades": _texto_longo(form.get("atividades")), "observacoes": _texto_longo(form.get("observacoes")),
        "franquia_id": ler_inteiro(form.get("franquia_id")) or None, **ler_endereco(form),
    }


def _erro_agencia(db: Session, dados: dict, exceto: int | None = None) -> str | None:
    if not dados["nome"]:
        return "Informe o nome da agência."
    if dados["documento"] and len(dados["documento"]) not in (11, 14):
        return "O CPF precisa ter 11 números e o CNPJ, 14."
    if dados["documento"]:
        consulta = select(Agencia).where(Agencia.documento == dados["documento"])
        if exceto:
            consulta = consulta.where(Agencia.id != exceto)
        if (outra := db.scalar(consulta.limit(1))):
            return f"Já existe uma agência com esse CPF/CNPJ: {outra.nome}."
    if dados["franquia_id"] and not db.get(Franquia, dados["franquia_id"]):
        dados["franquia_id"] = None
    return None


@rotas.get("/agencias", response_class=HTMLResponse)
def lista_agencias(request: Request, q: str = "", ver: str = "ativas", tipo: str = "", franquia: int = 0,
                   formato: str = "", db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    consulta = select(Agencia).options(selectinload(Agencia.franquia)).order_by(Agencia.nome)
    if ver == "ativas":
        consulta = consulta.where(Agencia.ativo.is_not(False))
    elif ver == "inativas":
        consulta = consulta.where(Agencia.ativo.is_(False))
    if tipo in TIPOS_NEGOCIO:
        consulta = consulta.where(Agencia.tipo_negocio == tipo)
    if franquia:
        consulta = consulta.where(Agencia.franquia_id == franquia)
    if q.strip():
        termo = f"%{q.strip()}%"
        condicoes = [Agencia.nome.ilike(termo), Agencia.razao_social.ilike(termo), Agencia.email_comercial.ilike(termo),
                     Agencia.cidade.ilike(termo)]
        if so_digitos(q):
            condicoes += [Agencia.documento.like(f"%{so_digitos(q)}%"), Agencia.telefone.like(f"%{so_digitos(q)}%")]
        consulta = consulta.where(or_(*condicoes))
    agencias = db.scalars(consulta).all()
    desde = datetime.now() - timedelta(days=365)
    vendas = {aid: (n, v) for aid, n, v in db.execute(
        select(Emissao.agencia_id, func.count(Emissao.id), func.sum(Emissao.preco_venda))
        .where(Emissao.status == "ativa", Emissao.agencia_id.is_not(None), Emissao.data_emissao >= desde)
        .group_by(Emissao.agencia_id))}
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [
            Coluna("Agência", lambda a: a.nome), Coluna("Razão social", lambda a: a.razao_social),
            Coluna("CPF / CNPJ", lambda a: col_.documento(a.documento)),
            Coluna("Tipo de negócio", lambda a: a.tipo_negocio or "B2B"),
            Coluna("Franquia", lambda a: a.franquia.nome if a.franquia else None),
            Coluna("Consolidadora", lambda a: sim_nao(a.consolidadora)),
            Coluna("Telefone 1", lambda a: col_.telefone(a.telefone)), Coluna("Telefone 2", lambda a: col_.telefone(a.telefone2)),
            Coluna("E-mail comercial", lambda a: a.email_comercial), Coluna("E-mail financeiro", lambda a: a.email_financeiro),
            Coluna("Site", lambda a: a.site), *col_.endereco(),
            Coluna("Nascimento ou fundação", lambda a: a.data_fundacao, "data"), Coluna("Porte", lambda a: a.porte),
            Coluna("Perfil", lambda a: a.perfil), Coluna("Limite de consultas", lambda a: a.limite_consultas),
            Coluna("Atividades", lambda a: a.atividades),
            Coluna("Emissões (12 meses)", lambda a: vendas.get(a.id, (0, 0))[0]),
            Coluna("Vendido (12 meses)", lambda a: vendas.get(a.id, (0, 0))[1] or 0, "dinheiro"),
            Coluna("Situação", lambda a: col_.situacao(a.ativo)), Coluna("Observação", lambda a: a.observacoes),
            Coluna("Cadastrada em", lambda a: a.criado_em, "data"),
        ]
        return exportar(request, db, usuario, "Agências", colunas, agencias)
    franquias = db.scalars(select(Franquia).order_by(Franquia.nome)).all()
    return render(request, "mvo/agencias.html", usuario, agencias=agencias, vendas=vendas, franquias=franquias,
                  q=q, ver=ver, tipo=tipo, franquia=franquia, tipos=TIPOS_NEGOCIO)


def _tela_agencia_form(request: Request, db: Session, usuario: Usuario, ag: Agencia | None, dados: dict | None = None):
    franquias = db.scalars(select(Franquia).where(Franquia.ativo.is_not(False)).order_by(Franquia.nome)).all()
    return render(request, "mvo/agencia_form.html", usuario, ag=ag, d=dados or ag, franquias=franquias,
                  tipos=TIPOS_NEGOCIO)


@rotas.get("/agencias/nova", response_class=HTMLResponse)
def nova_agencia(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    return _tela_agencia_form(request, db, usuario, None, {"tipo_negocio": "B2B", "ativo": True})


@rotas.post("/agencias/nova")
async def criar_agencia(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    dados = _dados_agencia(await request.form())
    erro = _erro_agencia(db, dados)
    if erro:
        avisar(request, erro, "erro")
        return _tela_agencia_form(request, db, usuario, None, dados)
    ag = Agencia(**dados, ativo=True)
    db.add(ag)
    db.flush()
    registrar(db, usuario, "agencia_salva", f"agencia:{ag.id}", ag.nome, ip_de(request))
    db.commit()
    avisar(request, f"Agência {ag.nome} cadastrada.")
    return voltar(f"/agencias/{ag.id}")


def _agencia_ou_404(db: Session, agencia_id: int) -> Agencia:
    ag = db.get(Agencia, agencia_id)
    if not ag:
        raise HTTPException(404, "Agência não encontrada.")
    return ag


@rotas.get("/agencias/{agencia_id}", response_class=HTMLResponse)
def ver_agencia(agencia_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    ag = _agencia_ou_404(db, agencia_id)
    emissoes = db.scalars(
        select(Emissao).where(Emissao.agencia_id == ag.id).options(selectinload(Emissao.passageiros))
        .order_by(Emissao.data_emissao.desc()).limit(50)).all()
    ativas = db.scalars(select(Emissao).where(Emissao.agencia_id == ag.id, Emissao.status == "ativa")).all()
    precos = db.scalars(select(PrecoPrograma).where(PrecoPrograma.agencia_id == ag.id)
                        .order_by(PrecoPrograma.programa, PrecoPrograma.tipo_negocio)).all()
    markups = db.scalars(select(Markup).where(Markup.agencia_id == ag.id).order_by(Markup.alvo, Markup.nome)).all()
    pedidos = db.scalars(select(PedidoEmissao).where(PedidoEmissao.agencia_id == ag.id)
                         .order_by(PedidoEmissao.criado_em.desc()).limit(20)).all()
    return render(request, "mvo/agencia.html", usuario, ag=ag, emissoes=emissoes, precos=precos, markups=markups,
                  pedidos=pedidos, tipos=TIPOS_NEGOCIO,
                  resumo={"emissoes": len(ativas), "vendido": round(sum(e.preco_venda or 0 for e in ativas), 2),
                          "lucro": round(sum(e.lucro or 0 for e in ativas if e.lucro is not None), 2)})


@rotas.get("/agencias/{agencia_id}/editar", response_class=HTMLResponse)
def editar_agencia_tela(agencia_id: int, request: Request, db: Session = Depends(get_db),
                        usuario: Usuario = Depends(pode_operar)):
    return _tela_agencia_form(request, db, usuario, _agencia_ou_404(db, agencia_id))


@rotas.post("/agencias/{agencia_id}/editar")
async def editar_agencia(agencia_id: int, request: Request, db: Session = Depends(get_db),
                         usuario: Usuario = Depends(pode_operar)):
    ag = _agencia_ou_404(db, agencia_id)
    form = await request.form()
    dados = _dados_agencia(form)
    erro = _erro_agencia(db, dados, exceto=ag.id)
    if erro:
        avisar(request, erro, "erro")
        return _tela_agencia_form(request, db, usuario, ag, {**dados, "ativo": form.get("ativo") == "true"})
    for chave, valor in dados.items():
        setattr(ag, chave, valor)
    ag.ativo = form.get("ativo") == "true"
    registrar(db, usuario, "agencia_salva", f"agencia:{ag.id}", ag.nome, ip_de(request))
    db.commit()
    avisar(request, "Agência salva.")
    return voltar(f"/agencias/{ag.id}")


@rotas.post("/agencias/{agencia_id}/excluir")
def excluir_agencia(agencia_id: int, request: Request, db: Session = Depends(get_db),
                    usuario: Usuario = Depends(pode_operar)):
    ag = _agencia_ou_404(db, agencia_id)
    usadas = db.scalar(select(func.count(Emissao.id)).where(Emissao.agencia_id == ag.id)) or 0
    usadas += db.scalar(select(func.count(PedidoEmissao.id)).where(PedidoEmissao.agencia_id == ag.id)) or 0
    if usadas:
        avisar(request, "Esta agência tem emissões ou pedidos registrados: para parar de usar, desmarque “Agência ativa”.",
               "erro")
        return voltar(f"/agencias/{ag.id}")
    nome = ag.nome
    db.delete(ag)
    registrar(db, usuario, "agencia_excluida", f"agencia:{agencia_id}", nome, ip_de(request))
    db.commit()
    avisar(request, f"Agência {nome} excluída.")
    return voltar("/agencias")


# ---------------------------------------------------------------- contratos


def _dados_contrato(form, usuario: Usuario) -> dict:
    status = form.get("status")
    dados = {
        "tipo": (ler_texto(form.get("tipo"), 60) or "").upper() or None, "programa": ler_texto(form.get("programa"), 80),
        "status": status if status in STATUS_CONTRATO else "ativo", "data_inicio": ler_data(form.get("data_inicio")),
        "data_vencimento": ler_data(form.get("data_vencimento")), "observacoes": _texto_longo(form.get("observacoes")),
    }
    if pode(usuario, "custos") and "valor_resgate" in form:  # quem não vê custos não apaga o valor
        dados["valor_resgate"] = ler_numero(form.get("valor_resgate"))
    return dados


@rotas.get("/contratos", response_class=HTMLResponse)
def lista_contratos(request: Request, status: str = "ativo", programa: str = "", q: str = "", vencimento: str = "",
                    formato: str = "", db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    hoje = date.today()
    consulta = select(Contrato).join(Colaborador).options(selectinload(Contrato.colaborador))
    if status in STATUS_CONTRATO:
        consulta = consulta.where(Contrato.status == status)
    if programa:
        consulta = consulta.where(Contrato.programa == programa)
    if q.strip():
        termo = f"%{q.strip()}%"
        condicoes = [Colaborador.nome.ilike(termo), Contrato.tipo.ilike(termo), Contrato.observacoes.ilike(termo)]
        if so_digitos(q):
            condicoes.append(Colaborador.cpf.like(f"%{so_digitos(q)}%"))
        consulta = consulta.where(or_(*condicoes))
    if vencimento == "vencidos":
        consulta = consulta.where(Contrato.data_vencimento < hoje)
    elif vencimento == "30dias":
        consulta = consulta.where(Contrato.data_vencimento >= hoje, Contrato.data_vencimento <= hoje + timedelta(days=30))
    contratos = db.scalars(consulta.order_by(Contrato.data_vencimento.is_(None), Contrato.data_vencimento,
                                             Colaborador.nome)).all()
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [
            Coluna("Colaborador", lambda k: k.colaborador.nome), Coluna("CPF", lambda k: col_.documento(k.colaborador.cpf)),
            Coluna("Tipo de contrato", lambda k: k.tipo), Coluna("Programa", lambda k: k.programa or "Todos"),
            Coluna("Início", lambda k: k.data_inicio, "data"), Coluna("Vencimento", lambda k: k.data_vencimento, "data"),
        ]
        if pode(usuario, "custos"):
            colunas.append(Coluna("Valor de resgate", lambda k: k.valor_resgate, "dinheiro"))
        colunas += [Coluna("Status", lambda k: STATUS_CONTRATO.get(k.status, k.status)),
                    Coluna("Vencido", lambda k: sim_nao(k.status == "ativo" and k.vencido)),
                    Coluna("Observações", lambda k: k.observacoes)]
        return exportar(request, db, usuario, "Contratos", colunas, contratos)
    ativos = db.scalars(select(Contrato).where(Contrato.status == "ativo")).all()
    resumo = {
        "ativos": len(ativos), "vencidos": sum(1 for k in ativos if k.vencido),
        "vencem": sum(1 for k in ativos if k.vence_logo), "resgate": round(sum(k.valor_resgate or 0 for k in ativos), 2),
    }
    colaboradores = db.scalars(select(Colaborador).where(Colaborador.ativo.is_(True)).order_by(Colaborador.nome)).all()
    programas = sorted(set(db.scalars(select(Contrato.programa).where(Contrato.programa.is_not(None)).distinct())))
    return render(request, "mvo/contratos.html", usuario, contratos=contratos, resumo=resumo, status=status,
                  programa=programa, q=q, vencimento=vencimento, colaboradores=colaboradores, programas=programas,
                  status_contrato=STATUS_CONTRATO, tipos_contrato=tipos_de_contrato(db))


@rotas.post("/contratos/novo")
async def criar_contrato(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    form = await request.form()
    col = db.get(Colaborador, ler_inteiro(form.get("colaborador_id")) or 0)
    destino = _destino(form, "/contratos")
    if not col:
        avisar(request, "Escolha o colaborador do contrato.", "erro")
        return voltar(destino)
    k = Contrato(colaborador_id=col.id, **_dados_contrato(form, usuario))
    db.add(k)
    db.flush()
    registrar(db, usuario, "contrato", f"contrato:{k.id}", f"Incluiu contrato {k.tipo or ''} de {col.nome}".replace("  ", " "),
              ip_de(request))
    db.commit()
    avisar(request, f"Contrato de {col.nome} incluído.")
    return voltar(destino)


@rotas.post("/contratos/{contrato_id}")
async def editar_contrato(contrato_id: int, request: Request, db: Session = Depends(get_db),
                          usuario: Usuario = Depends(pode_operar)):
    k = db.get(Contrato, contrato_id)
    if not k:
        raise HTTPException(404)
    form = await request.form()
    for chave, valor in _dados_contrato(form, usuario).items():
        setattr(k, chave, valor)
    registrar(db, usuario, "contrato", f"contrato:{k.id}", f"Alterou contrato de {k.colaborador.nome}", ip_de(request))
    db.commit()
    avisar(request, "Contrato salvo.")
    return voltar(_destino(form, "/contratos"))


@rotas.post("/contratos/{contrato_id}/excluir")
async def excluir_contrato(contrato_id: int, request: Request, db: Session = Depends(get_db),
                           usuario: Usuario = Depends(pode_operar)):
    k = db.get(Contrato, contrato_id)
    if not k:
        raise HTTPException(404)
    form = await request.form()
    nome = k.colaborador.nome
    db.delete(k)
    registrar(db, usuario, "contrato", f"contrato:{contrato_id}", f"Excluiu contrato de {nome}", ip_de(request))
    db.commit()
    avisar(request, "Contrato excluído.")
    return voltar(_destino(form, "/contratos"))


# ---------------------------------------------------------------- compras externas


def sincronizar_lancamento_da_compra(db: Session, compra: CompraExterna, usuario: Usuario,
                                     data_credito: date | None = None) -> None:
    """Compra com pontos recebidos numa conta = lançamento de compra (com custo) na conta. Mudou conta, pontos ou
    valor, ou saiu de "recebida": o lançamento anterior é cancelado (e um novo é feito, se for o caso)."""
    quer = compra.status == "recebida" and compra.conta_id and (compra.pontos or 0) > 0
    atual = db.get(Lancamento, compra.lancamento_id) if compra.lancamento_id else None
    if atual is not None and atual.status != "ativo":
        atual = None
    if atual is not None and (not quer or atual.conta_id != compra.conta_id or atual.pontos != compra.pontos
                              or (atual.valor or 0) != (compra.valor or 0) or atual.fornecedor_id != compra.fornecedor_id):
        data_credito = data_credito or atual.data_efetivacao
        cancelar_lancamento(db, atual)
        atual = None
    if quer and atual is None:
        conta = db.get(ContaPrograma, compra.conta_id)
        l = lancar(db, conta, "compra", compra.pontos, valor=compra.valor if compra.valor is not None else 0.0,
                   data_efetivacao=data_credito or date.today(), fornecedor_id=compra.fornecedor_id,
                   observacao=f"Compra externa nº {compra.id}" + (f" · {compra.observacao}" if compra.observacao else ""),
                   usuario=usuario.nome)
        compra.lancamento_id = l.id
    elif not quer:
        compra.lancamento_id = None
    db.flush()


def _dados_compra(form) -> dict:
    pontos = ler_inteiro(form.get("pontos"))
    valor, milheiro = ler_numero(form.get("valor")), ler_numero(form.get("preco_milheiro"))
    if valor is None and milheiro is not None and pontos:
        valor = round(pontos / 1000 * milheiro, 2)
    status = form.get("status")
    forma = ler_texto(form.get("forma_pagamento"), 30)
    return {
        "data_compra": ler_data(form.get("data_compra")) or date.today(),
        "fornecedor_id": ler_inteiro(form.get("fornecedor_id")) or None, "programa": ler_texto(form.get("programa"), 80),
        "pontos": pontos, "valor": valor, "forma_pagamento": forma, "vencimento": ler_data(form.get("vencimento")),
        "status": status if status in STATUS_COMPRA else "pendente",
        "conta_id": ler_inteiro(form.get("conta_id")) or None, "observacao": _texto_longo(form.get("observacao")),
    }


def _erro_compra(db: Session, dados: dict) -> str | None:
    if not dados["pontos"] or dados["pontos"] <= 0:
        return "Informe quantos pontos foram comprados."
    if dados["valor"] is None:
        return "Informe o valor pago (total ou preço do milheiro)."
    conta = db.get(ContaPrograma, dados["conta_id"]) if dados["conta_id"] else None
    if dados["conta_id"] and conta is None:
        dados["conta_id"] = None
    if conta is not None:
        if dados["programa"] and dados["programa"] != conta.programa:
            return f"A conta escolhida é do {conta.programa}, não do {dados['programa']}."
        dados["programa"] = conta.programa
    if dados["status"] == "recebida" and conta is None:
        return "Para marcar os pontos como recebidos, escolha a conta que recebeu."
    if not dados["programa"]:
        return "Escolha o programa."
    return None


@rotas.get("/compras-externas", response_class=HTMLResponse)
def lista_compras(request: Request, status: str = "", programa: str = "", forma: str = "", de: str = "", ate: str = "",
                  q: str = "", formato: str = "", db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    consulta = (select(CompraExterna).options(selectinload(CompraExterna.fornecedor),
                                              selectinload(CompraExterna.conta).selectinload(ContaPrograma.colaborador))
                .outerjoin(Fornecedor, CompraExterna.fornecedor_id == Fornecedor.id))
    if status in STATUS_COMPRA:
        consulta = consulta.where(CompraExterna.status == status)
    elif status == "abertas":
        consulta = consulta.where(CompraExterna.status.in_(["pendente", "paga"]))
    if programa:
        consulta = consulta.where(CompraExterna.programa == programa)
    if forma:
        consulta = consulta.where(CompraExterna.forma_pagamento == forma)
    inicio, fim = ler_data(de), ler_data(ate)
    if inicio:
        consulta = consulta.where(CompraExterna.data_compra >= inicio)
    if fim:
        consulta = consulta.where(CompraExterna.data_compra <= fim)
    if q.strip():
        termo = f"%{q.strip()}%"
        consulta = consulta.where(or_(Fornecedor.nome.ilike(termo), CompraExterna.observacao.ilike(termo)))
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [
            Coluna("Nº", lambda c: c.id), Coluna("Data da compra", lambda c: c.data_compra, "data"),
            Coluna("Fornecedor", lambda c: c.fornecedor.nome if c.fornecedor else None),
            Coluna("Programa", lambda c: c.programa),
            Coluna("Conta (colaborador)", lambda c: c.conta.colaborador.nome if c.conta else None),
            Coluna("Pontos", lambda c: c.pontos, "pontos"),
        ]
        if pode(usuario, "custos"):
            colunas += [Coluna("Valor", lambda c: c.valor, "dinheiro"),
                        Coluna("R$ por milheiro", lambda c: c.preco_milheiro, "dinheiro")]
        colunas += [Coluna("Forma de pagamento", lambda c: c.forma_pagamento),
                    Coluna("Vencimento", lambda c: c.vencimento, "data"),
                    Coluna("Status", lambda c: STATUS_COMPRA.get(c.status, c.status)),
                    Coluna("Pontos já na conta", lambda c: sim_nao(c.lancamento_id)),
                    Coluna("Observação", lambda c: c.observacao), Coluna("Registrada por", lambda c: c.usuario)]
        itens = db.scalars(consulta.order_by(CompraExterna.data_compra.desc(), CompraExterna.id.desc())).all()
        return exportar(request, db, usuario, "Compras externas", colunas, itens)
    compras = db.scalars(consulta.order_by(CompraExterna.data_compra.desc(), CompraExterna.id.desc()).limit(1000)).all()
    validas = [c for c in compras if c.status != "cancelada"]
    abertas = db.scalars(select(CompraExterna).where(CompraExterna.status.in_(["pendente", "paga"]))).all()
    resumo = {
        "a_pagar": round(sum(c.valor or 0 for c in abertas if c.status == "pendente"), 2),
        "a_receber": sum(c.pontos or 0 for c in abertas),
        "pontos": sum(c.pontos or 0 for c in validas), "valor": round(sum(c.valor or 0 for c in validas), 2),
        "vencidas": sum(1 for c in abertas if c.status == "pendente" and c.vencimento and c.vencimento < date.today()),
    }
    resumo["milheiro"] = round(resumo["valor"] / resumo["pontos"] * 1000, 2) if resumo["pontos"] else None
    programas = sorted(set(db.scalars(select(CompraExterna.programa).where(CompraExterna.programa.is_not(None)).distinct())))
    formas = sorted(set(METODOS_PAGAMENTO) | set(db.scalars(
        select(CompraExterna.forma_pagamento).where(CompraExterna.forma_pagamento.is_not(None)).distinct())))
    return render(request, "mvo/compras_externas.html", usuario, compras=compras, resumo=resumo, status=status,
                  programa=programa, forma=forma, de=de, ate=ate, q=q, programas=programas, formas=formas,
                  status_compra=STATUS_COMPRA, hoje_=date.today())


def _tela_compra(request: Request, db: Session, usuario: Usuario, compra: CompraExterna | None, d: dict,
                 status_code: int = 200):
    fornecedores = db.scalars(select(Fornecedor).where(Fornecedor.ativo.is_(True)).order_by(Fornecedor.nome)).all()
    programas = sorted(db.scalars(select(ProgramaMilhas.nome).where(ProgramaMilhas.ativo.is_not(False))), key=str.lower)
    lanc = db.get(Lancamento, compra.lancamento_id) if compra and compra.lancamento_id else None
    resposta = render(request, "mvo/compra_externa_form.html", usuario, compra=compra, d=d, fornecedores=fornecedores,
                      programas=programas, grupos=opcoes_contas(db), status_compra=STATUS_COMPRA,
                      formas=METODOS_PAGAMENTO, hoje_iso=date.today().isoformat(),
                      credito_iso=(lanc.data_efetivacao if lanc else date.today()).isoformat())
    resposta.status_code = status_code
    return resposta


@rotas.get("/compras-externas/nova", response_class=HTMLResponse)
def nova_compra(request: Request, conta: int = 0, fornecedor: int = 0, db: Session = Depends(get_db),
                usuario: Usuario = Depends(pode_comprar)):
    d: dict = {"data_compra": date.today(), "status": "pendente", "fornecedor_id": fornecedor or None}
    if conta and (c := db.get(ContaPrograma, conta)):
        d.update(conta_id=c.id, programa=c.programa)
    return _tela_compra(request, db, usuario, None, d)


@rotas.post("/compras-externas/nova")
async def criar_compra(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_comprar)):
    form = await request.form()
    dados = _dados_compra(form)
    erro = _erro_compra(db, dados)
    if erro:
        avisar(request, erro, "erro")
        return _tela_compra(request, db, usuario, None, dados, status_code=400)
    compra = CompraExterna(**dados, usuario=usuario.nome)
    db.add(compra)
    db.flush()
    try:
        sincronizar_lancamento_da_compra(db, compra, usuario, ler_data(form.get("data_credito")))
    except ErroMVO as e:
        db.rollback()
        avisar(request, str(e), "erro")
        return _tela_compra(request, db, usuario, None, dados, status_code=400)
    registrar(db, usuario, "compra_externa", f"compra:{compra.id}",
              f"{formatar_pontos(compra.pontos)} pontos {compra.programa} ({STATUS_COMPRA[compra.status]})", ip_de(request))
    db.commit()
    avisar(request, "Compra registrada." + (" Os pontos entraram na conta." if compra.lancamento_id else ""))
    return voltar("/compras-externas")


def _compra_ou_404(db: Session, compra_id: int) -> CompraExterna:
    compra = db.get(CompraExterna, compra_id)
    if not compra:
        raise HTTPException(404, "Compra não encontrada.")
    return compra


@rotas.get("/compras-externas/{compra_id}", response_class=HTMLResponse)
def ver_compra(compra_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_comprar)):
    compra = _compra_ou_404(db, compra_id)
    return _tela_compra(request, db, usuario, compra, compra)


@rotas.post("/compras-externas/{compra_id}")
async def editar_compra(compra_id: int, request: Request, db: Session = Depends(get_db),
                        usuario: Usuario = Depends(pode_comprar)):
    compra = _compra_ou_404(db, compra_id)
    form = await request.form()
    dados = _dados_compra(form)
    erro = _erro_compra(db, dados)
    if erro:
        avisar(request, erro, "erro")
        return _tela_compra(request, db, usuario, compra, {**dados, "id": compra.id}, status_code=400)
    for chave, valor in dados.items():
        setattr(compra, chave, valor)
    try:
        sincronizar_lancamento_da_compra(db, compra, usuario, ler_data(form.get("data_credito")))
    except ErroMVO as e:
        db.rollback()
        avisar(request, str(e), "erro")
        return voltar(f"/compras-externas/{compra_id}")
    registrar(db, usuario, "compra_externa", f"compra:{compra.id}",
              f"Alterou: {formatar_pontos(compra.pontos)} pontos {compra.programa} ({STATUS_COMPRA[compra.status]})",
              ip_de(request))
    db.commit()
    avisar(request, "Compra salva.")
    return voltar("/compras-externas")


@rotas.post("/compras-externas/{compra_id}/status")
async def mudar_status_compra(compra_id: int, request: Request, db: Session = Depends(get_db),
                              usuario: Usuario = Depends(pode_comprar)):
    compra = _compra_ou_404(db, compra_id)
    form = await request.form()
    novo = form.get("status")
    if novo not in STATUS_COMPRA:
        raise HTTPException(400)
    if novo == "recebida" and not compra.conta_id:
        avisar(request, "Escolha a conta que recebeu os pontos (Editar) antes de marcar como recebida.", "erro")
        return voltar(f"/compras-externas/{compra.id}")
    compra.status = novo
    sincronizar_lancamento_da_compra(db, compra, usuario)
    registrar(db, usuario, "compra_externa", f"compra:{compra.id}", f"Marcou como {STATUS_COMPRA[novo]}", ip_de(request))
    db.commit()
    avisar(request, {"paga": "Compra marcada como paga.", "recebida": "Pontos recebidos: entraram na conta.",
                     "cancelada": "Compra cancelada.", "pendente": "Compra voltou para pendente."}[novo])
    return voltar(_destino(form, "/compras-externas"))


@rotas.post("/compras-externas/{compra_id}/excluir")
def excluir_compra(compra_id: int, request: Request, db: Session = Depends(get_db),
                   usuario: Usuario = Depends(pode_comprar)):
    compra = _compra_ou_404(db, compra_id)
    compra.status = "cancelada"
    sincronizar_lancamento_da_compra(db, compra, usuario)  # cancela o lançamento, se houver
    db.delete(compra)
    registrar(db, usuario, "compra_externa", f"compra:{compra_id}", "Excluiu compra externa", ip_de(request))
    db.commit()
    avisar(request, "Compra excluída.")
    return voltar("/compras-externas")


# ---------------------------------------------------------------- programas (lista de contas)


@rotas.get("/contas", response_class=HTMLResponse)
def lista_contas(request: Request, q: str = "", programa: str = "", ver: str = "ativas", pessoal: int = 0,
                 limite: int = 300, formato: str = "", db: Session = Depends(get_db),
                 usuario: Usuario = Depends(pode_ver)):
    consulta = (select(ContaPrograma).join(Colaborador).options(selectinload(ContaPrograma.colaborador))
                .order_by(ContaPrograma.programa, Colaborador.nome))
    if ver == "ativas":
        consulta = consulta.where(ContaPrograma.ativa.is_(True), Colaborador.ativo.is_(True))
    elif ver == "inativas":
        consulta = consulta.where(or_(ContaPrograma.ativa.is_(False), Colaborador.ativo.is_(False)))
    elif ver == "com_saldo":
        consulta = consulta.where(ContaPrograma.ativa.is_(True), ContaPrograma.saldo > 0)
    if not pessoal:
        consulta = consulta.where(ContaPrograma.uso_pessoal.is_not(True))
    if programa:
        consulta = consulta.where(ContaPrograma.programa == programa)
    if q.strip():
        termo = f"%{q.strip()}%"
        condicoes = [Colaborador.nome.ilike(termo), ContaPrograma.observacoes.ilike(termo),
                     ContaPrograma.numero.ilike(termo)]
        if so_digitos(q):
            condicoes.append(Colaborador.cpf.like(f"%{so_digitos(q)}%"))
        consulta = consulta.where(or_(*condicoes))
    planilha = quer_planilha(formato)
    if planilha:
        checar_exportar(usuario)
    limite = max(50, min(limite, 3000))
    contas = db.scalars(consulta if planilha else consulta.limit(limite + 1)).all()
    mais = len(contas) > limite and not planilha
    contas = contas if planilha else contas[:limite]
    emissoes: dict[int, int] = defaultdict(int)
    if contas:
        for conta_id, n in db.execute(
            select(Emissao.conta_id, func.count(Emissao.id))
            .where(Emissao.status == "ativa", Emissao.conta_id.in_([c.id for c in contas]))
            .group_by(Emissao.conta_id)
        ):
            emissoes[conta_id] = n
    if planilha:
        ve_custo = pode(usuario, "custos")
        colunas = [
            Coluna("Programa", lambda c: c.programa), Coluna("Colaborador", lambda c: c.colaborador.nome),
            Coluna("CPF", lambda c: col_.documento(c.colaborador.cpf)), Coluna("Número / CPF na conta", lambda c: c.numero),
            Coluna("E-mail no programa", lambda c: c.email_programa), Coluna("Código da conta", lambda c: c.codigo_conta),
            Coluna("Login salvo", lambda c: sim_nao(c.tem_credenciais)),
            Coluna("Saldo", lambda c: c.saldo, "pontos"),
            Coluna("Disponível", lambda c: 0 if c.uso_pessoal else c.disponivel, "pontos"),
            Coluna("Pessoal", lambda c: c.saldo if c.uso_pessoal else (c.saldo_reservado or 0), "pontos"),
            Coluna("A creditar", lambda c: c.saldo_futuro or 0, "pontos"),
        ]
        if ve_custo:
            colunas += [Coluna("Custo médio (R$ por milheiro)", lambda c: c.custo_medio, "dinheiro"),
                        Coluna("Custo do saldo", lambda c: c.custo_total, "dinheiro"),
                        Coluna("Valor disponível", lambda c: 0 if c.uso_pessoal else _valor(c, c.disponivel), "dinheiro")]
        colunas += [Coluna("Emissões", lambda c: emissoes.get(c.id, 0)),
                    Coluna("Última conferência", lambda c: c.data_conciliacao, "data"),
                    Coluna("Uso pessoal", lambda c: sim_nao(c.uso_pessoal)),
                    Coluna("Situação", lambda c: "Ativa" if c.ativa and c.colaborador.ativo else "Inativa"),
                    Coluna("Observações", lambda c: c.observacoes)]
        return exportar(request, db, usuario, "Programas (contas)", colunas, contas)
    totais = {
        "contas": len(contas), "disponivel": sum(c.disponivel for c in contas if not c.uso_pessoal),
        "pessoal": sum((c.saldo or 0) if c.uso_pessoal else (c.saldo_reservado or 0) for c in contas),
        "valor": round(sum(_valor(c, c.disponivel) for c in contas if not c.uso_pessoal), 2),
    }
    programas = sorted(set(db.scalars(select(ContaPrograma.programa).distinct())), key=str.lower)
    return render(request, "mvo/contas.html", usuario, contas=contas, emissoes=emissoes, totais=totais, q=q,
                  programa=programa, ver=ver, pessoal=pessoal, programas=programas, mais=mais, limite=limite,
                  valor=_valor)


def _valor(conta: ContaPrograma, pontos: int | None) -> float:
    """Quanto os pontos custaram (pelo custo médio da conta)."""
    if not pontos or not conta.custo_medio:
        return 0.0
    return round(pontos / 1000 * conta.custo_medio, 2)


@rotas.get("/contas/nova", response_class=HTMLResponse)
def nova_conta(request: Request, colaborador: int = 0, db: Session = Depends(get_db),
               usuario: Usuario = Depends(pode_operar)):
    colaboradores = db.scalars(select(Colaborador).where(Colaborador.ativo.is_(True)).order_by(Colaborador.nome)).all()
    cfgs = db.scalars(select(ProgramaMilhas).where(ProgramaMilhas.ativo.is_not(False)).order_by(ProgramaMilhas.nome)).all()
    return render(request, "mvo/conta_form.html", usuario, colaboradores=colaboradores, colaborador_id=colaborador,
                  programas=cfgs)


@rotas.post("/contas/nova")
async def criar_conta_avulsa(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    form = await request.form()
    col = db.get(Colaborador, ler_inteiro(form.get("colaborador_id")) or 0)
    if not col:
        avisar(request, "Escolha o colaborador dono da conta.", "erro")
        return voltar("/contas/nova")
    conta = criar_conta_do_formulario(request, db, usuario, col, form)
    if isinstance(conta, str):
        db.rollback()
        avisar(request, conta, "erro")
        return voltar(f"/contas/nova?colaborador={col.id}")
    db.commit()
    avisar(request, f"Conta {conta.programa} criada para {col.nome}.")
    return voltar(f"/contas/{conta.id}")
