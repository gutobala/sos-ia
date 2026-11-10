"""Motor REAL da entrevista de abertura (Fase 1): RAG do sistema + LLM (papel "chat") + validação determinística dos slots.

Fluxo (contrato em modelos.py; plano do SOS em leia-o-sistema-de-floating-shell.md):
  criar      pii.prepara (modo local: anonimiza e troca imagem por OCR; modo contrato: passa direto) → RAG (wiki do sistema,
             regras de negócio e catálogo de funções) → gatilhos ocultos (config/gatilhos.yaml) → LLM (prompts/entrevista_v1.md)
             → slots revalidados pelo roteiro, perguntas filtradas (só slot vazio ou gatilho, máx. 5), regra documentada só se o
             trecho veio do RAG → grava no banco (apf.entrevista / entrevista_evento).
  responder  respostas entram nos slots (anonimizadas, menos no modo contrato) → nova rodada do LLM enquanto faltar slot e
             rodada ≤ 2. Depois disso (1.3.0), se ainda faltar item obrigatório, rodada de complemento: só as perguntas fixas do
             que falta (roteiros.perguntas_complemento), sem LLM e sem limite; quando não falta nada, pronto=true.
  regra      (contrato 1.1.0) a regra documentada NÃO muda o tipo sozinha: o segmento 1 fica CORRETIVO até o usuário decidir
             (POST /regra). Aceita → vira EVOLUTIVO_EXISTENTE e ganha mais uma rodada de perguntas; recusa → segue corretivo e o
             comentário vai para as dúvidas. Enquanto não decidir, `faltando` traz o slot "decisao_regra".
  contrato   LLM (prompts/contrato_v1.md) escreve título, objetivo, requisitos com critério de aceite, fora de escopo, integrações
             e dúvidas; `faltando` e `pode_confirmar` são sempre da regra determinística (nunca do LLM).
  confirmação (contrato 1.2.0; regras em confirmacao.py) o SOS manda o Escopo do Pedido editado → grupos (um chamado por tipo)
             e versões v0/v1 → PENDENTE; depois informa os CD_SOLICITACAO → CONFIRMADA.
Dois bancos: o do núcleo (RAG, só leitura aqui) e o da aplicação (entrevistas; db.conectar_app). Toda gravação vai para o segundo.
Sem LLM (chave ausente, erro ou recusa): roteiro fixo com os mesmos slots e analise_ia=false — o SOS segue funcionando.
O LLM nunca decide sozinho o que bloqueia: ele preenche e pergunta; quem diz se o slot está cheio é roteiros.revalida.
"""
import datetime
import os
import re
import threading
import time
import uuid

import psycopg
import yaml

from .. import config, embeddings, llm, pii
from .. import db as dbm
from ..menus import catalogo as menus_catalogo
from ..rag import busca, catalogo
from . import armazem, confirmacao as cf, doc_html, roteiros
from .mock import SLOT_PRINCIPAL
from .modelos import (CandidatoFuncionalidade, ChamadoGerado, Confirmacao, Contrato, DecisaoRegra, DecisaoRegraDocumentada,
                      Documentacao, Documento, EntrevistaPendente, EscopoChamado, EscopoConfirmado, EscopoEditado, EstadoEntrevista,
                      Intencao, Menus, NovaEntrevista, Origem, Pergunta, RegraDocumentada, RequisitoContrato, Respostas,
                      SecaoDocumentacao, Segmento, TipoRequisito, TipoSegmento, VersaoEscopo, VinculoChamados)

MAX_RODADAS = 2
MAX_PERGUNTAS = 5
MAX_SEGMENTOS = 3
MAX_CANDIDATOS = 5      # funcionalidades mostradas (de 30 da fusão, reordenadas pelo LLM)
PROMPT_ENTREVISTA = os.path.join(config.RAIZ, "prompts", "entrevista_v1.md")
PROMPT_CONTRATO = os.path.join(config.RAIZ, "prompts", "contrato_v1.md")
ARQ_GATILHOS = os.path.join(config.RAIZ, "config", "gatilhos.yaml")
TIPOS_REGRA = ("regras_de_negocio", "mensagens", "caso_de_uso")
TIPOS_DOCUMENTO = ("caso_de_uso", "user_story", "regras_de_negocio")     # 1.5.0: o que o usuário pode ler no SOS
MAX_DOCUMENTOS = 5           # por ponto de partida (1.5.1)
MAX_TRECHOS_PROMPT = 12      # com 1 ponto a busca dá até 8 (5 + 3 regras); com vários, o rodízio corta aqui
NOME_INTENCAO = {Intencao.PAROU_DE_FUNCIONAR: "Algo parou de funcionar", Intencao.MUDAR_EXISTENTE: "Quero mudar algo que já existe",
                 Intencao.ALGO_NOVO: "Preciso de algo novo"}
RETENCAO_DIAS = int(config.env("SOSIA_RETENCAO_DIAS", "0")) or None

_TIPOS = [t.value for t in TipoSegmento]
_ORIGENS = [o.value for o in Origem]
SCHEMA_ENTREVISTA = {
    "type": "object", "additionalProperties": False,
    "required": ["segmentos", "perguntas", "regra_documentada_trecho", "regra_documentada_orientacao", "nota", "dicas_medidor"],
    "properties": {
        "segmentos": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["tipo", "resumo", "ponto", "slots"],
            "properties": {"tipo": {"type": "string", "enum": _TIPOS}, "resumo": {"type": "string"}, "ponto": {"type": "integer"},
                           "slots": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                "required": ["codigo", "valor"],
                                                                "properties": {"codigo": {"type": "string"}, "valor": {"type": "string"}}}}}}},
        "perguntas": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["segmento", "slot", "ponto", "texto", "dica", "opcoes"],
            "properties": {"segmento": {"type": "integer"}, "slot": {"type": "string"}, "ponto": {"type": "integer"},
                           "texto": {"type": "string"},
                           "dica": {"type": "string"}, "opcoes": {"type": "array", "items": {"type": "string"}}}}},
        "regra_documentada_trecho": {"type": "integer"},
        "regra_documentada_orientacao": {"type": "string"},
        "nota": {"type": "integer"},
        "dicas_medidor": {"type": "array", "items": {"type": "string"}}}}
SCHEMA_CONTRATO = {
    "type": "object", "additionalProperties": False,
    "required": ["titulo", "objetivo", "requisitos", "fora_escopo", "duvidas", "integracoes"],
    "properties": {
        "titulo": {"type": "string"}, "objetivo": {"type": "string"},
        "requisitos": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["segmento", "tipo", "descricao", "criterio_aceite", "origem"],
            "properties": {"segmento": {"type": "integer"}, "tipo": {"type": "string", "enum": [t.value for t in TipoRequisito]},
                           "descricao": {"type": "string"}, "criterio_aceite": {"type": "string"},
                           "origem": {"type": "string", "enum": _ORIGENS}}}},
        "fora_escopo": {"type": "array", "items": {"type": "string"}},
        "duvidas": {"type": "array", "items": {"type": "string"}},
        "integracoes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["sistema", "sentido", "descricao", "origem"],
            "properties": {"sistema": {"type": "string"}, "sentido": {"type": "string", "enum": ["BUSCA", "GRAVA", "AMBOS"]},
                           "descricao": {"type": "string"}, "origem": {"type": "string", "enum": _ORIGENS}}}}}}
MAX_OPCOES = 4          # opções de resposta da IA por pergunta (+ "Não sei informar", posto pela aplicação)
MAX_CAR_OPCAO = 80


# ---------------------------------------------------------------- gatilhos e sistema
_cache = {}


def gatilhos():
    if "gat" not in _cache:
        itens = yaml.safe_load(open(ARQ_GATILHOS, encoding="utf-8")) or []
        _cache["gat"] = [dict(g, rx=re.compile(g["padrao"], re.I)) for g in itens]
    return _cache["gat"]


