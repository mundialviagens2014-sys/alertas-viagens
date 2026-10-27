"""Pedidos de emissão: agência ou cliente pede, a equipe emite (o pedido vira emissão com tudo preenchido)."""
import re
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import aeroportos, busca
from ..database import get_db
from ..exportar import Coluna, exportar, quer_planilha
from ..models import Cliente, ProgramaMilhas, Usuario
from ..models_mvo import STATUS_PEDIDO, TIPOS_PEDIDO, Agencia, PedidoEmissao
from ..mvo.contas import formatar_cpf, registrar, so_digitos
from ..web import (
    avisar,
    checar_exportar,
    destino_seguro,
    exigir,
    ip_de,
    ler_data,
    ler_inteiro,
    ler_numero,
    ler_texto,
    render,
    voltar,
)

rotas = APIRouter()
pode_ver = exigir("milhas_ver")
pode_operar = exigir("milhas_ver", "milhas_operar")

_CPF = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")


def ler_passageiros(texto: str | None) -> list[tuple[str, str]]:
    """Um passageiro por linha: "Maria Lima; 123.456.789-09" (o CPF pode vir em qualquer lugar da linha)."""
    passageiros = []
    for linha in (texto or "").splitlines():
        achado = _CPF.search(linha)
        cpf = so_digitos(achado.group()) if achado else ""
        nome = linha
        if achado:
            nome = re.sub(r"\bCPF\b:?", " ", _CPF.sub(" ", nome), flags=re.I)
        nome = " ".join(re.sub(r"[;,|\t]|\s-\s|^\s*-|-\s*$", " ", nome).split())
        if nome:
            passageiros.append((nome, cpf))
    return passageiros


def _dados_pedido(db: Session, form) -> dict:
    tipo = form.get("tipo_emissao")
    agencia_id = ler_inteiro(form.get("agencia_id")) or None
    if agencia_id and not db.get(Agencia, agencia_id):
        agencia_id = None
    cliente_nome = ler_texto(form.get("cliente_nome"), 160)
    cliente = db.scalar(select(Cliente).where(func.lower(Cliente.nome) == cliente_nome.lower())) if cliente_nome else None
    passageiros = (form.get("passageiros") or "").strip()
    qtd = ler_inteiro(form.get("qtd_passageiros"))
    if not qtd:
        qtd = len(ler_passageiros(passageiros)) or 1
    return {
        "agencia_id": agencia_id, "cliente_id": cliente.id if cliente else None,
        "requisitante_nome": ler_texto(form.get("requisitante_nome"), 160) or (cliente_nome if not cliente else None),
        "contato": ler_texto(form.get("contato"), 160),
        "tipo_emissao": tipo if tipo in TIPOS_PEDIDO else "simples",
        "programa": ler_texto(form.get("programa"), 80), "companhia": ler_texto(form.get("companhia"), 80),
        "origem": (ler_texto(form.get("origem")) or "").upper()[:3] or None,
        "destino": (ler_texto(form.get("destino")) or "").upper()[:3] or None,
        "data_ida": ler_data(form.get("data_ida")), "data_volta": ler_data(form.get("data_volta")),
        "qtd_passageiros": max(qtd, 1), "passageiros": passageiros or None,
        "observacoes": (form.get("observacoes") or "").strip() or None,
        "preco_combinado": ler_numero(form.get("preco_combinado")),
    }


def _erro_pedido(dados: dict) -> str | None:
    if not (dados["agencia_id"] or dados["cliente_id"] or dados["requisitante_nome"]):
        return "Informe quem pediu: a agência, o cliente ou o nome de quem pediu."
    if not (dados["origem"] and dados["destino"]) and not dados["observacoes"]:
        return "Informe o trecho (origem e destino) ou descreva o pedido nas observações."
    return None


def _pedido_no_lugar(termo: str):
    """Palavra da busca que é uma cidade ou um código: pedidos saindo ou chegando lá."""
    codigo = aeroportos.resolver(termo) if len(termo) >= 3 else None
    if not codigo:
        return false()
    codigos = aeroportos.codigos_do_lugar(codigo)
    return or_(PedidoEmissao.origem.in_(codigos), PedidoEmissao.destino.in_(codigos))


