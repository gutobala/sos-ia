"""Exporta da base de pesquisa `apf` só o necessário para o núcleo, para a database `sosia` (mesmo container apf_postgres).

Regras (plano de 05/11/2026):
  - corpus do RAG só com texto ANONIMIZADO: wiki (conhecimento_*), trechos e itens de chamados/documentos/anexos/atas do SOS e das
    OS de projeto, documentos com texto_anon não bloqueados e que não sejam CONTAGEM_PDF, anexos bloqueados só com metadados;
  - histórico de contagens INTEIRO (o estimador usa o legado de 2014+): contagens, funções, TD, AR/TR, sem nomes de pessoas;
  - chamados do legado (PORTAL_TI, Mantis) só como registro mínimo (número, sistema, datas, PF), sem nenhum texto;
  - colunas cruas fora: chamado.resumo/descricao/dados_origem, *_anexo.texto_extraido/cd_pessoa_resp/caminhos,
    documento.texto_extraido (caminho vira o nome do arquivo), nomes de responsável/revisor/validador/autor;
  - experimento, git, rascunhos da abertura e triagem de PII ficam fora.
Cópia por postgres_fdw dentro do container (SQL puro, sem dado passando pela máquina). Nunca toca o pf_ia_rag_postgres.

Uso (na raiz do sos-ia):  .venv\\Scripts\\python migracao\\exporta_nucleo.py              (simula: plano + contagens da origem)
                          .venv\\Scripts\\python migracao\\exporta_nucleo.py --aplicar    (recria sosia, carrega, verifica, gera dump)
Saída: dados\\sosia.dump (pg_dump -Fc, carregado pelo docker-compose) e dados\\exporta_nucleo_relatorio.txt
"""
import argparse
import os
import subprocess
import sys
import tempfile

CONTAINER = "apf_postgres"
ORIGEM, DESTINO = "apf", "sosia"
MARCA = "sos-ia:exporta_nucleo"
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS = os.path.join(RAIZ, "dados")
PRINCIPAL = "('SOS','OS_PROJETO')"
CH_PRINCIPAL = f"(SELECT id FROM apf.chamado WHERE origem IN {PRINCIPAL})"

