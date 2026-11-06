"""Prévia de PF a partir do texto do pedido (Fase 3; cópia do runtime de PRE-PROJETO/app/abertura.calcula_previa).

O LLM identifica as funções (prompt congelado previa_funcoes_v2_congelado_20261104 + convenções da fábrica + até 3 contagens
anteriores do mesmo sistema, só anteriores à data de referência); regras fixas (normaliza, expansão R3, sem duplicatas);
N execuções e fica a de PF mediano. PF principal = NESMA estimada (+0,6 por item MI); referência "tudo ALTA" ao lado.
A complexidade pelo histórico (historico.Historico.pf) é calculada à parte sobre a mesma lista.
"""
import datetime
import json
import os
from concurrent.futures import ThreadPoolExecutor

from .. import config, llm, pii
from . import regras

PROMPT = os.path.join(config.RAIZ, "prompts", "previa_funcoes_v2_congelado_20261104.md")
SCHEMA = os.path.join(config.RAIZ, "schemas", "schema_identificacao.json")
VAZIO = "(vazio nesta condição)"
FAIXAS = ((9, "pequena"), (25, "média"), (float("inf"), "grande"))
PADROES = (("lista de seleção (combo)", r"combo|lista de sele|autocomplet"),
           ("consulta implícita", r"impl[ií]cit"),
           ("exportação (Excel/PDF/CSV)", r"export|excel|xls|csv|pdf"),
           ("script, importação, carga ou migração", r"script|importa|carga|migra"),
           ("relatório", r"^(relat|gerar relat|emitir relat)"),
           ("listar, consultar, pesquisar, visualizar", r"^(listar|consultar|pesquisar|visualizar|detalhar)"))
_cache = {}


def _exclui(excluir):
    """Filtro de chamados que não podem servir de exemplo/convenção (ex.: os de um teste). Vazio em produção."""
    if not excluir:
        return "true", ()
    return "NOT (ch.numero = ANY(%s))", (list(map(str, excluir)),)


def tabela_convencoes(conn, excluir=None):
    chave = ("tab", tuple(sorted(map(str, excluir or ()))))
    if chave in _cache:
        return _cache[chave]
    filtro, par = _exclui(excluir)
    linhas = []
    for rot, rx in PADROES:
        r = conn.execute(
            f"""SELECT count(*) n, count(*) FILTER (WHERE f.tipo_funcao = 'CE') ce, count(*) FILTER (WHERE f.tipo_funcao = 'SE') se,
                       count(*) FILTER (WHERE f.tipo_funcao = 'EE') ee
                FROM apf.contagem_funcao f JOIN apf.contagem k ON k.id = f.contagem_id AND k.tipo = 'DETALHADA'
                JOIN apf.chamado ch ON ch.id = k.chamado_id
                WHERE f.tipo_funcao IN ('EE', 'CE', 'SE') AND f.nome_funcao ~* %s AND {filtro}""", (rx, *par)).fetchone()
        if r["n"]:
            pct = {t: round(100 * r[t.lower()] / r["n"]) for t in ("CE", "SE", "EE")}
            linhas.append(f"- {rot}: CE {pct['CE']}%, SE {pct['SE']}%, EE {pct['EE']}% (n = {r['n']})")
    extra = conn.execute(
        f"""SELECT round(avg(n), 1) media FROM (SELECT k.id, count(*) n FROM apf.contagem k JOIN apf.contagem_funcao f ON f.contagem_id = k.id
             JOIN apf.chamado ch ON ch.id = k.chamado_id WHERE k.tipo = 'DETALHADA' AND f.tipo_funcao IN ('ALI', 'AIE', 'EE', 'CE', 'SE')
             AND {filtro} GROUP BY k.id) z""", par).fetchone()
    txt = ("Classificação que a fábrica deste contrato costuma dar a funções com estes padrões de nome, medida nas contagens oficiais:\n"
           + "\n".join(linhas)
           + "\n- Cada operação de um cadastro (incluir, alterar, excluir) é contada como uma EE separada; a tela de edição costuma ter "
             "também uma consulta implícita (CE) que recupera o registro.\n"
           + "- Cada lista de seleção que lê dados de outro cadastro é contada como uma CE própria.\n"
           + f"- Em média, uma contagem detalhada tem {extra['media']} funções mensuráveis.")
    _cache[chave] = txt
    return txt


