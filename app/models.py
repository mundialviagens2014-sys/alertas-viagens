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


def agora() -> datetime:
    return datetime.now()


class Usuario(Base):
    """Membro da equipe que acessa o site."""

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    login: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(255))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    # admin, gestor, emissor, atendente (ver app/permissoes.py); vazio = admin (usuários antigos)
    papel: Mapped[str | None] = mapped_column(String(20))
    ultimo_acesso: Mapped[datetime | None] = mapped_column(DateTime)
    # comissão de quem emite passagens: % sobre o lucro ou sobre a venda das emissões do mês
    comissao_pct: Mapped[float | None] = mapped_column(Float)
    comissao_base: Mapped[str | None] = mapped_column(String(10), default="lucro")


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(160))
    telefone: Mapped[str] = mapped_column(String(20), index=True)  # só dígitos, com DDI
    email: Mapped[str | None] = mapped_column(String(160))
    documento: Mapped[str | None] = mapped_column(String(20), index=True)  # CPF/CNPJ (só dígitos)
    data_nascimento: Mapped[date | None] = mapped_column(Date)
    classificacao: Mapped[str | None] = mapped_column(String(60))
    observacoes: Mapped[str | None] = mapped_column(Text)
    consentimento: Mapped[bool] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    interesses: Mapped[list["Interesse"]] = relationship(
        back_populates="cliente", cascade="all, delete-orphan", order_by="Interesse.id"
    )
    matches: Mapped[list["Match"]] = relationship(back_populates="cliente", cascade="all, delete-orphan")


class Interesse(Base):
    """Trecho que o cliente quer fazer. Destino vazio = qualquer destino."""

    __tablename__ = "interesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    origem: Mapped[str] = mapped_column(String(3), index=True)
    destino: Mapped[str | None] = mapped_column(String(3), index=True)
    periodo_inicio: Mapped[date | None] = mapped_column(Date)
    periodo_fim: Mapped[date | None] = mapped_column(Date)
    preco_maximo: Mapped[float | None] = mapped_column(Float)
    observacao: Mapped[str | None] = mapped_column(String(255))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    cliente: Mapped[Cliente] = relationship(back_populates="interesses")


class Promocao(Base):
    __tablename__ = "promocoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    fonte: Mapped[str] = mapped_column(String(30), index=True)  # telegram, google_flights, manual, api
    fonte_detalhe: Mapped[str | None] = mapped_column(String(160))  # canal, busca etc.
    link_fonte: Mapped[str | None] = mapped_column(String(500))
    origem: Mapped[str] = mapped_column(String(3), index=True)
    destino: Mapped[str] = mapped_column(String(3), index=True)
    data_inicio: Mapped[date | None] = mapped_column(Date)
    data_fim: Mapped[date | None] = mapped_column(Date)
    datas_texto: Mapped[str | None] = mapped_column(String(160))
    preco: Mapped[float | None] = mapped_column(Float)
    milhas: Mapped[int | None] = mapped_column(Integer)
    programa: Mapped[str | None] = mapped_column(String(80))  # programa de milhas
    tipo_viagem: Mapped[str | None] = mapped_column(String(12))  # "ida_volta", "ida" ou vazio (não informado)
    companhia: Mapped[str | None] = mapped_column(String(80))
    link: Mapped[str | None] = mapped_column(String(500))
    texto_original: Mapped[str | None] = mapped_column(Text)
    confianca: Mapped[float] = mapped_column(Float, default=1.0)
    alerta: Mapped[str | None] = mapped_column(String(160))  # ex.: possível erro tarifário
    status: Mapped[str] = mapped_column(String(20), default="ativa", index=True)  # ativa, descartada
    coletada_em: Mapped[datetime] = mapped_column(DateTime, default=agora, index=True)

    matches: Mapped[list["Match"]] = relationship(back_populates="promocao", cascade="all, delete-orphan")

    @property
    def precisa_revisao(self) -> bool:
        from .config import settings

        return bool(self.alerta) or self.confianca < settings.confianca_minima


