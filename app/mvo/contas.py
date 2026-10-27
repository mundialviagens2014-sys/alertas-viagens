"""Regras das contas de milhas: saldo, custo médio, lançamentos, transferências e CPFs por conta.

Custo médio (R$ por milheiro): média ponderada das entradas com custo (compras, clubes, transferências
recebidas, saldo inicial). Bônus e ajustes de entrada aumentam os pontos sem custo e por isso baixam
a média. Saídas (emissão, resgate, transferência enviada, expiração) levam o custo médio do momento
e não mudam a média do que sobra. Tudo é recalculado a partir dos lançamentos, em ordem de data.
"""
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import ProgramaMilhas
from ..models_mvo import (
    TIPOS_LANCAMENTO,
    Colaborador,
    ContaPrograma,
    Emissao,
    Lancamento,
    RegistroAcesso,
)


class ErroMVO(ValueError):
    """Erro de regra de negócio com mensagem para mostrar à equipe."""


def so_digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", texto or "")


def formatar_cpf(digitos: str | None) -> str:
    d = so_digitos(digitos)
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return digitos or ""


def formatar_pontos(n: int | None) -> str:
    return f"{n:,}".replace(",", ".") if n is not None else "—"


# ---------------------------------------------------------------- recálculo


def recalcular_conta(db: Session, conta: ContaPrograma, hoje: date | None = None, _visitadas=None) -> ContaPrograma:
    """Refaz saldo, saldo futuro e custo do saldo a partir dos lançamentos ativos.
    Atualiza o custo apurado das saídas (e das emissões e transferências ligadas a elas)."""
    hoje = hoje or date.today()
    _visitadas = _visitadas if _visitadas is not None else set()
    _visitadas.add(conta.id)

    lancs = db.scalars(
        select(Lancamento).where(Lancamento.conta_id == conta.id, Lancamento.status == "ativo")
        .order_by(Lancamento.data_efetivacao, Lancamento.id)
    ).all()
    # o saldo inicial é o ponto de partida da conta no sistema: vem antes de qualquer movimento já vencido,
    # mesmo de um lançado depois com data anterior (ex.: emissão de ontem registrada depois da importação)
    lancs = sorted(lancs, key=lambda l: (l.tipo != "saldo_inicial" or l.data_efetivacao > hoje,
                                         l.data_efetivacao, l.id))

    saldo, custo, futuro = 0, 0.0, 0
    contas_a_refazer: list[ContaPrograma] = []
    for l in lancs:
        if l.data_efetivacao > hoje:
            futuro += l.pontos
            continue
        regra = TIPOS_LANCAMENTO.get(l.tipo, {})
        if l.pontos >= 0:
            if regra.get("custo_entrada"):
                custo += l.valor or 0.0
            saldo += l.pontos
        else:
            saida = -l.pontos
            custo_saida = round(custo * min(saida / saldo, 1.0), 2) if saldo > 0 and custo > 0 else 0.0
            custo -= custo_saida
            saldo -= saida
            if saldo <= 0:
                custo = 0.0
            if l.valor != custo_saida:
                l.valor = custo_saida
            # o custo apurado vai para a emissão e para a outra ponta da transferência
            if l.emissao_id:
                emissao = db.get(Emissao, l.emissao_id)
                if emissao and emissao.custo_milhas != custo_saida:
                    emissao.custo_milhas = custo_saida
            if l.tipo == "transferencia_saida" and l.par_id:
                par = db.get(Lancamento, l.par_id)
                if par and par.status == "ativo" and par.valor != custo_saida:
                    par.valor = custo_saida
                    if par.conta_id not in _visitadas:
                        contas_a_refazer.append(par.conta)

    conta.saldo = saldo
    conta.saldo_futuro = futuro
    conta.custo_total = round(max(custo, 0.0), 2)
    conta.calculado_em = datetime.now()
    db.flush()
    for outra in contas_a_refazer:
        recalcular_conta(db, outra, hoje, _visitadas)
    return conta


