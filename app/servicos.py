"""Regras de negócio: registrar promoções, cruzar com interesses e montar mensagens."""
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import aeroportos
from .config import settings
from .models import (
    CanalTelegram,
    Cliente,
    EstadoColetor,
    Interesse,
    Match,
    ProgramaMilhas,
    Promocao,
    TemplateMensagem,
    Usuario,
)
from .parser import PromocaoExtraida, detectar_tipo_viagem, extrair
from .programas import PROGRAMAS_PADRAO, PROGRAMAS_PONTOS, detectar_programa

MESES_CURTOS = ["", "jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
MESES_LONGOS = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
                "setembro", "outubro", "novembro", "dezembro"]

TEMPLATE_PADRAO = (
    "Olá, {nome_sobrenome}! ✈️\n\n"
    "A {agencia} encontrou uma promoção para o trecho que você procura:\n\n"
    "*{origem} → {destino}*\n"
    "{tipo_linha}"
    "💰 *{valor}*\n"
    "📅 {datas}\n"
    "{companhia_linha}"
    "\nQuer que a gente verifique a disponibilidade e reserve para você? É só responder esta mensagem."
)

TEMPLATE_V2 = (
    "Olá, {nome_sobrenome}! ✈️\n\n"
    "A {agencia} encontrou uma promoção para o trecho que você procura:\n\n"
    "*{origem} → {destino}*\n"
    "💰 *{valor}*\n"
    "📅 {datas}\n"
    "{companhia_linha}"
    "\nQuer que a gente verifique a disponibilidade e reserve para você? É só responder esta mensagem."
)

TIPOS_VIAGEM = {"ida_volta": "Ida e volta", "ida": "Somente ida"}

# modelo das versões anteriores: se a agência não mexeu nele, é trocado pelo novo
TEMPLATE_ANTERIOR = (
    "Olá, {primeiro_nome}! ✈️\n\n"
    "Encontramos uma promoção para o trecho que você procura:\n"
    "*{origem} → {destino}*\n"
    "💰 {valor}\n"
    "📅 {datas}\n"
    "{companhia_linha}"
    "\nQuer que a gente verifique a disponibilidade e reserve para você? É só responder esta mensagem.\n\n"
    "{agencia}"
)


# ---------------------------------------------------------------- formatação


def formatar_preco(valor: float | None) -> str:
    if valor is None:
        return ""
    inteiro = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "R$ " + (inteiro[:-3] if inteiro.endswith(",00") else inteiro)


def formatar_milhas(valor: int | None) -> str:
    return f"{valor:,} milhas".replace(",", ".") if valor else ""


# ---------------------------------------------------------------- valor das milhas

_cache_programas: dict[str, tuple[float | None, float]] | None = None


def limpar_cache_programas() -> None:
    global _cache_programas
    _cache_programas = None


def tabela_programas() -> dict[str, tuple[float | None, float]]:
    """nome do programa -> (R$ por milha, acréscimo fixo em R$). Fica em memória até ser alterada."""
    global _cache_programas
    if _cache_programas is None:
        from .database import SessionLocal

        with SessionLocal() as db:
            _cache_programas = {
                p.nome: (p.valor_milha, p.taxa_fixa or 0.0) for p in db.scalars(select(ProgramaMilhas))
            }
    return _cache_programas


def preco_reais(promo: Promocao) -> float | None:
    """Preço em dinheiro da promoção, ou as milhas convertidas pelo valor definido para o programa."""
    if promo.preco:
        return promo.preco
    if promo.milhas and promo.programa:
        valor, taxa = tabela_programas().get(promo.programa, (None, 0.0))
        if valor:
            return round(promo.milhas * valor + (taxa or 0), 2)
    return None


def detalhe_milhas(promo: Promocao) -> str:
    """Ex.: "24.000 milhas Azul Fidelidade × R$ 27/milheiro + R$ 80". Vazio se não for promoção em milhas."""
    if not promo.milhas or promo.preco:
        return ""
    texto = formatar_milhas(promo.milhas) + (f" {promo.programa}" if promo.programa else "")
    valor, taxa = tabela_programas().get(promo.programa or "", (None, 0.0))
    if valor:
        texto += f" × {formatar_preco(round(valor * 1000, 2))}/milheiro"
        if taxa:
            texto += f" + {formatar_preco(taxa)}"
    return texto


