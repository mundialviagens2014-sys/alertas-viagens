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

METODOS_PAGAMENTO = ["PIX", "PIX/Boleto", "Boleto", "Cartão de crédito", "Cartão de débito", "Transferência",
                     "Dinheiro", "Faturado"]

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
    rg: Mapped[str | None] = mapped_column(String(20))
    data_nascimento: Mapped[date | None] = mapped_column(Date)
    cep: Mapped[str | None] = mapped_column(String(9))
    endereco: Mapped[str | None] = mapped_column(String(200))
    numero: Mapped[str | None] = mapped_column(String(20))
    complemento: Mapped[str | None] = mapped_column(String(80))
    bairro: Mapped[str | None] = mapped_column(String(80))
    cidade: Mapped[str | None] = mapped_column(String(80))
    estado: Mapped[str | None] = mapped_column(String(2))

    contratos: Mapped[list["Contrato"]] = relationship(
        back_populates="colaborador", cascade="all, delete-orphan", order_by="Contrato.data_inicio.desc()"
    )
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
    email_programa: Mapped[str | None] = mapped_column(String(160))  # e-mail cadastrado no programa
    codigo_conta: Mapped[str | None] = mapped_column(String(60))
    token_cripto: Mapped[str | None] = mapped_column(Text)  # token para conciliação (com criptografia)
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
        return bool(self.login_cripto or self.senha_cripto or self.token_cripto)


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
    cep: Mapped[str | None] = mapped_column(String(9))
    endereco: Mapped[str | None] = mapped_column(String(200))
    numero: Mapped[str | None] = mapped_column(String(20))
    complemento: Mapped[str | None] = mapped_column(String(80))
    bairro: Mapped[str | None] = mapped_column(String(80))
    cidade: Mapped[str | None] = mapped_column(String(80))
    estado: Mapped[str | None] = mapped_column(String(2))


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
    usuario: Mapped[str | None] = mapped_column(String(120))  # quem registrou no sistema
    # quem emitiu a passagem (base da comissão); normalmente quem registrou
    emissor_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"), index=True)
    emissor_nome: Mapped[str | None] = mapped_column(String(120), index=True)
    metodo_pagamento: Mapped[str | None] = mapped_column(String(30))  # PIX, boleto, cartão...
    conta_recebedora: Mapped[str | None] = mapped_column(String(80))
    taxa_cartao: Mapped[float | None] = mapped_column(Float)  # custo da maquininha/parcelamento
    conexoes: Mapped[int | None] = mapped_column(Integer)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    conta: Mapped[ContaPrograma | None] = relationship()
    fornecedor: Mapped[Fornecedor | None] = relationship()
    cliente: Mapped[Cliente | None] = relationship()
    agencia: Mapped["Agencia | None"] = relationship(primaryjoin="foreign(Emissao.agencia_id) == Agencia.id",
                                                     viewonly=True)
    passageiros: Mapped[list["EmissaoPassageiro"]] = relationship(
        back_populates="emissao", cascade="all, delete-orphan", order_by="EmissaoPassageiro.id"
    )

    @property
    def total(self) -> float:
        """Total cobrado do cliente: passagem + taxas."""
        return round(sum(v or 0 for v in (self.preco_venda, self.taxa_embarque, self.taxa_bagagem)), 2)

    @property
    def custo(self) -> float | None:
        """Custo das milhas + outros custos (fornecedor, tarifa paga) + taxa do cartão."""
        if self.custo_milhas is None and self.outros_custos is None and not self.taxa_cartao:
            return None
        return round((self.custo_milhas or 0) + (self.outros_custos or 0) + (self.taxa_cartao or 0), 2)

    @property
    def lucro_pct(self) -> float | None:
        if self.lucro is None or not self.preco_venda:
            return None
        return round(self.lucro / self.preco_venda * 100, 1)

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


# ---------------------------------------------------------------- configuração (como no menu "Programa" do Manager)

CORES = {
    "azul": "#1b468f", "verde": "#168a4a", "laranja": "#d4621c", "vermelho": "#b42318", "roxo": "#6b3fa0",
    "amarelo": "#a35a00", "rosa": "#c2185b", "cinza": "#5a6782",
}
TIPOS_NEGOCIO = {"B2B": "B2B · agências", "B2C": "B2C · cliente final", "B2F": "B2F · franquias"}


