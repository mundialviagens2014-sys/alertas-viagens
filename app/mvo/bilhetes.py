"""Bilhetes: consulta da reserva no site da companhia (pelo robô de reservas) e os dados do bilhete.

Como funciona:
1. No MVO alguém pede: localizador + companhia (LA, G3, AD) + sobrenome + origem. Vira uma ConsultaReserva "pendente".
2. O robô de reservas (pasta robo/, roda num computador da agência) pergunta ao MVO se há pedido, abre o site da
   companhia no Chrome daquele computador, lê a reserva e devolve os dados neste formato (RESERVA):

   {"situacao": "confirmada",
    "trechos": [{"tipo": "ida", "voos": [{"numero": "LA3456", "origem": "FOR", "destino": "GRU",
                 "partida": "2026-12-10T05:30", "chegada": "2026-12-10T08:55",
                 "operado_por": "LATAM", "cabine": "Economy"}]}],
    "passageiros": [{"nome": "STEFANY COSTA", "bilhete": "9572...", "item_pessoal": 1, "bagagem_mao": 1,
                     "bagagem_despachada": 0}]}

3. O MVO guarda, liga com a emissão do mesmo localizador (se houver) e gera o PDF do bilhete.

Se o site pedir captcha ou outra verificação, o robô NÃO tenta passar: devolve "ajuda" e alguém consulta na mão.
O robô entra com um token próprio (Configurações → Robô de reservas); o MVO guarda só o hash dele.
"""
import hashlib
import json
import logging
import re
import secrets
import threading
from datetime import datetime, timedelta

import httpx

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models_mvo import COMPANHIAS_BILHETE, ConsultaReserva, Emissao
from ..servicos import gravar_estado, ler_estado

# páginas oficiais de "Minhas viagens" (o cliente confere a reserva por elas; vão no bilhete)
PAGINA_RESERVA = {
    "LA": "https://www.latamairlines.com/br/pt/minhas-viagens",
    "G3": "https://b2c.voegol.com.br/minhas-viagens/encontrar-viagem",
    "AD": "https://www.voeazul.com.br/br/pt/home/minhas-viagens",
}
# códigos que as pessoas digitam para cada companhia
APELIDOS = {"LA": "LA", "JJ": "LA", "LATAM": "LA", "TAM": "LA", "G3": "G3", "GOL": "G3", "SMILES": "G3",
            "AD": "AD", "AZUL": "AD", "TUDOAZUL": "AD"}

TEMPO_LENDO = timedelta(minutes=5)     # robô pegou e sumiu: volta para a fila
ROBO_ONLINE = timedelta(minutes=2)     # o robô pergunta a cada poucos segundos; sem notícia há 2 min = desligado
MAX_TENTATIVAS = 3

CHAVE_TOKEN, CHAVE_VISTO, CHAVE_MAQUINA, CHAVE_VERSAO = ("robo:token_hash", "robo:visto_em", "robo:maquina",
                                                        "robo:versao")


log = logging.getLogger(__name__)
NUVEM = "GitHub Actions"              # nome que o robô da nuvem manda no cabeçalho X-Robo-Maquina
ROBO_NUVEM_ATIVO = timedelta(minutes=20)  # a nuvem passa a cada 10 min: sem notícia há 20 min = parado


class PedidoInvalido(ValueError):
    pass


# ---------------------------------------------------------------- pedido


def normalizar_pedido(localizador: str, companhia: str, sobrenome: str, origem: str) -> dict:
    loc = re.sub(r"[^A-Z0-9]", "", (localizador or "").upper())
    if not 5 <= len(loc) <= 8:
        raise PedidoInvalido("O localizador tem 6 letras/números (ex.: DZUBSZ).")
    cia = APELIDOS.get(re.sub(r"[^A-Z0-9]", "", (companhia or "").upper()))
    if not cia:
        raise PedidoInvalido("Escolha a companhia: LATAM (LA), GOL (G3) ou Azul (AD).")
    nome = re.sub(r"\s+", " ", (sobrenome or "").strip())[:80]
    if len(nome) < 2:
        raise PedidoInvalido("Digite o sobrenome do passageiro, como está na passagem.")
    org = re.sub(r"[^A-Z]", "", (origem or "").upper())
    if org and len(org) != 3:
        raise PedidoInvalido("A origem é o código do aeroporto, com 3 letras (ex.: FOR).")
    return {"localizador": loc, "companhia": cia, "sobrenome": nome, "origem": org or None}


def emissao_do_localizador(db: Session, localizador: str) -> Emissao | None:
    return db.scalar(select(Emissao).where(func.upper(Emissao.localizador) == localizador.upper(),
                                           Emissao.status != "cancelada").order_by(Emissao.id.desc()).limit(1))


