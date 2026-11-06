"""Configurações do sistema, lidas de variáveis de ambiente (ou do arquivo .env)."""
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, unquote

BASE_DIR = Path(__file__).resolve().parent.parent


def _carregar_env(caminho: Path) -> None:
    """Carrega um arquivo .env simples (CHAVE=valor) sem sobrescrever o ambiente."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


_carregar_env(BASE_DIR / ".env")

# Horário de Brasília em todo o sistema (o servidor do Render usa o horário UTC, 3 horas à frente)
os.environ.setdefault("TZ", "America/Sao_Paulo")
if hasattr(time, "tzset"):
    time.tzset()


def _bool(nome: str, padrao: bool = False) -> bool:
    return os.getenv(nome, str(padrao)).strip().lower() in {"1", "true", "sim", "yes", "on"}


def _lista(nome: str) -> list[str]:
    return [item.strip() for item in os.getenv(nome, "").split(",") if item.strip()]


def normalizar_url_banco(url: str) -> str:
    """Aceita o endereço como o Supabase/Neon/Railway entregam (postgres:// ou postgresql://)."""
    url = url.strip().strip('"').strip("'")
    for prefixo in ("postgres://", "postgresql://", "postgresql+psycopg://"):
        if url.startswith(prefixo):
            resto = url[len(prefixo):]
            # senha com símbolos (@ # / ? %...) precisa ser codificada para caber no endereço
            if "@" in resto:
                credenciais, servidor = resto.rsplit("@", 1)
                if ":" in credenciais:
                    usuario, senha = credenciais.split(":", 1)
                    senha = senha.strip("[]") if senha.startswith("[") and senha.endswith("]") else senha
                    # aceita a senha escrita normal (com @), já codificada (%40) ou misturada
                    senha = quote(unquote(senha), safe="")
                    resto = f"{usuario}:{senha}@{servidor}"
            return "postgresql+psycopg://" + resto
    return url


class Settings:
    def __init__(self) -> None:
        self.agencia_nome = os.getenv("AGENCIA_NOME", "Agência de Viagens")
        # endereço do site nos links das mensagens (ex.: https://mundial-alertas.onrender.com); vazio = o endereço
        # que a equipe está usando
        self.site_url = os.getenv("SITE_URL", "").strip().rstrip("/")
        if self.site_url.startswith("http://") and not re.match(r"http://(localhost|127\.0\.0\.1)(:|$)", self.site_url):
            self.site_url = "https://" + self.site_url[len("http://"):]  # links dos clientes só por https
        self.database_url = normalizar_url_banco(os.getenv("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'alertas.db'}")
        self.secret_key = os.getenv("SECRET_KEY", "troque-esta-chave-em-producao")
        # chave das senhas das contas dos colaboradores (se vazia, é derivada da SECRET_KEY)
        self.chave_senhas = os.getenv("CHAVE_SENHAS", "")
        self.cookie_seguro = _bool("COOKIE_SEGURO", False)

        # Primeiro usuário da equipe, criado automaticamente se o banco estiver vazio
        self.admin_login = os.getenv("ADMIN_LOGIN", "admin")
        self.admin_senha = os.getenv("ADMIN_SENHA", "admin123")

        # Chave para a API de entrada de promoções (integrações externas)
        self.api_key = os.getenv("API_KEY", "")

        # Telegram (https://my.telegram.org -> API development tools)
        self.telegram_api_id = os.getenv("TELEGRAM_API_ID", "")
        self.telegram_api_hash = os.getenv("TELEGRAM_API_HASH", "")
        self.telegram_sessao = os.getenv("TELEGRAM_SESSAO", str(BASE_DIR / "telegram_agencia"))
        self.telegram_canais = _lista("TELEGRAM_CANAIS")

        # Google Flights via SerpApi (https://serpapi.com)
        self.serpapi_key = os.getenv("SERPAPI_KEY", "")
        self.google_flights_max_buscas = int(os.getenv("GOOGLE_FLIGHTS_MAX_BUSCAS", "30"))
        # teto de buscas da SerpApi por mês (o plano gratuito tem 250); dá para mudar em Configurações
        self.google_flights_limite_mes = int(os.getenv("GOOGLE_FLIGHTS_LIMITE_MES", "240"))

        # Leitor de grupos do WhatsApp (número da agência só para isso), dentro do próprio site
        self.whatsapp_leitura = _bool("WHATSAPP_LEITURA", True)
        self.whatsapp_sessao = os.getenv("WHATSAPP_SESSAO", str(BASE_DIR / "whatsapp-sessao.db"))

        # Leitura de imagens e textos de promoções por IA (Gemini, plano gratuito do Google AI Studio)
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.gemini_modelo = os.getenv("GEMINI_MODELO", "gemini-3.1-flash-lite").strip()
        self.gemini_max_por_dia = int(os.getenv("GEMINI_MAX_POR_DIA", "150"))
        self.gemini_max_por_rodada = int(os.getenv("GEMINI_MAX_POR_RODADA", "40"))
        self.imagens_dias = int(os.getenv("IMAGENS_DIAS", "30"))  # por quanto tempo a imagem da promoção fica guardada

        # Coleta automática dentro do próprio servidor web
        self.coleta_automatica = _bool("COLETA_AUTOMATICA", False)
        self.intervalo_telegram_min = int(os.getenv("INTERVALO_TELEGRAM_MIN", "20"))
        self.intervalo_google_horas = int(os.getenv("INTERVALO_GOOGLE_HORAS", "6"))
        self.intervalo_formulario_min = int(os.getenv("INTERVALO_FORMULARIO_MIN", "10"))
        self.intervalo_whatsapp_api_min = int(os.getenv("INTERVALO_WHATSAPP_API_MIN", "5"))  # envio automático (Meta)

        # Regras de negócio
        self.promo_validade_dias = int(os.getenv("PROMO_VALIDADE_DIAS", "5"))
        self.confianca_minima = float(os.getenv("CONFIANCA_MINIMA", "0.7"))


settings = Settings()
