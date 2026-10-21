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