def pedir(db: Session, localizador: str, companhia: str, sobrenome: str, origem: str, usuario) -> ConsultaReserva:
    p = normalizar_pedido(localizador, companhia, sobrenome, origem)
    # o mesmo pedido ainda na fila: não duplica
    aberta = db.scalar(select(ConsultaReserva).where(
        ConsultaReserva.localizador == p["localizador"], ConsultaReserva.companhia == p["companhia"],
        ConsultaReserva.status.in_(("pendente", "lendo"))).limit(1))
    if aberta:
        return aberta
    emissao = emissao_do_localizador(db, p["localizador"])
    c = ConsultaReserva(**p, emissao_id=emissao.id if emissao else None,
                        pedido_por=getattr(usuario, "nome", None), pedido_por_id=getattr(usuario, "id", None))
    db.add(c)
    db.commit()
    chamar_robo_nuvem()
    return c


def chamar_robo_nuvem() -> None:
    """Acorda o robô no GitHub Actions na hora (se o token estiver no Render). Em segundo plano: não segura a tela."""
    if not settings.github_token_robo or not settings.github_repo:
        return

    def chamar():
        try:
            r = httpx.post(f"https://api.github.com/repos/{settings.github_repo}/dispatches", timeout=15,
                           headers={"Authorization": f"Bearer {settings.github_token_robo}",
                                    "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                           json={"event_type": "bilhete"})
            if r.status_code >= 300:
                log.warning("GitHub não chamou o robô: %s %s", r.status_code, r.text[:200])
        except httpx.HTTPError as e:
            log.warning("GitHub não chamou o robô: %s", e)

    threading.Thread(target=chamar, daemon=True).start()


def pendentes(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(ConsultaReserva).where(
        ConsultaReserva.status.in_(("pendente", "lendo")))) or 0


def companhia_da_emissao(e: Emissao) -> str | None:
    """LA/G3/AD a partir da companhia ou do programa da emissão (LATAM Pass → LA, Smiles → G3, Azul Fidelidade → AD)."""
    texto = f"{e.companhia or ''} {e.programa or ''}".upper()
    for chave, cod in (("LATAM", "LA"), ("TAM", "LA"), ("SMILES", "G3"), ("GOL", "G3"), ("AZUL", "AD")):
        if chave in texto:
            return cod
    return None


def link_da_emissao(e: Emissao) -> str | None:
    """Atalho "Gerar bilhete" na emissão: abre Bilhetes com localizador, companhia, sobrenome e origem preenchidos."""
    if not e.localizador or e.status == "cancelada":
        return None
    from urllib.parse import urlencode
    nome = (e.passageiros[0].nome if e.passageiros else e.cliente_nome) or ""
    sobrenome = nome.strip().split()[-1] if nome.strip() else ""
    return "/bilhetes?" + urlencode({"localizador": e.localizador, "companhia": companhia_da_emissao(e) or "",
                                     "sobrenome": sobrenome, "origem": e.origem or ""})


def repetir(db: Session, c: ConsultaReserva) -> None:
    c.status, c.mensagem, c.iniciado_em, c.concluido_em, c.tentativas = "pendente", None, None, None, 0
    db.commit()
    chamar_robo_nuvem()


# ---------------------------------------------------------------- robô: token e presença


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def gerar_token(db: Session) -> str:
    """Token novo para o robô (o anterior para de valer). Aparece uma vez na tela; o banco guarda só o hash."""
    token = "mvo_robo_" + secrets.token_urlsafe(32)
    gravar_estado(db, CHAVE_TOKEN, _hash(token))
    db.commit()
    return token


def token_valido(db: Session, token: str | None) -> bool:
    guardado = ler_estado(db, CHAVE_TOKEN)
    return bool(token and guardado and secrets.compare_digest(_hash(token), guardado))


def tem_token(db: Session) -> bool:
    return bool(ler_estado(db, CHAVE_TOKEN))


def desligar_token(db: Session) -> None:
    gravar_estado(db, CHAVE_TOKEN, "")
    db.commit()


def marcar_visto(db: Session, maquina: str | None, versao: str | None) -> None:
    gravar_estado(db, CHAVE_VISTO, datetime.now().isoformat(timespec="seconds"))
    if maquina:
        gravar_estado(db, CHAVE_MAQUINA, maquina[:80])
    if versao:
        gravar_estado(db, CHAVE_VERSAO, versao[:20])


def situacao_robo(db: Session) -> dict:
    try:
        visto = datetime.fromisoformat(ler_estado(db, CHAVE_VISTO) or "")
    except ValueError:
        visto = None
    maquina = ler_estado(db, CHAVE_MAQUINA)
    na_nuvem = maquina == NUVEM
    return {"configurado": tem_token(db), "visto_em": visto, "nuvem": na_nuvem,
            "chamada_na_hora": bool(settings.github_token_robo),
            "online": bool(visto and datetime.now() - visto < (ROBO_NUVEM_ATIVO if na_nuvem else ROBO_ONLINE)),
            "maquina": maquina, "versao": ler_estado(db, CHAVE_VERSAO)}


# ---------------------------------------------------------------- robô: pegar pedido e devolver resultado


def proximo(db: Session) -> ConsultaReserva | None:
    """O pedido mais antigo na fila (ou um que o robô pegou e não devolveu há mais de 5 minutos)."""
    limite = datetime.now() - TEMPO_LENDO
    c = db.scalar(select(ConsultaReserva).where(or_(
        ConsultaReserva.status == "pendente",
        (ConsultaReserva.status == "lendo") & (ConsultaReserva.iniciado_em < limite),
    )).order_by(ConsultaReserva.criado_em).limit(1).with_for_update(skip_locked=True))
    if not c:
        return None
    if c.tentativas >= MAX_TENTATIVAS:
        c.status, c.mensagem, c.concluido_em = "erro", "O robô tentou 3 vezes e não conseguiu.", datetime.now()
        db.commit()
        return proximo(db)
    c.status, c.iniciado_em, c.tentativas = "lendo", datetime.now(), c.tentativas + 1
    db.commit()
    return c


def pedido_para_robo(c: ConsultaReserva) -> dict:
    return {"id": c.id, "localizador": c.localizador, "companhia": c.companhia, "sobrenome": c.sobrenome,
            "origem": c.origem, "pagina": PAGINA_RESERVA.get(c.companhia)}


def registrar_resultado(db: Session, c: ConsultaReserva, corpo: dict) -> None:
    if c.status == "pronta" and (reserva(c) or {}).get("fonte") == "print":
        return   # a equipe já gerou pelo print: o robô (barrado ou atrasado) não sobrescreve
    situacao = corpo.get("status")
    if situacao == "pronta":
        dados = limpar_reserva(corpo.get("dados") or {})
        if not dados["trechos"]:
            raise PedidoInvalido("A reserva veio sem voos.")
        c.dados, c.mensagem, c.status = json.dumps(dados, ensure_ascii=False), None, "pronta"
    elif situacao in ("nao_encontrada", "ajuda", "erro"):
        c.status, c.mensagem = situacao, str(corpo.get("mensagem") or "")[:500] or None
    else:
        raise PedidoInvalido("status deve ser pronta, nao_encontrada, ajuda ou erro.")
    c.concluido_em = datetime.now()
    if c.emissao_id is None:
        e = emissao_do_localizador(db, c.localizador)
        c.emissao_id = e.id if e else None
    db.commit()


# ---------------------------------------------------------------- dados da reserva (o que o robô devolve)


def _texto(v, n: int) -> str | None:
    v = re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""
    return v[:n] or None


def _iata(v) -> str | None:
    v = re.sub(r"[^A-Z]", "", str(v or "").upper())
    return v if len(v) == 3 else None


def _quando(v) -> str | None:
    """Data e hora no formato 2026-12-10T05:30 (sem fuso: a hora local do aeroporto, como no site)."""
    if not v:
        return None
    texto = str(v).strip()
    try:
        return datetime.fromisoformat(texto.replace("Z", "")[:19]).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _inteiro(v) -> int | None:
    try:
        return max(0, min(int(v), 9))
    except (TypeError, ValueError):
        return None


def limpar_reserva(bruto: dict) -> dict:
    """Confere e limpa o que o robô mandou: só os campos esperados, tamanhos limitados, datas e códigos válidos."""
    trechos = []
    for t in (bruto.get("trechos") or [])[:6]:
        voos = []
        for v in (t.get("voos") or [])[:6]:
            voo = {"numero": _texto(v.get("numero"), 10), "origem": _iata(v.get("origem")),
                   "destino": _iata(v.get("destino")), "partida": _quando(v.get("partida")),
                   "chegada": _quando(v.get("chegada")), "operado_por": _texto(v.get("operado_por"), 60),
                   "cabine": _texto(v.get("cabine"), 30)}
            if voo["origem"] and voo["destino"] and voo["partida"]:
                voos.append(voo)
        if voos:
            tipo = t.get("tipo") if t.get("tipo") in ("ida", "volta") else ("ida" if not trechos else "volta")
            trechos.append({"tipo": tipo, "voos": voos})
    passageiros = []
    for p in (bruto.get("passageiros") or [])[:9]:
        nome = _texto(p.get("nome"), 120)
        if nome:
            passageiros.append({"nome": nome.upper(), "bilhete": _texto(p.get("bilhete"), 20),
                                "item_pessoal": _inteiro(p.get("item_pessoal")),
                                "bagagem_mao": _inteiro(p.get("bagagem_mao")),
                                "bagagem_despachada": _inteiro(p.get("bagagem_despachada"))})
    return {"situacao": _texto(bruto.get("situacao"), 30) or "confirmada", "trechos": trechos,
            "passageiros": passageiros}


def reserva(c: ConsultaReserva) -> dict | None:
    try:
        return json.loads(c.dados) if c.dados else None
    except ValueError:
        return None


def nome_companhia(codigo: str) -> str:
    return COMPANHIAS_BILHETE.get(codigo, codigo)


# ---------------------------------------------------------------- reserva lida de um print (quando o robô é barrado)

ESQUEMA_PRINT = {
    "type": "OBJECT",
    "properties": {
        "e_reserva": {"type": "BOOLEAN", "description": "false se o arquivo não mostra uma reserva de voo"},
        "localizador": {"type": "STRING"},
        "trechos": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "tipo": {"type": "STRING", "enum": ["ida", "volta"]},
            "voos": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "numero": {"type": "STRING", "description": "código da cia + número, ex.: LA3456"},
                "origem": {"type": "STRING", "description": "código IATA de 3 letras"},
                "destino": {"type": "STRING", "description": "código IATA de 3 letras"},
                "partida": {"type": "STRING", "description": "AAAA-MM-DDTHH:MM (hora local do aeroporto)"},
                "chegada": {"type": "STRING", "description": "AAAA-MM-DDTHH:MM (hora local do aeroporto)"},
                "operado_por": {"type": "STRING"},
                "cabine": {"type": "STRING", "description": "ex.: Economy, Premium Economy, Light, Plus"}}}}}}},
        "passageiros": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "nome": {"type": "STRING", "description": "nome completo; 'SILVA/MARIA' vira 'MARIA SILVA'"},
            "bilhete": {"type": "STRING", "description": "número do bilhete eletrônico (e-ticket)"},
            "item_pessoal": {"type": "INTEGER"},
            "bagagem_mao": {"type": "INTEGER"},
            "bagagem_despachada": {"type": "INTEGER"}}}},
    },
    "required": ["e_reserva", "trechos"],
}
INSTRUCOES_PRINT = (
    "O arquivo é o print (ou PDF/e-mail) de uma reserva de passagem aérea no site ou app da companhia (LATAM, GOL, "
    "Azul...). Extraia os dados exatamente como aparecem; não invente nada. Regras:\n"
    "- cada voo é uma decolagem: uma conexão são dois voos no mesmo trecho; o trecho de retorno tem tipo 'volta';\n"
    "- partida e chegada em AAAA-MM-DDTHH:MM (hoje é {hoje}; se o ano não aparecer, use o próximo em que a data cai);\n"
    "- bagagens: quantidade de cada tipo por passageiro, só se aparecer (senão deixe vazio);\n"
    "- e_reserva=false se o arquivo não for uma reserva de voo.\n"
)
TIPOS_PRINT = {"image/jpeg", "image/png", "image/webp", "application/pdf"}


