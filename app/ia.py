"""Leitura de promoções por IA (Gemini): imagens de ofertas e textos que a leitura por regras não entende.

Os grupos mandam quase tudo como imagem (passagem, hotel, cruzeiro, pacote) sem texto junto. A imagem vai para o
Gemini, que devolve os dados organizados em JSON (tipo, trecho ou lugar, preço, datas, companhia, resumo).

- Chave: GEMINI_API_KEY (plano gratuito do Google AI Studio). Sem a chave, as imagens continuam ignoradas.
- Cota: o plano gratuito tem um número de leituras por dia; GEMINI_MAX_POR_DIA limita o que o sistema gasta.
  Passando do limite (ou se o Google responder "cota esgotada"), a leitura fica pendente e é feita depois.
- O que vai para a IA é só a promoção (imagem ou texto do grupo); nunca dados de clientes.
"""
import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import aeroportos
from .config import settings
from .models import TIPOS_PROMOCAO, EstadoColetor
from .parser import PromocaoExtraida, detectar_tipo_viagem

log = logging.getLogger(__name__)
URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
CHAVE_CONTAGEM = "ia:leituras:{dia}"


class IANaoConfigurada(RuntimeError):
    pass


class CotaExcedida(RuntimeError):
    """O Google recusou por limite de uso (429) ou o limite diário do sistema foi atingido."""


@dataclass
class Leitura:
    eh_promocao: bool = False
    tipo: str = "passagem"
    titulo: str | None = None
    origem: str | None = None   # cidade de saída (texto)
    destino: str | None = None  # cidade de chegada / do hotel (texto)
    preco: float | None = None
    milhas: int | None = None
    programa: str | None = None
    data_inicio: date | None = None
    data_fim: date | None = None
    datas_texto: str | None = None
    companhia: str | None = None
    link: str | None = None
    resumo: str | None = None
    confianca: float = 0.0
    tipo_viagem: str | None = None


ESQUEMA = {
    "type": "OBJECT",
    "properties": {
        "eh_promocao": {"type": "BOOLEAN", "description": "true se o conteúdo é uma oferta de viagem com valor"},
        "tipo": {"type": "STRING", "enum": list(TIPOS_PROMOCAO)},
        "titulo": {"type": "STRING", "description": "nome curto da oferta (hotel, pacote, cruzeiro); vazio para passagem"},
        "origem": {"type": "STRING", "description": "cidade de saída, se houver (ex.: Fortaleza)"},
        "destino": {"type": "STRING", "description": "cidade de destino ou do hotel (ex.: Lisboa, Porto de Galinhas)"},
        "preco_reais": {"type": "NUMBER", "description": "menor preço em reais por pessoa; 0 se não houver"},
        "milhas": {"type": "INTEGER", "description": "quantidade de milhas/pontos, se a oferta for em milhas; 0 se não"},
        "programa_milhas": {"type": "STRING", "description": "programa de milhas (Smiles, LATAM Pass, Azul...), se houver"},
        "data_inicio": {"type": "STRING", "description": "primeira data da viagem no formato AAAA-MM-DD, se der para saber"},
        "data_fim": {"type": "STRING", "description": "última data da viagem no formato AAAA-MM-DD, se der para saber"},
        "datas_texto": {"type": "STRING", "description": "as datas como estão escritas (ex.: 'março a junho/2027')"},
        "companhia": {"type": "STRING", "description": "companhia aérea, rede hoteleira ou armadora, se houver"},
        "ida_e_volta": {"type": "STRING", "enum": ["ida_volta", "ida", "nao_informado"]},
        "link": {"type": "STRING", "description": "link da oferta, se aparecer"},
        "resumo": {"type": "STRING", "description": "uma frase com o que está incluído e as condições (parcelamento, taxas, diárias)"},
        "confianca": {"type": "NUMBER", "description": "de 0 a 1: quanto você tem certeza dos dados lidos"},
    },
    "required": ["eh_promocao", "tipo", "confianca"],
}