def recalcular_vencidos(db: Session, hoje: date | None = None) -> int:
    """Contas com lançamentos futuros que já chegaram na data: entram no saldo."""
    hoje = hoje or date.today()
    contas = db.scalars(
        select(ContaPrograma).where(ContaPrograma.saldo_futuro != 0)
    ).all()
    refeitas = 0
    for conta in contas:
        calculado = conta.calculado_em.date() if conta.calculado_em else date.min
        tem_vencido = db.scalar(
            select(func.count(Lancamento.id)).where(
                Lancamento.conta_id == conta.id, Lancamento.status == "ativo",
                Lancamento.data_efetivacao <= hoje, Lancamento.data_efetivacao > calculado,
            )
        )
        if tem_vencido:
            recalcular_conta(db, conta, hoje)
            refeitas += 1
    db.commit()
    return refeitas


# ---------------------------------------------------------------- lançamentos


def lancar(
    db: Session,
    conta: ContaPrograma,
    tipo: str,
    pontos: int,
    *,
    valor: float | None = None,
    preco_milheiro: float | None = None,
    data_efetivacao: date | None = None,
    fornecedor_id: int | None = None,
    emissao_id: int | None = None,
    observacao: str | None = None,
    usuario: str | None = None,
    recalcular: bool = True,
) -> Lancamento:
    """Registra um movimento. `pontos` é sempre positivo; o sinal vem do tipo."""
    if tipo not in TIPOS_LANCAMENTO:
        raise ErroMVO(f"Tipo de lançamento desconhecido: {tipo}")
    if not pontos or pontos <= 0:
        raise ErroMVO("Informe a quantidade de pontos (maior que zero).")
    regra = TIPOS_LANCAMENTO[tipo]
    if regra["sinal"] > 0 and regra.get("custo_entrada") and valor is None and preco_milheiro is not None:
        valor = round(pontos / 1000 * preco_milheiro, 2)
    if regra["sinal"] < 0:
        valor = None  # custo de saída é apurado no recálculo
    l = Lancamento(
        conta_id=conta.id, tipo=tipo, pontos=regra["sinal"] * int(pontos), valor=valor,
        data_efetivacao=data_efetivacao or date.today(), fornecedor_id=fornecedor_id,
        emissao_id=emissao_id, observacao=(observacao or "").strip() or None, usuario=usuario,
    )
    db.add(l)
    db.flush()
    if recalcular:
        recalcular_conta(db, conta)
    return l


def transferir(
    db: Session,
    origem: ContaPrograma,
    destino: ContaPrograma,
    pontos: int,
    *,
    bonus_pct: float = 0.0,
    pontos_recebidos: int | None = None,
    data_efetivacao: date | None = None,
    data_credito: date | None = None,
    observacao: str | None = None,
    usuario: str | None = None,
) -> tuple[Lancamento, Lancamento]:
    """Transfere pontos de uma conta para outra (ex.: Livelo → Smiles com bônus). O custo vai junto."""
    if origem.id == destino.id:
        raise ErroMVO("Escolha contas diferentes para a transferência.")
    if not pontos or pontos <= 0:
        raise ErroMVO("Informe a quantidade de pontos a transferir.")
    recebidos = pontos_recebidos or int(round(pontos * (1 + (bonus_pct or 0) / 100)))
    obs = observacao or f"Transferência {origem.programa} → {destino.programa}"
    saida = lancar(db, origem, "transferencia_saida", pontos, data_efetivacao=data_efetivacao,
                   observacao=obs, usuario=usuario, recalcular=False)
    entrada = lancar(db, destino, "transferencia_entrada", recebidos,
                     data_efetivacao=data_credito or data_efetivacao, valor=0.0,
                     observacao=obs + (f" (bônus {bonus_pct:g}%)" if bonus_pct else ""), usuario=usuario,
                     recalcular=False)
    saida.par_id, entrada.par_id = entrada.id, saida.id
    db.flush()
    recalcular_conta(db, origem)  # apura o custo da saída e repassa para a entrada
    recalcular_conta(db, destino)
    return saida, entrada


def cancelar_lancamento(db: Session, lanc: Lancamento) -> list[ContaPrograma]:
    """Cancela o lançamento (e a outra ponta, se for transferência). Não apaga: fica no histórico."""
    afetadas = []
    for l in [lanc] + ([db.get(Lancamento, lanc.par_id)] if lanc.par_id else []):
        if l and l.status == "ativo":
            l.status = "cancelado"
            afetadas.append(l.conta)
    db.flush()
    for conta in afetadas:
        recalcular_conta(db, conta)
    return afetadas