def detecta_gatilhos(texto):
    """Gatilhos ocultos no texto. "não sei" / "sei lá" saem antes: o "sei" casava com o gatilho de integração (sistema SEI)."""
    t = roteiros.sem_nao_sei(texto)
    return [g["codigo"] for g in gatilhos() if g["rx"].search(t)]


def gatilhos_da_sustentacao():
    """Gatilhos que não viram pergunta ao usuário (`para: sustentacao` no gatilhos.yaml; 1.5.3: o de integração)."""
    return {g["codigo"] for g in gatilhos() if g.get("para") == "sustentacao"}


def termos_integracao(texto):
    """Os sistemas que o usuário citou (palavras do gatilho de integração), na ordem em que aparecem."""
    g = next((g for g in gatilhos() if g["codigo"] == "integracao"), None)
    return [m.group(0) for m in g["rx"].finditer(roteiros.sem_nao_sei(texto))] if g else []


def texto_do_usuario(est):
    return est["texto"] + "\n" + "\n".join(r["resposta"] for r in est["respostas"])


def resolve_sistema(conn, sigla):
    """(sistema_id, chave da wiki) pela sigla do SOS (ou alias). Chave da wiki = sigla minúscula com '_' no lugar de '-'."""
    r = conn.execute("""SELECT id, sigla FROM apf.sistema WHERE upper(sigla) = upper(%s)
                        UNION ALL SELECT s.id, s.sigla FROM apf.sistema_alias a JOIN apf.sistema s ON s.id = a.sistema_id
                        WHERE upper(a.alias) = upper(%s) LIMIT 1""", (sigla, sigla)).fetchone()
    base = (r["sigla"] if r else sigla).lower().replace("-", "_").replace(" ", "_")
    chave = base if _tem_wiki(conn, base) else None
    return (r["id"] if r else None), chave


def _tem_wiki(conn, chave):
    k = ("wiki", chave)
    if k not in _cache:
        _cache[k] = conn.execute("SELECT EXISTS (SELECT 1 FROM apf.conhecimento_trecho WHERE %s = ANY(sistemas_acesso))",
                                 (chave,)).fetchone()["exists"]
    return _cache[k]


# ---------------------------------------------------------------- RAG
_medicao = threading.local()     # segundos por etapa da última rag() desta thread (vai no evento "tempos" da criação)


def tempos_rag():
    return dict(getattr(_medicao, "tempos", {}))


def rag(conn, intencao, sistema_id, wiki, texto, com_catalogo=True):
    """(docs, candidatos). docs = trechos numerados da wiki para o prompt (híbrida + regras de negócio no corretivo).
    Mede o vetor da consulta (API de embeddings), a busca na wiki e o catálogo em `tempos_rag()`.
    `com_catalogo=False` (1.5.1, do 2º ponto de partida em diante): só a wiki; os candidatos vêm da busca do 1º ponto."""
    docs, cand = [], []
    q = texto[:4000]
    tempos, t = {}, time.time()
    _medicao.tempos = tempos
    if not com_catalogo:
        sistema_id = None
    if wiki or (sistema_id and intencao != Intencao.ALGO_NOVO):
        embeddings.consulta_sql(q)                       # o vetor fica em cache: busca e catálogo não chamam a API de novo
        tempos["embeddings_s"], t = round(time.time() - t, 2), time.time()
    if wiki:
        for x in busca.busca(conn, q, wiki, n=5, incluir_teste=False, expandir=False, max_por_pagina=1)["resultados"]:
            docs.append(dict(pagina=x["origem_dokuwiki"], titulo=x["titulo_pagina"], secao=x["secao"], tipo=x["tipo"],
                             texto=(x["texto"] or "")[:700], cos=round(float(x["cos"] or 0), 3)))
        if intencao == Intencao.PAROU_DE_FUNCIONAR:      # a busca híbrida rebaixa RN/mensagens; aqui elas são o que interessa
            col = embeddings.coluna_rag()
            vistos = {d["pagina"] + "|" + (d["secao"] or "") for d in docs}
            for x in conn.execute(
                    f"""SELECT origem_dokuwiki, titulo_pagina, secao, tipo, texto, 1 - ({col} <=> %(v)s::vector) cos
                        FROM apf.conhecimento_trecho WHERE {col} IS NOT NULL AND %(s)s = ANY(sistemas_acesso)
                          AND tipo IN ('regras_de_negocio', 'mensagens')
                        ORDER BY {col} <=> %(v)s::vector LIMIT 3""", dict(v=embeddings.consulta_sql(q), s=wiki)).fetchall():
                if x["origem_dokuwiki"] + "|" + (x["secao"] or "") not in vistos:
                    docs.append(dict(pagina=x["origem_dokuwiki"], titulo=x["titulo_pagina"], secao=x["secao"], tipo=x["tipo"],
                                     texto=(x["texto"] or "")[:700], cos=round(float(x["cos"]), 3)))
        tempos["wiki_s"], t = round(time.time() - t, 2), time.time()
    if sistema_id and intencao != Intencao.ALGO_NOVO:
        for c in catalogo.candidatos(conn, sistema_id, datetime.date.today() + datetime.timedelta(days=1), [q]):
            cand.append(dict(id=c["id"], nome=c["nome"], tipo_ifpug=c["tipo"], modulo=c["modulo"], similaridade=c["similaridade"],
                             texto=c["texto"]))
        tempos["catalogo_s"] = round(time.time() - t, 2)
    return docs, cand


def riscado(d):
    """Trecho revogado na wiki: seção riscada (`~~RN.189 …~~`) ou texto inteiro entre `~~ ~~`. Não vai ao prompt: o LLM apontava
    regra revogada como regra documentada (laboratório 1.3.2, RN151 do GAI). Risco parcial dentro do texto não conta."""
    t = (d.get("texto") or "").strip()
    return "~~" in (d.get("secao") or "") or (len(t) > 4 and t.startswith("~~") and t.endswith("~~"))


def sem_riscados(docs):
    return [d for d in docs if not riscado(d)]


def documentos(docs):
    """Documentação relacionada (1.5.0): dos trechos do RAG (já sem os revogados), só casos de uso, user stories e regras de
    negócio, uma vez por página, na ordem da busca, até 5. 1.5.1: até 5 por ponto de partida, agrupados pelo ponto (a página
    fica no ponto que a trouxe primeiro, no rodízio de junta_docs)."""
    out, vistos, por_ponto = [], set(), {}
    for d in docs:
        k = d.get("ponto")
        if d["tipo"] in TIPOS_DOCUMENTO and d["pagina"] not in vistos and por_ponto.get(k, 0) < MAX_DOCUMENTOS:
            vistos.add(d["pagina"])
            por_ponto[k] = por_ponto.get(k, 0) + 1
            out.append(Documento(pagina=d["pagina"], titulo=titulo_documento(d), tipo=d["tipo"], secao=d["secao"] or None,
                                 similaridade=d["cos"], ponto=k))
    # 1.5.2: dentro de cada ponto, casos de uso e user stories antes das regras (a página geral de regras é do sistema todo)
    return sorted(out, key=lambda x: (x.ponto or 0, x.tipo == "regras_de_negocio"))


def titulo_documento(d):
    """Título para o usuário. Na página de regras de negócio (uma só para o sistema todo, "gai - regras de negocio"), o nome da
    regra que casou: "Regras de negócio: RN99. Cálculo para 1°, 2° e 3° Edital"."""
    if d["tipo"] == "regras_de_negocio" and d.get("secao"):
        return f"Regras de negócio: {d['secao'].split(' > ')[-1].strip()}"
    return d["titulo"] or d["pagina"]


MAX_SECOES_REGRA = 10


def secoes_da_regra(secoes, casadas):
    """Seções de uma página de regras de negócio a mostrar (1.5.2): as que a busca trouxe nesta entrevista, com as regras-mãe
    (prefixo do caminho da seção) e as sub-regras, na ordem da página, até 10. Sem seção casada, None (a página inteira)."""
    casadas = [c for c in casadas if c]
    if not casadas or not any(s in casadas for s in secoes):
        return None
    def vale(s):
        return any(s == c or s.startswith(c + " > ") or (c.startswith(s + " > ") and s.count(" > ") >= 2) for c in casadas)
    manter = [i for i, s in enumerate(secoes) if vale(s)]
    return manter[:MAX_SECOES_REGRA]


