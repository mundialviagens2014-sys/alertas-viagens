"""Emissões (passagens emitidas) e disponibilidade (quem tem pontos e CPF livre para emitir)."""
from collections import OrderedDict
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Cliente, Match, ProgramaMilhas, Promocao, Usuario
from ..models_mvo import FORMAS_EMISSAO, Colaborador, ContaPrograma, Emissao, EmissaoPassageiro, Fornecedor
from ..mvo.contas import ErroMVO, disponibilidade, formatar_pontos, registrar, resumo_por_programa
from ..mvo.emissoes import cancelar_emissao, criar_emissao, editar_emissao
from ..permissoes import pode
from ..servicos import preco_reais
from ..web import avisar, exigir, ip_de, ler_data, ler_inteiro, ler_numero, render, voltar

rotas = APIRouter()
pode_ver = exigir("milhas_ver")
pode_operar = exigir("milhas_ver", "milhas_operar")


def _texto(valor) -> str:
    return (valor or "").strip() if isinstance(valor, str) else ""


# ---------------------------------------------------------------- início do Manager


@rotas.get("/manager")
def inicio_manager(usuario: Usuario = Depends(pode_ver)):
    """A chave "Manager" do menu leva para cá (por enquanto, a disponibilidade)."""
    return voltar("/disponibilidade")


# ---------------------------------------------------------------- disponibilidade


@rotas.get("/disponibilidade", response_class=HTMLResponse)
def tela_disponibilidade(request: Request, programa: str = "", pontos: str = "", passageiros: int = 0,
                         q: str = "", pessoal: int = 0, zeradas: int = 0, db: Session = Depends(get_db),
                         usuario: Usuario = Depends(pode_ver)):
    minimo = ler_inteiro(pontos) or 0
    linhas = disponibilidade(db, programa=programa or None, busca=q, pontos_min=minimo,
                             passageiros=max(passageiros, 0), incluir_pessoal=bool(pessoal),
                             incluir_zeradas=bool(zeradas))
    resumo = resumo_por_programa(db)
    programas = [r["programa"] for r in resumo]
    return render(request, "mvo/disponibilidade.html", usuario, linhas=linhas, resumo=resumo, programas=programas,
                  programa=programa, pontos=pontos, minimo=minimo, passageiros=passageiros, q=q, pessoal=pessoal,
                  zeradas=zeradas)


# ---------------------------------------------------------------- lista de emissões


