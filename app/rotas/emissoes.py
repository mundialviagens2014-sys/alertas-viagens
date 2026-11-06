"""Emissões (passagens emitidas) e disponibilidade (quem tem pontos e CPF livre para emitir)."""
from collections import OrderedDict
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import aeroportos, busca
from .. import colunas as col_
from ..database import get_db
from ..exportar import Coluna, exportar, quer_planilha, sim_nao
from ..models import Cliente, Match, ProgramaMilhas, Promocao, Usuario
from ..models_mvo import (
    FORMAS_EMISSAO,
    METODOS_PAGAMENTO,
    Agencia,
    Colaborador,
    ContaPrograma,
    Emissao,
    EmissaoPassageiro,
    Fornecedor,
    PedidoEmissao,
)
from ..mvo.comissoes import intervalo_do_mes, ler_mes, mes_vizinho
from ..mvo.contas import ErroMVO, disponibilidade, formatar_pontos, registrar, resumo_por_programa
from ..mvo.emissoes import FaltaInformacao, cancelar_emissao, criar_emissao, editar_emissao
from ..mvo.exclusoes import excluir_emissao
from ..mvo.precos import sugerir_preco
from ..permissoes import pode
from ..servicos import preco_reais
from ..web import avisar, checar_exportar, exigir, ip_de, ler_data, ler_inteiro, ler_numero, render, voltar
from .pedidos import marcar_emitido, valores_do_pedido

rotas = APIRouter()
pode_ver = exigir("milhas_ver")
pode_operar = exigir("milhas_ver", "milhas_operar")


def _texto(valor) -> str:
    return (valor or "").strip() if isinstance(valor, str) else ""


# ---------------------------------------------------------------- disponibilidade