def pontos(est):
    """Pontos de partida do estado (1.5.1: lista); entrevista da 1.5.0 guardava um só em "ponto"."""
    return est.get("pontos") or ([est["ponto"]] if est.get("ponto") else [])


def consultas_rag(est):
    """[(ponto, texto da busca)]: uma busca por ponto, com o caminho do menu antes do relato (as páginas da wiki citam o menu);
    o relato sozinho só quando não há ponto. `ponto` é o número (1 = o primeiro) ou None."""
    ps = pontos(est)
    if not ps:
        return [(None, est["texto"])]
    return [(i, f"Menu: {p['caminho']}.\n{est['texto']}") for i, p in enumerate(ps, 1)]


def junta_docs(listas):
    """Junta os trechos das buscas por ponto em rodízio (1º de cada ponto, depois o 2º…), sem repetir página+seção, e marca o
    ponto de cada trecho. Com uma busca só, a ordem é a dela."""
    out, vistos = [], set()
    for i in range(max((len(d) for _, d in listas), default=0)):
        for k, docs in listas:
            if i < len(docs):
                d = docs[i]
                chave = d["pagina"] + "|" + (d.get("secao") or "")
                if chave not in vistos:
                    vistos.add(chave)
                    out.append(dict(d, ponto=k))
    return out


def docs_prompt(est):
    """Trechos que vão ao prompt (numerados); o índice da regra documentada se refere a esta lista."""
    return est["docs"][:MAX_TRECHOS_PROMPT]


def valor_ponto(est, tipo, ponto=None):
    """(slot, valor) que o(s) ponto(s) de partida preenche(m) num segmento deste tipo, ou (None, None). `ponto`: o do segmento
    (só aquele caminho); sem ele, todos os caminhos unidos por "; "."""
    ps = pontos(est)
    slot = roteiros.slot_do_ponto(tipo) if ps else None
    if not slot:
        return None, None
    if ponto and 1 <= ponto <= len(ps):
        return slot, ps[ponto - 1]["valor"]
    return slot, roteiros.valor_dos_pontos([p["valor"] for p in ps])


def separa_caminhos(texto, est):
    """1.5.2: o LLM juntava os caminhos de pontos diferentes como se fossem uma tela só ("Em Venda Direta > Edital … > Tela de
    impressão, o que você faz…", teste do SOS de 10/11). Onde um caminho é seguido de " > " e de outro, troca por "; "."""
    cams = [p["caminho"] for p in pontos(est)]
    if len(cams) < 2 or not texto:
        return texto
    antes = None
    while antes != texto:
        antes = texto
        for a in cams:
            for b in cams:
                if a != b:
                    texto = texto.replace(f"{a} > {b}", f"{a}; {b}")
    return texto


def _fmt_ponto(est):
    ps = pontos(est)
    if not ps:
        return "(não informado: pergunte onde, se faltar)"
    lista = "\n".join(f'{i}. "{p["caminho"]}"' for i, p in enumerate(ps, 1))
    if Intencao(est["intencao"]) == Intencao.ALGO_NOVO:
        return (f"{lista}\n— é ONDE FICARIA o que o usuário quer (não é uma tela que já faz isso). Use como contexto; não preencha "
                "nenhum item só com ele.")
    txt = (f"{lista}\n— o usuário já indicou por onde entra no sistema; o item onde_ocorre (corretivo) ou funcionalidade "
           "(evolutivo) já está preenchido com " + ("ele" if len(ps) == 1 else "os caminhos") + ". NÃO pergunte onde é. Pergunte o "
           "que acontece a partir dali (corretivo) ou o que muda nessa tela (evolutivo), citando o caminho.")
    if len(ps) > 1:
        txt += ("\nCada número é uma TELA DIFERENTE: nunca junte dois caminhos com \">\" como se fossem um só. Numa pergunta que vale "
                "para mais de uma tela, cite os caminhos separados por \"; \" (ex.: \"Em A; B, o que você faz…\").")
        txt += ("\nCom mais de um ponto: o que muda é perguntado POR PONTO (uma pergunta por caminho, com `ponto` = o número dele). "
                "Se o relato deixar claro que cada ponto é uma mudança à parte, você pode criar um segmento por ponto, com `ponto` "
                "= o número; senão, use `ponto` 0 (o segmento vale para todos).")
    return txt


# ---------------------------------------------------------------- montagem do prompt
def _fmt_segs(segs):
    if not segs:
        return "(vazio)"
    out = []
    for s in segs:
        out.append(f"Segmento {s.n} ({s.tipo.value})" + (f" [ponto {s.ponto}]" if s.ponto else "") + f": {s.resumo}")
        out += [f"  - {x.codigo}: {x.valor or ''}" for x in s.slots]
    return "\n".join(out)


def _fmt_respostas(est):
    return "\n".join(f"- [{r['slot']}]" + (f" (ponto {r['ponto']})" if r.get("ponto") else "") + f" {r['pergunta']} → {r['resposta']}"
                     for r in est["respostas"]) or "(nenhuma)"


def _fmt_decisao(est):
    r, d = est.get("regra"), est.get("decisao")
    if not r:
        return "(nenhuma regra documentada mostrada ao usuário)"
    nome = r["secao"] or r["pagina"]
    if d == DecisaoRegra.ACEITA.value:
        return (f'O usuário CONFIRMOU que o sistema segue a regra documentada "{nome}" e quer mudá-la. O segmento 1 agora é '
                "EVOLUTIVO_EXISTENTE: preencha os itens de evolutivo com o que já foi dito e pergunte o que falta (em especial o que "
                "deve mudar na regra e um exemplo). Não volte a indicar regra documentada (use 0).")
    if d == DecisaoRegra.RECUSADA.value:
        return (f'O usuário disse que o problema NÃO é a regra "{nome}" (é defeito). Mantenha o segmento 1 CORRETIVO e não volte '
                "a indicar regra documentada (use 0).")
    return (f'A regra "{nome}" foi mostrada ao usuário e ele ainda não decidiu se é ela. Mantenha o segmento 1 CORRETIVO; '
            "não volte a indicar regra documentada (use 0).")


def _pendentes(est):
    """Gatilhos detectados e ainda não perguntados. 1.5.3: o de integração não é perguntado ao usuário (a Sustentação responde;
    vira dúvida no contrato)."""
    fora = set(est["gatilhos_feitos"]) | gatilhos_da_sustentacao()
    return [c for c in detecta_gatilhos(texto_do_usuario(est)) if c not in fora]


def prompt_entrevista(est, segs):
    pend = _pendentes(est)
    gat = {g["codigo"]: g for g in gatilhos()}
    cand = "\n".join(f"{i}. {c['nome']}" + (f" ({c['modulo']})" if c.get("modulo") else "") for i, c in enumerate(est["candidatos"], 1))
    varios = len(pontos(est)) > 1
    docs = "\n\n".join(f"[{i}] ({d['tipo']}) {d['titulo']} / {d['secao'] or ''}"
                       + (f" (ponto {d['ponto']})" if varios and d.get("ponto") else "") + f"\n{d['texto']}"
                       for i, d in enumerate(docs_prompt(est), 1))
    return (open(PROMPT_ENTREVISTA, encoding="utf-8").read()
            .replace("{{SISTEMA}}", est["sigla"]).replace("{{INTENCAO}}", NOME_INTENCAO[Intencao(est["intencao"])])
            .replace("{{RODADA}}", str(est["rodada"])).replace("{{RELATO}}", est["texto"]).replace("{{PONTO_PARTIDA}}", _fmt_ponto(est))
            .replace("{{RESPOSTAS}}", _fmt_respostas(est)).replace("{{SLOTS_ATUAIS}}", _fmt_segs(segs))
            .replace("{{DECISAO_REGRA}}", _fmt_decisao(est))
            .replace("{{CANDIDATOS}}", cand or "(nenhuma)").replace("{{DOCUMENTACAO}}", docs or "(nenhum trecho encontrado)")
            .replace("{{GATILHOS}}", "\n".join(f"- {c}: {gat[c]['descricao']}. Pergunta sugerida: {gat[c]['pergunta']}" for c in pend)
                     or "(nenhum)")), pend