# ---------------------------------------------------------------- passageiros (CPFs) por conta


def chave_passageiro(nome: str | None, cpf: str | None) -> str:
    d = so_digitos(cpf)
    if d:
        return "cpf:" + d
    n = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode().lower()
    return "nome:" + " ".join(n.split())


def configuracao_programa(db: Session, programa: str) -> ProgramaMilhas | None:
    return db.scalar(select(ProgramaMilhas).where(ProgramaMilhas.nome == programa))


def inicio_janela(cfg: ProgramaMilhas | None, hoje: date | None = None) -> date | None:
    hoje = hoje or date.today()
    if not cfg or not cfg.limite_cpfs or cfg.controla_emissoes is False:
        return None
    if (cfg.janela_cpfs or "ano") == "12m":
        return hoje - timedelta(days=365)
    return date(hoje.year, 1, 1)


def passageiros_no_periodo(db: Session, conta: ContaPrograma, desde: date | None) -> set[str]:
    """Passageiros já emitidos pela conta no período (CPF; sem CPF, pelo nome)."""
    if desde is None:
        return set()
    cfg = configuracao_programa(db, conta.programa)
    status = ["ativa"] if (cfg is None or cfg.cancelamento_devolve is not False) else ["ativa", "cancelada"]
    emissoes = db.scalars(
        select(Emissao).where(Emissao.conta_id == conta.id, Emissao.status.in_(status),
                              Emissao.data_emissao >= datetime.combine(desde, datetime.min.time()))
        .options(selectinload(Emissao.passageiros))
    ).all()
    chaves: set[str] = set()
    for e in emissoes:
        if e.passageiros:
            chaves.update(chave_passageiro(p.nome, p.cpf) for p in e.passageiros)
        else:  # emissão sem lista de passageiros: conta pela quantidade
            chaves.update(f"emissao:{e.id}:{i}" for i in range(e.qtd_passageiros or 1))
    return chaves


@dataclass
class SituacaoConta:
    conta: ContaPrograma
    cpfs_limite: int | None
    cpfs_usados: int
    emissoes_periodo: int

    @property
    def cpfs_livres(self) -> int | None:
        if self.cpfs_limite is None:
            return None
        return max(self.cpfs_limite - self.cpfs_usados, 0)

    def cabe(self, novos_passageiros: int) -> bool:
        return self.cpfs_livres is None or self.cpfs_livres >= novos_passageiros


def situacoes(db: Session, contas: list[ContaPrograma], cfgs: dict[str, ProgramaMilhas] | None = None,
              hoje: date | None = None) -> dict[int, SituacaoConta]:
    """CPFs usados e emissões no período de várias contas de uma vez (poucas consultas ao banco)."""
    hoje = hoje or date.today()
    if not contas:
        return {}
    if cfgs is None:
        cfgs = {p.nome: p for p in db.scalars(select(ProgramaMilhas))}
    padrao = hoje - timedelta(days=365)
    inicios = {c.id: inicio_janela(cfgs.get(c.programa), hoje) for c in contas}
    desde = min([d for d in inicios.values() if d] + [padrao])
    por_conta: dict[int, list[Emissao]] = defaultdict(list)
    ids = [c.id for c in contas]
    for lote in range(0, len(ids), 500):
        for e in db.scalars(
            select(Emissao).where(Emissao.conta_id.in_(ids[lote:lote + 500]), Emissao.status.in_(["ativa", "cancelada"]),
                                  Emissao.data_emissao >= datetime.combine(desde, time.min))
            .options(selectinload(Emissao.passageiros))
        ):
            por_conta[e.conta_id].append(e)

    saida = {}
    for c in contas:
        cfg = cfgs.get(c.programa)
        inicio = inicios[c.id]
        usados: set[str] = set()
        # cancelada só libera o CPF se o programa devolve o passageiro no cancelamento
        conta_canceladas = bool(cfg and cfg.cancelamento_devolve is False)
        if inicio:
            for e in por_conta[c.id]:
                if e.status != "ativa" and not conta_canceladas:
                    continue
                if e.data_emissao.date() >= inicio:
                    if e.passageiros:
                        usados.update(chave_passageiro(p.nome, p.cpf) for p in e.passageiros)
                    else:
                        usados.update(f"emissao:{e.id}:{i}" for i in range(e.qtd_passageiros or 1))
        contagem_desde = inicio or padrao
        emissoes = sum(1 for e in por_conta[c.id] if e.status == "ativa" and e.data_emissao.date() >= contagem_desde)
        saida[c.id] = SituacaoConta(c, cfg.limite_cpfs if cfg else None, len(usados), emissoes)
    return saida


