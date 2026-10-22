"""Tabelas do MVO: colaboradores de milhas, contas nos programas, movimentação de pontos e emissões."""
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .models import Cliente, agora

# ---------------------------------------------------------------- tipos de lançamento
# sinal: +1 entra pontos, -1 sai pontos. "custo_entrada": a entrada traz custo (R$) para o saldo.
TIPOS_LANCAMENTO: dict[str, dict] = {
    "compra": {"nome": "Compra", "sinal": 1, "custo_entrada": True},
    "clube": {"nome": "Clube", "sinal": 1, "custo_entrada": True},
    "transferencia_entrada": {"nome": "Transferência recebida", "sinal": 1, "custo_entrada": True},
    "bonus": {"nome": "Bônus", "sinal": 1, "custo_entrada": False},
    "saldo_inicial": {"nome": "Saldo inicial", "sinal": 1, "custo_entrada": True},
    "ajuste_entrada": {"nome": "Ajuste (entrada)", "sinal": 1, "custo_entrada": False},
    "emissao": {"nome": "Emissão", "sinal": -1},
    "resgate": {"nome": "Resgate", "sinal": -1},
    "transferencia_saida": {"nome": "Transferência enviada", "sinal": -1},
    "expiracao": {"nome": "Expiração", "sinal": -1},
    "ajuste_saida": {"nome": "Ajuste (saída)", "sinal": -1},
}
# os que a equipe lança à mão (os outros são criados pelo sistema)
TIPOS_MANUAIS = ["compra", "clube", "bonus", "resgate", "expiracao", "ajuste_entrada", "ajuste_saida",
                 "saldo_inicial"]

FORMAS_EMISSAO = {
    "programa_proprio": "Programa próprio (conta de colaborador)",
    "fornecedor": "Fornecedor / balcão (compra externa)",
    "pagante": "Tarifa paga (sem milhas)",
}


class Colaborador(Base):
    """Pessoa que cede as contas de milhas para a agência (no Manager: "fidelizado")."""

    __tablename__ = "colaboradores"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(160), index=True)
    cpf: Mapped[str | None] = mapped_column(String(14), index=True)  # só dígitos
    telefone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(160))
    pix: Mapped[str | None] = mapped_column(String(160))
    classificacao: Mapped[str | None] = mapped_column(String(60))
    consolidador: Mapped[str | None] = mapped_column(String(120))  # quem indicou / agrupa o colaborador
    observacoes: Mapped[str | None] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    contas: Mapped[list["ContaPrograma"]] = relationship(
        back_populates="colaborador", cascade="all, delete-orphan", order_by="ContaPrograma.programa"
    )