# ---------------------------------------------------------------- pós-processamento determinístico
def _segs(est):
    """Segmentos do estado, revalidados (tira os itens que saíram do roteiro numa entrevista começada antes)."""
    return [roteiros.revalida(Segmento.model_validate(s)) for s in est["segs"]]


def aplica(est, resp, pendentes=()):
    """Aplica a resposta do LLM ao estado sem confiar nele para o que bloqueia. Devolve a lista de segmentos final.
    `pendentes`: gatilhos mostrados ao LLM nesta rodada; pergunta "gatilho" só vale se houver algum (no máximo uma por gatilho)."""
    # regra documentada: só um trecho que o RAG trouxe, de tipo regra/mensagem/UC, só no corretivo e só uma vez
    i = resp["regra_documentada_trecho"]
    mostrados = docs_prompt(est)
    if (not est.get("regra") and Intencao(est["intencao"]) == Intencao.PAROU_DE_FUNCIONAR and 1 <= i <= len(mostrados)
            and mostrados[i - 1]["tipo"] in TIPOS_REGRA and resp["regra_documentada_orientacao"].strip()):
        d = mostrados[i - 1]
        est["regra"] = dict(pagina=d["pagina"], secao=d["secao"], trecho=d["texto"][:600], similaridade=d["cos"],
                            orientacao=resp["regra_documentada_orientacao"].strip())
    fixo = tipo_fixo_seg1(est)
    n_pontos = len(pontos(est))
    ponto_ok = (lambda k: k if n_pontos > 1 and isinstance(k, int) and 1 <= k <= n_pontos else None)

    antigos = {(s.n, s.tipo): {x.codigo: x.valor for x in s.slots if x.valor} for s in _segs(est)}
    brutos_segs = resp["segmentos"][:MAX_SEGMENTOS]
    pontos_segs = [ponto_ok(s.get("ponto")) for s in brutos_segs]
    if pontos_segs and pontos_segs[0] and set(pontos_segs) != set(range(1, n_pontos + 1)):
        pontos_segs[0] = None                             # algum ponto ficaria sem segmento: o 1º vale para todos
    segs = []
    for s, ponto_seg in zip(brutos_segs, pontos_segs):
        tipo = TipoSegmento(s["tipo"])
        n = len(segs) + 1
        brutos = {x["codigo"]: x["valor"].strip() for x in s["slots"] if x["valor"].strip()}
        if n == 1 and fixo and tipo != fixo:              # o LLM tentou mudar o tipo que só o usuário pode mudar
            tipo = fixo
            for a, b in (("funcionalidade", "onde_ocorre"), ("onde_ocorre", "funcionalidade")):
                if a in brutos and b not in brutos:
                    brutos[b] = brutos[a]
        codigos = {c for c, _, _ in roteiros.ROTEIROS[tipo]}
        valores = {c: v for c, v in brutos.items() if c in codigos}
        for c, v in antigos.get((n, tipo), {}).items():   # o LLM não apaga o que o usuário já respondeu
            if c not in valores or len(valores[c]) < len(v) * 0.5:
                valores[c] = v
        slot_p, valor_p = valor_ponto(est, tipo, ponto_seg)
        if slot_p in codigos and (n == 1 or ponto_seg):   # 1.5.0: o menu escolhido pelo usuário vale; o LLM não troca
            valores[slot_p] = valor_p
        segs.append(roteiros.revalida(Segmento(n=n, tipo=tipo, resumo=s["resumo"][:300] or "Pedido", ponto=ponto_seg,
                                               slots=roteiros.slots_vazios(tipo, valores))))
    if not segs:
        segs = segmentos_fixos(est)
    est["segs"] = [s.model_dump(mode="json") for s in segs]

    # perguntas: só slot vazio do roteiro ou gatilho; uma por slot (por ponto, no "o que muda" com vários pontos); máx. 5
    ps, vistos, n_gat = [], set(), 0
    if est["rodada"] <= MAX_RODADAS:
        por_n = {s.n: s for s in segs}
        for p in resp["perguntas"]:
            s = por_n.get(p["segmento"]) or segs[0]
            vazios = {x.codigo for x in s.slots if not x.preenchido}
            por_ponto = p["slot"] == roteiros.slot_por_ponto(s.tipo) and not s.ponto
            k = ponto_ok(p.get("ponto")) if por_ponto else None
            chave = (s.n, p["slot"], k) if p["slot"] != "gatilho" else ("g", p["texto"][:40])
            gat_ok = p["slot"] == "gatilho" and n_gat < len(pendentes)
            if (gat_ok or p["slot"] in vazios) and chave not in vistos and p["texto"].strip():
                vistos.add(chave)
                n_gat += p["slot"] == "gatilho"
                ps.append(dict(segmento=s.n, slot=p["slot"], texto=separa_caminhos(p["texto"].strip(), est),
                               dica=separa_caminhos(p["dica"].strip(), est) or None, ponto=k,
                               opcoes=opcoes_da_pergunta(est, s, p["slot"], p.get("opcoes"))))
        cobertos = {(p["segmento"], p["slot"]) for p in ps}
        cobertos |= {(p["segmento"], p["slot"], p["ponto"]) for p in ps}
        for f in roteiros.faltando(segs):                   # o LLM esqueceu um slot vazio: pergunta fixa do roteiro
            if (f.segmento, f.slot) in cobertos and not any(p["segmento"] == f.segmento and p["slot"] == f.slot and p["ponto"]
                                                             for p in ps):
                continue                                    # pergunta geral do LLM cobre o item (e todos os pontos)
            ps += [p for p in perguntas_fixas(est, segs, f) if (p["segmento"], p["slot"], p["ponto"]) not in cobertos]
    est["perguntas"] = [dict(p, id=f"r{est['rodada']}p{i}") for i, p in enumerate(ps[:MAX_PERGUNTAS], 1)]

    est["nota"] = nota(segs, resp["nota"])
    est["dicas"] = [x.strip() for x in resp["dicas_medidor"] if x.strip()][:3] or [f"Falta: {f.rotulo}" for f in roteiros.faltando(segs)][:3]
    return segs


def tipo_fixo_seg1(est):
    """Tipo que o segmento 1 TEM de ter: CORRETIVO com regra documentada pendente ou recusada; EVOLUTIVO_EXISTENTE se aceita."""
    if not est.get("regra"):
        return None
    return TipoSegmento.EVOLUTIVO_EXISTENTE if est.get("decisao") == DecisaoRegra.ACEITA.value else TipoSegmento.CORRETIVO


def opcoes(da_ia, slot):
    """Opções de resposta (1.4.0): até 4 da IA, curtas, sem repetir e sem "não sei" (esse quem põe é a aplicação, e só nos itens
    que aceitam). Pergunta de gatilho nunca ganha "Não sei informar"."""
    out, vistos = [], set()
    for o in da_ia or []:
        o = " ".join(str(o).split())
        if o and len(o) <= MAX_CAR_OPCAO and o.lower() not in vistos and not roteiros.e_nao_sei(o):
            vistos.add(o.lower())
            out.append(o)
    return out[:MAX_OPCOES] + roteiros.opcoes_fixas(slot)


def e_pergunta_de_ponto(est, seg, slot):
    """A pergunta é o "o que muda" de um segmento que parte de um ponto de partida (1.5.1): as opções são os tipos de mudança."""
    return bool(pontos(est)) and slot == roteiros.slot_por_ponto(seg.tipo)