# (tabela, filtro sobre a origem "x" (pode citar tabelas JÁ carregadas no destino como apf.*), {coluna: expressão}, [auto-refs])
TABELAS = [
    ("carga_lote", None, {}, []),
    ("roteiro_metricas", None, {}, []),
    ("empresa", None, {}, []),
    ("contrato", None, {}, []),
    ("ordem_servico", None, {}, []),
    ("profissional", "false", {}, []),                       # vazia; só a estrutura (FK de contagem)
    ("deflator", None, {}, []),
    ("tabela_complexidade", None, {}, []),
    ("politica_estimativa", None, {"definido_por": "NULL"}, []),
    ("sistema", None, {}, ["sistema_sucessor_id"]),
    ("sistema_alias", None, {}, []),
    ("chamado", None, {"resumo": "NULL", "descricao": "NULL", "dados_origem": "NULL",
                       "resumo_anon": f"CASE WHEN x.origem IN {PRINCIPAL} THEN x.resumo_anon END",
                       "descricao_anon": f"CASE WHEN x.origem IN {PRINCIPAL} THEN x.descricao_anon END"}, []),
    ("arquivo_fonte", None, {}, ["duplicado_de_id"]),
    ("contagem", None, {"responsavel_id": "NULL", "responsavel_nome": "NULL", "revisor_nome": "NULL", "validador_nome": "NULL",
                        "observacoes": "NULL"}, ["contagem_origem_id"]),
    ("funcao_sistema", None, {}, []),
    ("contagem_funcao", None, {"observacao": "NULL"}, []),
    ("contagem_funcao_td", None, {}, []),
    ("contagem_funcao_ar_tr", None, {}, []),
    ("contagem_inm", None, {}, []),
    ("contagem_vinculo", None, {"revisado_por": "NULL", "evidencia": "NULL"}, []),
    ("funcao_sistema_historico", None, {}, []),
    ("funcao_sinonimo", None, {}, []),
    ("funcao_sistema_emb", None, {}, []),
    ("baseline_snapshot", None, {"aprovada_por": "NULL"}, []),
    ("documento", f"x.texto_anon IS NOT NULL AND NOT x.bloqueado_corpus AND coalesce(x.tipo,'') <> 'CONTAGEM_PDF' "
                  f"AND x.chamado_id IN {CH_PRINCIPAL}",
     {"texto_extraido": "NULL", "caminho": "'doc:' || x.id || ':' || x.nome_arquivo", "papel_req_autor": "NULL"}, []),
    ("chamado_anexo", f"x.chamado_id IN {CH_PRINCIPAL}",
     {"titulo": "x.nome_arquivo", "caminho_origem": "NULL", "caminho_local": "NULL", "cd_pessoa_resp": "NULL",
      "texto_extraido": "NULL", "texto_anon": "CASE WHEN NOT x.bloqueado_corpus THEN x.texto_anon END"}, []),
    ("ata_conteudo", f"x.chamado_id IN {CH_PRINCIPAL}", {}, []),
    ("requisito_trecho",
     f"(x.origem IN ('CHAMADO_RESUMO','CHAMADO_DESCRICAO') AND x.chamado_id IN {CH_PRINCIPAL}) "
     "OR (x.origem = 'DOCUMENTO' AND x.origem_id IN (SELECT id FROM apf.documento)) "
     "OR (x.origem = 'ANEXO' AND x.origem_id IN (SELECT id FROM apf.chamado_anexo WHERE texto_anon IS NOT NULL)) "
     "OR (x.origem = 'ATA' AND x.origem_id IN (SELECT id FROM apf.ata_conteudo))", {}, []),
    ("requisito_item", f"x.chamado_id IN {CH_PRINCIPAL} OR x.documento_id IN (SELECT id FROM apf.documento)",
     {"nome": "left(coalesce(x.texto_anon, '[sem texto]'), 200)", "detalhamento": "NULL", "impacto": "NULL",
      "documento_id": "CASE WHEN x.documento_id IN (SELECT id FROM apf.documento) THEN x.documento_id END"}, []),
    ("requisito_item_funcao", "x.item_id IN (SELECT id FROM apf.requisito_item)", {}, []),
    ("conhecimento_trecho", None, {}, []),
    ("conhecimento_referencia", None, {}, []),
]

# colunas de texto varridas atrás de PII no destino (wiki à parte: documentação interna, só informada)
VARREDURA = [("chamado", "coalesce(resumo_anon,'')||' '||coalesce(descricao_anon,'')"), ("chamado_anexo", "coalesce(texto_anon,'')||' '||titulo"),
             ("documento", "coalesce(texto_anon,'')||' '||nome_arquivo"), ("requisito_trecho", "texto"),
             ("requisito_item", "coalesce(texto_anon,'')||' '||nome"), ("ata_conteudo", "coalesce(texto_anon,'')"),
             ("contagem", "concat_ws(' ', escopo, proposito, fronteira, descricao_alteracao, os_referencia)"),
             ("conhecimento_trecho", "texto")]
RX_PII = {"cpf": r"\d{3}\.\d{3}\.\d{3}-\d{2}", "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}",
          # telefone: (DDD) opcional + 4/5 dígitos-4 dígitos; não pega nº de processo (0070-000451/2012) nem datas/códigos
          "telefone": r"(?<![0-9/.-])(\(?0?[0-9]{2}\)?\s?)?9?[0-9]{4}-[0-9]{4}(?![0-9/-])"}
