"""Laboratório da regra documentada (pedido 1.3.2; docs/laboratorio-regra-documentada.md): mede o prompt atual da entrevista contra
as variantes V1 (regra que pode ser a CAUSA do efeito relatado, orientação condicional) e VB (V1 + até 2 candidatas e pergunta que
separa as regras), no gabarito migracao/gabarito_regra_documentada.json.

A bancada roda o Motor do produto (Motor.criar e Motor.responder) sem reescrever o caminho: só a GRAVAÇÃO (nucleo/entrevista/armazem,
banco sosia_app) é trocada por um armazenamento em memória. O banco sosia (busca, catálogo, nomes da anonimização) é o real.
Diferenças registradas:
  - _reordena_candidatos (LLM que reordena as funcionalidades) é pulado: candidatos na ordem da fusão (não afeta a regra);
  - variante VB simula as 3 mudanças que exigiria no produto: SCHEMA_ENTREVISTA + regras_candidatas/pergunta_regra; aplica() que
    guarda as candidatas e acrescenta a pergunta (slot "gatilho"); 2ª rodada com LLM garantida enquanto houver candidata pendente.
    Na 2ª rodada, um LLM no papel do usuário responde às perguntas só com a "situação real" do gabarito (nunca a regra).

Uso (na raiz):
  .venv\\Scripts\\python migracao\\mede_regra_documentada.py seca                 sem LLM: trechos do rag(), posição da regra certa, palavras em comum
  .venv\\Scripts\\python migracao\\mede_regra_documentada.py roda [--variantes atual,v1,vb] [--reps 3] [--teto 2.0] [--casos P1,C1]
  .venv\\Scripts\\python migracao\\mede_regra_documentada.py relatorio             métricas a partir de dados/regra_documentada_execucoes.json
"""
import argparse
import collections
import json
import os
import re
import sys
import unicodedata

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ["SOSIA_MOCK"] = "0"                     # motor real (o .env do laboratório está em mock)
os.environ.setdefault("SOSIA_PII_MODO", "local")

from nucleo import config, db, llm, precos  # noqa: E402,F401
from nucleo.entrevista import armazem, motor  # noqa: E402
from nucleo.entrevista.modelos import Intencao, NovaEntrevista, Respostas  # noqa: E402

GAB = os.path.join(RAIZ, "migracao", "gabarito_regra_documentada.json")
EXEC = os.path.join(RAIZ, "dados", "regra_documentada_execucoes.json")
SAIDA = os.path.join(RAIZ, "dados", "regra_documentada_gabarito.txt")
if "--novos" in sys.argv:              # gabarito de confirmação (casos novos, nunca usados para ajustar o prompt)
    sys.argv.remove("--novos")
    GAB = os.path.join(RAIZ, "migracao", "gabarito_regra_documentada_novos.json")
    EXEC = os.path.join(RAIZ, "dados", "regra_documentada_execucoes_novos.json")
    SAIDA = os.path.join(RAIZ, "dados", "regra_documentada_gabarito_novos.txt")
PROMPTS = {"atual": os.path.join(RAIZ, "prompts", "entrevista_v1.md"),
           "v1": os.path.join(RAIZ, "prompts", "experimentos", "entrevista_v1_causa.md"),
           "vb": os.path.join(RAIZ, "prompts", "experimentos", "entrevista_v1_causa_b.md")}
PROMPTS["v1r"] = PROMPTS["v1"]          # V1 + regra fixa: trecho riscado (revogado) não entra na busca nem vira regra
VAZIAS = set("""a o as os de da do das dos e em no na nos nas um uma uns umas que se por para com sem ao aos ou mas nao sim ja
               ele ela eles elas eu meu minha seu sua isso esse essa este esta foi ser ter tem sistema quando como mais""".split())