@rotas.get("/emissoes", response_class=HTMLResponse)
def lista_emissoes(request: Request, de: str = "", ate: str = "", q: str = "", programa: str = "", forma: str = "",
                   ver: str = "ativas", db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    hoje = date.today()
    inicio = ler_data(de) if de else hoje - timedelta(days=30)
    fim = ler_data(ate) if ate else None
    consulta = (select(Emissao).outerjoin(ContaPrograma, Emissao.conta_id == ContaPrograma.id)
                .outerjoin(Colaborador, ContaPrograma.colaborador_id == Colaborador.id)
                .options(selectinload(Emissao.passageiros), selectinload(Emissao.fornecedor),
                         selectinload(Emissao.conta).selectinload(ContaPrograma.colaborador)))
    if inicio:
        consulta = consulta.where(Emissao.data_emissao >= datetime.combine(inicio, time.min))
    if fim:
        consulta = consulta.where(Emissao.data_emissao < datetime.combine(fim + timedelta(days=1), time.min))
    if programa:
        consulta = consulta.where(Emissao.programa == programa)
    if forma in FORMAS_EMISSAO:
        consulta = consulta.where(Emissao.forma == forma)
    if ver in ("ativas", "canceladas"):
        consulta = consulta.where(Emissao.status == ("ativa" if ver == "ativas" else "cancelada"))
    if q.strip():
        termo = f"%{q.strip()}%"
        consulta = consulta.where(or_(
            Emissao.localizador.ilike(termo), Emissao.cliente_nome.ilike(termo), Colaborador.nome.ilike(termo),
            Emissao.passageiros.any(EmissaoPassageiro.nome.ilike(termo)),
        ))
    emissoes = db.scalars(consulta.order_by(Emissao.data_emissao.desc(), Emissao.id.desc()).limit(1000)).all()
    ativas = [e for e in emissoes if e.status == "ativa"]
    resumo = {
        "emissoes": len(ativas),
        "passageiros": sum(e.qtd_passageiros or 0 for e in ativas),
        "pontos": sum(e.pontos or 0 for e in ativas if e.forma == "programa_proprio"),
        "vendido": round(sum(e.preco_venda or 0 for e in ativas), 2),
        "custo": round(sum(e.custo or 0 for e in ativas), 2),
        "lucro": round(sum(e.lucro or 0 for e in ativas if e.lucro is not None), 2),
        "sem_venda": sum(1 for e in ativas if e.preco_venda is None),
    }
    programas = sorted(set(db.scalars(select(Emissao.programa).where(Emissao.programa.is_not(None)).distinct())))
    return render(request, "mvo/emissoes.html", usuario, emissoes=emissoes, resumo=resumo, programas=programas,
                  de=inicio.isoformat() if inicio else "", ate=fim.isoformat() if fim else "", q=q,
                  programa=programa, forma=forma, ver=ver)


# ---------------------------------------------------------------- nova emissão


def _contas_para_emitir(db: Session) -> "OrderedDict[str, list]":
    """Contas com pontos, por programa, a de menor custo primeiro (para o campo de escolha)."""
    grupos: OrderedDict[str, list] = OrderedDict()
    for linha in sorted(disponibilidade(db, incluir_zeradas=False), key=lambda l: l.conta.programa):
        grupos.setdefault(linha.conta.programa, []).append(linha)
    for programa, linhas in grupos.items():
        linhas.sort(key=lambda l: (l.custo_medio is None, l.custo_medio or 0, -l.conta.disponivel))
    return grupos


def _tela_emissao(request: Request, db: Session, usuario: Usuario, v: dict, passageiros: list[tuple[str, str]],
                  status_code: int = 200):
    conta_escolhida = db.get(ContaPrograma, v.get("conta_id")) if v.get("conta_id") else None
    grupos = _contas_para_emitir(db)
    if conta_escolhida and not any(l.conta.id == conta_escolhida.id for ls in grupos.values() for l in ls):
        # conta sem saldo (ex.: emissão forçada): continua na lista para não perder a escolha
        from ..mvo.contas import LinhaDisponibilidade, situacao

        cfg = db.scalar(select(ProgramaMilhas).where(ProgramaMilhas.nome == conta_escolhida.programa))
        grupos.setdefault(conta_escolhida.programa, []).append(
            LinhaDisponibilidade(conta_escolhida, situacao(db, conta_escolhida, cfg), cfg.preco_b2c if cfg else None))
    fornecedores = db.scalars(select(Fornecedor).where(Fornecedor.ativo.is_(True)).order_by(Fornecedor.nome)).all()
    clientes = db.scalars(select(Cliente.nome).where(Cliente.ativo.is_(True)).order_by(Cliente.nome)).all()
    programas = sorted(db.scalars(select(ProgramaMilhas.nome)))
    precos = {p.nome: p.preco_b2c for p in db.scalars(select(ProgramaMilhas)) if p.preco_b2c}
    resposta = render(request, "mvo/emissao_form.html", usuario, v=v, passageiros=passageiros or [("", "")],
                      grupos=grupos, fornecedores=fornecedores, clientes=clientes, programas=programas,
                      precos=precos, formas=FORMAS_EMISSAO)
    resposta.status_code = status_code
    return resposta


@rotas.get("/emissoes/nova", response_class=HTMLResponse)
def nova_emissao(request: Request, conta: int = 0, match: int = 0, promocao: int = 0,
                 db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    v: dict = {"forma": "programa_proprio", "data_emissao": date.today().isoformat()}
    if conta:
        c = db.get(ContaPrograma, conta)
        if c:
            v.update(conta_id=c.id, programa=c.programa)
    m = db.get(Match, match) if match else None
    promo = m.promocao if m else (db.get(Promocao, promocao) if promocao else None)
    if promo:
        v.update(origem=promo.origem, destino=promo.destino, tipo_viagem=promo.tipo_viagem or "",
                 companhia=promo.companhia or "", promocao_id=promo.id)
        if promo.milhas and not promo.preco:
            v.update(pontos=promo.milhas, programa=promo.programa or v.get("programa"))
        reais = preco_reais(promo)
        if reais:
            v["preco_venda"] = reais
        if promo.preco and not promo.milhas:
            v["forma"] = "pagante"
    if m:
        v.update(cliente_nome=m.cliente.nome, cliente_id=m.cliente.id)
    return _tela_emissao(request, db, usuario, v, [])


@rotas.post("/emissoes/nova")
async def salvar_emissao(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    form = await request.form()
    v = {k: form.get(k) for k in (
        "forma", "conta_id", "fornecedor_id", "programa", "companhia", "localizador", "origem", "destino",
        "tipo_viagem", "data_ida", "data_volta", "pontos", "preco_venda", "taxa_embarque", "taxa_bagagem",
        "outros_custos", "cliente_nome", "cliente_id", "promocao_id", "observacao", "data_emissao", "qtd_passageiros",
    )}
    v["forcar"] = form.get("forcar") == "true"
    nomes, cpfs = form.getlist("passageiro_nome"), form.getlist("passageiro_cpf")
    passageiros = [(_texto(n), _texto(c)) for n, c in zip(nomes, cpfs) if _texto(n)]

    forma = v["forma"] if v["forma"] in FORMAS_EMISSAO else "programa_proprio"
    conta = db.get(ContaPrograma, ler_inteiro(v["conta_id"])) if forma == "programa_proprio" and v["conta_id"] else None
    cliente_id = ler_inteiro(v["cliente_id"])
    cliente_nome = _texto(v["cliente_nome"])
    if cliente_nome and not cliente_id:
        achado = db.scalar(select(Cliente).where(func.lower(Cliente.nome) == cliente_nome.lower()))
        cliente_id = achado.id if achado else None
    elif cliente_id and not db.get(Cliente, cliente_id):
        cliente_id = None
    data_emissao = ler_data(v["data_emissao"])
    promocao_id = ler_inteiro(v["promocao_id"])
    try:
        emissao = criar_emissao(
            db, forma=forma, conta=conta,
            fornecedor_id=ler_inteiro(v["fornecedor_id"]) if forma == "fornecedor" else None,
            programa=_texto(v["programa"]) or None, companhia=v["companhia"], localizador=v["localizador"],
            origem=(_texto(v["origem"])[:3] or None), destino=(_texto(v["destino"])[:3] or None),
            tipo_viagem=v["tipo_viagem"], data_ida=ler_data(v["data_ida"]), data_volta=ler_data(v["data_volta"]),
            pontos=ler_inteiro(v["pontos"]), preco_venda=ler_numero(v["preco_venda"]),
            taxa_embarque=ler_numero(v["taxa_embarque"]), taxa_bagagem=ler_numero(v["taxa_bagagem"]),
            outros_custos=ler_numero(v["outros_custos"]), passageiros=passageiros,
            qtd_passageiros=ler_inteiro(v["qtd_passageiros"]), cliente_id=cliente_id, cliente_nome=cliente_nome,
            promocao_id=promocao_id if promocao_id and db.get(Promocao, promocao_id) else None,
            observacao=v["observacao"], usuario=usuario.nome,
            data_emissao=datetime.combine(data_emissao, datetime.now().time()) if data_emissao else None,
            forcar=v["forcar"],
        )
    except ErroMVO as erro:
        db.rollback()
        avisar(request, str(erro), "erro")
        v["pode_forcar"] = True
        return _tela_emissao(request, db, usuario, v, passageiros, status_code=400)
    resumo = f"{emissao.localizador or 'sem localizador'} {emissao.rota}".strip()
    if emissao.conta:
        resumo += f" · {formatar_pontos(emissao.pontos)} pontos de {emissao.conta.programa} de {emissao.conta.colaborador.nome}"
    registrar(db, usuario, "emissao", f"emissao:{emissao.id}", resumo, ip_de(request))
    db.commit()
    avisar(request, "Emissão registrada" + (f": {formatar_pontos(emissao.pontos)} pontos descontados da conta."
                                            if emissao.conta else "."))
    return voltar(f"/emissoes/{emissao.id}")


# ---------------------------------------------------------------- detalhe, edição e cancelamento


@rotas.get("/emissoes/{emissao_id}", response_class=HTMLResponse)
def ver_emissao(emissao_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    e = db.get(Emissao, emissao_id)
    if not e:
        raise HTTPException(404)
    return render(request, "mvo/emissao.html", usuario, e=e)


@rotas.post("/emissoes/{emissao_id}")
async def alterar_emissao(emissao_id: int, request: Request, db: Session = Depends(get_db),
                          usuario: Usuario = Depends(pode_operar)):
    e = db.get(Emissao, emissao_id)
    if not e:
        raise HTTPException(404)
    form = await request.form()
    localizador = _texto(form.get("localizador")).upper() or None
    if localizador and localizador != e.localizador and db.scalar(
        select(Emissao).where(Emissao.localizador == localizador, Emissao.status == "ativa", Emissao.id != e.id)
    ):
        avisar(request, f"Já existe outra emissão ativa com o localizador {localizador}.", "erro")
        return voltar(f"/emissoes/{e.id}")
    campos = dict(
        localizador=localizador, companhia=_texto(form.get("companhia")) or None,
        origem=_texto(form.get("origem"))[:3] or None, destino=_texto(form.get("destino"))[:3] or None,
        tipo_viagem=form.get("tipo_viagem") if form.get("tipo_viagem") in ("ida", "ida_volta") else None,
        data_ida=ler_data(form.get("data_ida")), data_volta=ler_data(form.get("data_volta")),
        preco_venda=ler_numero(form.get("preco_venda")), taxa_embarque=ler_numero(form.get("taxa_embarque")),
        taxa_bagagem=ler_numero(form.get("taxa_bagagem")), cliente_nome=_texto(form.get("cliente_nome")) or None,
        observacao=_texto(form.get("observacao")) or None,
    )
    if pode(usuario, "custos") and "outros_custos" in form:  # quem não vê custos não apaga o que o gestor lançou
        campos["outros_custos"] = ler_numero(form.get("outros_custos"))
    editar_emissao(db, e, **campos)
    registrar(db, usuario, "emissao_editada", f"emissao:{e.id}", e.localizador or f"emissão {e.id}", ip_de(request))
    db.commit()
    avisar(request, "Emissão salva.")
    return voltar(f"/emissoes/{e.id}")


@rotas.post("/emissoes/{emissao_id}/cancelar")
def cancelar(emissao_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    e = db.get(Emissao, emissao_id)
    if not e:
        raise HTTPException(404)
    if e.status != "ativa":
        avisar(request, "Esta emissão já está cancelada.", "erro")
        return voltar(f"/emissoes/{e.id}")
    cancelar_emissao(db, e)
    registrar(db, usuario, "emissao_cancelada", f"emissao:{e.id}", e.localizador or f"emissão {e.id}", ip_de(request))
    db.commit()
    avisar(request, "Emissão cancelada." + (" Os pontos voltaram para a conta." if e.conta else ""))
    return voltar(f"/emissoes/{e.id}")