SUBST = {"cpf": "[CPF]", "email": "[EMAIL]", "telefone": "[TELEFONE]"}
# colunas mascaradas no destino (texto que vai para prompts e para a rede interna)
MASCARAR = {"chamado": ["resumo_anon", "descricao_anon"], "chamado_anexo": ["texto_anon"], "documento": ["texto_anon"],
            "requisito_trecho": ["texto"], "requisito_item": ["texto_anon", "nome"], "ata_conteudo": ["texto_anon"],
            "contagem": ["escopo", "proposito", "fronteira", "descricao_alteracao", "os_referencia"],
            "conhecimento_trecho": ["texto", "texto_indexado"], "conhecimento_referencia": ["texto_destino"]}


def _rx_nome(n):
    import re
    return r"\m" + re.sub(r"\s+", r"\\s+", re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", n.strip())) + r"\M"


def mascara(nomes):
    """CPF, e-mail e telefone -> marcador; nomes conhecidos (contadores/revisores) -> [PESSOA]. Devolve [(tabela, coluna, n)]."""
    out = []
    for t, cols in MASCARAR.items():
        for col in cols:
            regras = [(RX_PII[k], SUBST[k]) for k in RX_PII] + [(_rx_nome(n), "[PESSOA]") for n in nomes]
            expr = col
            for rx, s in regras:
                expr = f"regexp_replace({expr}, '{rx.replace(chr(39), chr(39) * 2)}', '{s}', 'gi')"
            cond = " OR ".join(f"{col} ~* '{rx.replace(chr(39), chr(39) * 2)}'" for rx, _ in regras)
            n = psql(DESTINO, f"WITH u AS (UPDATE apf.{t} SET {col} = {expr} WHERE {col} IS NOT NULL AND ({cond}) RETURNING 1) "
                              f"SELECT count(*) FROM u;").strip()
            out.append((t, col, int(n or 0)))
    return out


def psql(db, sql=None, arquivo=None, at=True):
    """Roda SQL no container (arquivo copiado com docker cp + psql -f, como manda o projeto)."""
    if sql is not None:
        fd, arquivo = tempfile.mkstemp(suffix=".sql")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sql)
    dest = "/tmp/" + os.path.basename(arquivo)
    subprocess.run(["docker", "cp", arquivo, f"{CONTAINER}:{dest}"], check=True, capture_output=True)
    r = subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", "apf", "-d", db, "-v", "ON_ERROR_STOP=1", "-q"]
                       + (["-At", "-F", "|"] if at else []) + ["-f", dest], capture_output=True, text=True, encoding="utf-8")
    subprocess.run(["docker", "exec", CONTAINER, "rm", "-f", dest], capture_output=True)
    if sql is not None:
        os.remove(arquivo)
    if r.returncode != 0:
        raise RuntimeError(f"psql {db} falhou:\n{r.stderr[-3000:]}")
    return r.stdout


def colunas(t):
    out = psql(ORIGEM, f"SELECT column_name FROM information_schema.columns WHERE table_schema='apf' AND table_name='{t}' "
                       f"AND is_generated='NEVER' ORDER BY ordinal_position;")
    return [c for c in out.split("\n") if c]


def sql_carga():
    linhas = ["SET search_path TO apf, public;"]
    for t, filtro, over, autos in TABELAS:
        cols = colunas(t)
        exprs = []
        for c in cols:
            e = over.get(c, f"x.{c}")
            if c in autos:
                e = "NULL"
            exprs.append(e)
        linhas.append(f"INSERT INTO apf.{t} ({', '.join(cols)}) SELECT {', '.join(exprs)} FROM src.{t} x"
                      + (f" WHERE {filtro}" if filtro else "") + ";")
        for a in autos:
            linhas.append(f"UPDATE apf.{t} d SET {a} = s.{a} FROM src.{t} s WHERE s.id = d.id AND s.{a} IS NOT NULL "
                          f"AND s.{a} IN (SELECT id FROM apf.{t});")
        linhas.append(f"\\echo carregada {t}")
    # sequências no máximo de cada id
    linhas.append("""DO $$ DECLARE r record; BEGIN
  FOR r IN SELECT s.relname seq, t.relname tab, a.attname col FROM pg_class s JOIN pg_depend d ON d.objid = s.oid
           JOIN pg_class t ON t.oid = d.refobjid JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
           JOIN pg_namespace n ON n.oid = s.relnamespace WHERE s.relkind = 'S' AND n.nspname = 'apf' LOOP
    EXECUTE format('SELECT setval(%L, coalesce((SELECT max(%I) FROM apf.%I), 0) + 1, false)', 'apf.' || r.seq, r.col, r.tab);
  END LOOP; END $$;""")
    return "\n".join(linhas) + "\n"


