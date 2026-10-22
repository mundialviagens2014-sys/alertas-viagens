"""Programas de milhas: reconhecimento no texto da promoção.

O valor de cada milha (R$) é definido pela agência em Configurações e fica no banco
(tabela programas_milhas). Aqui ficam só os nomes e as formas como aparecem nos alertas.
"""
import re

from .aeroportos import sem_acento

# nome oficial -> formas como aparece nas mensagens (sem acento, minúsculas)
PROGRAMAS_PADRAO: dict[str, tuple[str, ...]] = {
    "Smiles": ("smiles",),
    "Azul Fidelidade": ("azul fidelidade", "tudoazul", "tudo azul"),
    "LATAM Pass": ("latam pass", "latampass", "latam fidelidade"),
    "TAP Miles&Go": ("miles&go", "miles & go", "milesandgo", "miles and go", "tap miles"),
    "Iberia Plus": ("iberia plus", "iberia avios"),
    "AAdvantage": ("aadvantage", "american airlines aadvantage"),
    "Flying Blue": ("flying blue",),
    "Qatar Privilege Club": ("privilege club", "qatar avios"),
    "British Airways Club": ("british airways club", "executive club", "ba avios"),
    "Aeroplan": ("aeroplan",),
    "LifeMiles": ("lifemiles", "life miles"),
    "ConnectMiles": ("connectmiles", "connect miles"),
    "Emirates Skywards": ("skywards",),
}

# Programas de pontos (bancos/cartões) usados nas contas dos colaboradores. Ficam fora do reconhecimento
# dos alertas: "Livelo" num alerta costuma ser bônus de transferência, não o programa da passagem.
PROGRAMAS_PONTOS: tuple[str, ...] = ("Livelo", "Esfera")

# Como os programas aparecem nas planilhas do Manager
NOMES_MANAGER: dict[str, str] = {
    "SMILES": "Smiles",
    "LATAM_PASS": "LATAM Pass",
    "LATAM": "LATAM Pass",
    "TUDO_AZUL": "Azul Fidelidade",
    "AZUL": "Azul Fidelidade",
    "AZUL_FIDELIDADE": "Azul Fidelidade",
    "AMERICAN_AIRLINES": "AAdvantage",
    "AADVANTAGE": "AAdvantage",
    "TAP": "TAP Miles&Go",
    "TAP_MILES_GO": "TAP Miles&Go",
    "LIVELO": "Livelo",
    "ESFERA": "Esfera",
    "IBERIA": "Iberia Plus",
    "FLYING_BLUE": "Flying Blue",
    "AVIOS": "Iberia Plus",
}

# Quando o alerta fala em milhas mas não cita o programa, usa o da companhia
PROGRAMA_DA_COMPANHIA = {
    "Gol": "Smiles",
    "Azul": "Azul Fidelidade",
    "LATAM": "LATAM Pass",
    "TAP": "TAP Miles&Go",
    "Iberia": "Iberia Plus",
    "American Airlines": "AAdvantage",
    "Air France": "Flying Blue",
    "KLM": "Flying Blue",
    "Qatar": "Qatar Privilege Club",
    "British Airways": "British Airways Club",
    "Avianca": "LifeMiles",
    "Copa": "ConnectMiles",
    "Copa Airlines": "ConnectMiles",
    "Emirates": "Emirates Skywards",
}


def _achar(texto_normalizado: str, nomes: dict[str, tuple[str, ...]]) -> str | None:
    achados = []
    for nome, formas in nomes.items():
        for forma in formas:
            m = re.search(rf"(?<![a-z0-9]){re.escape(forma)}(?![a-z0-9])", texto_normalizado)
            if m:
                achados.append((m.start(), nome))
    return min(achados)[1] if achados else None


def detectar_programa(texto: str | None, companhia: str | None = None,
                      extras: list[str] | None = None) -> str | None:
    """Programa citado no texto; senão, o programa da companhia aérea.

    `extras` são programas cadastrados pela agência que não estão na lista padrão."""
    norm = sem_acento(texto or "")
    nomes = dict(PROGRAMAS_PADRAO)
    for nome in extras or []:
        nomes.setdefault(nome, (sem_acento(nome),))
    return _achar(norm, nomes) or PROGRAMA_DA_COMPANHIA.get(companhia or "")


def nome_programa(texto: str | None, conhecidos: list[str] | None = None) -> str | None:
    """Nome oficial do programa a partir de como foi escrito (ex.: "TUDO_AZUL", "latam pass", "Smiles")."""
    if not texto or not texto.strip():
        return None
    bruto = texto.strip()
    chave = re.sub(r"[^A-Z0-9]+", "_", sem_acento(bruto).upper()).strip("_")
    if chave in NOMES_MANAGER:
        return NOMES_MANAGER[chave]
    todos = list(PROGRAMAS_PADRAO) + list(PROGRAMAS_PONTOS) + list(conhecidos or [])
    for nome in todos:
        if sem_acento(nome).lower() == sem_acento(bruto).lower():
            return nome
    achado = _achar(sem_acento(bruto).lower(), dict(PROGRAMAS_PADRAO))
    return achado or bruto
