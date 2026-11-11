import secrets
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def agora() -> datetime:
    return datetime.now()


def novo_token() -> str:
    """Código do endereço da página do cliente: 144 bits aleatórios (24 letras), impossível de adivinhar."""
    return secrets.token_urlsafe(18)


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
    # endereço da página do cliente com as promoções dele (/para-voce/<token>); trocar o token desativa o link antigo
    token_pagina: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, default=novo_token)

    interesses: Mapped[list["Interesse"]] = relationship(
        back_populates="cliente", cascade="all, delete-orphan", order_by="Interesse.id"
    )
    matches: Mapped[list["Match"]] = relationship(back_populates="cliente", cascade="all, delete-orphan")
    confirmacoes: Mapped[list["ConfirmacaoCadastro"]] = relationship(
        back_populates="cliente", cascade="all, delete-orphan", order_by="ConfirmacaoCadastro.id"
    )


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


# tipos de promoção: passagem aérea (origem → destino) e as demais, que têm um título e um lugar
TIPOS_PROMOCAO = {"passagem": "Passagem aérea", "hotel": "Hotel", "pacote": "Pacote", "cruzeiro": "Cruzeiro",
                  "outro": "Outra promoção"}


class Promocao(Base):
    __tablename__ = "promocoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    fonte: Mapped[str] = mapped_column(String(30), index=True)  # telegram, google_flights, manual, api
    fonte_detalhe: Mapped[str | None] = mapped_column(String(160))  # canal, busca etc.
    link_fonte: Mapped[str | None] = mapped_column(String(500))
    tipo: Mapped[str] = mapped_column(String(20), default="passagem", server_default="passagem", index=True)  # ver TIPOS_PROMOCAO
    # passagem: origem e destino (códigos). Hotel, pacote, cruzeiro: título e lugar; destino recebe o código da
    # cidade quando ela é conhecida (para casar com os clientes que querem ir para lá)
    origem: Mapped[str | None] = mapped_column(String(3), index=True)
    destino: Mapped[str | None] = mapped_column(String(3), index=True)
    titulo: Mapped[str | None] = mapped_column(String(160))  # ex.: "Resort Vila Galé Cumbuco com tudo incluído"
    lugar: Mapped[str | None] = mapped_column(String(120))  # ex.: "Porto de Galinhas", "Caribe"
    resumo: Mapped[str | None] = mapped_column(Text)  # o que está incluído, condições (lido pela IA)
    data_inicio: Mapped[date | None] = mapped_column(Date)
    data_fim: Mapped[date | None] = mapped_column(Date)
    datas_texto: Mapped[str | None] = mapped_column(String(160))
    preco: Mapped[float | None] = mapped_column(Float)
    milhas: Mapped[int | None] = mapped_column(Integer)
    programa: Mapped[str | None] = mapped_column(String(80))  # programa de milhas
    tipo_viagem: Mapped[str | None] = mapped_column(String(12))  # "ida_volta", "ida" ou vazio (não informado)
    parcelas: Mapped[int | None] = mapped_column(Integer)   # pacote vendido em parcelas: "preco" é o valor de cada uma
    companhia: Mapped[str | None] = mapped_column(String(80))
    link: Mapped[str | None] = mapped_column(String(500))
    texto_original: Mapped[str | None] = mapped_column(Text)
    confianca: Mapped[float] = mapped_column(Float, default=1.0)
    alerta: Mapped[str | None] = mapped_column(String(160))  # ex.: possível erro tarifário
    status: Mapped[str] = mapped_column(String(20), default="ativa", index=True)  # ativa, descartada
    coletada_em: Mapped[datetime] = mapped_column(DateTime, default=agora, index=True)

    matches: Mapped[list["Match"]] = relationship(back_populates="promocao", cascade="all, delete-orphan")
    imagem: Mapped["ImagemPromocao | None"] = relationship(back_populates="promocao", cascade="all, delete-orphan",
                                                           uselist=False)

    @property
    def passagem(self) -> bool:
        return (self.tipo or "passagem") == "passagem"

    @property
    def nome_tipo(self) -> str:
        return TIPOS_PROMOCAO.get(self.tipo or "passagem", "Promoção")

    @property
    def precisa_revisao(self) -> bool:
        from .config import settings

        return bool(self.alerta) or self.confianca < settings.confianca_minima


