"""Regras de negócio: registrar promoções, cruzar com interesses e montar mensagens."""
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import aeroportos
from .config import settings
from .models import (
    TIPOS_PROMOCAO,
    CanalTelegram,
    Cliente,
    ConfirmacaoCadastro,
    EstadoColetor,
    ImagemPromocao,
    Interesse,
    Match,
    ProgramaMilhas,
    Promocao,
    TemplateMensagem,
    Usuario,
    novo_token,
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

# confirmação para quem se cadastrou pelo formulário (editável em Configurações)
NOME_TEMPLATE_CONFIRMACAO = "Confirmação de cadastro"
TEMPLATE_CONFIRMACAO = (
    "Olá, {nome_sobrenome}! 😊\n\n"
    "Obrigado pelo seu cadastro na {agencia}! A partir de agora a gente fica de olho nas promoções para você:\n\n"
    "{trechos}\n\n"
    "Assim que aparecer uma boa oportunidade, avisamos por aqui. "
    "Quer mudar algum destino ou data? É só responder esta mensagem. ✈️"
)

# para quem se cadastrou sem nenhum trecho reconhecido: pergunta para onde quer viajar (editável em Configurações)
NOME_TEMPLATE_PERGUNTA = "Pergunta de trechos"
TEMPLATE_PERGUNTA = (
    "Olá, {primeiro_nome}! 😊\n\n"
    "Obrigado pelo seu cadastro na {agencia}! Para a gente procurar as melhores promoções para você, "
    "conta pra gente: *de onde você costuma sair e para quais destinos gostaria de viajar?*\n\n"
    "Pode responder assim, por exemplo:\n\n"
    "Fortaleza para Rio de Janeiro\n\n"
    "Se tiver uma época preferida (ex.: julho, dezembro), pode contar também. "
    "Assim que aparecer uma boa oportunidade, avisamos por aqui! ✈️"
)
# primeira versão do texto sugerido: quem ainda está com ela passa para a atual
_PERGUNTA_ANTIGA = (
    "Olá, {primeiro_nome}! 😊\n\n"
    "Obrigado pelo seu cadastro na {agencia}! Para a gente procurar as melhores promoções para você, "
    "conta pra gente: *de onde você costuma sair e para quais destinos gostaria de viajar?*\n\n"
    "Pode responder assim, por exemplo:\n"
    "✈️ Fortaleza → Lisboa\n"
    "✈️ Fortaleza → Gramado\n\n"
    "Se tiver uma época preferida (ex.: julho, dezembro) ou um valor máximo, pode contar também. "
    "Assim que aparecer uma boa oportunidade, avisamos por aqui! ✈️"
)


# página do cliente: o endereço vai no fim das mensagens (ou onde o modelo tiver {pagina} / {pagina_linha})
CAMINHO_PAGINA = "/para-voce/"
LINHA_PAGINA_PROMOCAO = "👉 Veja todas as promoções que separamos para você: {url}"
LINHA_PAGINA_CONFIRMACAO = "👉 As promoções que encontrarmos para você vão aparecer nesta página: {url}"
CHAVE_WHATSAPP_AGENCIA = "whatsapp_agencia"
CHAVE_LINK_NAS_MENSAGENS = "pagina_cliente_nas_mensagens"
NOME_TEMPLATE_PAGINA = "Link da página do cliente"
TEMPLATE_PAGINA = (
    "Olá, {nome_sobrenome}! ✈️\n\n"
    "A {agencia} separou {quantidade} para você:\n\n"
    "{resumo}\n\n"
    "👉 Veja todas, com datas e valores, na sua página: {pagina}\n\n"
    "Quer garantir alguma? É só responder esta mensagem."
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


def valor_parcela(valor: float) -> str:
    """Valor de parcela sempre com centavos: 500 → "500,00"."""
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_valor(promo: Promocao) -> str:
    reais = preco_reais(promo)
    if reais and getattr(promo, "parcelas", None):
        return f"R$ {valor_parcela(reais)} de {promo.parcelas}x"
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
        if not cliente.token_pagina:  # clientes de antes da página do cliente
            cliente.token_pagina = novo_token()
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

    from .mvo.migracoes import preparar_mvo

    preparar_mvo(db)


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
    if not promo.passagem:
        # hotel, pacote, cruzeiro: vale para quem quer ir para aquela cidade (sem limite de preço: é outro produto)
        if not (promo.destino and interesse.destino and aeroportos.mesmo_lugar(interesse.destino, promo.destino)):
            return False
        # viagem do site da agência tem cidade de saída: só vai para quem sai de lá (FOR → GIG não serve a quem sai de SP)
        if promo.fonte == "site" and promo.origem and interesse.origem and \
                not aeroportos.mesmo_lugar(interesse.origem, promo.origem):
            return False
        return periodos_se_cruzam(interesse.periodo_inicio, interesse.periodo_fim, promo.data_inicio, promo.data_fim)
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
    if not promo.preco or not promo.passagem:
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
    if dados.passagem:
        mesmo_lugar = [Promocao.origem.in_(_codigos_do_grupo(dados.origem)),
                       Promocao.destino.in_(_codigos_do_grupo(dados.destino))]
    else:  # hotel, pacote...: mesmo tipo e mesmo lugar (ou mesmo título)
        mesmo_lugar = [Promocao.tipo == dados.tipo,
                       or_(func.lower(Promocao.lugar) == (dados.lugar or "").lower(),
                           func.lower(Promocao.titulo) == (dados.titulo or "").lower())]
    return db.scalar(
        select(Promocao).where(
            *mesmo_lugar,
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
    imagem: bytes | None = None,
    mime: str | None = None,
) -> Resultado:
    """Salva uma promoção (a partir do texto ou de dados já estruturados) e gera os matches."""
    if dados is None:
        dados = extrair(texto or "")
    if not dados.completa and not (fonte == "site" and (dados.titulo or dados.lugar)):  # site: "A definir" também vale
        return Resultado(None, "incompleta")
    if (dados.preco is None and dados.milhas is None) and fonte in ("telegram", "api"):
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
        tipo=dados.tipo if dados.tipo in TIPOS_PROMOCAO else "passagem",
        origem=dados.origem,
        destino=dados.destino,
        titulo=(dados.titulo or "")[:160] or None,
        lugar=(dados.lugar or "")[:120] or None,
        resumo=dados.resumo or None,
        data_inicio=dados.data_inicio,
        data_fim=dados.data_fim,
        datas_texto=datas_texto or dados.datas_texto or None,
        preco=dados.preco,
        milhas=dados.milhas,
        programa=dados.programa if dados.milhas else None,
        tipo_viagem=dados.tipo_viagem,
        parcelas=dados.parcelas if dados.parcelas and dados.parcelas > 1 else None,
        companhia=dados.companhia,
        link=dados.link,
        texto_original=texto,
        confianca=dados.confianca,
    )
    promo.alerta = _preco_suspeito(db, promo)
    db.add(promo)
    db.flush()
    if imagem:
        guardar_imagem(db, promo, imagem, mime)
    qtd = gerar_matches_para_promocao(db, promo)
    return Resultado(promo, "criada", qtd)


# ---------------------------------------------------------------- imagem da promoção

LARGURA_MAX_IMAGEM = 1280


def preparar_imagem(dados: bytes, mime: str | None = None) -> tuple[bytes, str]:
    """Reduz a imagem (máx. 1280 px, JPEG) para guardar e enviar; se não for imagem conhecida, devolve como veio."""
    try:
        from io import BytesIO

        from PIL import Image

        im = Image.open(BytesIO(dados))
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if max(im.size) > LARGURA_MAX_IMAGEM:
            im.thumbnail((LARGURA_MAX_IMAGEM, LARGURA_MAX_IMAGEM))
        saida = BytesIO()
        im.save(saida, format="JPEG", quality=85, optimize=True)
        return saida.getvalue(), "image/jpeg"
    except Exception:  # Pillow ausente ou formato desconhecido
        return dados, mime or "image/jpeg"


def guardar_imagem(db: Session, promo: Promocao, dados: bytes, mime: str | None = None) -> None:
    dados, mime = preparar_imagem(dados, mime)
    if promo.imagem:
        promo.imagem.dados, promo.imagem.mime = dados, mime
    else:
        promo.imagem = ImagemPromocao(dados=dados, mime=mime)
    db.flush()


def limpar_imagens_antigas(db: Session, dias: int | None = None) -> int:
    """Apaga as imagens de promoções antigas (elas só servem enquanto a promoção vale)."""
    limite = datetime.now() - timedelta(days=dias or settings.imagens_dias)
    antigas = db.scalars(select(ImagemPromocao).join(Promocao).where(Promocao.coletada_em < limite)).all()
    for img in antigas:
        db.delete(img)
    db.commit()
    return len(antigas)


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


def _tem_lugar_do_link(template: str) -> bool:
    return bool(re.search(r"\{pagina(_linha)?\}", template))


def _formatar(template: str, valores: dict, pagina: str = "", linha_pagina: str = "") -> str:
    """Preenche o modelo; sem {pagina} nem {pagina_linha} no modelo, o link da página vai no fim."""
    try:
        texto = template.format_map(valores)
    except (ValueError, IndexError):  # chaves soltas no template
        texto = template
    if pagina and linha_pagina and not _tem_lugar_do_link(template):
        texto = texto.rstrip() + "\n\n" + linha_pagina.format(url=pagina)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


_RE_TRECHO_NO_MODELO = re.compile(r"\{origem\}\s*(?:→|->|➜|➡|✈|-|–|x)\s*\{destino\}")
EMOJI_TIPO = {"hotel": "🏨", "pacote": "🧳", "cruzeiro": "🚢", "outro": "🎟️"}


def descrever_trecho(promo: Promocao) -> str:
    """'Fortaleza → Lisboa' para passagem; o título (ou tipo + lugar) para as outras promoções."""
    if promo.passagem:
        return f"{aeroportos.nome_curto(promo.origem)} → {aeroportos.nome_curto(promo.destino)}"
    return promo.titulo or (f"{promo.nome_tipo} em {promo.lugar}" if promo.lugar else promo.nome_tipo)


def montar_mensagem(template: str, cliente: Cliente, promo: Promocao, pagina: str = "") -> str:
    """Mensagem de uma promoção. pagina: endereço da página do cliente (vazio = mensagem sem o link)."""
    if not promo.passagem:  # "*{origem} → {destino}*" no modelo vira o título da oferta
        template = _RE_TRECHO_NO_MODELO.sub("{trecho}", template).replace("o trecho que você procura",
                                                                            "o destino que você procura")
    tipo_linha = {"ida_volta": "🔁 Ida e volta\n", "ida": "➡️ Somente ida\n"}.get(promo.tipo_viagem or "", "")
    if not promo.passagem:
        tipo_linha = f"{EMOJI_TIPO.get(promo.tipo, '🎟️')} {promo.nome_tipo}" + (f" · {promo.lugar}\n" if promo.lugar else "\n")
    valores = _Seguro(
        nome=nome_bonito(cliente.nome),
        nome_sobrenome=nome_sobrenome(cliente.nome),
        primeiro_nome=nome_sobrenome(cliente.nome).split(" ")[0],
        origem=aeroportos.nome_curto(promo.origem) if promo.origem else "",
        destino=aeroportos.nome_curto(promo.destino) if promo.destino else (promo.lugar or ""),
        origem_codigo=promo.origem or "",
        destino_codigo=promo.destino or "",
        preco=formatar_valor(promo) if promo.parcelas else formatar_preco(promo.preco),   # parcelado: "R$ 500,00 de 12x"
        milhas=formatar_milhas(promo.milhas),
        programa=promo.programa or "",
        valor=formatar_valor(promo),
        datas=formatar_periodo(promo.data_inicio, promo.data_fim, promo.datas_texto),
        companhia=promo.companhia or "",
        companhia_linha=f"🛫 {promo.companhia}\n" if promo.companhia else "",
        tipo_viagem=TIPOS_VIAGEM.get(promo.tipo_viagem or "", ""),
        tipo_linha=tipo_linha,
        trecho=descrever_trecho(promo),
        titulo=promo.titulo or "",
        lugar=promo.lugar or "",
        resumo=promo.resumo or "",
        resumo_linha=f"📝 {promo.resumo}\n" if promo.resumo else "",
        tipo=promo.nome_tipo,
        link=promo.link or "",
        agencia=settings.agencia_nome,
        pagina=pagina,
        pagina_linha="\n" + LINHA_PAGINA_PROMOCAO.format(url=pagina) + "\n" if pagina else "",
    )
    texto = _formatar(template, valores, pagina, LINHA_PAGINA_PROMOCAO)
    if not promo.passagem and promo.resumo and "{resumo" not in template:
        # o modelo padrão fala de voo; nas outras promoções o resumo lido entra depois do valor/datas
        texto = re.sub(r"(📅[^\n]*\n)", lambda m: m.group(1) + f"📝 {promo.resumo}\n", texto, count=1)
    return texto


def link_whatsapp(telefone: str, mensagem: str) -> str:
    """Link que abre a conversa com a mensagem pronta. Usa api.whatsapp.com/send em vez de wa.me: o redirecionamento
    do wa.me estraga os emojis (viram "�") no WhatsApp do computador e no WhatsApp Web."""
    return f"https://api.whatsapp.com/send?phone={normalizar_telefone(telefone)}&text={quote(mensagem, safe='')}"


# ---------------------------------------------------------------- confirmação de cadastro (formulário)


def template_confirmacao(db: Session) -> TemplateMensagem:
    t = db.scalar(select(TemplateMensagem).where(TemplateMensagem.nome == NOME_TEMPLATE_CONFIRMACAO,
                                                 TemplateMensagem.padrao.is_not(True)))
    if t is None:
        t = TemplateMensagem(nome=NOME_TEMPLATE_CONFIRMACAO, texto=TEMPLATE_CONFIRMACAO, padrao=False)
        db.add(t)
        db.commit()
    return t


def template_pergunta(db: Session) -> TemplateMensagem:
    t = db.scalar(select(TemplateMensagem).where(TemplateMensagem.nome == NOME_TEMPLATE_PERGUNTA,
                                                 TemplateMensagem.padrao.is_not(True)))
    if t is None:
        t = TemplateMensagem(nome=NOME_TEMPLATE_PERGUNTA, texto=TEMPLATE_PERGUNTA, padrao=False)
        db.add(t)
        db.commit()
    elif t.texto == _PERGUNTA_ANTIGA:
        t.texto = TEMPLATE_PERGUNTA
        db.commit()
    return t


def periodo_extenso(inicio: date | None, fim: date | None) -> str:
    """Período escrito para o cliente: 'março a junho de 2027', 'março de 2027', '15/03/2027', 'qualquer data'."""
    def mes(d: date) -> str:
        return f"{MESES_LONGOS[d.month]} de {d.year}"

    if not inicio and not fim:
        return "qualquer data"
    if not inicio:
        return "até " + (mes(fim) if (fim + timedelta(days=1)).day == 1 else f"{fim:%d/%m/%Y}")
    if not fim:
        return "a partir de " + (mes(inicio) if inicio.day == 1 else f"{inicio:%d/%m/%Y}")
    if inicio.day == 1 and (fim + timedelta(days=1)).day == 1:  # meses inteiros
        if (inicio.year, inicio.month) == (fim.year, fim.month):
            return mes(inicio)
        if inicio.year == fim.year:
            return f"{MESES_LONGOS[inicio.month]} a {mes(fim)}"
        return f"{mes(inicio)} a {mes(fim)}"
    if inicio == fim:
        return f"{inicio:%d/%m/%Y}"
    if inicio.year == fim.year:
        return f"{inicio:%d/%m} a {fim:%d/%m/%Y}"
    return f"{inicio:%d/%m/%Y} a {fim:%d/%m/%Y}"


def _juntar(nomes: list[str]) -> str:
    """['Lisboa', 'Paris', 'Roma'] -> 'Lisboa, Paris e Roma'."""
    return nomes[0] if len(nomes) == 1 else (", ".join(nomes[:-1]) + " e " + nomes[-1] if nomes else "")


def _destinos(interesses: list[Interesse]) -> list[str]:
    nomes: list[str] = []
    for i in interesses:
        nome = aeroportos.nome_curto(i.destino)
        if nome not in nomes:
            nomes.append(nome)
    return ["qualquer destino"] if "qualquer destino" in nomes else nomes


def texto_trechos(interesses: list[Interesse]) -> str:
    """Um bloco por origem e período: '✈️ *Fortaleza → Lisboa e Paris*', '📅 março a junho de 2027', '💰 até R$ 3.500'."""
    grupos: dict[tuple, list[Interesse]] = {}
    for i in interesses:
        grupos.setdefault((i.origem, i.periodo_inicio, i.periodo_fim, i.preco_maximo), []).append(i)
    blocos = []
    for (origem, inicio, fim, preco), lista in grupos.items():
        linhas = [f"✈️ *{aeroportos.nome_curto(origem)} → {_juntar(_destinos(lista))}*",
                  f"📅 {periodo_extenso(inicio, fim)}"]
        if preco:
            linhas.append(f"💰 até {formatar_preco(preco)}")
        blocos.append("\n".join(linhas))
    return "\n\n".join(blocos)


def montar_confirmacao(template: str, cliente: Cliente, interesses: list[Interesse], pagina: str = "") -> str:
    primeiro = interesses[0] if interesses else None
    valores = _Seguro(
        nome=nome_bonito(cliente.nome),
        nome_sobrenome=nome_sobrenome(cliente.nome),
        primeiro_nome=nome_sobrenome(cliente.nome).split(" ")[0],
        agencia=settings.agencia_nome,
        trechos=texto_trechos(interesses) or "✈️ Os destinos e as datas que você escolheu no formulário",
        destinos=_juntar(_destinos(interesses)) or "os destinos que você escolheu",
        origem=aeroportos.nome_curto(primeiro.origem) if primeiro else "",
        destino=aeroportos.nome_curto(primeiro.destino) if primeiro else "o destino que você escolheu",
        datas=periodo_extenso(primeiro.periodo_inicio, primeiro.periodo_fim) if primeiro else "as datas que você escolheu",
        pagina=pagina,
        pagina_linha="\n" + LINHA_PAGINA_CONFIRMACAO.format(url=pagina) + "\n" if pagina else "",
    )
    return _formatar(template, valores, pagina, LINHA_PAGINA_CONFIRMACAO)


def trechos_da_confirmacao(conf: ConfirmacaoCadastro) -> list[Interesse]:
    """Trechos ativos que a confirmação cita: os da resposta do formulário ou, sem lista, todos os do cliente."""
    ativos = [i for i in conf.cliente.interesses if i.ativo]
    if not conf.trechos:
        return ativos
    ids = {int(x) for x in conf.trechos.split(",") if x.strip().isdigit()}
    return [i for i in ativos if i.id in ids]


def preparar_confirmacao(db: Session, cliente: Cliente, novos: list[Interesse], cliente_novo: bool) -> bool:
    """Põe na fila a confirmação de quem acabou de responder o formulário. Se já há uma esperando envio, os trechos
    novos entram nela. Quem já era cliente e não escolheu nenhum trecho novo não recebe outra."""
    pendente = next((c for c in cliente.confirmacoes if c.status == "nova"), None)
    if pendente is not None:
        if pendente.trechos:  # a pendente cita trechos específicos: soma os novos
            ids = [x for x in pendente.trechos.split(",") if x] + [str(i.id) for i in novos]
            pendente.trechos = ",".join(dict.fromkeys(ids))
        return True
    if not cliente_novo and not novos:
        return False
    db.add(ConfirmacaoCadastro(cliente=cliente, trechos=None if cliente_novo else ",".join(str(i.id) for i in novos)))
    return True



# ---------------------------------------------------------------- página do cliente (link com as promoções dele)


def link_pagina(site: str, cliente: Cliente) -> str:
    """Endereço da página do cliente ("" sem o endereço do site ou sem código)."""
    if not site or not cliente.token_pagina:
        return ""
    return f"{site.rstrip('/')}{CAMINHO_PAGINA}{cliente.token_pagina}"


def link_nas_mensagens(db: Session) -> bool:
    """A agência quer o link da página no fim das mensagens? (ligado, a não ser que desligue em Configurações)"""
    return ler_estado(db, CHAVE_LINK_NAS_MENSAGENS) != "0"


def whatsapp_agencia(db: Session) -> str:
    """Número de WhatsApp da agência (só dígitos, com DDI) para o botão "Quero esta promoção"; "" se não preenchido."""
    return ler_estado(db, CHAVE_WHATSAPP_AGENCIA) or ""


def visivel_na_pagina(m: Match) -> bool:
    """O envio entra na página do cliente? Só promoção ativa e conferida, que não foi pulada para ele, de um trecho
    que o cliente ainda acompanha (trecho pausado ou excluído tira as promoções dele da página)."""
    if m.status not in ("nova", "enviada") or m.promocao.status != "ativa" or m.promocao.precisa_revisao:
        return False
    if m.interesse is not None and m.interesse.ativo:
        return True
    # o trecho do envio foi pausado ou excluído: vale se outro trecho ativo do cliente combina com a promoção
    return any(i.ativo and interesse_compativel(i, m.promocao) for i in m.cliente.interesses)


def promocoes_da_pagina(db: Session, cliente: Cliente) -> list[Promocao]:
    """O que a página do cliente mostra: as promoções vigentes que combinam com ele (da mais nova para a mais
    antiga), menos as descartadas, as puladas para ele e as que ainda precisam de revisão."""
    limite = datetime.now() - timedelta(days=settings.promo_validade_dias)
    matches = db.scalars(
        select(Match).join(Promocao)
        .where(Match.cliente_id == cliente.id, Match.status.in_(("nova", "enviada")),
               Promocao.status == "ativa", Promocao.coletada_em >= limite)
        .options(selectinload(Match.promocao), selectinload(Match.interesse))
        .order_by(Promocao.coletada_em.desc(), Promocao.id.desc())
    ).all()
    return [m.promocao for m in matches if visivel_na_pagina(m)]


def _ordem_de_preco(p: Promocao) -> tuple:
    reais = preco_reais(p)
    return (reais is None, reais or 0, p.milhas or 0)


def grupos_da_pagina(promos: list[Promocao]) -> list[dict]:
    """Promoções agrupadas por trecho (cidade de origem e de destino), a mais barata primeiro em cada um;
    o trecho com a promoção mais recente vem antes (as promoções chegam da mais nova para a mais antiga)."""
    grupos: dict[tuple, dict] = {}
    for p in promos:
        if p.passagem:
            chave = ("passagem", aeroportos.grupo(p.origem), aeroportos.grupo(p.destino))
            nome = f"{aeroportos.nome_curto(chave[1])} → {aeroportos.nome_curto(chave[2])}"
            emoji = "✈️"
        else:  # hotel, pacote...: um grupo por tipo e lugar
            lugar = aeroportos.nome_curto(p.destino) if p.destino else (p.lugar or "")
            chave = (p.tipo, lugar.lower(), None)
            nome = f"{p.nome_tipo}{' · ' + lugar if lugar else ''}"
            emoji = EMOJI_TIPO.get(p.tipo, "🎟️")
        g = grupos.setdefault(chave, {"nome": nome, "emoji": emoji, "tipo": chave[0], "promocoes": [],
                                      "origem": aeroportos.nome_curto(chave[1]) if p.passagem else "",
                                      "destino": aeroportos.nome_curto(chave[2]) if p.passagem else ""})
        g["promocoes"].append(p)
    for g in grupos.values():
        g["promocoes"].sort(key=_ordem_de_preco)
    return list(grupos.values())


def resumo_da_pagina(promos: list[Promocao]) -> str:
    """Uma linha por trecho: '✈️ *Fortaleza → Rio de Janeiro*: a partir de R$ 389 (3 promoções)'."""
    linhas = []
    for g in grupos_da_pagina(promos):
        qtd, valor = len(g["promocoes"]), formatar_valor(g["promocoes"][0])
        linha = f"{g['emoji']} *{g['nome']}*"
        if valor != "consulte":
            linha += f": {'a partir de ' if qtd > 1 else ''}{valor}"
        if qtd > 1:
            linha += f" ({qtd} promoções)"
        linhas.append(linha)
    return "\n".join(linhas)


def template_pagina(db: Session) -> TemplateMensagem:
    t = db.scalar(select(TemplateMensagem).where(TemplateMensagem.nome == NOME_TEMPLATE_PAGINA,
                                                 TemplateMensagem.padrao.is_not(True)))
    if t is None:
        t = TemplateMensagem(nome=NOME_TEMPLATE_PAGINA, texto=TEMPLATE_PAGINA, padrao=False)
        db.add(t)
        db.commit()
    return t


def montar_mensagem_pagina(template: str, cliente: Cliente, promos: list[Promocao], pagina: str) -> str:
    """Mensagem com o link da página do cliente e o resumo das promoções que estão nela."""
    qtd = len(promos)
    valores = _Seguro(
        nome=nome_bonito(cliente.nome),
        nome_sobrenome=nome_sobrenome(cliente.nome),
        primeiro_nome=nome_sobrenome(cliente.nome).split(" ")[0],
        agencia=settings.agencia_nome,
        quantidade=f"{qtd} promoç{'ões' if qtd != 1 else 'ão'}",
        resumo=resumo_da_pagina(promos),
        pagina=pagina,
        pagina_linha="\n👉 " + pagina + "\n" if pagina else "",
    )
    return _formatar(template, valores, pagina, "👉 {url}")