# ---------------------------------------------------------------- utilidades
def sa(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def palavras(s):
    return {w for w in re.findall(r"[a-z0-9]+", sa(s)) if len(w) >= 3 and w not in VAZIAS}


def codigo(secao):
    """'... > RN.194 Exclusão' -> 'RN194'; None se a seção não for uma RN/MN numerada."""
    m = re.search(r"\b(RN|MN)\.?\s?0*(\d+)", secao or "")
    return f"{m.group(1)}{m.group(2)}" if m else None


def trecho_da_regra(conn, cod):
    for r in conn.execute("""SELECT origem_dokuwiki, titulo_pagina, secao, texto FROM apf.conhecimento_trecho WHERE origem_dokuwiki LIKE 'sistemas:gai:%%'
                             AND tipo = 'regras_de_negocio'""").fetchall():
        if codigo(r["secao"]) == cod:
            return r
    return None


def ncod(c):
    return codigo(c) or c


def rotulo(secao):
    """Código da RN/MN ou o nome curto da seção (fluxos de caso de uso)."""
    return codigo(secao) or (secao or "").split(">")[-1].strip()[:40]


def aceita(secao, regras, aceitas_secao):
    return codigo(secao) in {ncod(r) for r in regras} or any(a in (secao or "") for a in aceitas_secao)


def casos(filtro=None):
    g = json.load(open(GAB, encoding="utf-8"))
    cs = g["casos"]
    return g["sigla"], [c for c in cs if not filtro or c["id"] in filtro]


# ---------------------------------------------------------------- armazenamento em memória (só a gravação muda)
class _Conn:
    class info:
        transaction_status = 0

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class Memoria:
    def __init__(self):
        self.rows, self.eventos = {}, collections.defaultdict(list)

    def instala(self):
        armazem.criar = lambda conn, eid, req, sid, modo, ia, est, nota, dias=None: self.rows.__setitem__(
            eid, dict(id=eid, situacao="EM_ANDAMENTO", analise_ia=ia, estado=json.loads(json.dumps(est)), rodada=1))
        armazem.salvar = lambda conn, eid, rodada, sit, ia, nota, est: self.rows[eid].update(
            situacao=sit, analise_ia=ia, rodada=rodada, estado=json.loads(json.dumps(est)))
        armazem.evento = lambda conn, eid, rodada, tipo, info=None, conteudo=None: self.eventos[eid].append(
            dict(rodada=rodada, tipo=tipo, info=info, conteudo=conteudo))
        armazem.obter = lambda conn, eid: (dict(self.rows[eid], estado=json.loads(json.dumps(self.rows[eid]["estado"])))
                                           if eid in self.rows else None)
        motor.Motor._reordena_candidatos = staticmethod(
            lambda conn, eid, est: [{k: v for k, v in c.items() if k != "texto"} for c in est["candidatos"][:motor.MAX_CANDIDATOS]])


# ---------------------------------------------------------------- variante B (simula as mudanças do produto)
ORIG = dict(aplica=motor.aplica, prompt=motor.prompt_entrevista, pendentes=motor._pendentes, schema=motor.SCHEMA_ENTREVISTA)
SCHEMA_B = json.loads(json.dumps(motor.SCHEMA_ENTREVISTA))
SCHEMA_B["properties"]["regras_candidatas"] = {"type": "array", "items": {"type": "integer"}}
SCHEMA_B["properties"]["pergunta_regra"] = {"type": "string"}
SCHEMA_B["required"] = SCHEMA_B["required"] + ["regras_candidatas", "pergunta_regra"]


def _pendente_b(est):
    return bool(est.get("regras_candidatas")) and not est.get("regra") and not est.get("candidatas_resolvidas")


def _fmt_candidatas(est):
    if not _pendente_b(est):
        return "(nenhuma)"
    return ", ".join(f"[{i}]" for i in est["regras_candidatas"])


def prompt_b(est, segs):
    motor._pendentes = ORIG["pendentes"]
    try:
        p, pend = ORIG["prompt"](est, segs)
    finally:
        motor._pendentes = pendentes_b
    return p.replace("{{CANDIDATAS_REGRA}}", _fmt_candidatas(est)), pend


def pendentes_b(est):
    p = ORIG["pendentes"](est)
    return p + ["regra_candidata"] if _pendente_b(est) and est["rodada"] <= motor.MAX_RODADAS else p


def aplica_b(est, resp, pendentes=()):
    docs = est["docs"]
    validos = [i for i in resp.get("regras_candidatas") or [] if 1 <= i <= len(docs) and docs[i - 1]["tipo"] in motor.TIPOS_REGRA]
    validos = list(dict.fromkeys(validos))[:2]
    if _pendente_b(est):                                  # 2ª rodada: o trecho final tem de estar entre as candidatas
        if resp["regra_documentada_trecho"] not in est["regras_candidatas"]:
            resp = dict(resp, regra_documentada_trecho=0)
        est["candidatas_resolvidas"] = True
        return ORIG["aplica"](est, resp, pendentes)
    if (not est.get("regra") and len(validos) == 2 and (resp.get("pergunta_regra") or "").strip()
            and Intencao(est["intencao"]) == Intencao.PAROU_DE_FUNCIONAR and est["rodada"] < motor.MAX_RODADAS):
        est["regras_candidatas"] = validos
        segs = ORIG["aplica"](est, dict(resp, regra_documentada_trecho=0), pendentes)
        q = dict(segmento=1, slot="gatilho", texto=resp["pergunta_regra"].strip(), dica=None)
        ps = [q] + [{k: v for k, v in p.items() if k != "id"} for p in est["perguntas"]][:motor.MAX_PERGUNTAS - 1]
        est["perguntas"] = [dict(p, id=f"r{est['rodada']}p{i}") for i, p in enumerate(ps, 1)]
        return segs
    return ORIG["aplica"](est, resp, pendentes)


ORIG["rag"] = motor.rag


def usa_injecao(cod):
    if not cod:
        motor.rag = ORIG["rag"]
        return

    def rag_inj(conn, intencao, sid, wiki, texto):
        docs, cand = ORIG["rag"](conn, intencao, sid, wiki, texto)
        if cod not in [codigo(d["secao"]) for d in docs]:
            r = trecho_da_regra(conn, cod)
            docs.append(dict(pagina=r["origem_dokuwiki"], titulo=r["titulo_pagina"], secao=r["secao"], tipo="regras_de_negocio",
                             texto=(r["texto"] or "")[:700], cos=0.0))
        return docs, cand
    motor.rag = rag_inj


def usa_variante(v):
    motor.PROMPT_ENTREVISTA = PROMPTS[v.split("+")[0]]
    v = v.split("+")[0]
    if v == "vb":
        motor.aplica, motor.prompt_entrevista, motor._pendentes, motor.SCHEMA_ENTREVISTA = aplica_b, prompt_b, pendentes_b, SCHEMA_B
    else:
        motor.aplica, motor.prompt_entrevista = ORIG["aplica"], ORIG["prompt"]
        motor._pendentes, motor.SCHEMA_ENTREVISTA = ORIG["pendentes"], ORIG["schema"]


# ---------------------------------------------------------------- usuário simulado (só VB, 2ª rodada)
PROMPT_USUARIO = """Você é um funcionário da Terracap que abriu um chamado no sistema {sigla}. Responda às perguntas do atendimento
em português simples, como você responderia de verdade, usando SOMENTE o que você sabe pela situação abaixo. Se a situação não diz,
responda "não sei" ou dê uma resposta curta e plausível sem inventar regras do sistema. Não cite regras de negócio pelo número.

O que você escreveu no chamado: {relato}

O que você sabe da situação: {situacao}

Perguntas:
{perguntas}
"""
SCHEMA_USUARIO = {"type": "object", "additionalProperties": False, "required": ["respostas"],
                  "properties": {"respostas": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                          "required": ["id", "texto"],
                                                                          "properties": {"id": {"type": "string"},
                                                                                         "texto": {"type": "string"}}}}}}


