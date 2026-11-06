"""Complexidade pelos campos descritos no texto (cópia de PRE-PROJETO/app/complexidade_campos.estima): uma chamada a mais pede o
nº de TD e AR/TR de cada função; complexidade pelas tabelas IFPUG; função sem informação fica com o peso NESMA."""
import os

from .. import config, llm, pii
from . import regras

PROMPT = os.path.join(config.RAIZ, "prompts", "complexidade_campos_v1.md")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["funcoes"], "properties": {"funcoes": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["indice", "td", "ar", "base", "campos_contados"],
    "properties": {"indice": {"type": "integer"}, "td": {"type": "integer"}, "ar": {"type": "integer"},
                   "base": {"type": "string", "enum": ["TEXTO", "PARCIAL", "SEM_INFO"]},
                   "campos_contados": {"type": "string", "description": "resumo curto dos campos e arquivos contados (até 30 palavras)"}}}}}}


def estima(conn, texto, funcs, papel="previa"):
    """Devolve (pf, linhas, custo)."""
    vale = [f for f in funcs if f["tipo_ifpug"] in regras.PESOS and f["movimento"] not in ("SEM_ALTERACAO", "NAO_MENSURAVEL")]
    mi = regras.PF_MI * regras.n_mi(funcs)
    if not vale:
        return mi, [], 0.0
    lista = "\n".join(f"{i}. {f['tipo_ifpug']} {f['movimento']} {f['nome_funcao']}" for i, f in enumerate(vale, 1))
    texto_llm = pii.anonimizar(texto, conn) if config.MODO_PII == "local" else texto
    p = open(PROMPT, encoding="utf-8").read().replace("{{TEXTO}}", texto_llm).replace("{{FUNCOES}}", lista)
    resp, inf = llm.json_estruturado(p, SCHEMA, "complexidade", papel, temperatura=0, seed=7)
    por = {r["indice"]: r for r in resp["funcoes"]}
    pf, linhas = mi, []
    for i, f in enumerate(vale, 1):
        r = por.get(i)
        fator = regras.FATOR.get(f["movimento"], 0.75)
        if not r or r["base"] == "SEM_INFO":
            peso, cx = regras.PESO[f["tipo_ifpug"]], "NESMA"
        else:
            k = regras.complexidade(f["tipo_ifpug"], r["td"], r["ar"])
            peso, cx = regras.PESOS[f["tipo_ifpug"]][k], regras.CX[k]
        pf += peso * fator
        linhas.append(dict(f, td=r and r["td"], ar=r and r["ar"], base=r and r["base"], cx=cx, pf=peso * fator,
                           campos=r and r["campos_contados"]))
    return pf, linhas, inf.get("custo_usd") or 0.0