class Classificacao(Base):
    __tablename__ = "classificacoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), unique=True)
    cor: Mapped[str | None] = mapped_column(String(20), default="azul")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Consolidador(Base):
    """Grupo de fidelizados (quem indica ou reúne colaboradores)."""

    __tablename__ = "consolidadores"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), unique=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class GrupoProduto(Base):
    __tablename__ = "grupos_produto"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(80))
    requer_aerea: Mapped[bool] = mapped_column(Boolean, default=False)
    tipo: Mapped[str | None] = mapped_column(String(20), default="PROGRAMA")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Franquia(Base):
    __tablename__ = "franquias"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(160), index=True)
    razao_social: Mapped[str | None] = mapped_column(String(200))
    documento: Mapped[str | None] = mapped_column(String(20))
    responsavel: Mapped[str | None] = mapped_column(String(120))
    telefone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(160))
    cidade: Mapped[str | None] = mapped_column(String(80))
    estado: Mapped[str | None] = mapped_column(String(2))
    observacoes: Mapped[str | None] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Agencia(Base):
    """Agência parceira que compra emissões (B2B) ou franquia (B2F)."""

    __tablename__ = "agencias"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(160), index=True)
    razao_social: Mapped[str | None] = mapped_column(String(200))
    consolidadora: Mapped[bool] = mapped_column(Boolean, default=False)
    documento: Mapped[str | None] = mapped_column(String(20), index=True)
    data_fundacao: Mapped[date | None] = mapped_column(Date)
    cep: Mapped[str | None] = mapped_column(String(9))
    endereco: Mapped[str | None] = mapped_column(String(200))
    numero: Mapped[str | None] = mapped_column(String(20))
    complemento: Mapped[str | None] = mapped_column(String(80))
    bairro: Mapped[str | None] = mapped_column(String(80))
    cidade: Mapped[str | None] = mapped_column(String(80))
    estado: Mapped[str | None] = mapped_column(String(2))
    telefone: Mapped[str | None] = mapped_column(String(20))
    telefone2: Mapped[str | None] = mapped_column(String(20))
    email_comercial: Mapped[str | None] = mapped_column(String(160))
    email_financeiro: Mapped[str | None] = mapped_column(String(160))
    site: Mapped[str | None] = mapped_column(String(160))
    porte: Mapped[str | None] = mapped_column(String(40))
    perfil: Mapped[str | None] = mapped_column(String(60))
    tipo_negocio: Mapped[str | None] = mapped_column(String(3), default="B2B")
    limite_consultas: Mapped[int | None] = mapped_column(Integer)
    atividades: Mapped[str | None] = mapped_column(Text)
    observacoes: Mapped[str | None] = mapped_column(Text)
    franquia_id: Mapped[int | None] = mapped_column(ForeignKey("franquias.id", ondelete="SET NULL"), index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    franquia: Mapped[Franquia | None] = relationship()


class PrecoPrograma(Base):
    """Lista de preços: R$ por milheiro por programa e tipo de negócio, geral ou de uma agência."""

    __tablename__ = "precos_programa"

    id: Mapped[int] = mapped_column(primary_key=True)
    programa: Mapped[str] = mapped_column(String(80), index=True)
    agencia_id: Mapped[int | None] = mapped_column(ForeignKey("agencias.id", ondelete="CASCADE"), index=True)
    tipo_negocio: Mapped[str] = mapped_column(String(3), default="B2B")
    limite_pontos: Mapped[int | None] = mapped_column(Integer, default=0)  # 0 = sem limite
    preco_venda: Mapped[float | None] = mapped_column(Float)
    preco_compra: Mapped[float | None] = mapped_column(Float)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    agencia: Mapped[Agencia | None] = relationship()


class Markup(Base):
    """Acréscimo sobre o preço: por programa ou por companhia aérea (tarifa paga), geral ou de uma agência."""

    __tablename__ = "markups"

    id: Mapped[int] = mapped_column(primary_key=True)
    alvo: Mapped[str] = mapped_column(String(12), default="programa")  # programa, companhia
    nome: Mapped[str] = mapped_column(String(80))  # programa ou companhia
    agencia_id: Mapped[int | None] = mapped_column(ForeignKey("agencias.id", ondelete="CASCADE"), index=True)
    taxa_pct: Mapped[float | None] = mapped_column(Float)
    valor: Mapped[float | None] = mapped_column(Float)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    agencia: Mapped[Agencia | None] = relationship()


# ---------------------------------------------------------------- cadastros e movimentação


class Contrato(Base):
    """Contrato com o fidelizado (ex.: tipo DIAMANTE, valor de resgate, vencimento)."""

    __tablename__ = "contratos"

    id: Mapped[int] = mapped_column(primary_key=True)
    colaborador_id: Mapped[int] = mapped_column(ForeignKey("colaboradores.id", ondelete="CASCADE"), index=True)
    programa: Mapped[str | None] = mapped_column(String(80))
    tipo: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(15), default="ativo")  # ativo, encerrado, cancelado
    data_inicio: Mapped[date | None] = mapped_column(Date)
    data_vencimento: Mapped[date | None] = mapped_column(Date)
    valor_resgate: Mapped[float | None] = mapped_column(Float)
    observacoes: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    colaborador: Mapped[Colaborador] = relationship(back_populates="contratos")

    @property
    def vencido(self) -> bool:
        return bool(self.data_vencimento and self.data_vencimento < date.today())

    @property
    def vence_logo(self) -> bool:
        return bool(self.data_vencimento and 0 <= (self.data_vencimento - date.today()).days <= 30)