def milhas_sem_valor(promo: Promocao) -> bool:
    """Promoção em milhas cujo programa ainda não tem valor definido."""
    return bool(promo.milhas and not promo.preco and preco_reais(promo) is None)


def formatar_valor(promo: Promocao) -> str:
    reais = preco_reais(promo)
    if reais:
        return formatar_preco(reais)
    if promo.milhas:
        return formatar_milhas(promo.milhas)
    return "consulte"


def formatar_periodo(inicio: date | None, fim: date | None, texto: str | None = None) -> str:
    if texto:
        return texto
    if not inicio:
        return "datas a confirmar"
    fim = fim or inicio
    mes_inteiro_ini = inicio.day == 1
    mes_inteiro_fim = (fim + timedelta(days=1)).day == 1
    if mes_inteiro_ini and mes_inteiro_fim:
        a = f"{MESES_LONGOS[inicio.month]}/{inicio.year}"
        if (inicio.year, inicio.month) == (fim.year, fim.month):
            return a
        return f"{a} a {MESES_LONGOS[fim.month]}/{fim.year}"
    if inicio == fim:
        return inicio.strftime("%d/%m/%Y")
    return f"{inicio:%d/%m/%Y} a {fim:%d/%m/%Y}"


def normalizar_telefone(telefone: str) -> str:
    """Deixa só dígitos e acrescenta o DDI 55 em números brasileiros (DDD + número)."""
    digitos = re.sub(r"\D", "", telefone or "")
    digitos = digitos.lstrip("0")
    if len(digitos) in (10, 11):
        digitos = "55" + digitos
    return digitos


def formatar_telefone(digitos: str) -> str:
    if digitos.startswith("55") and len(digitos) in (12, 13):
        ddd, numero = digitos[2:4], digitos[4:]
        return f"({ddd}) {numero[:-4]}-{numero[-4:]}"
    return "+" + digitos


# ---------------------------------------------------------------- usuários


def gerar_hash_senha(senha: str) -> str:
    sal = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, 200_000)
    return f"pbkdf2${sal.hex()}${dk.hex()}"


def conferir_senha(senha: str, guardado: str) -> bool:
    try:
        _, sal_hex, dk_hex = guardado.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal_hex), 200_000)
    return hmac.compare_digest(dk.hex(), dk_hex)


def preparar_banco(db: Session) -> None:
    """Cria o primeiro usuário e o template padrão, se ainda não existirem."""
    if not db.scalar(select(func.count(Usuario.id))):
        db.add(Usuario(nome="Administrador", login=settings.admin_login, papel="admin",
                       senha_hash=gerar_hash_senha(settings.admin_senha)))
    # níveis de acesso (MVO): o primeiro usuário é o administrador; os outros que já existiam ficam com o
    # acesso mínimo (alertas) até o administrador definir o papel de cada um
    sem_papel = db.scalars(select(Usuario).where(Usuario.papel.is_(None)).order_by(Usuario.id)).all()
    if sem_papel:
        tem_admin = db.scalar(select(func.count(Usuario.id)).where(Usuario.papel == "admin"))
        for u in sem_papel:
            if not tem_admin:
                u.papel, tem_admin = "admin", 1
            else:
                u.papel = "atendente"
    if not db.scalar(select(func.count(TemplateMensagem.id))):
        db.add(TemplateMensagem(nome="Padrão", texto=TEMPLATE_PADRAO, padrao=True))
    for t in db.scalars(select(TemplateMensagem).where(TemplateMensagem.texto.in_([TEMPLATE_ANTERIOR, TEMPLATE_V2]))):
        t.texto = TEMPLATE_PADRAO
    # preço máximo absurdo (data lida como número em importações antigas): remove o limite
    for interesse in db.scalars(select(Interesse).where(Interesse.preco_maximo > 200_000)):
        interesse.preco_maximo = None
    for cliente in db.scalars(select(Cliente)):
        ajustado = ajustar_nome(cliente.nome)
        if ajustado != cliente.nome:
            cliente.nome = ajustado
    # grupos do Telegram que estavam no .env passam a ser gerenciados pelo site
    if settings.telegram_canais and not db.scalar(select(func.count(CanalTelegram.id))):
        for ident in settings.telegram_canais:
            db.add(CanalTelegram(identificador=ident))
    existentes = set(db.scalars(select(ProgramaMilhas.nome)))
    for nome in list(PROGRAMAS_PADRAO) + list(PROGRAMAS_PONTOS):
        if nome not in existentes:
            db.add(ProgramaMilhas(nome=nome))
    # preço de venda: o valor por milha (versão de alertas) e o milheiro B2C (MVO) andam juntos
    for prog in db.scalars(select(ProgramaMilhas)):
        if prog.preco_b2c is None and prog.valor_milha:
            prog.preco_b2c = round(prog.valor_milha * 1000, 4)
        elif prog.preco_b2c and not prog.valor_milha:
            prog.valor_milha = round(prog.preco_b2c / 1000, 6)
    db.commit()

    # promoções em milhas lidas por versões anteriores: identifica o programa
    extras = programas_personalizados(db)
    for promo in db.scalars(select(Promocao).where(Promocao.milhas.is_not(None), Promocao.programa.is_(None))):
        promo.programa = detectar_programa(promo.texto_original, promo.companhia, extras)
    for promo in db.scalars(select(Promocao).where(Promocao.tipo_viagem.is_(None), Promocao.texto_original.is_not(None))):
        tipo = detectar_tipo_viagem(promo.texto_original)
        if tipo:
            promo.tipo_viagem = tipo
    db.commit()
    limpar_cache_programas()