def exemplos(conn, sistema_id, dt_ref, k=3, excluir=None):
    filtro, par = _exclui(excluir)
    rows = conn.execute(
        f"""SELECT ch.numero, ch.resumo_anon, kk.dt_contagem, kk.id kid FROM apf.chamado ch
            JOIN LATERAL (SELECT k.id, k.dt_contagem FROM apf.contagem k WHERE k.chamado_id = ch.id AND k.tipo = 'DETALHADA'
                          AND k.dt_contagem IS NOT NULL ORDER BY k.dt_contagem DESC, k.id DESC LIMIT 1) kk ON true
            WHERE ch.origem = 'SOS' AND ch.status = 'FATURADO' AND ch.sistema_id = %s AND kk.dt_contagem < %s::date AND ch.dt_abertura < %s::date
              AND kk.dt_contagem >= ch.dt_abertura::date AND {filtro}
              AND (SELECT count(*) FROM apf.contagem_funcao f WHERE f.contagem_id = kk.id AND f.tipo_funcao IN ('ALI', 'AIE', 'EE', 'CE', 'SE')) BETWEEN 2 AND 8
            ORDER BY kk.dt_contagem DESC LIMIT %s""", (sistema_id, dt_ref, dt_ref, *par, k)).fetchall()
    mov = lambda a: "INCLUSAO" if (a or "").upper() == "I" else "EXCLUSAO" if (a or "").upper() == "E" else "ALTERACAO"  # noqa: E731
    blocos = []
    for i, r in enumerate(rows, 1):
        fs = conn.execute("""SELECT tipo_funcao, acao, nome_funcao FROM apf.contagem_funcao WHERE contagem_id = %s
                             AND tipo_funcao IN ('ALI', 'AIE', 'EE', 'CE', 'SE') ORDER BY ordem""", (r["kid"],)).fetchall()
        corpo = "\n".join(f"  {f['tipo_funcao']} | {mov(f['acao'])} | {f['nome_funcao']}" for f in fs)
        blocos.append(f"Exemplo {i} — pedido: {pii.anonimizar((r['resumo_anon'] or '')[:300], conn, 6000)}\n"
                      f"  Funções da contagem oficial:\n{pii.anonimizar(corpo, conn, 6000)}")
    return "\n\n".join(blocos)


def monta_prompt(conn, sigla, texto, data_ref, excluir=None):
    s = conn.execute("SELECT id FROM apf.sistema WHERE lower(sigla) = lower(%s)", (sigla,)).fetchone()
    exs = exemplos(conn, s["id"], data_ref, excluir=excluir) if s else ""
    p = open(PROMPT, encoding="utf-8").read()
    texto_llm = pii.anonimizar(texto, conn) if config.MODO_PII == "local" else texto
    return (p.replace("{{SISTEMA}}", sigla or "").replace("{{DATA_REFERENCIA}}", str(data_ref)).replace("{{TEXTO_CHAMADO}}", texto_llm)
             .replace("{{REQUISITOS_JSON}}", "(o chamado acima já traz as mudanças numeradas, uma por item)")
             .replace("{{DOCUMENTOS_RECUPERADOS}}", VAZIO).replace("{{CONVENCOES}}", tabela_convencoes(conn, excluir))
             .replace("{{EXEMPLOS}}", exs or "(nenhuma contagem anterior do mesmo sistema disponível)"))


def calcula(conn, sigla, texto, data_ref=None, execucoes=3, excluir=None, papel="previa"):
    """Devolve (prévia, lista final de funções). execucoes 1–3 (sementes 7, 8, 9); fica a de PF NESMA mediano."""
    data_ref = data_ref or datetime.date.today()
    p = monta_prompt(conn, sigla, texto, data_ref, excluir)
    schema = regras.aceita_mi(json.load(open(SCHEMA, encoding="utf-8")))
    sementes = (7, 8, 9)[: max(1, min(3, execucoes))]
    with ThreadPoolExecutor(max_workers=len(sementes)) as ex:
        res = list(ex.map(lambda sd: llm.json_estruturado(p, schema, "identificacao", papel, temperatura=0, seed=sd), sementes))
    rodadas = []
    for resp, _ in res:
        fs = regras.sem_duplicatas(regras.expande(regras.normaliza(resp["funcoes_candidatas"]), "R3"))
        n, nesma = regras.conta(fs)
        mi = regras.PF_MI * regras.n_mi(fs)
        rodadas.append(((n, regras.pf_alta(fs) + mi, nesma + mi), fs))
    rodadas.sort(key=lambda r: r[0][2])
    (n, pf_alta, pf), fs = rodadas[len(rodadas) // 2]
    conta_ok = lambda f: f["tipo_ifpug"] in regras.PESO and f["movimento"] not in ("SEM_ALTERACAO", "NAO_MENSURAVEL")  # noqa: E731
    item = lambda f: dict(tipo=f["tipo_ifpug"], movimento=f["movimento"], nome=f["nome_funcao"])  # noqa: E731
    custo = [inf.get("custo_usd") for _, inf in res]
    pv = dict(n_funcoes=n, n_mi=regras.n_mi(fs), pf=round(pf, 1), pf_alta=round(pf_alta, 1),
              faixa=next(rot for lim, rot in FAIXAS if pf <= lim), funcoes=[item(f) for f in fs if conta_ok(f)],
              nao_contadas=[item(f) for f in fs if not conta_ok(f)], pf_execucoes=[round(r[0][2], 1) for r in rodadas],
              modelo=res[0][1]["modelo"], custo_usd=None if None in custo else round(sum(custo), 5))
    return pv, fs