STATUS_CONTRATO = {"ativo": "Ativo", "encerrado": "Encerrado", "cancelado": "Cancelado"}
STATUS_COMPRA = {"pendente": "Pendente", "paga": "Paga", "recebida": "Pontos recebidos", "cancelada": "Cancelada"}


class CompraExterna(Base):
    """Compra de pontos fora (fornecedor, balcão, banco). Ao receber, vira compra na conta de destino."""

    __tablename__ = "compras_externas"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_compra: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    fornecedor_id: Mapped[int | None] = mapped_column(ForeignKey("fornecedores.id", ondelete="SET NULL"), index=True)
    programa: Mapped[str | None] = mapped_column(String(80))
    pontos: Mapped[int | None] = mapped_column(Integer)
    valor: Mapped[float | None] = mapped_column(Float)
    forma_pagamento: Mapped[str | None] = mapped_column(String(30))
    vencimento: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(15), default="pendente", index=True)
    conta_id: Mapped[int | None] = mapped_column(ForeignKey("contas_programa.id", ondelete="SET NULL"))
    lancamento_id: Mapped[int | None] = mapped_column(Integer)
    observacao: Mapped[str | None] = mapped_column(Text)
    usuario: Mapped[str | None] = mapped_column(String(120))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    fornecedor: Mapped[Fornecedor | None] = relationship()
    conta: Mapped[ContaPrograma | None] = relationship()

    @property
    def preco_milheiro(self) -> float | None:
        if not self.valor or not self.pontos:
            return None
        return round(self.valor / self.pontos * 1000, 2)


STATUS_PEDIDO = {"aguardando": "Aguardando", "emitido": "Emitido", "cancelado": "Cancelado"}
TIPOS_PEDIDO = {"simples": "Simples (só ida)", "ida_volta": "Ida e volta", "multitrecho": "Multitrecho"}