def responde_como_usuario(sigla, caso, sit, perguntas):
    p = PROMPT_USUARIO.format(sigla=sigla, relato=caso["relato"], situacao=sit["texto"],
                              perguntas="\n".join(f"- {q['id']}: {q['texto']}" for q in perguntas))
    r, info = llm.json_estruturado(p, SCHEMA_USUARIO, "respostas_usuario", "chat")
    ids = {q["id"] for q in perguntas}
    resp = [x for x in r["respostas"] if x["id"] in ids and x["texto"].strip()]
    return resp, info


# ---------------------------------------------------------------- uma execução
def riscado(d):
    """Seção inteira riscada na wiki (~~...~~) = regra excluída (confirmado pelo analista em 06/11/2026)."""
    t = (d.get("texto") or "").strip()
    return "~~" in (d.get("secao") or "") or (t.startswith("~~") and t.endswith("~~"))


def usa_filtro_riscados():
    atual = motor.rag

    def rag_sem_riscados(conn, intencao, sid, wiki, texto):
        docs, cand = atual(conn, intencao, sid, wiki, texto)
        return [d for d in docs if not riscado(d)], cand
    motor.rag = rag_sem_riscados


def executa(v, sigla, caso, sit, mem):
    usa_variante(v)
    usa_injecao(caso.get("injetar") if v.endswith("+inj") else None)
    if v.split("+")[0].endswith("r"):
        usa_filtro_riscados()
    m = motor.Motor(conectar_app=_Conn)
    e = m.criar(NovaEntrevista(cd_sistema=1, sigla=sigla, intencao=Intencao.PAROU_DE_FUNCIONAR, relato=caso["relato"]))
    est = mem.rows[e.id]["estado"]
    ev = mem.eventos[e.id]
    r1 = next((x for x in ev if x["tipo"] == "llm_entrevista"), None)
    out = dict(variante=v, caso=caso["id"], situacao=sit["rotulo"], docs=[dict(n=i, tipo=d["tipo"], secao=d["secao"], codigo=codigo(d["secao"]),
                                                                               cos=d["cos"]) for i, d in enumerate(est["docs"], 1)],
               r1=dict(trecho=r1["conteudo"]["regra_documentada_trecho"], orientacao=r1["conteudo"]["regra_documentada_orientacao"],
                       candidatas=r1["conteudo"].get("regras_candidatas"), pergunta_regra=r1["conteudo"].get("pergunta_regra"))
               if r1 else None, fallback=any(x["tipo"] == "fallback" for x in ev), perguntas_r1=[p.texto for p in e.perguntas],
               candidatas=est.get("regras_candidatas"), custo=sum((x["info"] or {}).get("custo_usd") or 0 for x in ev))
    final = e
    if v.startswith("vb") and est.get("regras_candidatas") and e.perguntas:
        resp, info = responde_como_usuario(sigla, caso, sit, [p.model_dump() for p in e.perguntas])
        out["custo"] += info.get("custo_usd") or 0
        out["respostas_usuario"] = resp
        final = m.responder(e.id, Respostas(respostas=resp))
        ev = mem.eventos[e.id]
        out["custo"] = sum((x["info"] or {}).get("custo_usd") or 0 for x in ev) + (info.get("custo_usd") or 0)
        r2 = [x for x in ev if x["tipo"] == "llm_entrevista"][1:2]
        out["r2"] = dict(trecho=r2[0]["conteudo"]["regra_documentada_trecho"],
                         orientacao=r2[0]["conteudo"]["regra_documentada_orientacao"]) if r2 else None
        out["fechou_sem_r2"] = not r2
    rg = final.regra_documentada
    out["regra"] = dict(secao=rg.secao, codigo=rotulo(rg.secao), orientacao=rg.orientacao) if rg else None
    return out