def programas_personalizados(db: Session) -> list[str]:
    return list(db.scalars(select(ProgramaMilhas.nome).where(ProgramaMilhas.personalizado.is_(True))))


# ---------------------------------------------------------------- estado dos coletores


def ler_estado(db: Session, chave: str) -> str | None:
    item = db.get(EstadoColetor, chave)
    return item.valor if item else None


def gravar_estado(db: Session, chave: str, valor: str) -> None:
    item = db.get(EstadoColetor, chave)
    if item:
        item.valor = valor
    else:
        db.add(EstadoColetor(chave=chave, valor=valor))
    db.commit()


# ---------------------------------------------------------------- matching


def periodos_se_cruzam(a_ini: date | None, a_fim: date | None, b_ini: date | None, b_fim: date | None) -> bool:
    if not (a_ini or a_fim) or not (b_ini or b_fim):
        return True  # sem data de um dos lados: não dá para excluir
    a_ini, a_fim = a_ini or date.min, a_fim or date.max
    b_ini, b_fim = b_ini or date.min, b_fim or date.max
    return a_ini <= b_fim and b_ini <= a_fim


def interesse_compativel(interesse: Interesse, promo: Promocao) -> bool:
    if not aeroportos.mesmo_lugar(interesse.origem, promo.origem):
        return False
    if interesse.destino and not aeroportos.mesmo_lugar(interesse.destino, promo.destino):
        return False
    if interesse.preco_maximo:
        reais = preco_reais(promo)  # dinheiro ou milhas convertidas
        if reais is not None and reais > interesse.preco_maximo:
            return False
        if reais is None and not promo.milhas:
            return False
        # milhas de um programa ainda sem valor definido: entra e a equipe avalia
    return periodos_se_cruzam(interesse.periodo_inicio, interesse.periodo_fim, promo.data_inicio, promo.data_fim)


def _interesses_ativos(db: Session):
    return db.scalars(
        select(Interesse).join(Cliente).where(Interesse.ativo.is_(True), Cliente.ativo.is_(True))
    ).all()


def _criar_match(db: Session, promo: Promocao, interesse: Interesse) -> bool:
    existe = db.scalar(select(Match.id).where(Match.promocao_id == promo.id, Match.cliente_id == interesse.cliente_id))
    if existe:
        return False
    db.add(Match(promocao_id=promo.id, cliente_id=interesse.cliente_id, interesse_id=interesse.id))
    db.flush()
    return True


def gerar_matches_para_promocao(db: Session, promo: Promocao) -> int:
    criados = 0
    for interesse in _interesses_ativos(db):
        if interesse_compativel(interesse, promo) and _criar_match(db, promo, interesse):
            criados += 1
    db.commit()
    return criados


def promocoes_vigentes(db: Session):
    limite = datetime.now() - timedelta(days=settings.promo_validade_dias)
    return db.scalars(
        select(Promocao).where(Promocao.status == "ativa", Promocao.coletada_em >= limite)
        .order_by(Promocao.coletada_em.desc())
    ).all()