class ContaPrograma(Base):
    """Conta de um colaborador em um programa (Smiles, LATAM Pass...). Saldo e custo médio são calculados
    a partir dos lançamentos (ver mvo/contas.py) e ficam guardados aqui para as listas serem rápidas."""

    __tablename__ = "contas_programa"
    __table_args__ = (UniqueConstraint("colaborador_id", "programa", name="uq_conta_colaborador_programa"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    colaborador_id: Mapped[int] = mapped_column(ForeignKey("colaboradores.id", ondelete="CASCADE"), index=True)
    programa: Mapped[str] = mapped_column(String(80), index=True)  # nome do ProgramaMilhas
    numero: Mapped[str | None] = mapped_column(String(40))  # número de fidelidade
    login_cripto: Mapped[str | None] = mapped_column(Text)
    senha_cripto: Mapped[str | None] = mapped_column(Text)
    uso_pessoal: Mapped[bool] = mapped_column(Boolean, default=False)  # conta de uso do próprio colaborador
    saldo_reservado: Mapped[int] = mapped_column(Integer, default=0)  # pontos que o colaborador guardou para si
    observacoes: Mapped[str | None] = mapped_column(Text)
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    data_conciliacao: Mapped[date | None] = mapped_column(Date)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    # calculados
    saldo: Mapped[int] = mapped_column(Integer, default=0)
    saldo_futuro: Mapped[int] = mapped_column(Integer, default=0)  # lançamentos com data de efetivação futura
    custo_total: Mapped[float] = mapped_column(Float, default=0.0)  # R$ que o saldo atual custou
    calculado_em: Mapped[datetime | None] = mapped_column(DateTime)

    colaborador: Mapped[Colaborador] = relationship(back_populates="contas")
    lancamentos: Mapped[list["Lancamento"]] = relationship(
        back_populates="conta", cascade="all, delete-orphan", foreign_keys="Lancamento.conta_id"
    )

    @property
    def custo_medio(self) -> float | None:
        """R$ por milheiro do saldo atual."""
        if self.saldo and self.saldo > 0 and self.custo_total:
            return round(self.custo_total / self.saldo * 1000, 2)
        return None

    @property
    def disponivel(self) -> int:
        """Pontos que a agência pode usar (saldo menos o que o colaborador reservou)."""
        return max((self.saldo or 0) - (self.saldo_reservado or 0), 0)

    @property
    def tem_credenciais(self) -> bool:
        return bool(self.login_cripto or self.senha_cripto)


class Fornecedor(Base):
    """Quem vende pontos ou emissões para a agência (no Manager: "fornecedor de pontos")."""

    __tablename__ = "fornecedores"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(160), index=True)
    documento: Mapped[str | None] = mapped_column(String(20))
    telefone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(160))
    pix: Mapped[str | None] = mapped_column(String(160))
    dias_pagamento: Mapped[int | None] = mapped_column(Integer)
    observacoes: Mapped[str | None] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Emissao(Base):
    __tablename__ = "emissoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_emissao: Mapped[datetime] = mapped_column(DateTime, default=agora, index=True)
    forma: Mapped[str] = mapped_column(String(20), default="programa_proprio")  # ver FORMAS_EMISSAO
    conta_id: Mapped[int | None] = mapped_column(ForeignKey("contas_programa.id", ondelete="SET NULL"), index=True)
    fornecedor_id: Mapped[int | None] = mapped_column(ForeignKey("fornecedores.id", ondelete="SET NULL"))
    programa: Mapped[str | None] = mapped_column(String(80))
    companhia: Mapped[str | None] = mapped_column(String(80))
    localizador: Mapped[str | None] = mapped_column(String(30), index=True)
    origem: Mapped[str | None] = mapped_column(String(3))
    destino: Mapped[str | None] = mapped_column(String(3))
    tipo_viagem: Mapped[str | None] = mapped_column(String(12))  # ida, ida_volta
    data_ida: Mapped[date | None] = mapped_column(Date)
    data_volta: Mapped[date | None] = mapped_column(Date)
    qtd_passageiros: Mapped[int] = mapped_column(Integer, default=1)
    pontos: Mapped[int | None] = mapped_column(Integer)
    preco_venda: Mapped[float | None] = mapped_column(Float)  # valor da passagem cobrado do cliente
    taxa_embarque: Mapped[float | None] = mapped_column(Float)
    taxa_bagagem: Mapped[float | None] = mapped_column(Float)
    outros_custos: Mapped[float | None] = mapped_column(Float)  # ex.: preço pago ao fornecedor/balcão
    custo_milhas: Mapped[float | None] = mapped_column(Float)  # calculado: custo médio dos pontos usados
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id", ondelete="SET NULL"), index=True)
    cliente_nome: Mapped[str | None] = mapped_column(String(160))
    agencia_id: Mapped[int | None] = mapped_column(Integer, index=True)  # etapa 2: agências parceiras
    pedido_id: Mapped[int | None] = mapped_column(Integer, index=True)  # etapa 2: pedidos de emissão
    promocao_id: Mapped[int | None] = mapped_column(ForeignKey("promocoes.id", ondelete="SET NULL"))
    observacao: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(15), default="ativa", index=True)  # ativa, cancelada
    usuario: Mapped[str | None] = mapped_column(String(120))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    conta: Mapped[ContaPrograma | None] = relationship()
    fornecedor: Mapped[Fornecedor | None] = relationship()
    cliente: Mapped[Cliente | None] = relationship()
    passageiros: Mapped[list["EmissaoPassageiro"]] = relationship(
        back_populates="emissao", cascade="all, delete-orphan", order_by="EmissaoPassageiro.id"
    )

    @property
    def total(self) -> float:
        """Total cobrado do cliente: passagem + taxas."""
        return round(sum(v or 0 for v in (self.preco_venda, self.taxa_embarque, self.taxa_bagagem)), 2)

    @property
    def custo(self) -> float | None:
        if self.custo_milhas is None and self.outros_custos is None:
            return None
        return round((self.custo_milhas or 0) + (self.outros_custos or 0), 2)

    @property
    def lucro(self) -> float | None:
        """Passagem vendida menos o custo das milhas (as taxas só passam pela agência)."""
        if self.preco_venda is None or self.custo is None:
            return None
        return round(self.preco_venda - self.custo, 2)

    @property
    def rota(self) -> str:
        if not (self.origem or self.destino):
            return ""
        seta = " ⇄ " if self.tipo_viagem == "ida_volta" else " → "
        return f"{self.origem or '?'}{seta}{self.destino or '?'}"


