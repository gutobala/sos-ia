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
