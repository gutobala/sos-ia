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
import uuid

import psycopg
import yaml

from .. import config, embeddings, llm, pii
from .. import db as dbm
from ..rag import busca, catalogo
from . import armazem, confirmacao as cf, roteiros
from .mock import PERGUNTA_SLOT, SLOT_PRINCIPAL
from .modelos import (CandidatoFuncionalidade, ChamadoGerado, Confirmacao, Contrato, DecisaoRegra, DecisaoRegraDocumentada,
                      EntrevistaPendente, EscopoChamado, EscopoConfirmado, EscopoEditado, EstadoEntrevista, Integracao, Intencao,
                      NovaEntrevista, Origem, Pergunta, RegraDocumentada, RequisitoContrato, Respostas, Segmento, TipoRequisito, TipoSegmento,
                      VersaoEscopo, VinculoChamados)

MAX_RODADAS = 2
MAX_PERGUNTAS = 5
MAX_SEGMENTOS = 3
MAX_CANDIDATOS = 5      # funcionalidades mostradas (de 30 da fusão, reordenadas pelo LLM)
PROMPT_ENTREVISTA = os.path.join(config.RAIZ, "prompts", "entrevista_v1.md")
PROMPT_CONTRATO = os.path.join(config.RAIZ, "prompts", "contrato_v1.md")
ARQ_GATILHOS = os.path.join(config.RAIZ, "config", "gatilhos.yaml")
TIPOS_REGRA = ("regras_de_negocio", "mensagens", "caso_de_uso")
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
            "type": "object", "additionalProperties": False, "required": ["tipo", "resumo", "slots"],
            "properties": {"tipo": {"type": "string", "enum": _TIPOS}, "resumo": {"type": "string"},
                           "slots": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                "required": ["codigo", "valor"],
                                                                "properties": {"codigo": {"type": "string"}, "valor": {"type": "string"}}}}}}},
        "perguntas": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["segmento", "slot", "texto", "dica", "opcoes"],
            "properties": {"segmento": {"type": "integer"}, "slot": {"type": "string"}, "texto": {"type": "string"},
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
def rag(conn, intencao, sistema_id, wiki, texto):
    """(docs, candidatos). docs = trechos numerados da wiki para o prompt (híbrida + regras de negócio no corretivo)."""
    docs, cand = [], []
    q = texto[:4000]
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
    if sistema_id and intencao != Intencao.ALGO_NOVO:
        for c in catalogo.candidatos(conn, sistema_id, datetime.date.today() + datetime.timedelta(days=1), [q]):
            cand.append(dict(id=c["id"], nome=c["nome"], tipo_ifpug=c["tipo"], modulo=c["modulo"], similaridade=c["similaridade"],
                             texto=c["texto"]))
    return docs, cand


def riscado(d):
    """Trecho revogado na wiki: seção riscada (`~~RN.189 …~~`) ou texto inteiro entre `~~ ~~`. Não vai ao prompt: o LLM apontava
    regra revogada como regra documentada (laboratório 1.3.2, RN151 do GAI). Risco parcial dentro do texto não conta."""
    t = (d.get("texto") or "").strip()
    return "~~" in (d.get("secao") or "") or (len(t) > 4 and t.startswith("~~") and t.endswith("~~"))


def sem_riscados(docs):
    return [d for d in docs if not riscado(d)]


# ---------------------------------------------------------------- montagem do prompt
def _fmt_segs(segs):
    if not segs:
        return "(vazio)"
    out = []
    for s in segs:
        out.append(f"Segmento {s.n} ({s.tipo.value}): {s.resumo}")
        out += [f"  - {x.codigo}: {x.valor or ''}" for x in s.slots]
    return "\n".join(out)


def _fmt_respostas(est):
    return "\n".join(f"- [{r['slot']}] {r['pergunta']} → {r['resposta']}" for r in est["respostas"]) or "(nenhuma)"


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
    texto = est["texto"] + "\n" + "\n".join(r["resposta"] for r in est["respostas"])
    return [c for c in detecta_gatilhos(texto) if c not in est["gatilhos_feitos"]]


def prompt_entrevista(est, segs):
    pend = _pendentes(est)
    gat = {g["codigo"]: g for g in gatilhos()}
    cand = "\n".join(f"{i}. {c['nome']}" + (f" ({c['modulo']})" if c.get("modulo") else "") for i, c in enumerate(est["candidatos"], 1))
    docs = "\n\n".join(f"[{i}] ({d['tipo']}) {d['titulo']} / {d['secao'] or ''}\n{d['texto']}" for i, d in enumerate(est["docs"], 1))
    return (open(PROMPT_ENTREVISTA, encoding="utf-8").read()
            .replace("{{SISTEMA}}", est["sigla"]).replace("{{INTENCAO}}", NOME_INTENCAO[Intencao(est["intencao"])])
            .replace("{{RODADA}}", str(est["rodada"])).replace("{{RELATO}}", est["texto"])
            .replace("{{RESPOSTAS}}", _fmt_respostas(est)).replace("{{SLOTS_ATUAIS}}", _fmt_segs(segs))
            .replace("{{DECISAO_REGRA}}", _fmt_decisao(est))
            .replace("{{CANDIDATOS}}", cand or "(nenhuma)").replace("{{DOCUMENTACAO}}", docs or "(nenhum trecho encontrado)")
            .replace("{{GATILHOS}}", "\n".join(f"- {c}: {gat[c]['descricao']}. Pergunta sugerida: {gat[c]['pergunta']}" for c in pend)
                     or "(nenhum)")), pend


# ---------------------------------------------------------------- pós-processamento determinístico
def _segs(est):
    return [Segmento.model_validate(s) for s in est["segs"]]


def aplica(est, resp, pendentes=()):
    """Aplica a resposta do LLM ao estado sem confiar nele para o que bloqueia. Devolve a lista de segmentos final.
    `pendentes`: gatilhos mostrados ao LLM nesta rodada; pergunta "gatilho" só vale se houver algum (no máximo uma por gatilho)."""
    # regra documentada: só um trecho que o RAG trouxe, de tipo regra/mensagem/UC, só no corretivo e só uma vez
    i = resp["regra_documentada_trecho"]
    if (not est.get("regra") and Intencao(est["intencao"]) == Intencao.PAROU_DE_FUNCIONAR and 1 <= i <= len(est["docs"])
            and est["docs"][i - 1]["tipo"] in TIPOS_REGRA and resp["regra_documentada_orientacao"].strip()):
        d = est["docs"][i - 1]
        est["regra"] = dict(pagina=d["pagina"], secao=d["secao"], trecho=d["texto"][:600], similaridade=d["cos"],
                            orientacao=resp["regra_documentada_orientacao"].strip())
    fixo = tipo_fixo_seg1(est)

    antigos = {(s.n, s.tipo): {x.codigo: x.valor for x in s.slots if x.valor} for s in _segs(est)}
    segs = []
    for s in resp["segmentos"][:MAX_SEGMENTOS]:
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
        segs.append(roteiros.revalida(Segmento(n=n, tipo=tipo, resumo=s["resumo"][:300] or "Pedido", slots=roteiros.slots_vazios(tipo, valores))))
    if not segs:
        segs = segmentos_fixos(est)
    est["segs"] = [s.model_dump(mode="json") for s in segs]

    # perguntas: só slot vazio do roteiro ou gatilho; uma por slot; máx. 5
    ps, vistos, n_gat = [], set(), 0
    if est["rodada"] <= MAX_RODADAS:
        por_n = {s.n: s for s in segs}
        for p in resp["perguntas"]:
            s = por_n.get(p["segmento"]) or segs[0]
            vazios = {x.codigo for x in s.slots if not x.preenchido}
            chave = (s.n, p["slot"]) if p["slot"] != "gatilho" else ("g", p["texto"][:40])
            gat_ok = p["slot"] == "gatilho" and n_gat < len(pendentes)
            if (gat_ok or p["slot"] in vazios) and chave not in vistos and p["texto"].strip():
                vistos.add(chave)
                n_gat += p["slot"] == "gatilho"
                ps.append(dict(segmento=s.n, slot=p["slot"], texto=p["texto"].strip(), dica=p["dica"].strip() or None,
                               opcoes=opcoes(p.get("opcoes"), p["slot"])))
        cobertos = {(p["segmento"], p["slot"]) for p in ps}
        for f in roteiros.faltando(segs):                   # o LLM esqueceu um slot vazio: pergunta fixa do roteiro
            if (f.segmento, f.slot) not in cobertos:
                t, d = PERGUNTA_SLOT[f.slot]
                ps.append(dict(segmento=f.segmento, slot=f.slot, texto=t, dica=d, opcoes=roteiros.opcoes_fixas(f.slot)))
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


def nota(segs, nota_llm=None):
    """Média entre a nota do LLM e a fração de slots obrigatórios preenchidos (o medidor não chega a 100 com slot vazio; "Não sei"
    não conta como preenchido)."""
    cheio = round(100 * roteiros.fracao_preenchida(segs))
    if nota_llm is None:
        return cheio
    return round((max(0, min(100, nota_llm)) + cheio) / 2)


def segmentos_fixos(est):
    t = roteiros.roteiro_da_intencao(Intencao(est["intencao"]))
    return [roteiros.revalida(Segmento(n=1, tipo=t, resumo=est["texto"].strip()[:120],
                                       slots=roteiros.slots_vazios(t, {SLOT_PRINCIPAL[t]: est["texto"].strip()[:1500]})))]


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
            t, d = PERGUNTA_SLOT[f.slot]
            ps.append(dict(segmento=f.segmento, slot=f.slot, texto=t, dica=d, opcoes=roteiros.opcoes_fixas(f.slot)))
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
            candidatos_funcionalidade=self._candidatos(est),
            regra_documentada=RegraDocumentada.model_validate(est["regra"]) if est.get("regra") else None,
            decisao_regra=est.get("decisao"), perguntas=[Pergunta.model_validate(p) for p in est["perguntas"]],
            complemento=bool(est.get("complemento") and est["perguntas"]), nota=est["nota"], dicas_medidor=est["dicas"],
            analise_ia=analise_ia)

    def _carrega(self, conn, eid):
        row = armazem.obter(conn, eid)
        if not row:
            raise KeyError(eid)
        return row, row["estado"]

    # ---- API
    def criar(self, req: NovaEntrevista) -> EstadoEntrevista:
        eid = str(uuid.uuid4())
        conn, app = self.conectar(), None
        try:
            app = self.conectar_app()
            sid, wiki = resolve_sistema(conn, req.sigla)
            texto, imgs, notas = pii.prepara(req.relato, [i.model_dump() for i in req.imagens], conn)
            est = dict(intencao=req.intencao.value, sigla=req.sigla, texto=texto, notas_pii=notas, rodada=1, segs=[], perguntas=[],
                       respostas=[], gatilhos_feitos=[], docs=[], candidatos=[], regra=None, decisao=None, nota=0, dicas=[], contrato=None)
            try:
                docs, est["candidatos"] = rag(conn, req.intencao, sid, wiki, texto)
                est["docs"] = sem_riscados(docs)
            except Exception as e:  # noqa: BLE001  (sem RAG a entrevista continua)
                conn.rollback()
                est["notas_pii"].append(f"RAG indisponível: {type(e).__name__}")
            armazem.criar(app, eid, req, sid, config.MODO_PII, True, est, 0, RETENCAO_DIAS)
            est["candidatos"] = self._reordena_candidatos(app, eid, est)
            ia = self._rodada(app, eid, est, imgs)
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
                est["respostas"].append(dict(id=r.id, segmento=p["segmento"], slot=p["slot"], pergunta=p["texto"], resposta=txt))
                if p["slot"] == "gatilho":
                    continue
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
        return Contrato(id=eid, titulo=c["titulo"].strip()[:200] or f"{est['sigla']} - pedido", objetivo=c["objetivo"].strip(),
                        requisitos=reqs, fora_escopo=[x for x in c["fora_escopo"] if x.strip()], duvidas=duvidas,
                        integracoes=[Integracao(**x) for x in c["integracoes"]], segmentos=segs, nota=est["nota"], faltando=falt,
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
            grupos = cf.monta_grupos(k, escopo, funcs)
            armazem.grava_escopo(app, eid, 0, 0, None, k.model_dump(mode="json"))
            armazem.grava_escopo(app, eid, 1, 1, escopo.cd_usuario,
                                 dict(escopo=escopo.model_dump(mode="json"), grupos=[g.model_dump(mode="json") for g in grupos],
                                      candidatos_confirmados=[dict(id=f.id, nome=f.nome, tipo_ifpug=f.tipo_ifpug) for f in funcs]))
            armazem.evento(app, eid, est["rodada"], "confirmacao",
                           conteudo=dict(grupos=[g.tipo.value for g in grupos], requisitos=len(escopo.requisitos),
                                         candidatos_confirmados=[f.id for f in funcs]))
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
    """Contrato sem IA: um requisito por segmento a partir dos slots (mesma lógica do mock)."""
    reqs, fora, integ = [], [], []
    for s in segs:
        v = {x.codigo: x.valor for x in s.slots}
        reqs.append(dict(segmento=s.n, tipo="CORRETIVO" if s.tipo == TipoSegmento.CORRETIVO else "EVOLUTIVO",
                         descricao=(v.get(SLOT_PRINCIPAL[s.tipo]) or s.resumo)[:1000],
                         criterio_aceite=roteiros.criterio_fixo(s.tipo, v)[:1000]))
        if v.get("fora_de_escopo") and not roteiros.nao_informado(v["fora_de_escopo"]) \
                and v["fora_de_escopo"].strip().lower() not in ("nada", "não", "nao"):
            fora.append(v["fora_de_escopo"])
        if v.get("outros_sistemas") and not roteiros.nao_informado(v["outros_sistemas"]) \
                and v["outros_sistemas"].strip().lower() not in ("não", "nao", "n"):
            integ.append(dict(sistema="(a identificar)", sentido="AMBOS", descricao=v["outros_sistemas"]))
    duv = [f"{r['pergunta']} → {r['resposta']}" for r in est["respostas"] if r["slot"] == "gatilho"]
    return dict(titulo=f"{est['sigla']} - {est['texto'].strip()[:80]}", objetivo=est["texto"].strip()[:300], requisitos=reqs,
                fora_escopo=fora, duvidas=duv, integracoes=integ)
