"""Catálogo de menus no banco do núcleo (apf.menu_sistema): gravação pelos extratores e leitura para GET /sistemas/{cd}/menus."""
import json
import os
import time

# sem importar nucleo.config: os extratores e os testes puros não dependem do .env
ARQ_DDL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "migracao", "sql", "04_menu_sistema.sql")
FONTES_PROPRIAS = ("forms", "benner", "hope", "agendamento")      # têm prioridade sobre a wiki
CACHE_S = 600
_cache = {}


def chave_sistema(sigla):
    """Mesma regra de motor.resolve_sistema: sigla minúscula, '_' no lugar de '-' e de espaço."""
    return (sigla or "").strip().lower().replace("-", "_").replace(" ", "_")


def garante(conn):
    conn.execute(open(ARQ_DDL, encoding="utf-8").read())


def sql_carga(sistema, fonte, itens, sistema_id=None):
    """SQL que troca os itens de (sistema, fonte) pelos novos: um DELETE e um INSERT por jsonb_populate_recordset.
    O mesmo texto vai para dados/menus.sql (aplicado pelo exporta_nucleo) e é executado com --aplicar."""
    linhas = [dict(sistema=sistema, sistema_id=sistema_id, fonte=fonte, id=x["id"], pai=x.get("pai"), nome=x["nome"],
                   folha=bool(x["folha"]), caminho=x["caminho"], detalhe=x.get("detalhe"), modulo=x.get("modulo"),
                   ordem=int(x["ordem"]), comando=x.get("comando"), paginas=list(x.get("paginas") or []))
              for x in itens]
    corpo = json.dumps(linhas, ensure_ascii=False)
    if "$menus$" in corpo:
        raise ValueError("texto de menu com o delimitador $menus$")
    return (f"DELETE FROM apf.menu_sistema WHERE sistema = '{sistema}' AND fonte = '{fonte}';\n"
            f"INSERT INTO apf.menu_sistema (sistema, sistema_id, fonte, id, pai, nome, folha, caminho, detalhe, modulo, ordem, comando, paginas)\n"
            f"SELECT sistema, sistema_id, fonte, id, pai, nome, folha, caminho, detalhe, modulo, ordem, comando, paginas\n"
            f"FROM jsonb_populate_recordset(NULL::apf.menu_sistema, $menus${corpo}$menus$::jsonb);\n")


def sistemas(conn):
    """({chave: (id, sigla)} de apf.sistema, {chave do alias: (id, sigla)})."""
    canon, alias = {}, {}
    for r in conn.execute("SELECT id, sigla FROM apf.sistema ORDER BY sigla"):
        canon[chave_sistema(r["sigla"])] = (r["id"], r["sigla"])
    for r in conn.execute("SELECT a.alias, s.id, s.sigla FROM apf.sistema_alias a JOIN apf.sistema s ON s.id = a.sistema_id"):
        k = chave_sistema(r["alias"])
        if k not in canon:
            alias.setdefault(k, (r["id"], r["sigla"]))
    return canon, alias


def chave_canonica(sistema_wiki, alias):
    """Chave do catálogo para um sistema da wiki: a da sigla oficial quando a wiki usa um alias ("esocial" -> "e_social")."""
    k = chave_sistema(sistema_wiki)
    return chave_sistema(alias[k][1]) if k in alias else k


def trechos_wiki(conn, alias, chaves=None):
    """Casos de uso e user stories, só a versão mais recente de cada seção, agrupados pela chave da sigla oficial.
    `chaves`: só esses sistemas (chaves canônicas)."""
    import collections
    rows = conn.execute("""SELECT DISTINCT ON (origem_dokuwiki, secao, ordem) sistema, tipo, secao, texto, origem_dokuwiki
                           FROM apf.conhecimento_trecho WHERE tipo IN ('caso_de_uso', 'user_story')
                           ORDER BY origem_dokuwiki, secao, ordem, data_versao DESC""").fetchall()
    por = collections.defaultdict(list)
    for r in rows:
        k = chave_canonica(r["sistema"], alias)
        if chaves is None or k in chaves:
            por[k].append(r)
    return por