@rotas.get("/pedidos", response_class=HTMLResponse)
def lista_pedidos(request: Request, status: str = "aguardando", q: str = "", agencia: int = 0, programa: str = "",
                  de: str = "", ate: str = "", formato: str = "", db: Session = Depends(get_db),
                  usuario: Usuario = Depends(pode_ver)):
    consulta = (select(PedidoEmissao).options(selectinload(PedidoEmissao.agencia), selectinload(PedidoEmissao.cliente))
                .outerjoin(Agencia, PedidoEmissao.agencia_id == Agencia.id)
                .outerjoin(Cliente, PedidoEmissao.cliente_id == Cliente.id))
    if status in STATUS_PEDIDO:
        consulta = consulta.where(PedidoEmissao.status == status)
    if agencia:
        consulta = consulta.where(PedidoEmissao.agencia_id == agencia)
    inicio, fim = ler_data(de), ler_data(ate)
    if inicio:
        consulta = consulta.where(PedidoEmissao.criado_em >= datetime.combine(inicio, time.min))
    if fim:
        consulta = consulta.where(PedidoEmissao.criado_em < datetime.combine(fim + timedelta(days=1), time.min))
    if programa:
        consulta = consulta.where(PedidoEmissao.programa == programa)
    consulta = busca.aplicar(
        consulta, q,
        [PedidoEmissao.requisitante_nome, Agencia.nome, Cliente.nome, PedidoEmissao.passageiros,
         PedidoEmissao.observacoes, PedidoEmissao.origem, PedidoEmissao.destino, PedidoEmissao.contato,
         PedidoEmissao.companhia, _pedido_no_lugar],
        [PedidoEmissao.passageiros, PedidoEmissao.contato])
    ordem = PedidoEmissao.criado_em.asc() if status == "aguardando" else PedidoEmissao.criado_em.desc()
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [
            Coluna("Nº", lambda p: p.id), Coluna("Criado em", lambda p: p.criado_em, "datahora"),
            Coluna("Registrado por", lambda p: p.usuario), Coluna("Status", lambda p: STATUS_PEDIDO.get(p.status, p.status)),
            Coluna("Agência", lambda p: p.agencia.nome if p.agencia else None),
            Coluna("Quem pediu", lambda p: p.requisitante_nome), Coluna("Cliente", lambda p: p.cliente.nome if p.cliente else None),
            Coluna("Contato", lambda p: p.contato), Coluna("Tipo", lambda p: TIPOS_PEDIDO.get(p.tipo_emissao or "", None)),
            Coluna("Origem", lambda p: p.origem), Coluna("Destino", lambda p: p.destino),
            Coluna("Ida", lambda p: p.data_ida, "data"), Coluna("Volta", lambda p: p.data_volta, "data"),
            Coluna("Programa", lambda p: p.programa), Coluna("Companhia", lambda p: p.companhia),
            Coluna("Passageiros", lambda p: p.qtd_passageiros),
            Coluna("Nomes e CPFs", lambda p: "; ".join(l.strip() for l in (p.passageiros or "").splitlines() if l.strip())),
            Coluna("Preço combinado", lambda p: p.preco_combinado, "dinheiro"), Coluna("Observações", lambda p: p.observacoes),
            Coluna("Atendido por", lambda p: p.atendido_por), Coluna("Atendido em", lambda p: p.atendido_em, "datahora"),
            Coluna("Tempo até atender", lambda p: p.tempo_aberto() if p.atendido_em else None),
            Coluna("Emissão nº", lambda p: p.emissao_id),
        ]
        return exportar(request, db, usuario, "Pedidos de emissão", colunas, db.scalars(consulta.order_by(ordem)).all())
    pedidos = db.scalars(consulta.order_by(ordem).limit(500)).all()
    contagem = dict(db.execute(select(PedidoEmissao.status, func.count(PedidoEmissao.id))
                               .group_by(PedidoEmissao.status)).all())
    agencias = db.scalars(select(Agencia).where(Agencia.ativo.is_not(False)).order_by(Agencia.nome)).all()
    programas = sorted({p for p in db.scalars(select(PedidoEmissao.programa).distinct()) if p}, key=str.lower)
    return render(request, "mvo/pedidos.html", usuario, pedidos=pedidos, status=status, q=q, agencia_filtro=agencia, de=de,
                  ate=ate, contagem=contagem, agencias=agencias, status_pedido=STATUS_PEDIDO, tipos=TIPOS_PEDIDO,
                  agora=datetime.now(), programas=programas, programa=programa)


