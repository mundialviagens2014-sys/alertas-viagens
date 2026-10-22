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
    from .collectors import google_flights, telegram

    coletor = {
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
    from .formulario import CHAVE_URL
    from .servicos import ler_estado

    with SessionLocal() as db:
        if not ler_estado(db, CHAVE_URL):
            return
    executar("formulario")


def _atualizar_saldos_do_dia() -> int:
    from .mvo.contas import recalcular_vencidos

    with SessionLocal() as db:
        refeitas = recalcular_vencidos(db)
    if refeitas:
        log.info("Saldos atualizados com lançamentos do dia: %s conta(s)", refeitas)
    return refeitas


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
    agendador.add_job(_sincronizar_formulario_se_configurado, "interval", minutes=settings.intervalo_formulario_min,
                      next_run_time=agora, id="formulario", max_instances=1, coalesce=True)
    # pontos com data futura (ex.: bônus que cai no dia 15) entram no saldo na data
    agendador.add_job(_atualizar_saldos_do_dia, "cron", hour=0, minute=5, id="saldos", max_instances=1, coalesce=True)
    agendador.start()
    log.info("Coleta automática ligada: %s", [j.id for j in agendador.get_jobs()])
    return agendador