class PedidoEmissao(Base):
    """Pedido de emissão de uma agência ou cliente, aguardando alguém emitir."""

    __tablename__ = "pedidos_emissao"

    id: Mapped[int] = mapped_column(primary_key=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, index=True)
    status: Mapped[str] = mapped_column(String(15), default="aguardando", index=True)
    requisitante_nome: Mapped[str | None] = mapped_column(String(160))
    agencia_id: Mapped[int | None] = mapped_column(ForeignKey("agencias.id", ondelete="SET NULL"), index=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id", ondelete="SET NULL"), index=True)
    contato: Mapped[str | None] = mapped_column(String(160))
    tipo_emissao: Mapped[str | None] = mapped_column(String(20), default="simples")
    programa: Mapped[str | None] = mapped_column(String(80))
    companhia: Mapped[str | None] = mapped_column(String(80))
    origem: Mapped[str | None] = mapped_column(String(3))
    destino: Mapped[str | None] = mapped_column(String(3))
    data_ida: Mapped[date | None] = mapped_column(Date)
    data_volta: Mapped[date | None] = mapped_column(Date)
    qtd_passageiros: Mapped[int | None] = mapped_column(Integer, default=1)
    passageiros: Mapped[str | None] = mapped_column(Text)  # nomes e CPFs, um por linha
    observacoes: Mapped[str | None] = mapped_column(Text)
    preco_combinado: Mapped[float | None] = mapped_column(Float)
    emissao_id: Mapped[int | None] = mapped_column(Integer)
    usuario: Mapped[str | None] = mapped_column(String(120))
    atendido_por: Mapped[str | None] = mapped_column(String(120))
    atendido_em: Mapped[datetime | None] = mapped_column(DateTime)

    agencia: Mapped[Agencia | None] = relationship()
    cliente: Mapped[Cliente | None] = relationship()

    @property
    def nome_requisitante(self) -> str:
        if self.agencia:
            return self.agencia.nome + (f" · {self.requisitante_nome}" if self.requisitante_nome else "")
        if self.cliente:
            return self.cliente.nome
        return self.requisitante_nome or "—"

    def minutos_aberto(self, agora_: datetime | None = None) -> int:
        fim = self.atendido_em or agora_ or datetime.now()
        return max(int((fim - self.criado_em).total_seconds() // 60), 0)

    def tempo_aberto(self, agora_: datetime | None = None, curto: bool = False) -> str:
        """Quanto tempo o pedido esperou (até ser atendido ou até agora): "5 horas e 20 minutos" ou "5h 20min"."""
        dias, resto = divmod(self.minutos_aberto(agora_), 24 * 60)
        horas, mins = divmod(resto, 60)
        partes = []
        if curto:
            partes = [f"{dias}d" if dias else "", f"{horas}h" if horas else "", f"{mins}min" if mins and not dias else ""]
            return " ".join(p for p in partes if p) or "agora"
        if dias:
            partes.append(f"{dias} dia{'s' if dias > 1 else ''}")
        if horas:
            partes.append(f"{horas} hora{'s' if horas > 1 else ''}")
        if not dias and mins:
            partes.append(f"{mins} minuto{'s' if mins > 1 else ''}")
        return " e ".join(partes) or "agora"


# ---------------------------------------------------------------- bilhetes: consulta da reserva pelo robô

COMPANHIAS_BILHETE: dict[str, str] = {"LA": "LATAM", "G3": "GOL", "AD": "Azul"}
SITUACOES_CONSULTA: dict[str, str] = {
    "pendente": "Na fila do robô",
    "lendo": "Robô consultando",
    "pronta": "Reserva lida",
    "nao_encontrada": "Reserva não encontrada",
    "ajuda": "Precisa de uma pessoa",
    "erro": "Não deu certo",
}


class ConsultaReserva(Base):
    """Pedido de consulta de reserva no site da companhia, feito no MVO e atendido pelo robô de reservas (que roda
    num computador da agência). Quando o robô devolve os dados, o MVO gera o bilhete para o cliente."""

    __tablename__ = "consultas_reserva"

    id: Mapped[int] = mapped_column(primary_key=True)
    localizador: Mapped[str] = mapped_column(String(10), index=True)
    companhia: Mapped[str] = mapped_column(String(2))  # LA, G3, AD
    sobrenome: Mapped[str] = mapped_column(String(80))
    origem: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(15), default="pendente", index=True)  # ver SITUACOES_CONSULTA
    dados: Mapped[str | None] = mapped_column(Text)   # JSON da reserva lida (trechos, voos, passageiros, bagagem)
    mensagem: Mapped[str | None] = mapped_column(Text)   # o que o robô disse quando não conseguiu
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    emissao_id: Mapped[int | None] = mapped_column(ForeignKey("emissoes.id", ondelete="SET NULL"), index=True)
    pedido_por: Mapped[str | None] = mapped_column(String(120))
    pedido_por_id: Mapped[int | None] = mapped_column(Integer)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, index=True)
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime)

    emissao: Mapped["Emissao | None"] = relationship()

    @property
    def nome_companhia(self) -> str:
        return COMPANHIAS_BILHETE.get(self.companhia, self.companhia)

    @property
    def nome_status(self) -> str:
        return SITUACOES_CONSULTA.get(self.status, self.status)
