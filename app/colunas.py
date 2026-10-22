"""Pedaços de colunas usados em várias planilhas exportadas (documento, telefone, endereço...)."""
from .exportar import Coluna
from .mvo.contas import formatar_cpf
from .servicos import formatar_telefone


def documento(valor) -> str | None:
    return formatar_cpf(valor) if valor else None


def telefone(valor) -> str | None:
    return formatar_telefone(valor) if valor else None


def endereco(prefixo=lambda o: o) -> list[Coluna]:
    """CEP, endereço, número, complemento, bairro, cidade e UF (de o ou de prefixo(o))."""
    def campo(nome):
        return lambda o: getattr(prefixo(o), nome, None) if prefixo(o) is not None else None

    return [Coluna("CEP", campo("cep")), Coluna("Endereço", campo("endereco")), Coluna("Número", campo("numero")),
            Coluna("Complemento", campo("complemento")), Coluna("Bairro", campo("bairro")),
            Coluna("Cidade", campo("cidade")), Coluna("UF", campo("estado"))]


def situacao(ativo) -> str:
    return "Inativo" if ativo is False else "Ativo"