def _tela_pedido(request: Request, db: Session, usuario: Usuario, pedido: PedidoEmissao | None, d, status_code: int = 200):
    agencias = db.scalars(select(Agencia).where(Agencia.ativo.is_not(False)).order_by(Agencia.nome)).all()
    clientes = db.scalars(select(Cliente.nome).where(Cliente.ativo.is_(True)).order_by(Cliente.nome)).all()
    programas = sorted(db.scalars(select(ProgramaMilhas.nome).where(ProgramaMilhas.ativo.is_not(False))), key=str.lower)
    resposta = render(request, "mvo/pedido_form.html", usuario, pedido=pedido, d=d, agencias=agencias,
                      clientes=clientes, programas=programas, tipos=TIPOS_PEDIDO, status_pedido=STATUS_PEDIDO,
                      agora=datetime.now())
    resposta.status_code = status_code
    return resposta


@rotas.get("/pedidos/novo", response_class=HTMLResponse)
def novo_pedido(request: Request, agencia: int = 0, cliente: int = 0, db: Session = Depends(get_db),
                usuario: Usuario = Depends(pode_operar)):
    d: dict = {"tipo_emissao": "ida_volta", "qtd_passageiros": 1}
    if agencia and db.get(Agencia, agencia):
        d["agencia_id"] = agencia
    if cliente and (c := db.get(Cliente, cliente)):
        d.update(cliente_nome=c.nome, contato=c.telefone)
    return _tela_pedido(request, db, usuario, None, d)


