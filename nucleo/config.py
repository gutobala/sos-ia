"""Configuração do núcleo: tudo por variável de ambiente (arquivo .env opcional na raiz, nunca versionado)."""
import os
import re

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSAO = "0.1.0"


def _carrega_env():
    p = os.path.join(RAIZ, ".env")
    if os.path.exists(p):
        for linha in open(p, encoding="utf-8"):
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                v = re.split(r"\s+#", v, maxsplit=1)[0].strip().strip('"').strip("'")   # tira comentário no fim da linha
                os.environ.setdefault(k.strip(), v)


_carrega_env()


def _limpa(v):
    """Tira comentário de fim de linha. Vale também para o que chega do ambiente: o docker compose passa "VAR= # comentário" como
    o valor "# comentário"."""
    v = v.strip()
    if v.startswith("#"):
        return ""
    return re.split(r"\s+#", v, maxsplit=1)[0].strip()


def env(nome, padrao=None):
    v = os.environ.get(nome)
    if v is None:
        return padrao
    v = _limpa(v)
    return v if v != "" or padrao is None else padrao


MOCK = env("SOSIA_MOCK", "0") == "1"
# Token compartilhado SOS -> serviço (cabeçalho X-SOSIA-Token). No modo mock, sem token definido, vale "mock".
TOKEN = env("SOSIA_TOKEN") or ("mock" if MOCK else None)
DSN = env("SOSIA_DSN", "host=127.0.0.1 port=5433 dbname=sosia user=apf password=apf_local sslmode=disable gssencmode=disable")
LLM = env("SOSIA_LLM", "openai")            # openai (testes, chave pessoal) | azure (produção, contrato)
EMB = env("SOSIA_EMB", "azure")             # azure (text-embedding-3-large; padrão desde 05/11/2026) | e5 (CPU local)
# local: anonimiza antes do LLM e troca imagens pelo OCR anonimizado (obrigatório fora do contrato Azure)
# contrato: texto e imagens vão à Azure como vieram (exige aprovação da Segurança/LGPD)
MODO_PII = env("SOSIA_PII_MODO", "local")
# modelos por papel (nomes = deployments na Azure; na OpenAI, nomes de modelo)
MODELO_CHAT = env("SOSIA_MODELO_CHAT", "gpt-5.4-mini")
MODELO_RACIOCINIO = env("SOSIA_MODELO_RACIOCINIO", "gpt-5.4")
MODELO_PREVIA = env("SOSIA_MODELO_PREVIA", "gpt-4.1")   # prévia de PF: o validado no holdout (05/11/2026); 5.4 só com prompt readaptado
MODELO_ANONIMIZACAO = env("SOSIA_MODELO_ANONIMIZACAO", "gpt-5.4-mini")
MODELO_EMBEDDING = env("SOSIA_MODELO_EMBEDDING", "text-embedding-3-large")
REASONING_EFFORT = env("SOSIA_REASONING_EFFORT", "low")
MAX_TOKENS = int(env("SOSIA_MAX_TOKENS", "6000"))
for _nome, _v, _ok in (("SOSIA_PII_MODO", MODO_PII, ("local", "contrato")), ("SOSIA_LLM", LLM, ("openai", "azure")),
                       ("SOSIA_EMB", EMB, ("e5", "azure"))):
    if _v not in _ok:      # valor inválido nunca pode cair em "não anonimiza" por omissão
        raise RuntimeError(f"{_nome}={_v!r} inválido; use {' ou '.join(_ok)}")
if MODO_PII == "contrato" and LLM != "azure":
    raise RuntimeError("SOSIA_PII_MODO=contrato só é permitido com SOSIA_LLM=azure (o contrato cobre só a Azure)")