def contagens(db):
    sql = " UNION ALL ".join(f"SELECT '{t}', count(*) FROM apf.{t}" for t, *_ in TABELAS) + ";"
    return {k: int(v) for k, v in (l.split("|") for l in psql(db, sql).split("\n") if l)}


def simula():
    print(f"SIMULAÇÃO: nada é criado. Origem {ORIGEM} -> destino {DESTINO} (container {CONTAINER}).\n")
    n = contagens(ORIGEM)
    for t, filtro, over, autos in TABELAS:
        regra = "todas as linhas" if not filtro else ("nenhuma linha (só estrutura)" if filtro == "false" else f"filtro: {filtro[:110]}")
        anul = [c for c, e in over.items() if e == "NULL"]
        print(f"  {t:26} origem {n[t]:7} | {regra}" + (f" | anula: {', '.join(anul)}" if anul else ""))
    print("\nRode com --aplicar para criar a database e gerar o dump.")


def aplica():
    rel = []

    def log(s=""):
        print(s, flush=True)
        rel.append(s)
    existe = psql("postgres", f"SELECT coalesce(shobj_description(oid, 'pg_database'), '') FROM pg_database WHERE datname='{DESTINO}';").strip()
    if existe and existe != MARCA:
        sys.exit(f"a database {DESTINO} existe e não foi criada por este script (comentário '{existe}'): nada foi feito")
    psql("postgres", f"DROP DATABASE IF EXISTS {DESTINO};")
    psql("postgres", f"CREATE DATABASE {DESTINO} OWNER apf; COMMENT ON DATABASE {DESTINO} IS '{MARCA}';")
    log(f"database {DESTINO} criada")
    # estrutura: extensões, schema, função norm e DDL das tabelas (pg_dump --schema-only)
    fn = psql(ORIGEM, "SELECT pg_get_functiondef('apf.norm'::regproc);")
    psql(DESTINO, "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS unaccent; "
                  "CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE SCHEMA apf AUTHORIZATION apf;\n" + fn + ";\n")
    args = ["docker", "exec", CONTAINER, "pg_dump", "-U", "apf", "-d", ORIGEM, "--schema-only", "--no-owner", "-f", "/tmp/ddl_nucleo.sql"]
    for t, *_ in TABELAS:
        args += ["-t", f"apf.{t}"]
    subprocess.run(args, check=True, capture_output=True)
    r = subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", "apf", "-d", DESTINO, "-v", "ON_ERROR_STOP=1", "-q", "-f",
                        "/tmp/ddl_nucleo.sql"], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError("DDL falhou:\n" + r.stderr[-3000:])
    log("estrutura criada (pg_dump --schema-only das tabelas do núcleo)")
    # carga pelo postgres_fdw
    psql(DESTINO, f"""CREATE EXTENSION postgres_fdw;
CREATE SERVER origem FOREIGN DATA WRAPPER postgres_fdw OPTIONS (dbname '{ORIGEM}', fetch_size '2000');
CREATE USER MAPPING FOR apf SERVER origem OPTIONS (user 'apf', password 'apf_local');
CREATE SCHEMA src; IMPORT FOREIGN SCHEMA apf LIMIT TO ({', '.join(t for t, *_ in TABELAS)}) FROM SERVER origem INTO src;""")
    out = psql(DESTINO, sql_carga(), at=False)
    log(f"carga: {out.count('carregada')} tabelas")
    psql(DESTINO, "DROP SCHEMA src CASCADE; DROP USER MAPPING FOR apf SERVER origem; DROP SERVER origem; DROP EXTENSION postgres_fdw;")
    nomes = [n for n in psql(ORIGEM, "SELECT DISTINCT trim(x) FROM (SELECT responsavel_nome x FROM apf.contagem UNION SELECT revisor_nome "
                                     "FROM apf.contagem UNION SELECT validador_nome FROM apf.contagem) s WHERE x ~ '\\S+\\s+\\S+' "
                                     "AND length(trim(x)) >= 8;").split("\n") if n]
    log("\n## Mascaramento no núcleo (só em sosia; a base apf não muda)")
    for t, col, n in mascara(nomes):
        log(f"  {t}.{col}: {n} linhas alteradas")
    psql(DESTINO, "VACUUM ANALYZE;")
    # verificação: contagens e PII
    no, nd = contagens(ORIGEM), contagens(DESTINO)
    log("\n## Linhas por tabela (origem -> núcleo)")
    for t, *_ in TABELAS:
        log(f"  {t:26} {no[t]:8} -> {nd[t]:8}")
    log("\n## Varredura de PII no núcleo (padrões crus; [EMAIL]/[CPF] anonimizados não contam)")
    falhas = 0
    for t, expr in VARREDURA:
        partes = ", ".join(f"count(*) FILTER (WHERE ({expr}) ~ '{rx}')" for rx in RX_PII.values())
        v = psql(DESTINO, f"SELECT {partes} FROM apf.{t};").strip().split("|")
        achados = dict(zip(RX_PII, map(int, v)))
        falhas += sum(achados.values())
        log(f"  {t:22} " + "  ".join(f"{k} {n}" for k, n in achados.items()))
    if nomes:
        arr = "ARRAY[" + ",".join("'%" + n.replace("'", "''") + "%'" for n in nomes) + "]"
        log(f"\n## Nomes de contadores/revisores ({len(nomes)}) encontrados no texto do núcleo")
        for t, expr in VARREDURA:
            k = int(psql(DESTINO, f"SELECT count(*) FROM apf.{t} WHERE ({expr}) ILIKE ANY ({arr});").strip())
            log(f"  {t:22} {k}")
            falhas += k
    log(f"\nresultado da varredura: {'OK' if falhas == 0 else f'{falhas} ACHADOS — revisar antes de distribuir o dump'}")
    # dump para o docker-compose
    os.makedirs(DADOS, exist_ok=True)
    subprocess.run(["docker", "exec", CONTAINER, "pg_dump", "-U", "apf", "-d", DESTINO, "-Fc", "--no-owner", "-f", "/tmp/sosia.dump"],
                   check=True, capture_output=True)
    subprocess.run(["docker", "cp", f"{CONTAINER}:/tmp/sosia.dump", os.path.join(DADOS, "sosia.dump")], check=True, capture_output=True)
    subprocess.run(["docker", "exec", CONTAINER, "rm", "-f", "/tmp/sosia.dump", "/tmp/ddl_nucleo.sql"], capture_output=True)
    tam = os.path.getsize(os.path.join(DADOS, "sosia.dump")) / 2 ** 20
    tam_db = psql(DESTINO, f"SELECT pg_size_pretty(pg_database_size('{DESTINO}'));").strip()
    log(f"\ndump: dados\\sosia.dump ({tam:.0f} MB; database {tam_db})")
    with open(os.path.join(DADOS, "exporta_nucleo_relatorio.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(rel) + "\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    aplica() if a.aplicar else simula()