INSTRUCOES = (
    "Você lê ofertas de viagem publicadas em grupos de WhatsApp e Telegram no Brasil (passagens aéreas, hotéis, "
    "pacotes, cruzeiros). Extraia os dados da oferta que está na imagem ou no texto e responda só com o JSON pedido.\n"
    "Regras:\n"
    "- Hoje é {hoje}. Datas sem ano são as próximas a partir de hoje.\n"
    "- Preço: o menor valor por pessoa em reais. Se a oferta for em milhas, informe as milhas e o programa.\n"
    "- Passagem aérea: tipo 'passagem', com origem e destino (cidades). Hotel/resort: 'hotel'. Pacote (aéreo + hotel "
    "ou roteiro): 'pacote'. Cruzeiro: 'cruzeiro'. Outra coisa com valor: 'outro'.\n"
    "- Se não for uma oferta de viagem (propaganda genérica, bom dia, perguntas), eh_promocao = false.\n"
    "- Cidades em português, sem o nome do aeroporto. Não invente dados: campo desconhecido fica vazio."
)


def configurada() -> bool:
    return bool(settings.gemini_api_key)


# ---------------------------------------------------------------- cota do dia


def leituras_hoje(db: Session) -> int:
    item = db.get(EstadoColetor, CHAVE_CONTAGEM.format(dia=date.today().isoformat()))
    return int(item.valor) if item else 0


def _contar_leitura(db: Session) -> None:
    chave = CHAVE_CONTAGEM.format(dia=date.today().isoformat())
    item = db.get(EstadoColetor, chave)
    if item:
        item.valor = str(int(item.valor) + 1)
    else:
        db.add(EstadoColetor(chave=chave, valor="1"))
    # contagens de dias anteriores não servem mais
    for antigo in db.scalars(select(EstadoColetor).where(EstadoColetor.chave.like("ia:leituras:%"),
                                                          EstadoColetor.chave != chave)):
        db.delete(antigo)
    db.commit()


def cota_disponivel(db: Session) -> bool:
    return configurada() and leituras_hoje(db) < settings.gemini_max_por_dia


# ---------------------------------------------------------------- chamada ao Gemini


def _data(valor) -> date | None:
    if not valor or not isinstance(valor, str):
        return None
    try:
        return date.fromisoformat(valor.strip()[:10])
    except ValueError:
        return None


def _numero(valor) -> float | None:
    if isinstance(valor, bool) or valor in (None, ""):
        return None
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _texto(valor) -> str | None:
    if not isinstance(valor, str):
        return None
    valor = " ".join(valor.split())
    return valor or None


def interpretar(resposta: dict, hoje: date | None = None) -> Leitura:
    """Transforma a resposta JSON do modelo numa Leitura (tolerante a campos faltando ou errados)."""
    hoje = hoje or date.today()
    tipo = resposta.get("tipo") if resposta.get("tipo") in TIPOS_PROMOCAO else "passagem"
    inicio, fim = _data(resposta.get("data_inicio")), _data(resposta.get("data_fim"))
    if inicio and inicio < hoje:  # ano errado para trás: a IA às vezes usa o ano atual para meses já passados
        inicio = inicio.replace(year=inicio.year + 1)
        fim = fim.replace(year=fim.year + 1) if fim and fim < inicio else fim
    if fim and inicio and fim < inicio:
        fim = inicio
    milhas = _numero(resposta.get("milhas"))
    ida_volta = resposta.get("ida_e_volta")
    return Leitura(
        eh_promocao=bool(resposta.get("eh_promocao")),
        tipo=tipo,
        titulo=_texto(resposta.get("titulo")),
        origem=_texto(resposta.get("origem")),
        destino=_texto(resposta.get("destino")),
        preco=_numero(resposta.get("preco_reais")),
        milhas=int(milhas) if milhas else None,
        programa=_texto(resposta.get("programa_milhas")),
        data_inicio=inicio,
        data_fim=fim or inicio,
        datas_texto=_texto(resposta.get("datas_texto")),
        companhia=_texto(resposta.get("companhia")),
        link=_texto(resposta.get("link")),
        resumo=_texto(resposta.get("resumo")),
        confianca=max(0.0, min(1.0, _numero(resposta.get("confianca")) or 0.0)),
        tipo_viagem=ida_volta if ida_volta in ("ida_volta", "ida") else None,
    )