class ImagemPromocao(Base):
    """A imagem da promoção (grupos mandam a oferta como imagem): fica guardada para a equipe mandar ao cliente
    e para a página do cliente. Imagens de promoções antigas são apagadas depois de um tempo."""

    __tablename__ = "imagens_promocao"

    id: Mapped[int] = mapped_column(primary_key=True)
    promocao_id: Mapped[int] = mapped_column(ForeignKey("promocoes.id", ondelete="CASCADE"), unique=True, index=True)
    mime: Mapped[str] = mapped_column(String(40), default="image/jpeg")
    dados: Mapped[bytes] = mapped_column(LargeBinary)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    meta_media_id: Mapped[str | None] = mapped_column(String(80))  # id da imagem já enviada à Meta (vale 30 dias)
    meta_media_em: Mapped[datetime | None] = mapped_column(DateTime)

    promocao: Mapped[Promocao] = relationship(back_populates="imagem")


class LeituraPendente(Base):
    """Imagem ou texto que ainda não foi lido pela IA (cota do dia esgotada ou falha): tenta de novo na próxima
    rodada."""

    __tablename__ = "leituras_pendentes"

    id: Mapped[int] = mapped_column(primary_key=True)
    fonte: Mapped[str] = mapped_column(String(30))
    fonte_detalhe: Mapped[str | None] = mapped_column(String(160))
    link_fonte: Mapped[str | None] = mapped_column(String(500))
    texto: Mapped[str | None] = mapped_column(Text)
    mime: Mapped[str | None] = mapped_column(String(40))
    imagem: Mapped[bytes | None] = mapped_column(LargeBinary)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    erro: Mapped[str | None] = mapped_column(String(300))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


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


class ConfirmacaoCadastro(Base):
    """Mensagem de confirmação para quem se cadastrou pelo formulário ("vamos ficar de olho nas promoções de março
    para Lisboa"). Entra na fila de envio e a equipe manda pelo WhatsApp com um clique."""

    __tablename__ = "confirmacoes_cadastro"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    # trechos escolhidos nesta resposta (ids separados por vírgula); vazio = todos os trechos do cliente (cliente novo)
    trechos: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="nova", index=True)  # nova, enviada, descartada
    enviado_por: Mapped[str | None] = mapped_column(String(120))
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime)
    envios: Mapped[int | None] = mapped_column(Integer, default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    cliente: Mapped[Cliente] = relationship(back_populates="confirmacoes")


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


class GrupoWhatsApp(Base):
    """Grupo do WhatsApp que o leitor (número da agência só para isso) acompanha. Só os marcados como ativos
    têm as mensagens lidas; os outros aparecem na lista para a equipe escolher."""

    __tablename__ = "grupos_whatsapp"

    id: Mapped[int] = mapped_column(primary_key=True)
    jid: Mapped[str] = mapped_column(String(80), unique=True, index=True)  # ex.: 1203630123456789@g.us
    nome: Mapped[str | None] = mapped_column(String(200))
    ativo: Mapped[bool] = mapped_column(Boolean, default=False)
    mensagens: Mapped[int] = mapped_column(Integer, default=0)  # lidas desde que foi ligado
    promocoes: Mapped[int] = mapped_column(Integer, default=0)
    ultima_mensagem: Mapped[datetime | None] = mapped_column(DateTime)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class ArquivoSessao(Base):
    """Arquivos que precisam sobreviver ao reinício do servidor (o disco do Render é apagado a cada publicação):
    hoje, a sessão do WhatsApp."""

    __tablename__ = "arquivos_sessao"

    nome: Mapped[str] = mapped_column(String(60), primary_key=True)
    dados: Mapped[bytes] = mapped_column(LargeBinary)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, onupdate=agora)