def opcoes_da_pergunta(est, seg, slot, da_ia=None):
    """Opções de uma pergunta: no "o que muda" de um ponto, as da aplicação (fluxos do caso de uso ligado ou genéricas, sem
    LLM); nas demais, as da IA filtradas (opcoes)."""
    if e_pergunta_de_ponto(est, seg, slot):
        return roteiros.opcoes_do_ponto()
    return opcoes(da_ia, slot)


def perguntas_fixas(est, segs, f):
    """Perguntas fixas de um item de `faltando` (roteiro sem IA ou item que o LLM esqueceu). O "o que muda" de um segmento sem
    ponto próprio, com 2+ pontos e ainda sem resposta, vira uma pergunta por ponto ("Em <caminho>, o que deve mudar?")."""
    s = next((x for x in segs if x.n == f.segmento), None)
    ps = pontos(est)
    if s is not None and e_pergunta_de_ponto(est, s, f.slot):
        valor = next((x.valor for x in s.slots if x.codigo == f.slot), None)
        if not roteiros.conteudo(valor):                    # sem resposta ainda: pergunta citando o caminho
            alvo = [(s.ponto, ps[s.ponto - 1])] if s.ponto else [(i if len(ps) > 1 else None, p) for i, p in enumerate(ps, 1)]
            return [dict(segmento=s.n, slot=f.slot, ponto=k, opcoes=roteiros.opcoes_do_ponto(),
                         **dict(zip(("texto", "dica"), roteiros.pergunta_do_ponto(p["caminho"])))) for k, p in alvo]
    t, d = roteiros.pergunta_do_item(segs, f)
    return [dict(segmento=f.segmento, slot=f.slot, texto=t, dica=d, ponto=None,
                 opcoes=roteiros.opcoes_do_ponto() if s is not None and e_pergunta_de_ponto(est, s, f.slot)
                 else roteiros.opcoes_fixas(f.slot))]


def nota(segs, nota_llm=None):
    """Média entre a nota do LLM e a fração de slots obrigatórios preenchidos (o medidor não chega a 100 com slot vazio; "Não sei"
    não conta como preenchido)."""
    cheio = round(100 * roteiros.fracao_preenchida(segs))
    if nota_llm is None:
        return cheio
    return round((max(0, min(100, nota_llm)) + cheio) / 2)


def segmentos_fixos(est):
    t = roteiros.roteiro_da_intencao(Intencao(est["intencao"]))
    valores = {SLOT_PRINCIPAL[t]: est["texto"].strip()[:1500]}
    if pontos(est) and roteiros.slot_por_ponto(t) == SLOT_PRINCIPAL[t] and len(pontos(est)) > 1:
        valores = {}                                    # 1.5.1: com vários pontos, o que muda é perguntado por ponto
    slot_p, valor_p = valor_ponto(est, t)
    if slot_p:
        valores[slot_p] = valor_p
    return [roteiros.revalida(Segmento(n=1, tipo=t, resumo=est["texto"].strip()[:120], slots=roteiros.slots_vazios(t, valores)))]


def roteiro_fixo(est):
    """Fallback sem IA: segmentos do cardápio, perguntas fixas dos slots vazios e dos gatilhos."""
    segs = _segs(est) or segmentos_fixos(est)
    est["segs"] = [s.model_dump(mode="json") for s in segs]
    ps = []
    if est["rodada"] <= MAX_RODADAS:
        gat = {g["codigo"]: g for g in gatilhos()}
        pend = _pendentes(est)
        ps += [dict(segmento=1, slot="gatilho", texto=gat[c]["pergunta"], dica=None, opcoes=[]) for c in pend[:1]]
        est["gatilhos_feitos"] += pend[:1]
        for f in roteiros.faltando(segs):
            ps += perguntas_fixas(est, segs, f)
    est["perguntas"] = [dict(p, id=f"r{est['rodada']}p{i}") for i, p in enumerate(ps[:MAX_PERGUNTAS], 1)]
    est["nota"] = nota(segs)
    est["dicas"] = [f"Falta: {f.rotulo}" for f in roteiros.faltando(segs)][:3]


# ---------------------------------------------------------------- motor
def _situacao(est, atual):
    """Situação depois de uma rodada; PENDENTE/CONFIRMADA só mudam pela confirmação."""
    if atual in cf.SITUACOES_TRAVADAS:
        return atual
    return "EM_ANDAMENTO" if est["perguntas"] else "PRONTA"


def _anonimiza_resposta(texto, conn, max_car):
    """Respostas e comentários seguem o texto do relato: anonimizados em todo modo, menos no contrato."""
    return texto if config.MODO_PII == "contrato" else pii.anonimizar(texto, conn, max_car)