def situacao(db: Session, conta: ContaPrograma, cfg: ProgramaMilhas | None = None,
             hoje: date | None = None) -> SituacaoConta:
    cfg = cfg if cfg is not None else configuracao_programa(db, conta.programa)
    return situacoes(db, [conta], {conta.programa: cfg} if cfg else {}, hoje)[conta.id]


def passageiros_novos(db: Session, conta: ContaPrograma, passageiros: list[tuple[str, str | None]],
                      hoje: date | None = None) -> int:
    """Quantos dos passageiros ainda não foram emitidos por essa conta no período (os que consomem CPF)."""
    cfg = configuracao_programa(db, conta.programa)
    usados = passageiros_no_periodo(db, conta, inicio_janela(cfg, hoje))
    return len({chave_passageiro(n, c) for n, c in passageiros} - usados)


# ---------------------------------------------------------------- disponibilidade


@dataclass
class LinhaDisponibilidade:
    conta: ContaPrograma
    situacao: SituacaoConta
    preco_b2c: float | None

    @property
    def custo_medio(self) -> float | None:
        return self.conta.custo_medio


def disponibilidade(
    db: Session,
    *,
    programa: str | None = None,
    programas: set[str] | None = None,
    busca: str = "",
    pontos_min: int = 0,
    passageiros: int = 0,
    incluir_pessoal: bool = False,
    incluir_zeradas: bool = True,
) -> list[LinhaDisponibilidade]:
    """Contas ativas com saldo e CPFs livres; as que servem para a emissão vêm primeiro, pelo menor custo."""
    consulta = (
        select(ContaPrograma).join(Colaborador)
        .where(ContaPrograma.ativa.is_(True), Colaborador.ativo.is_(True))
        .options(selectinload(ContaPrograma.colaborador))
    )
    if programa:
        consulta = consulta.where(ContaPrograma.programa == programa)
    if programas is not None:
        consulta = consulta.where(ContaPrograma.programa.in_(list(programas) or [""]))
    if not incluir_pessoal:
        consulta = consulta.where(ContaPrograma.uso_pessoal.is_(False))
    if not incluir_zeradas or pontos_min:
        consulta = consulta.where(ContaPrograma.saldo > 0)
    from ..busca import aplicar as aplicar_busca

    consulta = aplicar_busca(consulta, busca, [Colaborador.nome, ContaPrograma.numero, ContaPrograma.observacoes],
                             [Colaborador.cpf, ContaPrograma.numero])
    contas = [
        c for c in db.scalars(consulta).all()
        if not (pontos_min and c.disponivel < pontos_min) and not (not incluir_zeradas and c.disponivel <= 0)
    ]
    cfgs = {p.nome: p for p in db.scalars(select(ProgramaMilhas))}
    sits = situacoes(db, contas, cfgs)

    linhas = []
    for conta in contas:
        sit = sits[conta.id]
        if passageiros and not sit.cabe(passageiros):
            continue
        cfg = cfgs.get(conta.programa)
        linhas.append(LinhaDisponibilidade(conta, sit, cfg.preco_b2c if cfg else None))

    def ordem(l: LinhaDisponibilidade):
        sem_custo = l.custo_medio is None
        return (l.conta.disponivel <= 0, sem_custo, l.custo_medio or 0, -l.conta.disponivel, l.conta.colaborador.nome)

    return sorted(linhas, key=ordem)


