"""Manager: página inicial (painel), clientes (compradores) e comissões por emissor."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Cliente, Usuario
from ..models_mvo import CompraExterna, ContaPrograma, Contrato, Emissao, Lancamento, PedidoEmissao
from ..mvo.comissoes import (
    BASES,
    comissoes_do_mes,
    intervalo_do_mes,
    ler_mes,
    mes_vizinho,
    nome_do_mes,
    planilha,
)
from ..mvo.contas import registrar, resumo_por_programa, so_digitos
from ..permissoes import pode
from ..servicos import ajustar_nome, normalizar_telefone
from ..web import avisar, exigir, ip_de, ler_data, ler_numero, render, voltar

rotas = APIRouter()
pode_ver = exigir("milhas_ver")
pode_operar = exigir("milhas_ver", "milhas_operar")
pode_custos = exigir("milhas_ver", "custos")
pode_definir_comissao = exigir("custos", "configurar")


def _texto(valor) -> str | None:
    valor = (valor or "").strip() if isinstance(valor, str) else ""
    return valor or None


# ---------------------------------------------------------------- início do Manager


@rotas.get("/manager", response_class=HTMLResponse)
def painel(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    hoje = date.today()
    inicio, fim = intervalo_do_mes(hoje.year, hoje.month)
    do_mes = db.scalars(
        select(Emissao).where(Emissao.status == "ativa", Emissao.data_emissao >= inicio, Emissao.data_emissao < fim)
    ).all()
    mes = {
        "emissoes": len(do_mes),
        "passageiros": sum(e.qtd_passageiros or 0 for e in do_mes),
        "pontos": sum(e.pontos or 0 for e in do_mes if e.forma == "programa_proprio"),
        "vendido": round(sum(e.preco_venda or 0 for e in do_mes), 2),
        "lucro": round(sum(e.lucro or 0 for e in do_mes if e.lucro is not None), 2),
    }
    ultimas = db.scalars(
        select(Emissao).options(selectinload(Emissao.conta).selectinload(ContaPrograma.colaborador))
        .order_by(Emissao.data_emissao.desc(), Emissao.id.desc()).limit(8)
    ).all()
    a_creditar = db.scalars(
        select(Lancamento).where(Lancamento.status == "ativo", Lancamento.data_efetivacao > hoje)
        .options(selectinload(Lancamento.conta).selectinload(ContaPrograma.colaborador))
        .order_by(Lancamento.data_efetivacao).limit(8)
    ).all()
    pedidos = db.scalars(
        select(PedidoEmissao).where(PedidoEmissao.status == "aguardando")
        .options(selectinload(PedidoEmissao.agencia), selectinload(PedidoEmissao.cliente))
        .order_by(PedidoEmissao.criado_em).limit(8)
    ).all()
    contratos = db.scalars(select(Contrato).where(Contrato.status == "ativo", Contrato.data_vencimento.is_not(None),
                                                  Contrato.data_vencimento <= hoje + timedelta(days=30))).all()
    compras = db.scalars(select(CompraExterna).where(CompraExterna.status.in_(["pendente", "paga"]))).all()
    avisos_manager = {
        "contratos_vencidos": sum(1 for k in contratos if k.vencido),
        "contratos_vencem": sum(1 for k in contratos if not k.vencido),
        "compras_receber": sum(1 for c in compras), "pontos_receber": sum(c.pontos or 0 for c in compras),
        "compras_vencidas": sum(1 for c in compras if c.status == "pendente" and c.vencimento and c.vencimento < hoje),
    }
    return render(request, "mvo/painel.html", usuario, programas=resumo_por_programa(db), mes=mes,
                  nome_mes=nome_do_mes(hoje.year, hoje.month), ultimas=ultimas, a_creditar=a_creditar,
                  mes_atual=f"{hoje.year:04d}-{hoje.month:02d}", pedidos=pedidos, agora=datetime.now(),
                  avisos_manager=avisos_manager)


# ---------------------------------------------------------------- clientes (quem compra passagens)


def _totais_por_cliente(db: Session, ids: list[int] | None = None) -> dict[int, dict]:
    consulta = select(Emissao).where(Emissao.status == "ativa", Emissao.cliente_id.is_not(None))
    if ids is not None:
        consulta = consulta.where(Emissao.cliente_id.in_(ids or [0]))
    totais: dict[int, dict] = {}
    for e in db.scalars(consulta):
        t = totais.setdefault(e.cliente_id, {"emissoes": 0, "passageiros": 0, "vendido": 0.0, "lucro": 0.0,
                                             "ultima": None})
        t["emissoes"] += 1
        t["passageiros"] += e.qtd_passageiros or 0
        t["vendido"] += e.preco_venda or 0
        t["lucro"] += e.lucro or 0
        if t["ultima"] is None or e.data_emissao > t["ultima"]:
            t["ultima"] = e.data_emissao
    return totais


@rotas.get("/manager/clientes", response_class=HTMLResponse)
def lista_clientes(request: Request, q: str = "", ver: str = "todos", ordem: str = "nome",
                   db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    consulta = select(Cliente).order_by(Cliente.nome)
    if q.strip():
        termo = f"%{q.strip()}%"
        condicoes = [Cliente.nome.ilike(termo), Cliente.email.ilike(termo)]
        if so_digitos(q):
            condicoes += [Cliente.telefone.like(f"%{so_digitos(q)}%"), Cliente.documento.like(f"%{so_digitos(q)}%")]
        consulta = consulta.where(or_(*condicoes))
    if ver == "inativos":
        consulta = consulta.where(Cliente.ativo.is_(False))
    elif ver != "todos":
        consulta = consulta.where(Cliente.ativo.is_(True))
    clientes = db.scalars(consulta.limit(3000)).all()
    totais = _totais_por_cliente(db)
    if ver == "compraram":
        clientes = [c for c in clientes if c.id in totais]
    if ordem == "vendido":
        clientes.sort(key=lambda c: -totais.get(c.id, {}).get("vendido", 0))
    elif ordem == "recentes":
        clientes.sort(key=lambda c: totais.get(c.id, {}).get("ultima") or datetime.min, reverse=True)
    resumo = {
        "clientes": len(clientes),
        "compraram": sum(1 for c in clientes if c.id in totais),
        "vendido": round(sum(totais.get(c.id, {}).get("vendido", 0) for c in clientes), 2),
    }
    return render(request, "mvo/clientes_manager.html", usuario, clientes=clientes, totais=totais, q=q, ver=ver,
                  ordem=ordem, resumo=resumo)


def _dados_cliente(form) -> dict:
    tel = so_digitos(form.get("telefone"))
    return {
        "nome": ajustar_nome(form.get("nome") or ""),
        "telefone": normalizar_telefone(tel) if tel else "",
        "email": _texto(form.get("email")),
        "documento": so_digitos(form.get("documento")) or None,
        "data_nascimento": ler_data(form.get("data_nascimento")),
        "classificacao": _texto(form.get("classificacao")),
        "observacoes": _texto(form.get("observacoes")),
    }


def _erro_cliente(db: Session, dados: dict, exceto: int | None = None) -> str | None:
    if not dados["nome"]:
        return "Informe o nome."
    if len(dados["telefone"]) < 10:
        return "Informe o WhatsApp com DDD (o cadastro de clientes é o mesmo dos alertas)."
    if dados["documento"] and len(dados["documento"]) not in (11, 14):
        return "O CPF precisa ter 11 números (CNPJ, 14)."
    consulta = select(Cliente).where(Cliente.telefone == dados["telefone"])
    if exceto:
        consulta = consulta.where(Cliente.id != exceto)
    outro = db.scalar(consulta)
    if outro:
        return f"Esse WhatsApp já é de {outro.nome}."
    return None


@rotas.get("/manager/clientes/novo", response_class=HTMLResponse)
def novo_cliente(request: Request, usuario: Usuario = Depends(pode_operar)):
    return render(request, "mvo/cliente_manager_form.html", usuario, dados={})


@rotas.post("/manager/clientes/novo")
async def criar_cliente(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    form = await request.form()
    dados = _dados_cliente(form)
    erro = _erro_cliente(db, dados)
    if erro:
        avisar(request, erro, "erro")
        return render(request, "mvo/cliente_manager_form.html", usuario, dados=dados)
    cliente = Cliente(**dados, consentimento=form.get("consentimento") == "true", ativo=True)
    db.add(cliente)
    db.flush()
    registrar(db, usuario, "cliente_salvo", f"cliente:{cliente.id}", cliente.nome, ip_de(request))
    db.commit()
    avisar(request, f"Cliente {cliente.nome} cadastrado.")
    return voltar(f"/manager/clientes/{cliente.id}")


@rotas.get("/manager/clientes/{cliente_id}", response_class=HTMLResponse)
def ver_cliente(cliente_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(404)
    emissoes = db.scalars(
        select(Emissao).where(Emissao.cliente_id == cliente.id)
        .options(selectinload(Emissao.passageiros), selectinload(Emissao.conta).selectinload(ContaPrograma.colaborador))
        .order_by(Emissao.data_emissao.desc())
    ).all()
    totais = _totais_por_cliente(db, [cliente.id]).get(cliente.id, {"emissoes": 0, "passageiros": 0, "vendido": 0,
                                                                     "lucro": 0, "ultima": None})
    passageiros: dict[str, dict] = {}
    for e in emissoes:
        if e.status != "ativa":
            continue
        for p in e.passageiros:
            chave = so_digitos(p.cpf) or p.nome.lower()
            passageiros.setdefault(chave, {"nome": p.nome, "cpf": p.cpf, "viagens": 0})["viagens"] += 1
    return render(request, "mvo/cliente_manager.html", usuario, cliente=cliente, emissoes=emissoes, totais=totais,
                  passageiros=sorted(passageiros.values(), key=lambda p: -p["viagens"]))


@rotas.post("/manager/clientes/{cliente_id}")
async def editar_cliente(cliente_id: int, request: Request, db: Session = Depends(get_db),
                         usuario: Usuario = Depends(pode_operar)):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(404)
    form = await request.form()
    dados = _dados_cliente(form)
    erro = _erro_cliente(db, dados, exceto=cliente.id)
    if erro:
        avisar(request, erro, "erro")
        return voltar(f"/manager/clientes/{cliente.id}")
    for chave, valor in dados.items():
        setattr(cliente, chave, valor)
    cliente.consentimento = form.get("consentimento") == "true"
    cliente.ativo = form.get("ativo") == "true"
    registrar(db, usuario, "cliente_salvo", f"cliente:{cliente.id}", cliente.nome, ip_de(request))
    db.commit()
    avisar(request, "Cadastro salvo.")
    return voltar(f"/manager/clientes/{cliente.id}")


# ---------------------------------------------------------------- comissões por emissor


@rotas.get("/comissoes", response_class=HTMLResponse)
def tela_comissoes(request: Request, mes: str = "", db: Session = Depends(get_db),
                   usuario: Usuario = Depends(pode_custos)):
    ano, m = ler_mes(mes)
    linhas = comissoes_do_mes(db, ano, m)
    totais = {
        "emissoes": sum(l.quantidade for l in linhas),
        "passageiros": sum(l.passageiros for l in linhas),
        "vendido": round(sum(l.vendido for l in linhas), 2),
        "lucro": round(sum(l.lucro for l in linhas), 2),
        "comissao": round(sum(l.comissao or 0 for l in linhas), 2),
    }
    equipe = db.scalars(select(Usuario).where(Usuario.ativo.is_(True)).order_by(Usuario.nome)).all()
    hoje = date.today()
    return render(request, "mvo/comissoes.html", usuario, linhas=linhas, totais=totais, ano=ano, m=m,
                  mes=f"{ano:04d}-{m:02d}", nome_mes=nome_do_mes(ano, m), anterior=mes_vizinho(ano, m, -1),
                  proximo=mes_vizinho(ano, m, 1), eh_atual=(ano, m) == (hoje.year, hoje.month), bases=BASES,
                  equipe=equipe, pode_definir=pode(usuario, "custos", "configurar"))


@rotas.post("/comissoes/percentuais")
async def salvar_percentuais(request: Request, db: Session = Depends(get_db),
                             usuario: Usuario = Depends(pode_definir_comissao)):
    form = await request.form()
    alterados = []
    for u in db.scalars(select(Usuario)):
        if f"pct_{u.id}" not in form:
            continue
        pct = ler_numero(form.get(f"pct_{u.id}"))
        base = form.get(f"base_{u.id}") if form.get(f"base_{u.id}") in BASES else "lucro"
        pct = round(pct, 2) if pct and pct > 0 else None
        if (pct, base) != (u.comissao_pct, u.comissao_base or "lucro"):
            u.comissao_pct, u.comissao_base = pct, base
            alterados.append(f"{u.nome}: {pct or 0}% {BASES[base]}")
    if alterados:
        registrar(db, usuario, "usuario_alterado", None, "Comissão — " + "; ".join(alterados), ip_de(request))
    db.commit()
    avisar(request, "Percentuais de comissão salvos." if alterados else "Nada mudou nos percentuais.")
    return voltar(f"/comissoes?mes={form.get('mes') or ''}")


@rotas.get("/comissoes/{mes}.xlsx")
def baixar_comissoes(mes: str, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_custos)):
    ano, m = ler_mes(mes)
    conteudo = planilha(comissoes_do_mes(db, ano, m), ano, m)
    return Response(conteudo, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="comissoes-{ano:04d}-{m:02d}.xlsx"'})

