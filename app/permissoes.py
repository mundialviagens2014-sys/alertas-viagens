"""Níveis de acesso do MVO.

Cada usuário tem um papel; cada papel libera um conjunto de permissões.
Na atualização, o primeiro usuário (o administrador original) vira Administrador e os outros que já
existiam viram Atendente (só alertas); o administrador ajusta em Equipe e acessos.
"""

PERMISSOES: dict[str, str] = {
    "alertas": "Alertas: fila de envio, clientes, promoções e histórico",
    "milhas_ver": "Milhas: ver colaboradores, contas, saldos, emissões e disponibilidade",
    "milhas_operar": "Milhas: lançar compras, emissões, pedidos e editar contas",
    "custos": "Ver custos, valores pagos, preços de compra e lucro",
    "senhas": "Ver login e senha das contas dos colaboradores (fica registrado)",
    "configurar": "Configurações: programas e preços, grupos do Telegram, formulário, mensagem",
    "usuarios": "Gerenciar usuários e ver o registro de acessos",
}

PAPEIS: dict[str, dict] = {
    "admin": {
        "nome": "Administrador",
        "descricao": "Acesso total, inclusive usuários e registro de acessos.",
        "permissoes": set(PERMISSOES),
    },
    "gestor": {
        "nome": "Gestor",
        "descricao": "Vê e opera tudo (custos, senhas, configurações), menos usuários.",
        "permissoes": set(PERMISSOES) - {"usuarios"},
    },
    "emissor": {
        "nome": "Emissor",
        "descricao": "Alertas, disponibilidade e emissões; vê os logins das contas, mas não custos nem lucro.",
        "permissoes": {"alertas", "milhas_ver", "milhas_operar", "senhas"},
    },
    "atendente": {
        "nome": "Atendente",
        "descricao": "Somente alertas: fila de envio, clientes e promoções.",
        "permissoes": {"alertas"},
    },
}

PAPEL_SEGURO = "atendente"  # papel desconhecido ou vazio: o mínimo de acesso


def papel_de(usuario) -> str:
    papel = getattr(usuario, "papel", None)
    return papel if papel in PAPEIS else PAPEL_SEGURO


def nome_papel(usuario_ou_papel) -> str:
    papel = usuario_ou_papel if isinstance(usuario_ou_papel, str) else papel_de(usuario_ou_papel)
    return PAPEIS.get(papel, PAPEIS[PAPEL_SEGURO])["nome"]


def permissoes_de(usuario) -> set[str]:
    return PAPEIS[papel_de(usuario)]["permissoes"]


def pode(usuario, *permissoes: str) -> bool:
    """Tem todas as permissões pedidas."""
    if usuario is None:
        return False
    dono = permissoes_de(usuario)
    return all(p in dono for p in permissoes)


def pode_algum(usuario, *permissoes: str) -> bool:
    """Tem pelo menos uma das permissões pedidas."""
    if usuario is None:
        return False
    dono = permissoes_de(usuario)
    return any(p in dono for p in permissoes)