def quem_pode_emitir(db: Session, promos, limite: int = 3) -> dict[int, dict]:
    """Para cada promoção em milhas: as contas com pontos e CPF livre para emitir, a de menor custo primeiro."""
    em_milhas = [p for p in promos if p.milhas and p.programa and not p.preco]
    if not em_milhas:
        return {}
    linhas = disponibilidade(db, programas={p.programa for p in em_milhas}, incluir_zeradas=False)
    por_programa: dict[str, list[LinhaDisponibilidade]] = defaultdict(list)
    for l in linhas:
        por_programa[l.conta.programa].append(l)
    saida = {}
    for p in em_milhas:
        servem = [l for l in por_programa[p.programa] if l.conta.disponivel >= p.milhas and l.situacao.cabe(1)]
        saida[p.id] = {"programa": p.programa, "milhas": p.milhas, "contas": servem[:limite], "total": len(servem)}
    return saida


def resumo_por_programa(db: Session) -> list[dict]:
    """Pontos e valor em estoque por programa (contas ativas, fora de uso pessoal)."""
    contas = db.scalars(
        select(ContaPrograma).join(Colaborador)
        .where(ContaPrograma.ativa.is_(True), Colaborador.ativo.is_(True), ContaPrograma.uso_pessoal.is_(False))
    ).all()
    grupos: dict[str, dict] = {}
    for c in contas:
        g = grupos.setdefault(c.programa, {"programa": c.programa, "contas": 0, "com_saldo": 0, "pontos": 0,
                                           "disponivel": 0, "futuro": 0, "custo": 0.0})
        g["contas"] += 1
        g["com_saldo"] += 1 if (c.saldo or 0) > 0 else 0
        g["pontos"] += max(c.saldo or 0, 0)
        g["disponivel"] += c.disponivel
        g["futuro"] += c.saldo_futuro or 0
        g["custo"] += c.custo_total or 0.0
    for g in grupos.values():
        g["custo"] = round(g["custo"], 2)
        g["custo_medio"] = round(g["custo"] / g["pontos"] * 1000, 2) if g["pontos"] > 0 and g["custo"] else None
    return sorted(grupos.values(), key=lambda r: -r["pontos"])


# ---------------------------------------------------------------- registro de acessos


ACOES_REGISTRO: dict[str, str] = {
    "login": "Entrou no sistema",
    "login_recusado": "Login recusado (senha errada ou acesso bloqueado)",
    "ver_credenciais": "Viu login e senha de conta",
    "credenciais_alteradas": "Alterou login/senha de conta",
    "colaborador_criado": "Cadastrou colaborador",
    "colaborador_editado": "Alterou colaborador",
    "conta_criada": "Criou conta em programa",
    "conta_editada": "Alterou conta",
    "lancamento": "Lançou pontos",
    "lancamento_cancelado": "Cancelou lançamento",
    "transferencia": "Transferiu pontos",
    "emissao": "Registrou emissão",
    "emissao_editada": "Alterou emissão",
    "emissao_cancelada": "Cancelou emissão",
    "fornecedor_salvo": "Cadastrou/alterou fornecedor",
    "cliente_salvo": "Cadastrou/alterou cliente",
    "usuario_criado": "Criou acesso",
    "usuario_alterado": "Alterou acesso",
    "senha_redefinida": "Redefiniu senha de usuário",
    "precos_alterados": "Alterou programas e preços",
    "configuracao": "Alterou configuração do Manager",
    "importacao_manager": "Importou planilha do Manager",
    "agencia_salva": "Cadastrou/alterou agência",
    "agencia_excluida": "Excluiu agência",
    "contrato": "Cadastrou/alterou/excluiu contrato",
    "compra_externa": "Registrou/alterou compra externa",
    "pedido": "Registrou/alterou pedido de emissão",
    "exportacao": "Exportou planilha",
    "exclusao": "Excluiu cadastro",
}


def registrar(db: Session, usuario, acao: str, alvo: str | None = None, detalhe: str | None = None,
              ip: str | None = None) -> None:
    db.add(RegistroAcesso(
        usuario_id=getattr(usuario, "id", None), usuario_nome=getattr(usuario, "nome", None),
        acao=acao, alvo=alvo, detalhe=detalhe, ip=ip,
    ))
