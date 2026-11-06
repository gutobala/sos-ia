"""Cliente do LLM do núcleo: OpenAI (testes, chave pessoal) ou Azure OpenAI (produção, contrato), saída em JSON Schema estrito.

Modelo por papel (config.MODELO_CHAT / MODELO_RACIOCINIO / MODELO_ANONIMIZACAO). Imagens só no modo de PII "contrato"
(no modo local, pii.prepara troca a imagem pelo texto do OCR anonimizado antes de chegar aqui).
"""
import json
import os
import re
import time

from . import config, precos


class LLMIndisponivel(RuntimeError):
    pass


def disponivel():
    if config.LLM == "azure":
        return bool(os.environ.get("AZURE_OPENAI_API_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT"))
    return bool(os.environ.get("OPENAI_API_KEY"))


def cliente():
    if not disponivel():
        raise LLMIndisponivel(f"chave do provedor '{config.LLM}' não configurada")
    if config.LLM == "azure":
        from openai import AzureOpenAI
        return AzureOpenAI(azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"], api_key=os.environ["AZURE_OPENAI_API_KEY"],
                           api_version=os.environ.get("AZURE_OPENAI_API_VERSION") or "2025-04-01-preview")
    from openai import OpenAI
    return OpenAI()


def modelo(papel):
    return {"chat": config.MODELO_CHAT, "raciocinio": config.MODELO_RACIOCINIO, "previa": config.MODELO_PREVIA,
            "anonimizacao": config.MODELO_ANONIMIZACAO}.get(papel, papel)


def raciocinio(mod):
    """Modelos de raciocínio (série o, gerações 5/6): o limite de tokens inclui o raciocínio; podem recusar temperature/seed."""
    return bool(re.match(r"^(o\d|gpt-5|gpt-6)", mod)) and "chat" not in mod


def _conteudo(prompt, imagens):
    if not imagens:
        return prompt
    if config.MODO_PII != "contrato":
        raise LLMIndisponivel("imagens só podem ir ao LLM no modo de PII 'contrato'")
    partes = [{"type": "text", "text": prompt}]
    for im in imagens:
        partes.append({"type": "image_url", "image_url": {"url": f"data:{im['mime']};base64,{im['base64']}"}})
    return partes


def json_estruturado(prompt, schema, nome="resposta", papel="chat", imagens=None, temperatura=None, seed=None,
                     reasoning_effort=None, max_tokens=None):
    """Envia o prompt e devolve (dict, info{modelo, tokens_entrada, tokens_cache, tokens_saida, tokens_raciocinio, custo_usd, segundos})."""
    mod = modelo(papel)
    kw = dict(model=mod, messages=[{"role": "user", "content": _conteudo(prompt, imagens)}],
              response_format={"type": "json_schema", "json_schema": {"name": nome, "strict": True, "schema": schema}})
    if raciocinio(mod):
        kw["max_completion_tokens"] = max_tokens or config.MAX_TOKENS
        kw["reasoning_effort"] = reasoning_effort or config.REASONING_EFFORT
    else:
        kw["temperature"] = 0 if temperatura is None else temperatura
        kw["seed"] = 7 if seed is None else seed
    if temperatura is not None and raciocinio(mod):
        kw["temperature"] = temperatura          # alguns deployments 5.x aceitam (o ANIA usa 0,3); se recusar, sai abaixo
    cli = cliente()
    t0 = time.time()
    custo = 0.0
    while True:
        r = _cria(cli, kw)
        u = r.usage
        det_in = getattr(u, "prompt_tokens_details", None)
        cache = (getattr(det_in, "cached_tokens", 0) or 0) if det_in else 0
        c = precos.custo_usd(mod, u.prompt_tokens, u.completion_tokens, cache)
        custo = None if c is None or custo is None else custo + c
        # modelo de raciocínio que gastou o limite pensando devolve conteúdo vazio: repete com o dobro, até MAX_TOKENS_TETO
        lim = kw.get("max_completion_tokens")
        if r.choices[0].finish_reason == "length" and lim and lim < MAX_TOKENS_TETO:
            kw["max_completion_tokens"] = min(lim * 2, MAX_TOKENS_TETO)
            continue
        break
    msg = r.choices[0].message
    if getattr(msg, "refusal", None):
        raise LLMIndisponivel(f"o modelo recusou a solicitação: {msg.refusal}")
    if r.choices[0].finish_reason == "length" or not msg.content:
        raise LLMIndisponivel(f"resposta cortada pelo limite de tokens ({kw.get('max_completion_tokens')})")
    det_out = getattr(u, "completion_tokens_details", None)
    info = dict(modelo=mod, tokens_entrada=u.prompt_tokens, tokens_cache=cache, tokens_saida=u.completion_tokens,
                tokens_raciocinio=getattr(det_out, "reasoning_tokens", None) if det_out else None,
                custo_usd=custo, segundos=round(time.time() - t0, 1), max_tokens=kw.get("max_completion_tokens"))
    return json.loads(msg.content), info


MAX_TOKENS_TETO = 16000      # o mesmo teto do ANIA para o modelo de raciocínio


def _cria(cli, kw):
    for _ in range(5):                            # tira o parâmetro que o modelo disser que não aceita e tenta de novo
        try:
            return cli.chat.completions.create(**kw)
        except Exception as e:  # noqa: BLE001
            txt = str(e).lower()
            if not any(s in txt for s in ("unsupported", "not supported", "unrecognized", "does not support")):
                raise
            tirou = False
            for p in ("temperature", "seed", "reasoning_effort", "max_completion_tokens"):
                if p in kw and p in txt:
                    kw.pop(p)
                    tirou = True
            if not tirou:
                raise
    raise LLMIndisponivel("parâmetros recusados pelo modelo")