@rotas.get("/disponibilidade", response_class=HTMLResponse)
def tela_disponibilidade(request: Request, programa: str = "", pontos: str = "", passageiros: int = 0,
                         q: str = "", pessoal: int = 0, zeradas: int = 0, formato: str = "",
                         db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    minimo = ler_inteiro(pontos) or 0
    linhas = disponibilidade(db, programa=programa or None, busca=q, pontos_min=minimo,
                             passageiros=max(passageiros, 0), incluir_pessoal=bool(pessoal),
                             incluir_zeradas=bool(zeradas))
    precos = {p.nome: p for p in db.scalars(select(ProgramaMilhas))}
    if quer_planilha(formato):
        checar_exportar(usuario)
        ve_custo = pode(usuario, "custos")

        def preco(campo):
            return lambda l: getattr(precos[l.conta.programa], campo) if l.conta.programa in precos else None

        colunas = [
            Coluna("Colaborador", lambda l: l.conta.colaborador.nome), Coluna("CPF", lambda l: col_.documento(l.conta.colaborador.cpf)),
            Coluna("Classificação", lambda l: l.conta.colaborador.classificacao), Coluna("Programa", lambda l: l.conta.programa),
            Coluna("Disponível", lambda l: l.conta.disponivel, "pontos"),
            Coluna("Reservado", lambda l: l.conta.saldo_reservado or 0, "pontos"),
            Coluna("A creditar", lambda l: l.conta.saldo_futuro or 0, "pontos"),
            Coluna("CPFs livres", lambda l: "sem limite" if l.situacao.cpfs_livres is None else l.situacao.cpfs_livres),
            Coluna("Limite de CPFs", lambda l: l.situacao.cpfs_limite),
            Coluna("Emissões no período", lambda l: l.situacao.emissoes_periodo),
        ]
        if ve_custo:
            colunas.append(Coluna("Custo médio (milheiro)", lambda l: l.custo_medio, "dinheiro"))
        colunas += [Coluna("Venda B2C (milheiro)", preco("preco_b2c"), "dinheiro"),
                    Coluna("Venda B2B (milheiro)", preco("preco_b2b"), "dinheiro"),
                    Coluna("Venda B2F (milheiro)", preco("preco_b2f"), "dinheiro")]
        if ve_custo:
            colunas.append(Coluna("Margem B2C (milheiro)", lambda l: round(l.preco_b2c - l.custo_medio, 2)
                                  if l.preco_b2c and l.custo_medio else None, "dinheiro"))
        colunas.append(Coluna("Uso pessoal", lambda l: sim_nao(l.conta.uso_pessoal)))
        return exportar(request, db, usuario, "Disponibilidade", colunas, linhas)
    resumo = resumo_por_programa(db)
    programas = [r["programa"] for r in resumo]
    return render(request, "mvo/disponibilidade.html", usuario, linhas=linhas, resumo=resumo, programas=programas,
                  programa=programa, pontos=pontos, minimo=minimo, passageiros=passageiros, q=q, pessoal=pessoal,
                  zeradas=zeradas, precos=precos)


# ---------------------------------------------------------------- lista de emissões


def opcoes_emissores(db: Session) -> list[tuple[str, str]]:
    """(valor do filtro, nome): usuários da equipe e nomes de emissores antigos sem usuário."""
    usuarios = db.scalars(select(Usuario).order_by(Usuario.ativo.desc(), Usuario.nome)).all()
    opcoes = [(str(u.id), u.nome) for u in usuarios]
    nomes_usuarios = {u.nome.lower() for u in usuarios}
    for nome in db.scalars(select(Emissao.emissor_nome).where(Emissao.emissor_id.is_(None),
                                                              Emissao.emissor_nome.is_not(None)).distinct()):
        if nome.lower() not in nomes_usuarios:
            opcoes.append(("n:" + nome, nome))
    return opcoes


def filtrar_emissor(consulta, emissor: str):
    if not emissor:
        return consulta
    if emissor.isdigit():
        return consulta.where(Emissao.emissor_id == int(emissor))
    if emissor == "-":
        return consulta.where(Emissao.emissor_id.is_(None), Emissao.emissor_nome.is_(None))
    if emissor.startswith("n:"):
        return consulta.where(Emissao.emissor_id.is_(None), Emissao.emissor_nome == emissor[2:])
    return consulta


def colunas_emissoes(ve_custo: bool) -> list[Coluna]:
    colunas = [
        Coluna("Data", lambda e: e.data_emissao, "datahora"), Coluna("Localizador", lambda e: e.localizador),
        Coluna("Status", lambda e: "Cancelada" if e.status != "ativa" else "Ativa"),
        Coluna("Forma", lambda e: FORMAS_EMISSAO.get(e.forma, e.forma)), Coluna("Programa", lambda e: e.programa),
        Coluna("Companhia", lambda e: e.companhia), Coluna("Origem", lambda e: e.origem), Coluna("Destino", lambda e: e.destino),
        Coluna("Viagem", lambda e: {"ida": "Só ida", "ida_volta": "Ida e volta"}.get(e.tipo_viagem or "", None)),
        Coluna("Ida", lambda e: e.data_ida, "data"), Coluna("Volta", lambda e: e.data_volta, "data"),
        Coluna("Passageiros", lambda e: e.qtd_passageiros),
        Coluna("Nomes dos passageiros", lambda e: [p.nome for p in e.passageiros]),
        Coluna("CPFs dos passageiros", lambda e: [col_.documento(p.cpf) for p in e.passageiros if p.cpf]),
        Coluna("Cliente", lambda e: e.cliente_nome), Coluna("Agência", lambda e: e.agencia.nome if e.agencia else None),
        Coluna("Conta (colaborador)", lambda e: e.conta.colaborador.nome if e.conta else None),
        Coluna("Fornecedor", lambda e: e.fornecedor.nome if e.fornecedor else None),
        Coluna("Pontos", lambda e: e.pontos, "pontos"), Coluna("Preço de venda", lambda e: e.preco_venda, "dinheiro"),
        Coluna("Taxa de embarque", lambda e: e.taxa_embarque, "dinheiro"), Coluna("Bagagem", lambda e: e.taxa_bagagem, "dinheiro"),
        Coluna("Total ao cliente", lambda e: e.total, "dinheiro"),
    ]
    if ve_custo:
        colunas += [Coluna("Custo das milhas", lambda e: e.custo_milhas, "dinheiro"),
                    Coluna("Outros custos", lambda e: e.outros_custos, "dinheiro"),
                    Coluna("Taxa do cartão", lambda e: e.taxa_cartao, "dinheiro"),
                    Coluna("Custo total", lambda e: e.custo, "dinheiro"), Coluna("Lucro", lambda e: e.lucro, "dinheiro"),
                    Coluna("Lucro %", lambda e: round(e.lucro_pct, 1) if e.lucro_pct is not None else None, "pct")]
    colunas += [Coluna("Emissor", lambda e: e.emissor_nome), Coluna("Pagamento do cliente", lambda e: e.metodo_pagamento),
                Coluna("Conta que recebeu", lambda e: e.conta_recebedora), Coluna("Conexões", lambda e: e.conexoes),
                Coluna("Pedido nº", lambda e: e.pedido_id), Coluna("Registrada por", lambda e: e.usuario),
                Coluna("Observação", lambda e: e.observacao)]
    return colunas


def _emissao_no_lugar(termo: str):
    """Palavra da busca que é uma cidade ou um código: emissões saindo ou chegando lá."""
    codigo = aeroportos.resolver(termo) if len(termo) >= 3 else None
    if not codigo:
        return false()
    codigos = aeroportos.codigos_do_lugar(codigo)
    return or_(Emissao.origem.in_(codigos), Emissao.destino.in_(codigos))


@rotas.get("/emissoes", response_class=HTMLResponse)
def lista_emissoes(request: Request, de: str = "", ate: str = "", q: str = "", programa: str = "", forma: str = "",
                   ver: str = "ativas", mes: str = "", emissor: str = "", agencia: int = 0, formato: str = "",
                   db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    hoje = date.today()
    if mes:
        ano_mes = ler_mes(mes)
        inicio_dt, fim_dt = intervalo_do_mes(*ano_mes)
        inicio, fim = inicio_dt.date(), fim_dt.date() - timedelta(days=1)
        mes = f"{ano_mes[0]:04d}-{ano_mes[1]:02d}"
    else:
        inicio = ler_data(de) if de else hoje - timedelta(days=30)
        fim = ler_data(ate) if ate else None
    consulta = (select(Emissao).outerjoin(ContaPrograma, Emissao.conta_id == ContaPrograma.id)
                .outerjoin(Colaborador, ContaPrograma.colaborador_id == Colaborador.id)
                .outerjoin(Agencia, Emissao.agencia_id == Agencia.id)
                .options(selectinload(Emissao.passageiros), selectinload(Emissao.fornecedor), selectinload(Emissao.agencia),
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
    consulta = busca.aplicar(
        consulta, q,
        [Emissao.localizador, Emissao.cliente_nome, Colaborador.nome, Emissao.emissor_nome, Agencia.nome,
         Emissao.companhia, Emissao.observacao, _emissao_no_lugar,
         lambda termo: Emissao.passageiros.any(busca.sem_acento_sql(EmissaoPassageiro.nome).contains(termo, autoescape=True))],
        [lambda numero: Emissao.passageiros.any(EmissaoPassageiro.cpf.contains(numero, autoescape=True))])
    if agencia:
        consulta = consulta.where(Emissao.agencia_id == agencia)
    consulta = filtrar_emissor(consulta, emissor)
    if quer_planilha(formato):
        checar_exportar(usuario)
        itens = db.scalars(consulta.order_by(Emissao.data_emissao.desc(), Emissao.id.desc())).all()
        return exportar(request, db, usuario, "Emissões", colunas_emissoes(pode(usuario, "custos")), itens)
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
    # quem emitiu, no período filtrado (para conferir a comissão)
    por_emissor: dict[str, dict] = {}
    for e in ativas:
        nome = e.emissor_nome or "Sem emissor informado"
        linha = por_emissor.setdefault(nome, {"nome": nome, "emissoes": 0, "passageiros": 0, "vendido": 0.0,
                                              "lucro": 0.0, "filtro": str(e.emissor_id) if e.emissor_id else
                                              ("n:" + e.emissor_nome if e.emissor_nome else "-")})
        linha["emissoes"] += 1
        linha["passageiros"] += e.qtd_passageiros or 0
        linha["vendido"] += e.preco_venda or 0
        linha["lucro"] += e.lucro or 0
    programas = sorted(set(db.scalars(select(Emissao.programa).where(Emissao.programa.is_not(None)).distinct())))
    meses = [mes_vizinho(hoje.year, hoje.month, -i) for i in range(0, 13)]
    agencias = db.scalars(select(Agencia).where(Agencia.id.in_(
        select(Emissao.agencia_id).where(Emissao.agencia_id.is_not(None)).distinct())).order_by(Agencia.nome)).all()
    return render(request, "mvo/emissoes.html", usuario, emissoes=emissoes, resumo=resumo, programas=programas,
                  de=inicio.isoformat() if inicio else "", ate=fim.isoformat() if fim else "", q=q,
                  programa=programa, forma=forma, ver=ver, mes=mes, meses=meses, emissor=emissor,
                  emissores=opcoes_emissores(db), agencias=agencias, agencia_filtro=agencia,
                  por_emissor=sorted(por_emissor.values(), key=lambda l: -l["vendido"]))


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
    equipe = db.scalars(select(Usuario).where(Usuario.ativo.is_(True)).order_by(Usuario.nome)).all()
    agencias = db.scalars(select(Agencia).where(Agencia.ativo.is_not(False)).order_by(Agencia.nome)).all()
    pedido = db.get(PedidoEmissao, ler_inteiro(str(v.get("pedido_id") or "")) or 0) if v.get("pedido_id") else None
    resposta = render(request, "mvo/emissao_form.html", usuario, v=v, passageiros=passageiros or [("", "")],
                      grupos=grupos, fornecedores=fornecedores, clientes=clientes, programas=programas,
                      precos=precos, formas=FORMAS_EMISSAO, equipe=equipe, metodos=METODOS_PAGAMENTO,
                      agencias=agencias, pedido=pedido)
    resposta.status_code = status_code
    return resposta


@rotas.get("/emissoes/nova", response_class=HTMLResponse)
def nova_emissao(request: Request, conta: int = 0, match: int = 0, promocao: int = 0, cliente: int = 0,
                 pedido: int = 0, agencia: int = 0, db: Session = Depends(get_db),
                 usuario: Usuario = Depends(pode_operar)):
    v: dict = {"forma": "programa_proprio", "data_emissao": date.today().isoformat(), "emissor_id": usuario.id}
    if agencia and db.get(Agencia, agencia):
        v["agencia_id"] = agencia
    if pedido and (p := db.get(PedidoEmissao, pedido)):
        if p.status != "aguardando":
            avisar(request, f"O pedido nº {p.id} está {p.status}.", "erro")
        valores, passageiros = valores_do_pedido(p)
        v.update(valores)
        if conta and (c := db.get(ContaPrograma, conta)):
            v.update(conta_id=c.id, programa=c.programa)
        return _tela_emissao(request, db, usuario, v, passageiros)
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
    elif cliente and (c := db.get(Cliente, cliente)):
        v.update(cliente_nome=c.nome, cliente_id=c.id)
    return _tela_emissao(request, db, usuario, v, [])


@rotas.post("/emissoes/nova")
async def salvar_emissao(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    form = await request.form()
    v = {k: form.get(k) for k in (
        "forma", "conta_id", "fornecedor_id", "programa", "companhia", "localizador", "origem", "destino",
        "tipo_viagem", "data_ida", "data_volta", "pontos", "preco_venda", "taxa_embarque", "taxa_bagagem",
        "outros_custos", "cliente_nome", "cliente_id", "promocao_id", "observacao", "data_emissao", "qtd_passageiros",
        "emissor_id", "metodo_pagamento", "conta_recebedora", "taxa_cartao", "conexoes", "pedido_id", "agencia_id",
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
    emissor = db.get(Usuario, ler_inteiro(v["emissor_id"])) if ler_inteiro(v["emissor_id"]) else None
    emissor = emissor or usuario
    v["emissor_id"] = emissor.id
    agencia_id = ler_inteiro(v["agencia_id"])
    agencia_id = agencia_id if agencia_id and db.get(Agencia, agencia_id) else None
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
            forcar=v["forcar"], emissor_id=emissor.id, emissor_nome=emissor.nome,
            metodo_pagamento=_texto(v["metodo_pagamento"]) or None,
            conta_recebedora=_texto(v["conta_recebedora"]) or None,
            taxa_cartao=ler_numero(v["taxa_cartao"]) if pode(usuario, "custos") else None,
            conexoes=ler_inteiro(v["conexoes"]), pedido_id=ler_inteiro(v["pedido_id"]), agencia_id=agencia_id,
        )
    except ErroMVO as erro:
        db.rollback()
        avisar(request, str(erro), "erro")
        v["pode_forcar"] = not isinstance(erro, FaltaInformacao)
        return _tela_emissao(request, db, usuario, v, passageiros, status_code=400)
    resumo = f"{emissao.localizador or 'sem localizador'} {emissao.rota}".strip()
    if emissao.conta:
        resumo += f" · {formatar_pontos(emissao.pontos)} pontos de {emissao.conta.programa} de {emissao.conta.colaborador.nome}"
    registrar(db, usuario, "emissao", f"emissao:{emissao.id}", resumo, ip_de(request))
    pedido = marcar_emitido(db, emissao.pedido_id, emissao.id, usuario)
    db.commit()
    avisar(request, "Emissão registrada" + (f": {formatar_pontos(emissao.pontos)} pontos descontados da conta."
                                            if emissao.conta else ".")
           + (f" Pedido nº {pedido.id} marcado como emitido." if pedido else ""))
    return voltar(f"/emissoes/{emissao.id}")


@rotas.post("/emissoes/ler-imagem")
async def ler_imagem_emissao(request: Request, imagem: UploadFile = File(...), db: Session = Depends(get_db),
                             usuario: Usuario = Depends(pode_operar)):
    """Lê o print de um pedido de emissão com a IA e devolve os campos para o formulário (nada é gravado aqui:
    a equipe confere e clica em Registrar emissão)."""
    import logging

    from .. import ia
    from ..leitura import TAMANHO_MAX_IMAGEM
    from ..servicos import preparar_imagem

    conteudo = await imagem.read()
    if not conteudo or len(conteudo) > TAMANHO_MAX_IMAGEM:
        return JSONResponse({"erro": "Envie uma imagem JPEG, PNG ou WebP de até 8 MB."}, status_code=400)
    if not ia.configurada():
        return JSONResponse({"erro": "A leitura de imagens precisa da chave GEMINI_API_KEY nas variáveis do Render."},
                            status_code=400)
    if not ia.cota_disponivel(db):
        return JSONResponse({"erro": "A cota gratuita de leituras por IA acabou por hoje. Preencha à mão."}, status_code=429)
    conteudo, mime = preparar_imagem(conteudo, imagem.content_type)
    try:
        dados = ia.ler_emissao(conteudo, mime)
        ia.registrar_uso(db)
    except ia.CotaExcedida:
        return JSONResponse({"erro": "A cota gratuita de leituras por IA acabou por hoje. Preencha à mão."}, status_code=429)
    except Exception as erro:  # rede, resposta estranha
        logging.getLogger(__name__).warning("Leitura do print da emissão falhou: %s", erro)
        return JSONResponse({"erro": "Não consegui ler a imagem agora. Tente de novo ou preencha à mão."}, status_code=502)
    return JSONResponse(campos_da_leitura(db, dados))


def _norm(texto) -> str:
    return " ".join(busca.sem_acento(texto).split())


def campos_da_leitura(db: Session, d: dict) -> dict:
    """Converte a leitura da IA nos campos do formulário (nomes do <form>) e acha a agência, o cliente e o emissor."""
    from ..models_mvo import Agencia

    def texto(chave) -> str:
        return str(d.get(chave) or "").strip()

    def numero(chave):
        try:
            n = float(d.get(chave) or 0)
        except (TypeError, ValueError):
            return ""
        return "" if not n else (str(int(n)) if chave == "pontos" else f"{n:.2f}".replace(".", ","))

    campos = {
        "data_emissao": texto("data_pedido"), "programa": texto("programa").upper(), "companhia": texto("companhia").upper(),
        "localizador": texto("localizador").upper(), "origem": texto("origem").upper()[:3],
        "destino": texto("destino").upper()[:3], "data_ida": texto("data_ida"), "data_volta": texto("data_volta"),
        "tipo_viagem": texto("tipo_voo") if texto("tipo_voo") in ("ida", "ida_volta") else "",
        "pontos": numero("pontos"), "preco_venda": numero("valor_passagem"),
        "taxa_embarque": numero("taxa_embarque"), "taxa_bagagem": numero("taxa_bagagem"),
        "cliente_nome": texto("cliente") or texto("requisitante"), "metodo_pagamento": texto("metodo_pagamento"),
    }
    obs = [x for x in (texto("observacao"), texto("voos"), texto("tipo_emissao") and "Tipo emissão: " + texto("tipo_emissao")) if x]
    campos["observacao"] = " · ".join(dict.fromkeys(obs))
    passageiros = [{"nome": str(p.get("nome") or "").strip(), "cpf": str(p.get("cpf") or "").strip()}
                   for p in (d.get("passageiros") or []) if isinstance(p, dict) and (p.get("nome") or p.get("cpf"))]
    # agência pelo campo Usuário (ex.: UNIVERSO TURISMO), comparando sem acento nem maiúsculas
    agencia_id, nome_usuario = "", _norm(texto("usuario"))
    if nome_usuario:
        for a in db.scalars(select(Agencia).where(Agencia.ativo.is_not(False))):
            if nome_usuario in (_norm(a.nome), _norm(a.razao_social or "")):
                agencia_id = str(a.id)
                break
    emissor_id = ""
    if texto("emissor"):
        alvo = _norm(texto("emissor"))
        for u in db.scalars(select(Usuario).where(Usuario.ativo.is_(True))):
            if _norm(u.nome) == alvo or _norm(u.login or "") == alvo:
                emissor_id = str(u.id)
                break
    return {"campos": campos, "passageiros": passageiros, "qtd_passageiros": d.get("qtd_passageiros") or len(passageiros),
            "agencia_id": agencia_id, "agencia_lida": texto("usuario"), "emissor_id": emissor_id}


@rotas.get("/emissoes/sugestao")
def sugestao_de_preco(programa: str = "", pontos: str = "", agencia: int = 0, companhia: str = "",
                      db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    """Preço de venda sugerido pela lista de preços (tipo de negócio da agência) + markup."""
    ag = db.get(Agencia, agencia) if agencia else None
    s = sugerir_preco(db, programa.strip() or None, ler_inteiro(pontos), ag, companhia.strip() or None)
    if s is None or s.total is None:
        return JSONResponse({"total": None})
    return JSONResponse({"total": s.total, "tipo": s.tipo, "milheiro": s.milheiro, "base": s.base,
                         "markup_pct": s.markup_pct, "markup_valor": s.markup_valor,
                         "acima_do_limite": s.acima_do_limite, "limite_pontos": s.limite_pontos})


# ---------------------------------------------------------------- detalhe, edição e cancelamento


@rotas.get("/emissoes/{emissao_id}", response_class=HTMLResponse)
def ver_emissao(emissao_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    e = db.get(Emissao, emissao_id)
    if not e:
        raise HTTPException(404)
    equipe = db.scalars(select(Usuario).order_by(Usuario.ativo.desc(), Usuario.nome)).all()
    agencias = db.scalars(select(Agencia).order_by(Agencia.ativo.desc(), Agencia.nome)).all()
    pedido = db.get(PedidoEmissao, e.pedido_id) if e.pedido_id else None
    return render(request, "mvo/emissao.html", usuario, e=e, equipe=equipe, metodos=METODOS_PAGAMENTO,
                  agencias=agencias, pedido=pedido)


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
    if pode(usuario, "custos") and "taxa_cartao" in form:
        campos["taxa_cartao"] = ler_numero(form.get("taxa_cartao"))
    if "emissor_id" in form:
        emissor = db.get(Usuario, ler_inteiro(form.get("emissor_id"))) if ler_inteiro(form.get("emissor_id")) else None
        if emissor:
            campos.update(emissor_id=emissor.id, emissor_nome=emissor.nome)
    if "agencia_id" in form:
        agencia_id = ler_inteiro(form.get("agencia_id"))
        campos["agencia_id"] = agencia_id if agencia_id and db.get(Agencia, agencia_id) else None
    if "metodo_pagamento" in form:
        campos.update(metodo_pagamento=_texto(form.get("metodo_pagamento")) or None,
                      conta_recebedora=_texto(form.get("conta_recebedora")) or None,
                      conexoes=ler_inteiro(form.get("conexoes")))
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


@rotas.post("/emissoes/{emissao_id}/excluir")
def apagar_emissao(emissao_id: int, request: Request, db: Session = Depends(get_db),
                   usuario: Usuario = Depends(pode_operar)):
    e = db.get(Emissao, emissao_id)
    if not e:
        raise HTTPException(404)
    descricao = f"Emissão {e.localizador or e.id} {e.rota}".strip()
    try:
        excluir_emissao(db, e)
    except ErroMVO as erro:
        db.rollback()
        avisar(request, str(erro), "erro")
        return voltar(f"/emissoes/{emissao_id}")
    registrar(db, usuario, "exclusao", f"emissao:{emissao_id}", descricao, ip_de(request))
    db.commit()
    avisar(request, f"{descricao} excluída.")
    return voltar("/emissoes?ver=canceladas")

