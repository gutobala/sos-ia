"""Sincronização diária da wiki, numa thread da API (como o aquecimento). Falha nunca derruba o serviço: vai para o log e para
apf.wiki_sync_execucao.

SOSIA_WIKI_SYNC_HORA: hora de Brasília (UTC-3, sem horário de verão desde 2019), padrão "03:00"; vazio ou "0" desliga.
"""
import datetime
import logging
import threading
import time

from .. import config

log = logging.getLogger("uvicorn.error.wiki")
BRASILIA = datetime.timezone(datetime.timedelta(hours=-3))
_ultimo = {}


def hora():
    v = (config.env("SOSIA_WIKI_SYNC_HORA", "03:00") or "").strip()
    if v in ("", "0"):
        return None
    h, _, m = v.partition(":")
    return int(h), int(m or 0)


def proxima(agora, hm):
    """Próximo horário hm (hora, minuto) de Brasília depois de `agora` (com fuso)."""
    local = agora.astimezone(BRASILIA)
    alvo = local.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if alvo <= local:
        alvo += datetime.timedelta(days=1)
    return alvo


def roda_uma():
    from . import sincroniza
    try:
        res = sincroniza.executa(aplicar=True, origem="agenda", log=log.info)
        rag = res.get("rag", {})
        log.info("wiki: novas %s, alteradas %s, removidas %s, vetores US$ %s, erros %s", len(rag.get("novas", [])),
                 len(rag.get("alteradas", [])), len(rag.get("removidas", [])), res.get("vetores", {}).get("custo_usd"), res["erros"])
        _limpa_caches()
        _ultimo.update(res)
    except Exception:  # noqa: BLE001
        log.exception("sincronização da wiki falhou")


def _limpa_caches():
    """Sistema que ganhou wiki e menus refeitos aparecem sem reiniciar a API."""
    try:
        from ..entrevista import motor
        for k in [k for k in motor._cache if isinstance(k, tuple) and k[0] == "wiki"]:
            motor._cache.pop(k, None)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..menus import catalogo
        catalogo._cache.clear()
    except Exception:  # noqa: BLE001
        pass


def _laco(hm):
    while True:
        agora = datetime.datetime.now(datetime.timezone.utc)
        alvo = proxima(agora, hm)
        log.info("sincronização da wiki agendada para %s (Brasília)", alvo.strftime("%d/%m %H:%M"))
        time.sleep(max(1.0, (alvo - agora).total_seconds()))
        roda_uma()


def inicia():
    """Chamado na subida da API (motor real). Thread daemon."""
    hm = hora()
    if config.MOCK or hm is None:
        return None
    th = threading.Thread(target=_laco, args=(hm,), name="sosia-wiki", daemon=True)
    th.start()
    return th