def gerar_json(partes: list[dict], esquema: dict, cliente_http: httpx.Client | None = None, timeout: int = 90,
               temperatura: float = 0.1):
    """Uma chamada ao Gemini que devolve JSON no formato do esquema (None se a resposta vier estranha).
    Levanta CotaExcedida no limite do plano gratuito e httpx.HTTPError nas demais falhas."""
    if not configurada():
        raise IANaoConfigurada("Preencha GEMINI_API_KEY.")
    corpo = {
        "contents": [{"role": "user", "parts": partes}],
        "generationConfig": {"temperature": temperatura, "response_mime_type": "application/json", "response_schema": esquema},
    }
    proprio = cliente_http is None
    cliente_http = cliente_http or httpx.Client(timeout=timeout)
    try:
        r = cliente_http.post(URL.format(modelo=settings.gemini_modelo), json=corpo,
                              headers={"x-goog-api-key": settings.gemini_api_key})
    finally:
        if proprio:
            cliente_http.close()
    if r.status_code == 429:
        raise CotaExcedida("O Gemini recusou a leitura: limite de uso do plano gratuito atingido.")
    r.raise_for_status()
    try:
        candidatos = r.json().get("candidates") or []
        bruto = "".join(p.get("text", "") for p in candidatos[0]["content"]["parts"])
        return json.loads(bruto)
    except (KeyError, IndexError, ValueError, TypeError) as erro:
        log.warning("Resposta inesperada do Gemini: %s", erro)
        return None


ESQUEMA_INTENCAO = {
    "type": "OBJECT",
    "properties": {"intencao": {"type": "STRING", "enum": ["interesse", "duvida", "recusa", "sair", "outro"]}},
    "required": ["intencao"],
}
INSTRUCOES_INTENCAO = (
    "Você classifica a resposta de um cliente de uma agência de viagens a uma mensagem de promoção enviada pelo "
    "WhatsApp. Responda só com a intenção:\n"
    "- interesse: quer a promoção, pede para reservar/emitir, pede mais detalhes para fechar, demonstra entusiasmo;\n"
    "- duvida: faz perguntas (datas, preço, parcelamento, bagagem, horários), diz que vai pensar ou ver;\n"
    "- recusa: não quer, achou caro, sem interesse agora;\n"
    "- sair: pede para não receber mais mensagens;\n"
    "- outro: saudação, agradecimento, figurinha, assunto sem relação.\n"
    "Resposta do cliente:\n"
)


def classificar_intencao(texto: str, cliente_http: httpx.Client | None = None) -> str | None:
    """Intenção da resposta do cliente (interesse, duvida, recusa, sair, outro) pela IA; None se não deu."""
    dados = gerar_json([{"text": INSTRUCOES_INTENCAO + (texto or "").strip()[:1500]}], ESQUEMA_INTENCAO,
                       cliente_http=cliente_http, timeout=30, temperatura=0.0)
    intencao = (dados or {}).get("intencao") if isinstance(dados, dict) else None
    return intencao if intencao in ("interesse", "duvida", "recusa", "sair", "outro") else None