def cargas_wiki(conn, chaves=None):
    """[(chave, "wiki", itens, sistema_id)] dos caminhos de menu citados na wiki. `chaves`: só esses sistemas."""
    from . import wiki
    canon, alias = sistemas(conn)
    sis = {**alias, **canon}
    out = []
    for k, ts in sorted(trechos_wiki(conn, alias, chaves).items()):
        siglas = {k} | {a for a, (_, s) in alias.items() if chave_sistema(s) == k}
        itens = wiki.extrai(ts, k, siglas)
        if itens:
            out.append((k, "wiki", itens, sis.get(k, (None,))[0]))
    return out


def recarrega_wiki(conn, sistemas_wiki):
    """Refaz os menus de fonte wiki dos sistemas da wiki indicados (depois de uma sincronização). Os menus próprios (FORMS etc.)
    não mudam. Não faz commit. Devolve {chave: nº de itens}."""
    _, alias = sistemas(conn)
    chaves = {chave_canonica(s, alias) for s in sistemas_wiki}
    if not chaves:
        return {}
    garante(conn)
    cargas = {k: (f, itens, sid) for k, f, itens, sid in cargas_wiki(conn, chaves)}
    out = {}
    for k in sorted(chaves):
        if k in cargas:
            f, itens, sid = cargas[k]
            conn.execute(sql_carga(k, f, itens, sid))
            out[k] = len(itens)
        else:
            conn.execute("DELETE FROM apf.menu_sistema WHERE sistema = %s AND fonte = 'wiki'", (k,))
            out[k] = 0
        _cache.pop(k, None)
    return out


def _sigla_canonica(conn, sigla):
    r = conn.execute("""SELECT sigla FROM apf.sistema WHERE upper(sigla) = upper(%s)
                        UNION ALL SELECT s.sigla FROM apf.sistema_alias a JOIN apf.sistema s ON s.id = a.sistema_id
                        WHERE upper(a.alias) = upper(%s) LIMIT 1""", (sigla, sigla)).fetchone()
    return r["sigla"] if r else sigla


def lista(conn, sigla):
    """{fonte, modulos, menus} do sistema. Prioridade: fonte própria (forms/benner/…), senão wiki, senão "nenhuma".
    Sigla desconhecida ou catálogo ainda não carregado também dão "nenhuma" (nunca erro). Cache de 10 min por sistema."""
    k = chave_sistema(sigla)
    hit = _cache.get(k)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    vazio = dict(fonte="nenhuma", modulos=[], menus=[])
    if not conn.execute("SELECT to_regclass('apf.menu_sistema') IS NOT NULL AS ok").fetchone()["ok"]:
        return vazio
    chaves = list(dict.fromkeys([chave_sistema(_sigla_canonica(conn, sigla)), k]))
    rows = conn.execute("""SELECT fonte, id, pai, nome, folha, caminho, detalhe, modulo FROM apf.menu_sistema
                           WHERE sistema = ANY(%s) ORDER BY modulo NULLS FIRST, pai NULLS FIRST, ordem, caminho""", (chaves,)).fetchall()
    fontes = {r["fonte"] for r in rows}
    fonte = next((f for f in FONTES_PROPRIAS if f in fontes), "wiki" if "wiki" in fontes else None)
    if not fonte:
        out = vazio
    else:
        menus = [{c: r[c] for c in ("id", "pai", "nome", "folha", "caminho", "detalhe", "modulo")} for r in rows if r["fonte"] == fonte]
        out = dict(fonte=fonte, modulos=list(dict.fromkeys(m["modulo"] for m in menus if m["modulo"])), menus=_em_arvore(menus))
    _cache[k] = (time.time(), out)
    return out


def _em_arvore(menus):
    """Ordem de leitura: cada item seguido dos filhos (pai antes do filho), irmãos pela ordem gravada."""
    filhos, ids = {}, {m["id"] for m in menus}
    for m in menus:
        filhos.setdefault(m["pai"] if m["pai"] in ids else None, []).append(m)
    out = []

    def desce(pai):
        for m in filhos.get(pai, []):
            out.append(m)
            desce(m["id"])
    desce(None)
    return out