class EnvioWhatsApp(Base):
    """Mensagem mandada pela API oficial do WhatsApp (Meta): a quem, por qual modelo, e o que a Meta respondeu
    (enviada, entregue, lida, falhou). Serve para o histórico e para contar o custo."""

    __tablename__ = "envios_whatsapp"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id", ondelete="SET NULL"), index=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id", ondelete="SET NULL"), index=True)
    confirmacao_id: Mapped[int | None] = mapped_column(ForeignKey("confirmacoes_cadastro.id", ondelete="SET NULL"))
    telefone: Mapped[str] = mapped_column(String(20))
    tipo: Mapped[str] = mapped_column(String(20), default="promocao")  # promocao, confirmacao, resposta, teste
    modelo: Mapped[str | None] = mapped_column(String(120))  # nome do modelo aprovado na Meta
    wamid: Mapped[str | None] = mapped_column(String(255), index=True)  # id da mensagem na Meta (passa de 120)
    status: Mapped[str] = mapped_column(String(20), default="enviada")  # enviada, entregue, lida, falhou
    erro: Mapped[str | None] = mapped_column(String(300))
    texto: Mapped[str | None] = mapped_column(Text)   # resposta da equipe (texto livre), para mostrar na conversa
    automatico: Mapped[bool] = mapped_column(Boolean, default=False)
    enviado_por: Mapped[str | None] = mapped_column(String(120))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, index=True)
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime)


class RespostaWhatsApp(Base):
    """Mensagem que um cliente mandou para o número da API (chega pelo webhook da Meta)."""

    __tablename__ = "respostas_whatsapp"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id", ondelete="SET NULL"), index=True)
    telefone: Mapped[str] = mapped_column(String(20), index=True)
    nome: Mapped[str | None] = mapped_column(String(160))  # nome do perfil no WhatsApp
    texto: Mapped[str | None] = mapped_column(Text)
    wamid: Mapped[str | None] = mapped_column(String(255), unique=True)
    recebido_em: Mapped[datetime] = mapped_column(DateTime, default=agora, index=True)
    lida: Mapped[bool] = mapped_column(Boolean, default=False)
    descadastrou: Mapped[bool] = mapped_column(Boolean, default=False)
    intencao: Mapped[str | None] = mapped_column(String(20), index=True)  # interesse, duvida, recusa, sair, outro
    intencao_por: Mapped[str | None] = mapped_column(String(10))  # "regras" ou "ia"


class ConversaAtendimento(Base):
    """A equipe abriu a conversa com o cliente no número de atendimento (0400, aplicativo WhatsApp Business).
    O sistema não lê esse aplicativo: o registro é o clique em "Conversar no 0400", que leva o cartão do CRM
    para "Em conversa"."""

    __tablename__ = "conversas_atendimento"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    aberta_em: Mapped[datetime] = mapped_column(DateTime, default=agora, index=True)
    aberta_por: Mapped[str | None] = mapped_column(String(120))
    # "equipe" = clique em "Conversar no 0400"; "cliente" = o cliente tocou no botão da mensagem (Falar com um atendente)
    origem: Mapped[str | None] = mapped_column(String(10), default="equipe")
    promocao_id: Mapped[int | None] = mapped_column(Integer)


class Reengajamento(Base):
    """Mensagem de retorno ("trazer de volta") para cliente sem resposta há um tempo ou sem compra há meses.
    canal: "api" (0500), "whatsapp" (manual) ou "pulado" (a equipe tirou da lista até a próxima vez)."""

    __tablename__ = "reengajamentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    motivo: Mapped[str] = mapped_column(String(20))          # sem_resposta, sem_compra
    canal: Mapped[str] = mapped_column(String(10))           # api, whatsapp, pulado
    quando: Mapped[datetime] = mapped_column(DateTime, default=agora, index=True)
    por: Mapped[str | None] = mapped_column(String(120))
    envio_id: Mapped[int | None] = mapped_column(Integer)


class EtapaCRM(Base):
    """Etapa do funil (CRM) definida à mão para um cliente. Vale até acontecer um evento mais novo (envio, resposta,
    pedido, emissão), que volta a mover o cartão sozinho."""

    __tablename__ = "etapas_crm"

    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), primary_key=True)
    etapa: Mapped[str] = mapped_column(String(20))
    definido_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    definido_por: Mapped[str | None] = mapped_column(String(120))
    motivo: Mapped[str | None] = mapped_column(String(255))


class EstadoColetor(Base):
    """Guarda o ponto onde cada coletor parou (ex.: último id lido de um canal)."""

    __tablename__ = "estado_coletor"

    chave: Mapped[str] = mapped_column(String(200), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)  # o token da API da Meta cifrado passa de 500 caracteres
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, onupdate=agora)
