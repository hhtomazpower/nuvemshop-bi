import logging
import os
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app import app, get_db_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scheduler")

SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "30"))

def get_connected_store_ids():
    """Retorna todos os store_id ativos da tabela connected_stores."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT store_id FROM connected_stores WHERE is_active = TRUE")
    store_ids = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return store_ids

def log_sync(store_id, entity, status, records_processed, records_upserted,
             error_message=None, started_at=None, finished_at=None):
    """Registra uma execução na tabela sync_logs."""
    duration_ms = None
    if started_at and finished_at:
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sync_logs
            (store_id, entity, status, records_processed, records_upserted,
             error_message, started_at, finished_at, duration_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (store_id, entity, status, records_processed, records_upserted,
          error_message, started_at, finished_at, duration_ms))
    conn.commit()
    cur.close()
    conn.close()

def run_sync_all():
    """Dispara o sync de todas as lojas ativas via test_client e audita em sync_logs."""
    store_ids = get_connected_store_ids()
    if not store_ids:
        logger.warning("Nenhuma loja ativa em connected_stores — nada a sincronizar.")
        return

    client = app.test_client()

    for store_id in store_ids:
        started_at = datetime.now(timezone.utc)
        logger.info("Iniciando sync da loja %s", store_id)

        try:
            resp = client.post(f"/api/sync/{store_id}")
            finished_at = datetime.now(timezone.utc)
            payload = resp.get_json() or {}

            if resp.status_code == 200:
                synced = payload.get("synced", {})
                errors = payload.get("errors", [])

                for entity, count in synced.items():
                    # CORREÇÃO 1: extrair a mensagem de erro específica da entidade
                    entity_error = None
                    status = "success"
                    for err in errors:
                        if entity in err:
                            status = "error"
                            entity_error = err  # ex: "orders: KeyError: 'payment_method'"
                            break

                    log_sync(store_id, entity, status, count, count,
                             error_message=entity_error,
                             started_at=started_at, finished_at=finished_at)

                # CORREÇÃO 2: logar erros no console (antes só logava "Sync OK")
                if errors:
                    logger.warning(
                        "Sync concluído com ERROS loja %s: %s | erros: %s",
                        store_id, synced, errors
                    )
                else:
                    logger.info("Sync OK loja %s: %s", store_id, synced)
            else:
                # Resposta não-200 — erro geral
                error_msg = str(payload.get("error", resp.status_code))
                log_sync(store_id, "all", "error", 0, 0,
                         error_message=error_msg,
                         started_at=started_at, finished_at=finished_at)
                logger.error("Falha no sync da loja %s: %s", store_id, error_msg)

        except Exception as e:
            finished_at = datetime.now(timezone.utc)
            log_sync(store_id, "all", "error", 0, 0, error_message=str(e),
                     started_at=started_at, finished_at=finished_at)
            logger.exception("Exceção no sync da loja %s: %s", store_id, e)

def main():
    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_sync_all,
        IntervalTrigger(minutes=SYNC_INTERVAL_MINUTES),
        id="sync_all_stores",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    logger.info("Scheduler iniciado — intervalo de %s min", SYNC_INTERVAL_MINUTES)
    scheduler.start()

if __name__ == "__main__":
    main()