@rotas.post("/pedidos/novo")
async def criar_pedido(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    form = await request.form()
    dados = _dados_pedido(db, form)
    erro = _erro_pedido(dados)
    if erro:
        avisar(request, erro, "erro")
        return _tela_pedido(request, db, usuario, None, {**dados, "cliente_nome": form.get("cliente_nome")}, 400)
    pedido = PedidoEmissao(**dados, usuario=usuario.nome)
    db.add(pedido)
    db.flush()
    registrar(db, usuario, "pedido", f"pedido:{pedido.id}", f"Novo pedido nº {pedido.id}: {pedido.nome_requisitante}",
              ip_de(request))
    db.commit()
    avisar(request, f"Pedido nº {pedido.id} registrado. Quando emitir, use o botão “Emitir”: a emissão já vem preenchida.")
    return voltar("/pedidos")


def _pedido_ou_404(db: Session, pedido_id: int) -> PedidoEmissao:
    pedido = db.get(PedidoEmissao, pedido_id)
    if not pedido:
        raise HTTPException(404, "Pedido não encontrado.")
    return pedido


@rotas.get("/pedidos/{pedido_id}", response_class=HTMLResponse)
def ver_pedido(pedido_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    pedido = _pedido_ou_404(db, pedido_id)
    d = {c: getattr(pedido, c) for c in ("agencia_id", "requisitante_nome", "contato", "tipo_emissao", "programa",
                                         "companhia", "origem", "destino", "data_ida", "data_volta", "qtd_passageiros",
                                         "passageiros", "observacoes", "preco_combinado")}
    d["cliente_nome"] = pedido.cliente.nome if pedido.cliente else None
    return _tela_pedido(request, db, usuario, pedido, d)


@rotas.post("/pedidos/{pedido_id}")
async def editar_pedido(pedido_id: int, request: Request, db: Session = Depends(get_db),
                        usuario: Usuario = Depends(pode_operar)):
    pedido = _pedido_ou_404(db, pedido_id)
    form = await request.form()
    dados = _dados_pedido(db, form)
    erro = _erro_pedido(dados)
    if erro:
        avisar(request, erro, "erro")
        return _tela_pedido(request, db, usuario, pedido, {**dados, "cliente_nome": form.get("cliente_nome")}, 400)
    for chave, valor in dados.items():
        setattr(pedido, chave, valor)
    registrar(db, usuario, "pedido", f"pedido:{pedido.id}", f"Alterou pedido nº {pedido.id}", ip_de(request))
    db.commit()
    avisar(request, "Pedido salvo.")
    return voltar(f"/pedidos/{pedido.id}")


@rotas.post("/pedidos/{pedido_id}/status")
async def mudar_status_pedido(pedido_id: int, request: Request, db: Session = Depends(get_db),
                              usuario: Usuario = Depends(pode_operar)):
    pedido = _pedido_ou_404(db, pedido_id)
    form = await request.form()
    novo = form.get("status")
    if novo not in ("aguardando", "cancelado"):
        raise HTTPException(400)
    pedido.status = novo
    if novo == "cancelado":
        pedido.atendido_por, pedido.atendido_em = usuario.nome, datetime.now()
    else:
        pedido.atendido_por = pedido.atendido_em = None
        pedido.emissao_id = None
    registrar(db, usuario, "pedido", f"pedido:{pedido.id}",
              f"{'Cancelou' if novo == 'cancelado' else 'Reabriu'} pedido nº {pedido.id}", ip_de(request))
    db.commit()
    avisar(request, f"Pedido nº {pedido.id} {'cancelado' if novo == 'cancelado' else 'reaberto'}.")
    return voltar(destino_seguro(form.get("voltar_para"), "/pedidos"))


@rotas.post("/pedidos/{pedido_id}/excluir")
def excluir_pedido(pedido_id: int, request: Request, db: Session = Depends(get_db),
                   usuario: Usuario = Depends(pode_operar)):
    pedido = _pedido_ou_404(db, pedido_id)
    descricao = f"Pedido nº {pedido.id} ({pedido.nome_requisitante})"
    db.delete(pedido)
    registrar(db, usuario, "exclusao", f"pedido:{pedido_id}", descricao, ip_de(request))
    db.commit()
    avisar(request, f"{descricao} excluído.")
    return voltar("/pedidos")


def marcar_emitido(db: Session, pedido_id: int | None, emissao_id: int, usuario: Usuario) -> PedidoEmissao | None:
    """Depois de registrar a emissão de um pedido: o pedido fica "emitido", ligado à emissão."""
    pedido = db.get(PedidoEmissao, pedido_id) if pedido_id else None
    if pedido is None or pedido.status == "cancelado":
        return None
    pedido.status, pedido.emissao_id = "emitido", emissao_id
    pedido.atendido_por, pedido.atendido_em = usuario.nome, datetime.now()
    return pedido


def valores_do_pedido(pedido: PedidoEmissao) -> tuple[dict, list[tuple[str, str]]]:
    """Campos da tela de nova emissão preenchidos com o pedido."""
    v = {
        "pedido_id": pedido.id, "origem": pedido.origem, "destino": pedido.destino,
        "data_ida": pedido.data_ida.isoformat() if pedido.data_ida else "",
        "data_volta": pedido.data_volta.isoformat() if pedido.data_volta else "",
        "tipo_viagem": {"simples": "ida", "ida_volta": "ida_volta"}.get(pedido.tipo_emissao or "", ""),
        "companhia": pedido.companhia or "", "qtd_passageiros": pedido.qtd_passageiros,
        "agencia_id": pedido.agencia_id, "preco_venda": pedido.preco_combinado,
        "observacao": pedido.observacoes or "",
    }
    if pedido.programa:
        v["programa"] = pedido.programa
    if pedido.cliente:
        v.update(cliente_nome=pedido.cliente.nome, cliente_id=pedido.cliente.id)
    elif pedido.requisitante_nome and not pedido.agencia_id:
        v["cliente_nome"] = pedido.requisitante_nome
    return v, [(nome, formatar_cpf(cpf) if cpf else "") for nome, cpf in ler_passageiros(pedido.passageiros)]