class Match(Base):
    """Ligação entre uma promoção e um cliente interessado."""

    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("promocao_id", "cliente_id", name="uq_match_promocao_cliente"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    promocao_id: Mapped[int] = mapped_column(ForeignKey("promocoes.id", ondelete="CASCADE"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    interesse_id: Mapped[int | None] = mapped_column(ForeignKey("interesses.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="nova", index=True)  # nova, enviada, descartada
    enviado_por: Mapped[str | None] = mapped_column(String(120))
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime)  # último envio
    envios: Mapped[int | None] = mapped_column(Integer, default=0)  # quantas vezes foi enviada
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    promocao: Mapped[Promocao] = relationship(back_populates="matches")
    cliente: Mapped[Cliente] = relationship(back_populates="matches")
    interesse: Mapped[Interesse | None] = relationship()


class TemplateMensagem(Base):
    __tablename__ = "templates_mensagem"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(80))
    texto: Mapped[str] = mapped_column(Text)
    padrao: Mapped[bool] = mapped_column(Boolean, default=False)


class ProgramaMilhas(Base):
    """Valor que a agência cobra por milha em cada programa (para converter promoções em R$)."""

    __tablename__ = "programas_milhas"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(80), unique=True)
    valor_milha: Mapped[float | None] = mapped_column(Float)  # R$ por milha (ex.: 0,027)
    taxa_fixa: Mapped[float | None] = mapped_column(Float)  # acréscimo em R$ por promoção (taxas, serviço)
    personalizado: Mapped[bool] = mapped_column(Boolean, default=False)
    # MVO: preços por milheiro (R$ por 1.000 milhas) e limite de passageiros (CPFs) por conta
    preco_b2b: Mapped[float | None] = mapped_column(Float)  # agências parceiras
    preco_b2c: Mapped[float | None] = mapped_column(Float)  # cliente final (usado nos alertas)
    preco_b2f: Mapped[float | None] = mapped_column(Float)  # franquias
    limite_cpfs: Mapped[int | None] = mapped_column(Integer)  # vazio = sem limite
    janela_cpfs: Mapped[str | None] = mapped_column(String(10))  # "ano" (ano civil) ou "12m" (últimos 12 meses)
    nome_manager: Mapped[str | None] = mapped_column(String(80))  # como aparece no Manager (ex.: SMILES)
    # "Tipo de programa" do Manager: como o programa funciona
    tipo_uso: Mapped[str | None] = mapped_column(String(20), default="Programa")
    grupo_produto_id: Mapped[int | None] = mapped_column(Integer)
    cia_aerea: Mapped[str | None] = mapped_column(String(80))
    numeracao: Mapped[str | None] = mapped_column(String(20), default="Informado")
    tipo_parceiro: Mapped[str | None] = mapped_column(String(30), default="Fidelizado")
    unidade: Mapped[str | None] = mapped_column(String(20), default="Milhas")
    moeda: Mapped[str | None] = mapped_column(String(30), default="Real (BRL)")
    exige_descricao: Mapped[bool | None] = mapped_column(Boolean, default=False)
    necessita_login: Mapped[bool | None] = mapped_column(Boolean, default=True)
    exige_senha_resgate: Mapped[bool | None] = mapped_column(Boolean, default=False)
    pode_ofertar: Mapped[bool | None] = mapped_column(Boolean, default=False)
    saldo_implantacao: Mapped[bool | None] = mapped_column(Boolean, default=False)
    controla_emissoes: Mapped[bool | None] = mapped_column(Boolean, default=True)
    transferencia_cruzada: Mapped[bool | None] = mapped_column(Boolean, default=False)
    controla_saldo: Mapped[bool | None] = mapped_column(Boolean, default=True)
    cancelamento_devolve: Mapped[bool | None] = mapped_column(Boolean, default=True)  # cancelar libera o CPF
    conciliacao: Mapped[bool | None] = mapped_column(Boolean, default=False)
    precisa_token: Mapped[bool | None] = mapped_column(Boolean, default=False)
    aceita_interline: Mapped[bool | None] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool | None] = mapped_column(Boolean, default=True)
    observacoes: Mapped[str | None] = mapped_column(Text)


class CanalTelegram(Base):
    """Grupo ou canal do Telegram que o sistema lê em busca de promoções."""

    __tablename__ = "canais_telegram"

    id: Mapped[int] = mapped_column(primary_key=True)
    identificador: Mapped[str] = mapped_column(String(120), unique=True)  # @usuario ou -100...
    nome: Mapped[str | None] = mapped_column(String(200))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    ultima_leitura: Mapped[datetime | None] = mapped_column(DateTime)
    ultimo_erro: Mapped[str | None] = mapped_column(String(300))


class EstadoColetor(Base):
    """Guarda o ponto onde cada coletor parou (ex.: último id lido de um canal)."""

    __tablename__ = "estado_coletor"

    chave: Mapped[str] = mapped_column(String(200), primary_key=True)
    valor: Mapped[str] = mapped_column(String(500))
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, onupdate=agora)
