"""Conexões com os dois bancos do serviço.

  núcleo     database sosia, schema apf (SOSIA_DSN): RAG, catálogo, histórico. Restaurável do dump; descartável.
  aplicação  database sosia_app, schema app (SOSIA_APP_DSN): entrevistas, Escopo do Pedido confirmado e vínculo com os
             chamados do SOS. Instância e volume próprios, com backup: é dado de produção.
"""
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


def conectar_app(timeout=5):
    """Conexão com o banco da aplicação (schema app, criado se faltar)."""
    c = psycopg.connect(config.APP_DSN, connect_timeout=timeout, row_factory=dict_row)
    c.execute("CREATE SCHEMA IF NOT EXISTS app")
    c.execute("SET search_path TO app, public")
    c.commit()
    return c


@contextmanager
def db_app():
    c = conectar_app()
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


def ping_app():
    """True se o banco da aplicação responde."""
    try:
        with psycopg.connect(config.APP_DSN, connect_timeout=2) as c:
            return c.execute("SELECT 1").fetchone()[0] == 1
    except Exception:  # noqa: BLE001
        return False