def ler_print(arquivo: bytes | None = None, mime: str = "", texto: str | None = None, cliente_http=None) -> dict | None:
    """A IA lê o print/PDF/texto da reserva e devolve no formato que o robô mandaria (None se não for reserva)."""
    import base64
    from datetime import date

    from .. import ia

    partes = [{"text": INSTRUCOES_PRINT.format(hoje=date.today().strftime("%d/%m/%Y"))}]
    if texto:
        partes.append({"text": "Texto da reserva:\n" + texto[:30000]})
    if arquivo:
        partes.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(arquivo).decode()}})
    lido = ia.gerar_json(partes, ESQUEMA_PRINT, cliente_http=cliente_http)
    if not isinstance(lido, dict) or lido.get("e_reserva") is False:
        return None
    for p in lido.get("passageiros") or []:
        nome = str(p.get("nome") or "")
        if "/" in nome:   # SOBRENOME/NOME, como as companhias escrevem
            sobrenome, primeiro = nome.split("/", 1)
            p["nome"] = f"{primeiro.strip()} {sobrenome.strip()}"
    return lido


def aplicar_print(db: Session, c: ConsultaReserva, lido: dict) -> None:
    """Grava a reserva lida do print como se o robô tivesse lido: a tela, o PDF e o envio funcionam igual."""
    dados = limpar_reserva(lido)
    if not dados["trechos"]:
        raise PedidoInvalido("Não achei voos com data e horário no arquivo. Confira se é a reserva.")
    dados["fonte"] = "print"
    c.dados, c.mensagem, c.status, c.concluido_em = json.dumps(dados, ensure_ascii=False), None, "pronta", datetime.now()
    if c.emissao_id is None:
        e = emissao_do_localizador(db, c.localizador)
        c.emissao_id = e.id if e else None
    db.commit()