def recalcular_matches(db: Session) -> dict:
    """Depois de mudar o valor das milhas: tira da fila quem ficou acima do orçamento
    e inclui quem passou a caber. Envios já feitos não são alterados."""
    removidos = criados = 0
    for promo in promocoes_vigentes(db):
        if not promo.milhas or promo.preco:
            continue
        na_fila = db.scalars(select(Match).where(Match.promocao_id == promo.id, Match.status == "nova")).all()
        for m in na_fila:
            if m.interesse and not interesse_compativel(m.interesse, promo):
                db.delete(m)
                removidos += 1
        db.flush()
        criados += gerar_matches_para_promocao(db, promo)
    db.commit()
    return {"removidos": removidos, "criados": criados}


def gerar_matches_para_interesse(db: Session, interesse: Interesse) -> int:
    """Quando um interesse é cadastrado, procura nas promoções ainda vigentes."""
    if not interesse.ativo:
        return 0
    criados = 0
    for promo in promocoes_vigentes(db):
        if interesse_compativel(interesse, promo) and _criar_match(db, promo, interesse):
            criados += 1
    db.commit()
    return criados


# ---------------------------------------------------------------- registro de promoções


@dataclass
class Resultado:
    promocao: Promocao | None
    motivo: str  # criada, duplicada, incompleta
    matches: int = 0


def _preco_suspeito(db: Session, promo: Promocao) -> str | None:
    """Marca preço muito abaixo da média do mesmo trecho (possível erro tarifário)."""
    if not promo.preco:
        return None
    media = db.scalar(
        select(func.avg(Promocao.preco)).where(
            Promocao.origem.in_(_codigos_do_grupo(promo.origem)),
            Promocao.destino.in_(_codigos_do_grupo(promo.destino)),
            Promocao.preco.is_not(None),
            Promocao.status == "ativa",
        )
    )
    qtd = db.scalar(
        select(func.count(Promocao.id)).where(
            Promocao.origem.in_(_codigos_do_grupo(promo.origem)),
            Promocao.destino.in_(_codigos_do_grupo(promo.destino)),
            Promocao.preco.is_not(None),
        )
    )
    if media and qtd >= 3 and promo.preco < media * 0.5:
        return f"Preço {round((1 - promo.preco / media) * 100)}% abaixo da média do trecho: possível erro tarifário"
    return None


def _codigos_do_grupo(codigo: str) -> list[str]:
    g = aeroportos.grupo(codigo)
    return [c for c, (_, gr) in aeroportos.AEROPORTOS.items() if gr == g] or [codigo]


def _duplicada(db: Session, dados: PromocaoExtraida) -> Promocao | None:
    """Mesma promoção publicada em vários canais nos últimos 3 dias."""
    limite = datetime.now() - timedelta(days=3)
    return db.scalar(
        select(Promocao).where(
            Promocao.origem.in_(_codigos_do_grupo(dados.origem)),
            Promocao.destino.in_(_codigos_do_grupo(dados.destino)),
            Promocao.preco.is_(dados.preco) if dados.preco is None else Promocao.preco == dados.preco,
            Promocao.milhas.is_(dados.milhas) if dados.milhas is None else Promocao.milhas == dados.milhas,
            Promocao.data_inicio.is_(dados.data_inicio) if dados.data_inicio is None
            else Promocao.data_inicio == dados.data_inicio,
            Promocao.coletada_em >= limite,
        ).limit(1)
    )


