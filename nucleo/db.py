"""Conexão com o banco do núcleo (database sosia, schema apf). DSN em SOSIA_DSN."""
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from . import config


def conectar(timeout=5):
    c = psycopg.connect(config.DSN, connect_timeout=timeout, row_factory=dict_row)
    c.execute("SET search_path TO apf, public")
    try:
        from pgvector.psycopg import register_vector
        register_vector(c)
    except Exception:  # noqa: BLE001  (extensão ausente num banco vazio)
        pass
    return c


@contextmanager
def db():
    c = conectar()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def ping():
    """True se o banco responde e tem o núcleo carregado (tabela de chamados)."""
    try:
        with psycopg.connect(config.DSN, connect_timeout=2) as c:
            return c.execute("SELECT to_regclass('apf.chamado') IS NOT NULL").fetchone()[0]
    except Exception:  # noqa: BLE001
        return False