def ler(texto: str | None = None, imagem: bytes | None = None, mime: str = "image/jpeg",
        cliente_http: httpx.Client | None = None, hoje: date | None = None) -> Leitura:
    """Manda a imagem e/ou o texto para o Gemini e devolve a leitura. Levanta CotaExcedida quando o Google recusa
    por limite de uso, e httpx.HTTPError nas demais falhas."""
    if not configurada():
        raise IANaoConfigurada("Preencha GEMINI_API_KEY para ler imagens de promoções.")
    hoje = hoje or date.today()
    partes: list[dict] = [{"text": INSTRUCOES.format(hoje=hoje.strftime("%d/%m/%Y"))}]
    if imagem:
        import base64

        partes.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(imagem).decode()}})
    if texto and texto.strip():
        partes.append({"text": "Texto da mensagem:\n" + texto.strip()[:4000]})
    if len(partes) == 1:
        return Leitura()
    dados = gerar_json(partes, ESQUEMA, cliente_http=cliente_http)
    if dados is None:
        return Leitura()
    leitura = interpretar(dados if isinstance(dados, dict) else {}, hoje)
    if leitura.tipo_viagem is None and texto:
        leitura.tipo_viagem = detectar_tipo_viagem(texto)
    return leitura


# ---------------------------------------------------------------- da leitura para a promoção


def para_extraida(leitura: Leitura) -> PromocaoExtraida:
    """Converte a leitura em dados de promoção: cidades viram códigos; hotel/pacote guardam título e lugar."""
    cod_origem = aeroportos.resolver(leitura.origem) if leitura.origem else None
    cod_destino = aeroportos.resolver(leitura.destino) if leitura.destino else None
    passagem = leitura.tipo == "passagem"
    if passagem and cod_origem and cod_destino and aeroportos.mesmo_lugar(cod_origem, cod_destino):
        cod_destino = None
    dados = PromocaoExtraida(
        tipo=leitura.tipo,
        origem=cod_origem if passagem else None,
        destino=cod_destino,
        preco=leitura.preco,
        milhas=leitura.milhas,
        programa=leitura.programa,
        data_inicio=leitura.data_inicio,
        data_fim=leitura.data_fim,
        datas_texto=leitura.datas_texto,
        companhia=leitura.companhia,
        link=leitura.link,
        tipo_viagem=leitura.tipo_viagem if passagem else None,
        titulo=None if passagem else (leitura.titulo or _titulo_padrao(leitura)),
        lugar=None if passagem else leitura.destino,
        resumo=leitura.resumo,
        confianca=round(leitura.confianca, 2),
    )
    if passagem and not dados.completa and (leitura.origem or leitura.destino):
        # cidade que o sistema não conhece: a equipe confere (fica com confiança baixa)
        dados.confianca = min(dados.confianca, 0.5)
    return dados


def _titulo_padrao(leitura: Leitura) -> str | None:
    nome = TIPOS_PROMOCAO.get(leitura.tipo, "Promoção")
    if leitura.destino:
        return f"{nome} em {leitura.destino}" if leitura.tipo != "cruzeiro" else f"{nome} pelo {leitura.destino}"
    return leitura.companhia and f"{nome} {leitura.companhia}" or None


def vale_a_pena_ler(texto: str | None) -> bool:
    """Texto sem imagem só vai para a IA se parecer uma oferta (tem valor ou fala de hotel, pacote, cruzeiro...)."""
    if not texto or len(texto.strip()) < 25:
        return False
    norm = texto.lower()
    return bool(re.search(r"r\$|\breais\b|milhas|pontos|hotel|resort|pousada|pacote|cruzeiro|di[aá]rias?|noites|"
                          r"\d{1,3}(\.\d{3})+|\d+,\d{2}", norm))


def registrar_uso(db: Session) -> None:
    """Conta uma leitura feita hoje (para o limite diário)."""
    _contar_leitura(db)


# ---------------------------------------------------------------- leitura do print de uma emissão (Manager)