class EmissaoPassageiro(Base):
    __tablename__ = "emissao_passageiros"

    id: Mapped[int] = mapped_column(primary_key=True)
    emissao_id: Mapped[int] = mapped_column(ForeignKey("emissoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(160))
    cpf: Mapped[str | None] = mapped_column(String(14), index=True)

    emissao: Mapped[Emissao] = relationship(back_populates="passageiros")


class Lancamento(Base):
    """Movimento de pontos de uma conta. Entradas (compra, bônus...) têm pontos positivos; saídas, negativos."""

    __tablename__ = "lancamentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    conta_id: Mapped[int] = mapped_column(ForeignKey("contas_programa.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(String(25), index=True)  # ver TIPOS_LANCAMENTO
    pontos: Mapped[int] = mapped_column(Integer)
    # entradas: R$ pago (vira custo do saldo). Saídas: custo apurado pelo sistema (pontos × custo médio)
    valor: Mapped[float | None] = mapped_column(Float)
    data_lancamento: Mapped[datetime] = mapped_column(DateTime, default=agora)
    data_efetivacao: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    fornecedor_id: Mapped[int | None] = mapped_column(ForeignKey("fornecedores.id", ondelete="SET NULL"))
    emissao_id: Mapped[int | None] = mapped_column(ForeignKey("emissoes.id", ondelete="SET NULL"), index=True)
    par_id: Mapped[int | None] = mapped_column(Integer, index=True)  # transferência: o lançamento da outra conta
    observacao: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(15), default="ativo", index=True)  # ativo, cancelado
    usuario: Mapped[str | None] = mapped_column(String(120))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    conta: Mapped[ContaPrograma] = relationship(back_populates="lancamentos", foreign_keys=[conta_id])
    fornecedor: Mapped[Fornecedor | None] = relationship()
    emissao: Mapped[Emissao | None] = relationship()

    @property
    def nome_tipo(self) -> str:
        return TIPOS_LANCAMENTO.get(self.tipo, {}).get("nome", self.tipo)

    @property
    def entrada(self) -> bool:
        return self.pontos > 0

    @property
    def preco_milheiro(self) -> float | None:
        if self.valor is None or not self.pontos:
            return None
        return round(abs(self.valor) / abs(self.pontos) * 1000, 2)

    @property
    def futuro(self) -> bool:
        return self.data_efetivacao > date.today()


class RegistroAcesso(Base):
    """Registro de ações sensíveis (ver senha, alterar conta, excluir...). No Manager: "log de programa"."""

    __tablename__ = "registro_acessos"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(Integer, index=True)
    usuario_nome: Mapped[str | None] = mapped_column(String(120))
    acao: Mapped[str] = mapped_column(String(40), index=True)
    alvo: Mapped[str | None] = mapped_column(String(80), index=True)  # ex.: conta:12
    detalhe: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, index=True)