# ---------------------------------------------------------------- seca
def seca(filtro):
    sigla, cs = casos(filtro)
    with db.db() as conn:
        sid, wiki = motor.resolve_sistema(conn, sigla)
        print(f"{sigla}: sistema_id={sid} wiki={wiki}\n")
        for c in cs:
            texto, _, _ = motor.pii.prepara(c["relato"], [], conn)
            docs, _ = motor.rag(conn, Intencao.PAROU_DE_FUNCIONAR, sid, wiki, texto)
            cods = [codigo(d["secao"]) for d in docs]
            alvo = c["regra"] or ([c["perto"]] if c.get("perto") else [])
            alvo = [ncod(a) for a in alvo]
            pos = {a: (cods.index(a) + 1 if a in cods else None) for a in alvo}
            pos.update({a: next((i for i, d in enumerate(docs, 1) if a in (d["secao"] or "")), None) for a in c.get("aceitas_secao", [])})
            comum = {}
            for a in alvo:
                r = trecho_da_regra(conn, a)
                comum[a] = sorted(palavras(c["relato"]) & palavras((r["secao"] or "") + " " + (r["texto"] or ""))) if r else "(regra não achada)"
            print(f"{c['id']:4} {c['tipo']}  {'regra ' + '/'.join(c['regra']) if c['regra'] else 'nenhuma (perto: ' + (c.get('perto') or '-') + ')'}"
                  f"   posição: {pos}")
            if texto != c["relato"]:
                print(f"     anonimizado: {texto}")
            for i, d in enumerate(docs, 1):
                print(f"     [{i}] {d['tipo'][:12]:12} {d['cos']:.3f}  {(d['secao'] or '')[-70:]}")
            for a, w in comum.items():
                print(f"     em comum com {a}: {w}")
            print()