def registrar_promocao(
    db: Session,
    *,
    texto: str | None = None,
    dados: PromocaoExtraida | None = None,
    fonte: str,
    fonte_detalhe: str | None = None,
    link_fonte: str | None = None,
    datas_texto: str | None = None,
) -> Resultado:
    """Salva uma promoção (a partir do texto ou de dados já estruturados) e gera os matches."""
    if dados is None:
        dados = extrair(texto or "")
    if not dados.completa:
        return Resultado(None, "incompleta")
    if (dados.preco is None and dados.milhas is None) and fonte == "telegram":
        return Resultado(None, "incompleta")  # mensagem sem valor costuma não ser promoção
    existente = _duplicada(db, dados)
    if existente:
        return Resultado(existente, "duplicada")
    if dados.milhas and not dados.programa:
        dados.programa = detectar_programa(texto, dados.companhia, programas_personalizados(db))

    promo = Promocao(
        fonte=fonte,
        fonte_detalhe=fonte_detalhe,
        link_fonte=link_fonte,
        origem=dados.origem,
        destino=dados.destino,
        data_inicio=dados.data_inicio,
        data_fim=dados.data_fim,
        datas_texto=datas_texto or None,
        preco=dados.preco,
        milhas=dados.milhas,
        programa=dados.programa if dados.milhas else None,
        tipo_viagem=dados.tipo_viagem,
        companhia=dados.companhia,
        link=dados.link,
        texto_original=texto,
        confianca=dados.confianca,
    )
    promo.alerta = _preco_suspeito(db, promo)
    db.add(promo)
    db.flush()
    qtd = gerar_matches_para_promocao(db, promo)
    return Resultado(promo, "criada", qtd)


# ---------------------------------------------------------------- mensagens


def template_padrao(db: Session) -> TemplateMensagem:
    t = db.scalar(select(TemplateMensagem).where(TemplateMensagem.padrao.is_(True)))
    if t is None:
        t = TemplateMensagem(nome="Padrão", texto=TEMPLATE_PADRAO, padrao=True)
        db.add(t)
        db.commit()
    return t


class _Seguro(dict):
    def __missing__(self, chave):  # variável desconhecida no template fica vazia
        return ""


_PARTICULAS = {"da", "de", "do", "das", "dos", "e", "di", "du", "del", "van", "von"}


def nome_bonito(nome: str | None) -> str:
    """'MARIA DA SILVA oliveira' -> 'Maria da Silva Oliveira' (nomes vindos de formulário)."""
    palavras = (nome or "").split()
    saida = []
    for i, p in enumerate(palavras):
        baixa = p.lower()
        if i and baixa in _PARTICULAS:
            saida.append(baixa)
        else:
            saida.append("-".join(parte[:1].upper() + parte[1:].lower() for parte in p.split("-")))
    return " ".join(saida)


def ajustar_nome(nome: str | None) -> str:
    """Corrige nomes digitados todo em maiúsculas ou minúsculas; mantém os demais como estão."""
    nome = " ".join((nome or "").split())
    return nome_bonito(nome) if nome.isupper() or nome.islower() else nome


def nome_sobrenome(nome: str | None) -> str:
    """Primeiro nome + último sobrenome: 'Maria da Silva Oliveira' -> 'Maria Oliveira'."""
    palavras = [p for p in nome_bonito(nome).split() if p.lower() not in _PARTICULAS]
    if len(palavras) <= 1:
        return palavras[0] if palavras else ""
    return f"{palavras[0]} {palavras[-1]}"


def montar_mensagem(template: str, cliente: Cliente, promo: Promocao) -> str:
    valores = _Seguro(
        nome=nome_bonito(cliente.nome),
        nome_sobrenome=nome_sobrenome(cliente.nome),
        primeiro_nome=nome_sobrenome(cliente.nome).split(" ")[0],
        origem=aeroportos.nome_curto(promo.origem),
        destino=aeroportos.nome_curto(promo.destino),
        origem_codigo=promo.origem,
        destino_codigo=promo.destino,
        preco=formatar_preco(promo.preco),
        milhas=formatar_milhas(promo.milhas),
        programa=promo.programa or "",
        valor=formatar_valor(promo),
        datas=formatar_periodo(promo.data_inicio, promo.data_fim, promo.datas_texto),
        companhia=promo.companhia or "",
        companhia_linha=f"🛫 {promo.companhia}\n" if promo.companhia else "",
        tipo_viagem=TIPOS_VIAGEM.get(promo.tipo_viagem or "", ""),
        tipo_linha={"ida_volta": "🔁 Ida e volta\n", "ida": "➡️ Somente ida\n"}.get(promo.tipo_viagem or "", ""),
        link=promo.link or "",
        agencia=settings.agencia_nome,
    )
    try:
        texto = template.format_map(valores)
    except (ValueError, IndexError):  # chaves soltas no template
        texto = template
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def link_whatsapp(telefone: str, mensagem: str) -> str:
    return f"https://wa.me/{normalizar_telefone(telefone)}?text={quote(mensagem)}"