ESQUEMA_EMISSAO = {
    "type": "OBJECT",
    "properties": {
        "data_pedido": {"type": "STRING", "description": "AAAA-MM-DD"},
        "usuario": {"type": "STRING", "description": "campo Usuário (agência ou pessoa que pediu)"},
        "requisitante": {"type": "STRING"},
        "tipo_voo": {"type": "STRING", "enum": ["ida", "ida_volta", ""]},
        "tipo_emissao": {"type": "STRING"},
        "programa": {"type": "STRING"},
        "companhia": {"type": "STRING"},
        "localizador": {"type": "STRING"},
        "origem": {"type": "STRING", "description": "código IATA de 3 letras da partida da ida"},
        "destino": {"type": "STRING", "description": "código IATA de 3 letras do destino da ida"},
        "data_ida": {"type": "STRING", "description": "AAAA-MM-DD"},
        "data_volta": {"type": "STRING", "description": "AAAA-MM-DD"},
        "pontos": {"type": "NUMBER", "description": "soma dos pontos da ida e da volta"},
        "valor_passagem": {"type": "NUMBER", "description": "soma do Valor Passagem da ida e da volta, em reais"},
        "taxa_embarque": {"type": "NUMBER", "description": "soma das taxas de embarque"},
        "taxa_bagagem": {"type": "NUMBER", "description": "soma das taxas de bagagem"},
        "qtd_passageiros": {"type": "INTEGER"},
        "passageiros": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "nome": {"type": "STRING"}, "cpf": {"type": "STRING"}}}},
        "cliente": {"type": "STRING"},
        "emissor": {"type": "STRING"},
        "metodo_pagamento": {"type": "STRING"},
        "voos": {"type": "STRING", "description": "números dos voos, ex.: IDA G39178 - VOLTA G31559"},
        "observacao": {"type": "STRING"},
    },
}
INSTRUCOES_EMISSAO = (
    "A imagem é o print de um pedido de emissão de passagem aérea com milhas (sistema Manager ou parecido) de uma "
    "agência de viagens no Brasil. Extraia os dados exatamente como aparecem; não invente. Campos ausentes ficam vazios "
    "(texto vazio ou 0). Regras:\n"
    "- datas no formato AAAA-MM-DD (hoje é {hoje});\n"
    "- origem e destino: códigos IATA de 3 letras da IDA (ex.: 'GRU Guarulhos Int.' → GRU; 'FOR Pinto Martins' → FOR);\n"
    "- tipo_voo: ida_volta se houver voo de volta ou estiver escrito IDA E VOLTA; ida se só ida;\n"
    "- pontos, valor_passagem, taxa_embarque e taxa_bagagem: SOME os valores da ida e da volta; valores em reais "
    "como número (R$ 1.234,56 → 1234.56);\n"
    "- programa: nome do programa de milhas (SMILES, LATAM PASS, TUDO AZUL...); companhia: a cia aérea (GOL, LATAM, AZUL...);\n"
    "- localizador: o código da reserva (6 letras/números), se aparecer;\n"
    "- usuario: o que estiver em 'Usuário'; requisitante: 'Nome Requisitante';\n"
    "- voos: os números dos voos de ida e volta; observacao: o texto do campo Observação."
)


def ler_emissao(imagem: bytes, mime: str = "image/jpeg", cliente_http: httpx.Client | None = None,
                hoje: date | None = None) -> dict:
    """Lê o print de um pedido de emissão e devolve os campos (para conferir no formulário antes de registrar)."""
    if not configurada():
        raise IANaoConfigurada("Preencha GEMINI_API_KEY para ler imagens.")
    hoje = hoje or date.today()
    partes = [{"text": INSTRUCOES_EMISSAO.format(hoje=hoje.strftime("%d/%m/%Y"))},
              {"inline_data": {"mime_type": mime, "data": base64.b64encode(imagem).decode()}}]
    return gerar_json(partes, ESQUEMA_EMISSAO, cliente_http=cliente_http) or {}