# ---------------------------------------------------------------- roda
def roda(variantes, reps, teto, filtro):
    sigla, cs = casos(filtro)
    hist = json.load(open(EXEC, encoding="utf-8")) if os.path.exists(EXEC) else []
    feito = collections.Counter((h["variante"], h["caso"], h["situacao"]) for h in hist)
    gasto = sum(h["custo"] for h in hist)
    mem = Memoria()
    mem.instala()
    plano = [(v, c) for v in variantes for c in cs]
    plano += [(v + "+inj", c) for v in variantes if v in ("atual", "v1", "v1r") for c in cs if c.get("injetar")]
    for v, c in plano:
        if True:
            sits = c["situacoes"] if v == "vb" else [dict(rotulo="", texto="", regra=c["regra"])]
            for s in sits:
                for _ in range(reps - feito[(v, c["id"], s["rotulo"])]):
                    if gasto >= teto:
                        print(f"TETO de US$ {teto:.2f} atingido; parando.")
                        json.dump(hist, open(EXEC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                        return
                    o = executa(v, sigla, c, s, mem)
                    gasto += o["custo"]
                    hist.append(o)
                    rg = o["regra"]["codigo"] if o["regra"] else "-"
                    print(f"{v:5} {c['id']:4}{s['rotulo']:1}  regra={rg:7} cand={o.get('candidatas')}  US$ {gasto:.3f}", flush=True)
                    json.dump(hist, open(EXEC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"gasto total: US$ {gasto:.3f}")


# ---------------------------------------------------------------- relatório
def relatorio():
    sigla, cs = casos()
    por_id = {c["id"]: c for c in cs}
    hist = json.load(open(EXEC, encoding="utf-8"))
    L = []
    pr = lambda s="": (print(s), L.append(s))  # noqa: E731
    grupos = collections.defaultdict(list)
    for h in hist:
        grupos[(h["variante"], h["caso"], h["situacao"])].append(h)
    pr("Laboratório da regra documentada (pedido 1.3.2): prompt atual × V1 × VB, gabarito migracao/gabarito_regra_documentada.json")
    pr(f"Motor do produto com gravação em memória; {config.MODELO_CHAT}; modo PII {config.MODO_PII}; decisão por maioria das repetições.\n")
    resumo = {}
    for v in ("atual", "v1", "vb", "v1r", "atual+inj", "v1+inj", "v1r+inj"):
        if not any(k[0] == v for k in grupos):
            continue
        fa = fa_q = n_neg = n_q = sens = n_pos = instavel = n_grupos = 0
        linhas = []
        for (vv, cid, sit), hs in sorted(grupos.items()):
            if vv != v:
                continue
            c = por_id[cid]
            s_ = next((s for s in c["situacoes"] if s["rotulo"] == sit), None) if v == "vb" and sit else None
            regras = s_["regra"] if s_ else c["regra"]
            secs = s_["aceitas_secao"] if s_ else c["aceitas_secao"]
            aceitas = set(map(ncod, regras)) | set(secs)
            decis = [h["regra"]["codigo"] if h["regra"] else "-" for h in hs]
            certos = [bool(h["regra"]) and aceita(h["regra"]["secao"], regras, secs) for h in hs]
            maioria, nmai = collections.Counter(decis).most_common(1)[0]
            n_grupos += 1
            instavel += nmai < len(decis)
            if aceitas:
                n_pos += 1
                ok = sum(certos) * 2 > len(hs)
                sens += ok
            else:
                n_neg += 1
                ok = maioria == "-"
                fa += not ok
                if c["tipo"] == "Q":
                    n_q += 1
                    fa_q += not ok
            extra = ""
            if v == "vb":
                perg = sum(1 for h in hs if h.get("candidatas"))
                perdeu = sum(1 for h in hs if h.get("fechou_sem_r2"))
                extra = f"  pergunta {perg}/{len(hs)}" + (f"  fechou sem 2ª rodada {perdeu}" if perdeu else "")
            linhas.append(f"   {cid:4}{sit:1} {c['tipo']}  esperado {'/'.join(sorted(aceitas)) or 'nenhuma':12} decisões {' '.join(decis):24} "
                          f"{'OK ' if ok else 'ERRO'}{extra}" + (f"  instável" if nmai < len(decis) else ""))
        custo = sum(h["custo"] for h in hist if h["variante"] == v)
        resumo[v] = dict(fa=fa, n_neg=n_neg, fa_q=fa_q, n_q=n_q, sens=sens, n_pos=n_pos, instavel=instavel, n=n_grupos, custo=custo)
        pr(f"## {v}: falso alarme {fa}/{n_neg} (quase-regras {fa_q}/{n_q})  sensibilidade {sens}/{n_pos}  instáveis {instavel}/{n_grupos}"
           f"  custo US$ {custo:.3f}")
        for x in linhas:
            pr(x)
        pr()
    pr("## Orientações das regras apontadas (para leitura)")
    for h in hist:
        if h["regra"]:
            pr(f"   {h['variante']:5} {h['caso']:4}{h['situacao']:1} {h['regra']['codigo']}: {h['regra']['orientacao']}")
    pr("\n## Perguntas que separam as regras (VB)")
    for h in hist:
        if h["variante"] == "vb" and h.get("candidatas"):
            pr(f"   {h['caso']:4}{h['situacao']:1} candidatas {h['candidatas']}: {(h['r1'] or {}).get('pergunta_regra')}")
    pr(f"\nCusto total: US$ {sum(h['custo'] for h in hist):.3f} em {len(hist)} execuções.")
    open(SAIDA, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\n-> {SAIDA}")
    return resumo


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("etapa", choices=["seca", "roda", "relatorio"])
    ap.add_argument("--variantes", default="atual,v1,vb")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--teto", type=float, default=2.0)
    ap.add_argument("--casos", default="")
    a = ap.parse_args()
    filtro = set(a.casos.split(",")) if a.casos else None
    if a.etapa == "seca":
        seca(filtro)
    elif a.etapa == "roda":
        roda(a.variantes.split(","), a.reps, a.teto, filtro)
    else:
        relatorio()