class Motor:
    def __init__(self, conectar=None, conectar_app=None):
        self.conectar = conectar or dbm.conectar
        self.conectar_app = conectar_app or dbm.conectar_app

    # ---- interno
    def _rodada(self, conn, eid, est, imagens=None):
        """Uma rodada do LLM; em qualquer falha, roteiro fixo. Devolve analise_ia."""
        if llm.disponivel():
            try:
                prompt, pend = prompt_entrevista(est, _segs(est))
                resp, info = llm.json_estruturado(prompt, SCHEMA_ENTREVISTA, "entrevista", "chat", imagens=imagens or None)
                armazem.evento(conn, eid, est["rodada"], "llm_entrevista", info, resp)
                est["gatilhos_feitos"] += pend
                aplica(est, resp, pend)
                return True
            except Exception as e:  # noqa: BLE001
                conn.rollback() if conn.info.transaction_status == 3 else None
                armazem.evento(conn, eid, est["rodada"], "fallback", conteudo=dict(erro=f"{type(e).__name__}: {str(e)[:400]}"))
        roteiro_fixo(est)
        return False

    @staticmethod
    def _reordena_candidatos(conn, eid, est):
        """Das até 30 funcionalidades da fusão, o LLM escolhe as mais prováveis; sem LLM (ou em falha) fica a ordem da fusão.
        Guarda só as MAX_CANDIDATOS primeiras, sem o texto enriquecido."""
        cands = est["candidatos"]
        if len(cands) > MAX_CANDIDATOS and llm.disponivel():
            try:
                cands, info = catalogo.reordena(cands, est["texto"], est["sigla"])
                armazem.evento(conn, eid, est["rodada"], "llm_candidatos", info, dict(ids=[c.get("id") for c in cands[:MAX_CANDIDATOS]]))
            except Exception as e:  # noqa: BLE001
                conn.rollback() if conn.info.transaction_status == 3 else None
                armazem.evento(conn, eid, est["rodada"], "fallback_candidatos", conteudo=dict(erro=f"{type(e).__name__}: {str(e)[:400]}"))
        return [{k: v for k, v in c.items() if k != "texto"} for c in cands[:MAX_CANDIDATOS]]

    @staticmethod
    def _candidatos(est):
        return [CandidatoFuncionalidade(id=c.get("id"), nome=c["nome"], tipo_ifpug=c.get("tipo_ifpug"), similaridade=c["similaridade"],
                                        trecho=c.get("modulo") or None) for c in est["candidatos"]]

    def _estado(self, eid, est, analise_ia):
        return EstadoEntrevista(
            id=eid, rodada=min(est["rodada"], 3), pronto=not est["perguntas"], segmentos=_segs(est),
            candidatos_funcionalidade=self._candidatos(est), documentos=documentos(est["docs"]),
            regra_documentada=RegraDocumentada.model_validate(est["regra"]) if est.get("regra") else None,
            decisao_regra=est.get("decisao"), perguntas=[Pergunta.model_validate(p) for p in est["perguntas"]],
            complemento=bool(est.get("complemento") and est["perguntas"]), nota=est["nota"], dicas_medidor=est["dicas"],
            analise_ia=analise_ia)

    @staticmethod
    def _pontos(req, conn):
        """Pontos de partida no estado (1.5.1: lista; o ponto_partida da 1.5.0 vira lista de 1). Caminho digitado (fonte livre)
        segue a regra do relato (anonimizado, menos no modo contrato); rótulo de menu não tem dado pessoal. Repetidos saem."""
        out, vistos = [], set()
        for p in req.pontos():
            livre = p.fonte.value == "livre"
            caminho = _anonimiza_resposta(p.caminho.strip(), conn, 300) if livre else p.caminho.strip()
            detalhe = (_anonimiza_resposta(p.detalhe.strip(), conn, 500) if livre else p.detalhe.strip()) if p.detalhe else None
            chave = (p.menu_id or caminho.lower(), p.fonte.value)
            if chave not in vistos:
                vistos.add(chave)
                out.append(dict(caminho=caminho, detalhe=detalhe, menu_id=p.menu_id, fonte=p.fonte.value,
                                valor=roteiros.valor_do_ponto(caminho, p.fonte.value, detalhe)))
        return out

    @staticmethod
    def _busca(conn, intencao, sid, wiki, est):
        """Uma busca por ponto de partida (1.5.1); docs juntos em rodízio e marcados pelo ponto; candidatos só da 1ª busca.
        Os tempos das buscas se somam."""
        listas, cand, tempos = [], [], {}
        for i, (k, q) in enumerate(consultas_rag(est)):
            d, c = rag(conn, intencao, sid, wiki, q) if i == 0 else rag(conn, intencao, sid, wiki, q, com_catalogo=False)
            listas.append((k, sem_riscados(d)))
            cand = c if i == 0 else cand
            for e, s in tempos_rag().items():
                tempos[e] = round(tempos.get(e, 0) + s, 2)
        _medicao.tempos = dict(tempos, buscas=len(listas))
        return junta_docs(listas), cand

    def _carrega(self, conn, eid):
        row = armazem.obter(conn, eid)
        if not row:
            raise KeyError(eid)
        return row, row["estado"]

    # ---- API
    def criar(self, req: NovaEntrevista) -> EstadoEntrevista:
        eid = str(uuid.uuid4())
        t0 = t = time.time()
        tempos = {}

        def marca(etapa):
            nonlocal t
            tempos[etapa], t = round(time.time() - t, 2), time.time()

        conn, app = self.conectar(), None
        try:
            app = self.conectar_app()
            marca("conexoes_s")
            sid, wiki = resolve_sistema(conn, req.sigla)
            marca("resolve_sistema_s")
            texto, imgs, notas = pii.prepara(req.relato, [i.model_dump() for i in req.imagens], conn)
            marca("pii_s")
            est = dict(intencao=req.intencao.value, sigla=req.sigla, wiki=wiki, texto=texto, notas_pii=notas, rodada=1, segs=[],
                       perguntas=[], respostas=[], gatilhos_feitos=[], docs=[], candidatos=[], regra=None, decisao=None, nota=0, dicas=[],
                       contrato=None, pontos=self._pontos(req, conn))
            _medicao.tempos = {}
            try:
                est["docs"], est["candidatos"] = self._busca(conn, req.intencao, sid, wiki, est)
            except Exception as e:  # noqa: BLE001  (sem RAG a entrevista continua)
                conn.rollback()
                est["notas_pii"].append(f"RAG indisponível: {type(e).__name__}")
            marca("rag_s")
            tempos.update(tempos_rag())
            armazem.criar(app, eid, req, sid, config.MODO_PII, True, est, 0, RETENCAO_DIAS)
            if est["pontos"]:                                             # 1.5.0: para a Fase 3 medir o efeito do menu
                ev = {k: est["pontos"][0][k] for k in ("fonte", "menu_id")}
                if len(est["pontos"]) > 1:                                # 1.5.1: todos os pontos, na ordem
                    ev["pontos"] = [{k: p[k] for k in ("fonte", "menu_id")} for p in est["pontos"]]
                armazem.evento(app, eid, 1, "ponto_partida", conteudo=ev)
            est["candidatos"] = self._reordena_candidatos(app, eid, est)
            marca("reordena_candidatos_s")
            ia = self._rodada(app, eid, est, imgs)
            marca("llm_entrevista_s")
            tempos["total_s"] = round(time.time() - t0, 2)
            armazem.evento(app, eid, 1, "tempos", conteudo=tempos)       # 1.3.3: onde está o tempo da 1ª entrevista a frio
            armazem.salvar(app, eid, est["rodada"], _situacao(est, None), ia, est["nota"], est)
            app.commit()
            return self._estado(eid, est, ia)
        finally:
            conn.close()
            if app is not None:
                app.close()

    def obter(self, eid) -> EstadoEntrevista:
        app = self.conectar_app()
        try:
            row, est = self._carrega(app, eid)
            return self._estado(eid, est, row["analise_ia"])
        finally:
            app.close()

    def responder(self, eid, resp: Respostas) -> EstadoEntrevista:
        app = self.conectar_app()
        conn = None
        try:
            row, est = self._carrega(app, eid)
            cf.verifica_aberta(row["situacao"])
            if not est["perguntas"]:
                return self._estado(eid, est, row["analise_ia"])
            por_id = {p["id"]: p for p in est["perguntas"]}
            segs = _segs(est)
            for r in resp.respostas:
                p = por_id.get(r.id)
                if not p or not r.texto.strip():
                    continue
                if conn is None:
                    conn = self.conectar()
                txt = _anonimiza_resposta(r.texto.strip(), conn, 5000)
                est["respostas"].append(dict(id=r.id, segmento=p["segmento"], slot=p["slot"], pergunta=p["texto"], resposta=txt,
                                             ponto=p.get("ponto")))
                if p["slot"] == "gatilho":
                    continue
                ps_est = pontos(est)
                if p.get("ponto") and len(ps_est) > 1 and p["ponto"] <= len(ps_est):   # 1.5.1: o item guarda de qual tela é
                    txt = roteiros.marca_ponto(ps_est[p["ponto"] - 1]["caminho"]) + txt
                for i, s in enumerate(segs):
                    if s.n == p["segmento"]:
                        sl = [x.model_copy(update={"valor": roteiros.resposta_no_slot(x.codigo, x.valor, txt)}) if x.codigo == p["slot"] else x
                              for x in s.slots]
                        segs[i] = roteiros.revalida(s.model_copy(update={"slots": sl}))
            est["segs"] = [s.model_dump(mode="json") for s in segs]
            armazem.evento(app, eid, est["rodada"], "complemento" if est.get("complemento") else "respostas",
                           conteudo=[dict(id=r.id, car=len(r.texto)) for r in resp.respostas])
            est["rodada"] = min(est["rodada"] + 1, MAX_RODADAS + 1)     # fica em 3 durante o complemento
            ia = row["analise_ia"]                                        # o complemento não mexe em analise_ia (não é falha do LLM)
            falta = roteiros.faltando(segs)
            if est["rodada"] > MAX_RODADAS or (not falta and not _pendentes(est)):
                # 1.3.0: depois das 2 rodadas, se ainda faltar item obrigatório, rodada de complemento (perguntas fixas, sem LLM)
                est["complemento"] = est["rodada"] > MAX_RODADAS and bool(falta)
                if est["complemento"]:
                    est["complementos"] = est.get("complementos", 0) + 1
                    est["perguntas"] = roteiros.perguntas_complemento(segs, est["complementos"])
                else:
                    est["perguntas"] = []
                est["nota"] = nota(segs, est["nota"])
                est["dicas"] = [f"Falta: {f.rotulo}" for f in falta][:3]
            else:
                ia = self._rodada(app, eid, est) and ia
            est["contrato"] = None
            armazem.salvar(app, eid, est["rodada"], "EM_ANDAMENTO" if est["perguntas"] else "PRONTA", ia, est["nota"], est)
            app.commit()
            return self._estado(eid, est, ia)
        finally:
            app.close()
            if conn is not None:
                conn.close()

    def decidir_regra(self, eid, dec: DecisaoRegraDocumentada) -> EstadoEntrevista:
        app = self.conectar_app()
        conn = None
        try:
            row, est = self._carrega(app, eid)
            cf.verifica_aberta(row["situacao"])
            if not est.get("regra"):
                raise ValueError("a entrevista não tem regra documentada")
            if est.get("decisao"):
                raise ValueError("a regra documentada já foi decidida")
            com = (dec.comentario or "").strip()
            if com:
                conn = self.conectar()
                com = _anonimiza_resposta(com, conn, 2000)
            if com:
                est["respostas"].append(dict(id="regra", segmento=1, slot="decisao_regra", pergunta="Comentário sobre a regra documentada",
                                             resposta=com))
            ia = row["analise_ia"]
            segs = _segs(est)
            if dec.aceita:
                est["decisao"] = DecisaoRegra.ACEITA.value
                novo = roteiros.converte_para_evolutivo(segs[0])
                if com:                                         # "o que quer mudar na regra" já vale como o que muda
                    novo = roteiros.revalida(novo.model_copy(update={"slots": [
                        x.model_copy(update={"valor": com}) if x.codigo == "o_que_muda" else x for x in novo.slots]}))
                est["segs"] = [s.model_dump(mode="json") for s in [novo] + segs[1:]]
                est["rodada"] = min(est["rodada"], MAX_RODADAS)  # mudou de roteiro: mais uma rodada de perguntas
                est["complemento"] = False                       # essa rodada é do LLM; se ainda faltar depois, volta o complemento
                armazem.evento(app, eid, est["rodada"], "decisao_regra", conteudo=dict(aceita=True))
                ia = self._rodada(app, eid, est) and ia
            else:
                est["decisao"] = DecisaoRegra.RECUSADA.value
                armazem.evento(app, eid, est["rodada"], "decisao_regra", conteudo=dict(aceita=False))
            est["contrato"] = None
            armazem.salvar(app, eid, est["rodada"], "EM_ANDAMENTO" if est["perguntas"] else "PRONTA", ia, est["nota"], est)
            app.commit()
            return self._estado(eid, est, ia)
        finally:
            app.close()
            if conn is not None:
                conn.close()

    def _monta_contrato(self, app, eid, row, est, gerar=True):
        """Contrato a partir do estado. Gera pelo LLM (ou pelo roteiro fixo) só se ainda não houver um em est["contrato"]."""
        segs = [roteiros.revalida(s) for s in _segs(est)]
        ia = row["analise_ia"]
        if est.get("contrato"):
            c = est["contrato"]
        else:
            if not gerar:
                raise cf.ConfirmacaoInvalida("o Escopo do Pedido ainda não foi gerado: chame POST /entrevistas/{id}/contrato antes")
            c = None
            if llm.disponivel():
                try:
                    c, info = llm.json_estruturado(self._prompt_contrato(est, segs), SCHEMA_CONTRATO, "contrato", "chat")
                    armazem.evento(app, eid, est["rodada"], "llm_contrato", info, c)
                except Exception as e:  # noqa: BLE001
                    armazem.evento(app, eid, est["rodada"], "fallback", conteudo=dict(erro=f"{type(e).__name__}: {str(e)[:400]}"))
            if c is None:
                c, ia = contrato_fixo(est, segs), False
            est["contrato"] = c
        n_seg = {s.n for s in segs}
        reqs = [RequisitoContrato(n=i, tipo=TipoRequisito(r["tipo"]), segmento=r["segmento"] if r["segmento"] in n_seg else 1,
                                  descricao=r["descricao"].strip(), criterio_aceite=r["criterio_aceite"].strip() or "Definir com o usuário.",
                                  origem=r.get("origem") or Origem.USUARIO)
                for i, r in enumerate(c["requisitos"], 1) if r["descricao"].strip()]
        falt = roteiros.faltando_com_regra(segs, bool(est.get("regra")), est.get("decisao"))
        duvidas = [x for x in c["duvidas"] if x.strip()]
        duvidas += [d for d in roteiros.duvidas_nao_informado(segs) if d not in duvidas]   # "Não sei": sempre, não depende do LLM
        integ = roteiros.duvida_integracao(termos_integracao(texto_do_usuario(est)))      # 1.5.3: a Sustentação confirma
        if integ and integ not in duvidas:
            duvidas.append(integ)
        return Contrato(id=eid, titulo=c["titulo"].strip()[:200] or f"{est['sigla']} - pedido", objetivo=c["objetivo"].strip(),
                        requisitos=reqs, fora_escopo=[], duvidas=duvidas,      # 1.5.3: fora de escopo e integrações: Sustentação
                        integracoes=[], segmentos=segs, nota=est["nota"], faltando=falt,
                        regra_documentada=RegraDocumentada.model_validate(est["regra"]) if est.get("regra") else None,
                        decisao_regra=est.get("decisao"),
                        pode_confirmar=not falt, analise_ia=ia)

    def contrato(self, eid) -> Contrato:
        app = self.conectar_app()
        try:
            row, est = self._carrega(app, eid)
            out = self._monta_contrato(app, eid, row, est)
            sit = row["situacao"] if row["situacao"] in cf.SITUACOES_TRAVADAS else "CONTRATO_GERADO"
            armazem.salvar(app, eid, est["rodada"], sit, out.analise_ia, est["nota"], est)
            app.commit()
            return out
        finally:
            app.close()

    # ---- confirmação (contrato 1.2.0)
    def confirmacao(self, eid, escopo: EscopoEditado) -> EscopoConfirmado:
        app = self.conectar_app()
        try:
            row, est = self._carrega(app, eid)
            cf.verifica_aberta(row["situacao"])
            k = self._monta_contrato(app, eid, row, est, gerar=False)
            cf.verifica_confirmavel(k)
            funcs = cf.funcionalidades_confirmadas(escopo, self._candidatos(est))
            lidos = cf.documentos_lidos(escopo, documentos(est["docs"]))
            ps = pontos(est)
            grupos = cf.monta_grupos(k, escopo, funcs, n_pontos=len(ps))
            armazem.grava_escopo(app, eid, 0, 0, None, k.model_dump(mode="json"))
            armazem.grava_escopo(app, eid, 1, 1, escopo.cd_usuario,
                                 dict(escopo=escopo.model_dump(mode="json"), grupos=[g.model_dump(mode="json") for g in grupos],
                                      candidatos_confirmados=[dict(id=f.id, nome=f.nome, tipo_ifpug=f.tipo_ifpug) for f in funcs],
                                      documentos_lidos=lidos, ponto_partida=ps[0] if ps else None, pontos_partida=ps))
            armazem.evento(app, eid, est["rodada"], "confirmacao",
                           conteudo=dict(grupos=[g.tipo.value for g in grupos], requisitos=len(escopo.requisitos),
                                         candidatos_confirmados=[f.id for f in funcs], documentos_lidos=lidos))
            inf = cf.inferidos(k, escopo)
            if inf["confirmados"] or inf["removidos"]:
                armazem.evento(app, eid, est["rodada"], "inferidos", conteudo=inf)
            armazem.muda_situacao(app, eid, "PENDENTE")
            app.commit()
            return EscopoConfirmado(id=eid, situacao="PENDENTE", grupos=grupos, analise_ia=k.analise_ia, nota=k.nota, funcionalidades=funcs,
                                    regra_documentada=cf.regra_aceita(k))
        finally:
            app.close()

    def vincular(self, eid, vinculo: VinculoChamados) -> Confirmacao:
        app = self.conectar_app()
        try:
            row, est = self._carrega(app, eid)
            if row["situacao"] not in cf.SITUACOES_TRAVADAS:
                raise cf.ConfirmacaoInvalida("a entrevista não tem Escopo do Pedido confirmado: chame POST /entrevistas/{id}/confirmacao antes")
            v1 = armazem.escopo(app, eid, 1)
            tipos = [g["tipo"] for g in v1["conteudo"]["grupos"]]
            if not cf.valida_vinculo(tipos, vinculo, armazem.chamados(app, eid)):
                try:
                    retirados = armazem.vincula(app, eid, [(c.tipo.value, c.cd_solicitacao) for c in vinculo.chamados], vinculo.cd_pacote)
                except psycopg.errors.UniqueViolation:                 # corrida: outra chamada vinculou o mesmo número agora
                    app.rollback()
                    raise cf.ConfirmacaoInvalida("um dos chamados informados acabou de ser vinculado a outra entrevista; repita")
                armazem.muda_situacao(app, eid, "CONFIRMADA")
                armazem.evento(app, eid, est["rodada"], "vinculo", conteudo=vinculo.model_dump(mode="json"))
                for r in retirados:      # número reaproveitado: o vínculo antigo foi para o histórico; registra nas duas entrevistas
                    sub = dict(cd_solicitacao=r["cd_solicitacao"], tipo=r["tipo"], de=str(r["entrevista_id"]), para=eid)
                    armazem.evento(app, eid, est["rodada"], "vinculo_substituido", conteudo=sub)
                    armazem.evento(app, r["entrevista_id"], None, "vinculo_retirado", conteudo=sub)
                app.commit()
            return self._confirmacao(eid, armazem.chamados(app, eid))
        finally:
            app.close()

    @staticmethod
    def _confirmacao(eid, linhas):
        return Confirmacao(id=eid, situacao="CONFIRMADA",
                           chamados=[ChamadoGerado(tipo=r["tipo"], cd_solicitacao=r["cd_solicitacao"]) for r in linhas],
                           cd_pacote=next((r["cd_pacote"] for r in linhas if r["cd_pacote"]), None))

    def escopo_do_chamado(self, cd_solicitacao) -> EscopoChamado:
        app = self.conectar_app()
        try:
            ch = armazem.chamado(app, cd_solicitacao)
            if not ch:
                raise KeyError(cd_solicitacao)
            vs = [VersaoEscopo(versao=v["versao"], origem=v["origem"], cd_usuario=v["cd_usuario"], criado_em=v["criado_em"].isoformat(),
                               conteudo=v["conteudo"]) for v in armazem.versoes(app, ch["entrevista_id"])]
            return EscopoChamado(cd_solicitacao=ch["cd_solicitacao"], entrevista_id=str(ch["entrevista_id"]), tipo=ch["tipo"],
                                 cd_pacote=ch["cd_pacote"], versoes=vs)
        finally:
            app.close()

    def pendentes(self, horas) -> list[EntrevistaPendente]:
        app = self.conectar_app()
        try:
            return [EntrevistaPendente(id=str(r["id"]), cd_sistema=r["cd_sistema"], sigla=r["sigla"], atualizado_em=r["atualizado_em"].isoformat(),
                                       grupos=[g["tipo"] for g in (r["grupos"] or [])]) for r in armazem.pendentes(app, horas)]
        finally:
            app.close()

    # ---- menus e documentação (contrato 1.5.0)
    def menus(self, cd_sistema, sigla) -> Menus:
        """Catálogo de menus do sistema (pela sigla; o cd_sistema do SOS fica só no caminho da rota)."""
        conn = self.conectar()
        try:
            return Menus.model_validate(menus_catalogo.lista(conn, sigla))
        finally:
            conn.close()

    def documentacao(self, pagina, eid) -> Documentacao:
        """Página da wiki para o usuário ler. Só páginas `sistemas:<wiki do sistema da entrevista>:…`; as demais (e entrevista
        inexistente) → KeyError (404). Versão mais recente, seções na ordem, sem as revogadas."""
        app = self.conectar_app()
        conn = self.conectar()
        try:
            _, est = self._carrega(app, eid)
            wiki = est.get("wiki", "")
            if "wiki" not in est:                               # entrevista anterior à 1.5.0
                wiki = resolve_sistema(conn, est["sigla"])[1]
            if not wiki or not pagina.startswith(f"sistemas:{wiki}:"):
                raise KeyError(pagina)
            rows = conn.execute(
                """SELECT titulo_pagina, tipo, secao, texto FROM apf.conhecimento_trecho
                   WHERE origem_dokuwiki = %(p)s
                     AND data_versao = (SELECT max(data_versao) FROM apf.conhecimento_trecho WHERE origem_dokuwiki = %(p)s)
                   ORDER BY ordem""", dict(p=pagina)).fetchall()
            if not rows:
                raise KeyError(pagina)
            validas = [r for r in rows if not riscado(r) and (r["texto"] or "").strip()]
            secoes = [SecaoDocumentacao(secao=r["secao"] or r["titulo_pagina"], texto=r["texto"] or "",
                                        html=doc_html.para_html(r["texto"])) for r in validas]
            titulo, total = rows[0]["titulo_pagina"], len(secoes)
            if rows[0]["tipo"] == "regras_de_negocio":           # 1.5.2: só a regra que casou, não as ~360 seções da página
                casadas = [d.get("secao") for d in est.get("docs", []) if d["pagina"] == pagina]
                manter = secoes_da_regra([r["secao"] or "" for r in validas], casadas)
                if manter is not None:
                    secoes = [secoes[i] for i in manter]
                    titulo = titulo_documento(dict(tipo="regras_de_negocio", secao=next(c for c in casadas if c), titulo=titulo))
            return Documentacao(pagina=pagina, titulo=titulo, tipo=rows[0]["tipo"], secoes=secoes,
                                parcial=len(secoes) < total, secoes_total=total)
        finally:
            app.close()
            conn.close()

    @staticmethod
    def _prompt_contrato(est, segs):
        gat = "\n".join(f"- {r['pergunta']} → {r['resposta']}" for r in est["respostas"] if r["slot"] == "gatilho") or "(nenhuma)"
        regra = "(nenhuma)"
        if est.get("regra"):
            dec = {DecisaoRegra.ACEITA.value: "O USUÁRIO CONFIRMOU que o sistema segue esta regra e quer mudá-la (o pedido é mudar a "
                                              "regra; cite a regra no objetivo).",
                   DecisaoRegra.RECUSADA.value: "O USUÁRIO DISSE que o problema NÃO é esta regra (trate como defeito; registre em "
                                                "dúvidas que a equipe deve confirmar se a regra se aplica)."}.get(
                est.get("decisao"), "O usuário ainda não decidiu se é esta regra (registre isso em dúvidas).")
            com = "".join(f" Comentário do usuário: {r['resposta']}" for r in est["respostas"] if r["slot"] == "decisao_regra")
            regra = f"{est['regra']['pagina']} / {est['regra']['secao']}: {est['regra']['trecho']}" + chr(10) + dec + com
        return (open(PROMPT_CONTRATO, encoding="utf-8").read().replace("{{SISTEMA}}", est["sigla"]).replace("{{RELATO}}", est["texto"])
                .replace("{{SEGMENTOS}}", _fmt_segs(segs)).replace("{{GATILHOS}}", gat).replace("{{REGRA}}", regra))


def contrato_fixo(est, segs):
    """Contrato sem IA: um requisito por segmento a partir dos slots (mesma lógica do mock). Fora de escopo e integrações ficam
    vazios (1.5.3: a Sustentação responde; o sistema citado entra nas dúvidas em `_monta_contrato`)."""
    reqs = []
    for s in segs:
        v = {x.codigo: x.valor for x in s.slots}
        reqs.append(dict(segmento=s.n, tipo="CORRETIVO" if s.tipo == TipoSegmento.CORRETIVO else "EVOLUTIVO",
                         descricao=(v.get(SLOT_PRINCIPAL[s.tipo]) or s.resumo)[:1000],
                         criterio_aceite=roteiros.criterio_fixo(s.tipo, v)[:1000]))
    duv = [f"{r['pergunta']} → {r['resposta']}" for r in est["respostas"] if r["slot"] == "gatilho"]
    return dict(titulo=f"{est['sigla']} - {est['texto'].strip()[:80]}", objetivo=est["texto"].strip()[:300], requisitos=reqs,
                fora_escopo=[], duvidas=duv, integracoes=[])
