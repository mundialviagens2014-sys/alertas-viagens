"""Execução das coletas: manual (botão no site / linha de comando) ou agendada."""
import logging
import threading
from datetime import datetime, timezone

from .config import settings
from .database import SessionLocal

log = logging.getLogger(__name__)
_trava = threading.Lock()
ultima_execucao: dict[str, dict] = {}


def executar(fonte: str, reler: bool = False, so_canal: int | None = None) -> dict:
    """Roda um coletor ('telegram' ou 'google_flights') e devolve o resumo."""
    from . import formulario
    from .collectors import google_flights, site_agencia, telegram

    coletor = {
        "site": site_agencia.coletar,
        "telegram": lambda db: telegram.coletar(db, reler=reler, so_canal=so_canal),
        "google_flights": google_flights.coletar,
        "formulario": formulario.sincronizar,
    }[fonte]
    if not _trava.acquire(blocking=False):
        return {"erro": "Já existe uma coleta em andamento. Tente de novo em instantes."}
    db = SessionLocal()
    try:
        resumo = coletor(db)
    except Exception as erro:  # mostra o motivo na tela em vez de derrubar o site
        log.exception("Falha na coleta %s", fonte)
        resumo = {"erro": str(erro)}
    finally:
        db.close()
        _trava.release()
    resumo["em"] = datetime.now().strftime("%d/%m %H:%M")
    ultima_execucao[fonte] = resumo
    return resumo


def listar_grupos_telegram() -> list[dict]:
    """Grupos da conta do Telegram (usa a mesma trava das coletas: a sessão não aceita dois acessos)."""
    from .collectors import telegram

    if not _trava.acquire(timeout=60):
        raise RuntimeError("Uma coleta está em andamento. Tente de novo em instantes.")
    try:
        return telegram.listar_grupos()
    finally:
        _trava.release()


def _sincronizar_formulario_se_configurado() -> None:
    from .formulario import configurado

    with SessionLocal() as db:
        if not configurado(db):
            return
    executar("formulario")


def _atualizar_saldos_do_dia() -> int:
    from .mvo.contas import recalcular_vencidos
    from .servicos import limpar_imagens_antigas

    with SessionLocal() as db:
        refeitas = recalcular_vencidos(db)
        apagadas = limpar_imagens_antigas(db)
    if refeitas:
        log.info("Saldos atualizados com lançamentos do dia: %s conta(s)", refeitas)
    if apagadas:
        log.info("Imagens de promoções antigas apagadas: %s", apagadas)
    return refeitas


def _enviar_whatsapp_automatico() -> None:
    """Envio automático pela API da Meta (quando ligado em Configurações → WhatsApp automático)."""
    from . import whatsapp_api

    if not settings.site_url:
        return  # sem SITE_URL não dá para montar o link da página do cliente que vai na mensagem
    with SessionLocal() as db:
        if not whatsapp_api.automatico_ligado(db):
            return
        try:
            resumo = whatsapp_api.enviar_pendentes(db, settings.site_url)
        except Exception:
            log.exception("Falha no envio automático pelo WhatsApp")
            return
    if resumo["promocoes"] or resumo["confirmacoes"] or resumo["falhas"]:
        log.info("WhatsApp automático: %s promoções, %s confirmações, %s falhas, %s para depois",
                 resumo["promocoes"], resumo["confirmacoes"], resumo["falhas"], resumo["pulados"])
    ultima_execucao["whatsapp_api"] = {**resumo, "em": datetime.now().strftime("%d/%m %H:%M")}


def _classificar_respostas_whatsapp() -> None:
    """CRM: dá intenção (interesse, dúvida, recusa...) às respostas novas dos clientes, pela IA quando houver cota."""
    from . import crm, ia

    with SessionLocal() as db:
        try:
            resumo = crm.classificar_pendentes(db, limite=10, usar_ia=ia.configurada())
        except Exception:
            log.exception("Falha ao classificar respostas do WhatsApp")
            return
    if resumo["ia"] or resumo["regras"]:
        log.info("Respostas do WhatsApp classificadas: %s pelas regras, %s pela IA", resumo["regras"], resumo["ia"])


def iniciar_agendador():
    """Agenda as coletas dentro do processo do site (COLETA_AUTOMATICA=true)."""
    from apscheduler.schedulers.background import BackgroundScheduler

    agendador = BackgroundScheduler(timezone="America/Sao_Paulo")
    agora = datetime.now(timezone.utc)  # com fuso: "agora" sem fuso era lido como horário de Brasília (3h depois)
    if settings.telegram_api_id:
        agendador.add_job(executar, "interval", args=["telegram"], minutes=settings.intervalo_telegram_min,
                          next_run_time=agora, id="telegram", max_instances=1, coalesce=True)
    if settings.serpapi_key:
        agendador.add_job(executar, "interval", args=["google_flights"], hours=settings.intervalo_google_horas,
                          id="google_flights", max_instances=1, coalesce=True)
    if settings.site_firebase_projeto:
        agendador.add_job(executar, "interval", args=["site"], minutes=settings.intervalo_site_min,
                          next_run_time=agora, id="site", max_instances=1, coalesce=True)
    agendador.add_job(_sincronizar_formulario_se_configurado, "interval", minutes=settings.intervalo_formulario_min,
                      next_run_time=agora, id="formulario", max_instances=1, coalesce=True)
    agendador.add_job(_enviar_whatsapp_automatico, "interval", minutes=settings.intervalo_whatsapp_api_min,
                      id="whatsapp_api", max_instances=1, coalesce=True)
    agendador.add_job(_classificar_respostas_whatsapp, "interval", minutes=5, id="crm_intencoes", max_instances=1,
                      coalesce=True)
    # pontos com data futura (ex.: bônus que cai no dia 15) entram no saldo na data
    agendador.add_job(_atualizar_saldos_do_dia, "cron", hour=0, minute=5, id="saldos", max_instances=1, coalesce=True)
    agendador.start()
    log.info("Coleta automática ligada: %s", [j.id for j in agendador.get_jobs()])
    return agendador
