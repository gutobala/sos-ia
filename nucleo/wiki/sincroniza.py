"""Sincroniza o RAG da wiki (apf.conhecimento_trecho / conhecimento_referencia) com a DokuWiki, pela API. Simula por padrão.

Passos (pedido docs/pedido-sincronizacao-wiki.md):
  1. lista as páginas 'sistemas:*' e baixa só as que mudaram de data ou de tamanho desde a última execução (cache mascarado em
     apf.wiki_pagina_sync; na 1ª execução baixa todas, ~5 min);
  2. converte a wiki INTEIRA em memória (nucleo.wiki.conversao/trechos, a lógica do laboratório): o índice de uma página dá o
     nome de outra e a página inicial dá a categoria do sistema, então uma alteração pode mudar trechos de páginas que não mudaram;
  3. mascara (CPF, e-mail, telefone e nomes conhecidos) antes de gravar e de gerar o vetor;
  4. compara, página a página, os trechos novos com os gravados e grava só as páginas diferentes, cada uma numa transação
     (apaga e insere; a versão antiga vai para apf.wiki_trecho_arquivo). Nunca ficam duas versões da mesma página;
  5. vetor 3-large só para os textos novos (texto igual reaproveita o vetor, inclusive o e5); o e5 fica NULL nas linhas novas;
  6. refaz por diferença as referências, o sistemas_acesso e a data_versao (= lastModified da página); páginas novas ganham o
     dt_pagina_criada (versão mais antiga da wiki ou 1ª data do histórico de mudanças, o que vier antes);
  7. refaz os menus de fonte wiki dos sistemas afetados e grava o relatório em apf.wiki_sync_execucao.
A API da entrevista continua respondendo: cada página é gravada numa transação curta e a leitura não espera a escrita.
"""
import collections
import contextlib
import datetime
import hashlib
import json
import logging
import os
import re
import time

from .. import config, db as dbm, embeddings, precos
from . import cliente as cli, conversao, mascara, trechos as tr

TRAVA = 51990211                       # pg_advisory_lock: uma sincronização por vez (agenda × manual)
DDL = os.path.join(config.RAIZ, "migracao", "sql", "05_wiki_sync.sql")
MAX_REMOVIDAS = 0.05                   # mais que 5% das páginas sumindo de uma vez = problema na API, não na wiki
TENTATIVAS, ESPERA_S = 3, 5            # getPage: tentativas por página e espera crescente entre elas
# Carga espalhada no banco (a mesma instância atende a API da entrevista):
PAUSA_PAGINA_S = 0.2                   # entre as páginas regravadas
MAX_DATA_VERSAO, LOTE_DATA_VERSAO, PAUSA_LOTE_S = 600, 50, 1.0   # data_versao das páginas iguais: por execução, por commit, pausa
CAMPOS = ("secao", "ordem", "texto", "texto_indexado", "sistema", "arquivo_vault", "tipo", "peso", "situacao", "categoria",
          "titulo_pagina", "definicoes", "refs")
RX_HISTORICO = re.compile(r"(?im)^\s*={2,6}.*hist[óo]rico de mudan.*$")
RX_TITULO = re.compile(r"(?m)^\s*={2,6}[^=\n]+={2,6}\s*$")
RX_DATA = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
log_padrao = logging.getLogger("uvicorn.error.wiki")


class SincronizacaoAbortada(RuntimeError):
    pass


# ------------------------------------------------------------------ funções puras (testadas sem banco nem API)
def data_historico(texto_bruto, hoje=None):
    """Data mais antiga da tabela "Histórico de mudanças" da página (DokuWiki), entre 2010 e hoje; None se não houver."""
    hoje = hoje or datetime.date.today()
    m = RX_HISTORICO.search(texto_bruto or "")
    if not m:
        return None
    resto = texto_bruto[m.end():]
    prox = RX_TITULO.search(resto)
    datas = []
    for d, mes, a in RX_DATA.findall(resto[:prox.start()] if prox else resto):
        try:
            x = datetime.date(int(a), int(mes), int(d))
        except ValueError:
            continue
        if datetime.date(2010, 1, 1) <= x <= hoje:
            datas.append(x)
    return datetime.datetime.combine(min(datas), datetime.time(), datetime.timezone.utc) if datas else None


def chave_comparacao(t):
    """O que define um trecho igual: texto, metadados, definições e citações (o `hash` antigo não entra: era sem máscara)."""
    return tuple(json.dumps(t[c], ensure_ascii=False, sort_keys=True) if c == "definicoes" else
                 (float(t[c]) if c == "peso" else (list(t[c]) if c == "refs" else t[c])) for c in CAMPOS)


def classifica(antigos, novos):
    """antigos/novos: {página: [trechos]}. -> (novas, alteradas, removidas, iguais), listas de páginas."""
    novas = sorted(set(novos) - set(antigos))
    removidas = sorted(set(antigos) - set(novos))
    alteradas, iguais = [], []
    for p in sorted(set(novos) & set(antigos)):
        a = [chave_comparacao(t) for t in sorted(antigos[p], key=lambda t: t["ordem"])]
        n = [chave_comparacao(t) for t in sorted(novos[p], key=lambda t: t["ordem"])]
        (iguais if a == n else alteradas).append(p)
    return novas, alteradas, removidas, iguais


def mascara_trechos(trechos, refs, nomes):
    """Aplica a máscara nos textos que vão ao banco e ao vetor; o hash passa a ser o do texto mascarado."""
    for t in trechos:
        t["texto"] = mascara.texto(t["texto"], nomes)
        t["texto_indexado"] = mascara.texto(t["texto_indexado"], nomes)
        t["definicoes"] = {k: mascara.texto(v, nomes) for k, v in t["definicoes"].items()}
        t["n_caracteres"] = len(t["texto"])
        t["hash"] = hashlib.sha256(t["texto_indexado"].encode("utf-8")).hexdigest()
    for r in refs:
        r["texto_destino"] = mascara.texto(r["texto_destino"], nomes)


# ------------------------------------------------------------------ banco
def _existe(conn, tabela):
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (tabela,)).fetchone()["ok"]


def _vetor(v):
    """Literal pgvector. O vetor lido do banco vem como pgvector.Vector (não é iterável); o da API, como numpy."""
    if v is None:
        return None
    if hasattr(v, "to_list"):
        v = v.to_list()
    return embeddings.vetor_sql(v)


class Sincronizacao:
    def __init__(self, aplicar=False, sistema=None, tudo=False, origem="manual", log=None, wiki=None, conn=None, teto_usd=1.0,
                 forcar=False):
        self.aplicar, self.sistema, self.tudo, self.origem = aplicar, sistema, tudo, origem
        self.log = log or print
        self.wiki = wiki or cli.Wiki()
        self.conn = conn
        self.teto_usd, self.forcar = teto_usd, forcar
        self.res = dict(aplicado=aplicar, sistema=sistema, url=self.wiki.endereco, erros=[])
        self.exec_id = None

    @contextlib.contextmanager
    def _tx(self):
        """Uma transação de verdade, com commit no fim. (No psycopg 3, `conn.transaction()` dentro da transação implícita das
        leituras vira SAVEPOINT, e nada seria gravado antes do fim da execução.)"""
        self.conn.commit()
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---- 1. o que mudou na wiki
    @staticmethod
    def _tenta(fn, *args):
        """Chamada à API com novas tentativas: a wiki às vezes passa do timeout numa página (4 de 4.884 na 1ª carga) e o DNS
        já falhou de passagem (11/11/2026)."""
        for k in range(TENTATIVAS):
            try:
                return fn(*args)
            except Exception:  # noqa: BLE001
                if k == TENTATIVAS - 1:
                    raise
                time.sleep(ESPERA_S * (k + 1))

    def _baixa_pagina(self, i):
        return self._tenta(self.wiki.pagina, i)

    def baixa(self):
        remotas = self._tenta(self.wiki.paginas)
        cache = {}
        if _existe(self.conn, "apf.wiki_pagina_sync"):
            for r in self.conn.execute("SELECT origem_dokuwiki, last_modified, tamanho, texto_wiki, dt_primeira_versao "
                                       "FROM apf.wiki_pagina_sync WHERE removida_em IS NULL").fetchall():
                cache[r["origem_dokuwiki"]] = r
        self.conn.commit()                    # não segura transação aberta durante o download (minutos)
        if cache and len(remotas) < (1 - MAX_REMOVIDAS) * len(cache) and not self.forcar:
            raise SincronizacaoAbortada(f"a API listou {len(remotas)} páginas e o cache tem {len(cache)}: nada foi feito "
                                        "(use --forcar se a wiki encolheu de verdade)")
        nomes = mascara.regex_nomes()
        textos, baixar, w = {}, [], dict(paginas=len(remotas), primeira_carga=not cache, novas=[], alteradas=[], so_data=0)
        for i, (lm, tam) in remotas.items():
            c = cache.get(i)
            if c is None or self.tudo or lm > c["last_modified"] or tam != c["tamanho"]:
                baixar.append(i)
            else:
                textos[i] = c["texto_wiki"]
        t0 = time.time()
        self.baixadas, self.indisponiveis = {}, set()
        for n, i in enumerate(baixar, 1):
            try:
                texto = mascara.bruto(self._baixa_pagina(i), nomes)
            except Exception as e:  # noqa: BLE001
                self.res["erros"].append(f"baixar {i}: {type(e).__name__}: {str(e)[:200]}")
                if i in cache:
                    textos[i] = cache[i]["texto_wiki"]      # fica a versão anterior; a próxima execução tenta de novo
                else:
                    self.indisponiveis.add(i)
                continue
            textos[i] = texto
            c = cache.get(i)
            if c is None:
                w["novas"].append(i)
            elif texto.replace("\r\n", "\n") != c["texto_wiki"].replace("\r\n", "\n"):
                w["alteradas"].append(i)
            else:
                w["so_data"] += 1
            self.baixadas[i] = texto
            if n % 500 == 0:
                self.log(f"  baixadas {n}/{len(baixar)} ({time.time() - t0:.0f}s)")
        w["removidas"] = sorted(set(cache) - set(remotas))
        w["baixadas"], w["baixar_s"] = len(baixar), round(time.time() - t0, 1)
        w["indisponiveis"] = sorted(self.indisponiveis)
        if w["primeira_carga"]:
            w["novas"] = []                   # sem cache, tudo é "novo" para o cache; o que conta é a comparação com o RAG
        self.res["wiki"] = w
        self.remotas, self.cache, self.textos = remotas, cache, textos
        if self.aplicar:
            self.grava_cache()
        if self.indisponiveis and self.aplicar:
            # sem o texto, a página sumiria do RAG, e um índice que falta muda o nome de outras páginas: não grava nada
            raise SincronizacaoAbortada(f"{len(self.indisponiveis)} páginas não baixaram depois de {TENTATIVAS} tentativas "
                                        f"({', '.join(sorted(self.indisponiveis)[:5])}): nada foi gravado no RAG")
        self.log(f"wiki: {len(remotas)} páginas; baixadas {len(baixar)} em {w['baixar_s']}s" +
                 ("" if w["primeira_carga"] else f" (novas {len(w['novas'])}, alteradas {len(w['alteradas'])}, "
                                                 f"só a data {w['so_data']}, removidas {len(w['removidas'])})"))

    # ---- 2 e 3. conversão completa e máscara
    def converte(self):
        t0 = time.time()
        conv = conversao.Conversor(self.textos, self._tenta(self.wiki.midias))
        self.notas = conv.converte()
        self.trechos = tr.carrega(self.notas)
        self.refs = tr.resolve(self.trechos)
        cruz, _ = tr.acesso_e_cruzadas(self.trechos, self.refs, self.notas)
        mascara_trechos(self.trechos, self.refs, mascara.regex_nomes())
        for e in conv.stats.get("erros_paginas", []):
            self.res["erros"].append("converter " + e)
        self.res["conversao"] = dict(notas=len(self.notas), trechos=len(self.trechos), referencias=len(self.refs), cruzadas=cruz,
                                     midia_nao_encontrada=conv.stats["midia_nao_encontrada"], segundos=round(time.time() - t0, 1))
        self.log(f"conversão: {len(self.notas)} notas, {len(self.trechos)} trechos, {len(self.refs)} referências "
                 f"({time.time() - t0:.0f}s)")

    def no_escopo(self, sistema):
        return self.sistema is None or sistema == self.sistema

    # ---- 4. comparação com o banco
    def compara(self):
        self.antigos = collections.defaultdict(list)
        for r in self.conn.execute(
                """SELECT id, origem_dokuwiki, secao, ordem, texto, texto_indexado, sistema, arquivo_vault, tipo, peso, situacao,
                          categoria, titulo_pagina, definicoes, refs, data_versao, sistemas_acesso, dt_pagina_criada,
                          dt_pagina_meta FROM apf.conhecimento_trecho WHERE fonte = 'wiki'""").fetchall():
            if self.no_escopo(r["sistema"]):
                self.antigos[r["origem_dokuwiki"]].append(r)
        self.novos = collections.defaultdict(list)
        for t in self.trechos:
            if self.no_escopo(t["sistema"]):
                self.novos[t["origem_dokuwiki"]].append(t)
        for p in self.indisponiveis:            # (só na simulação) sem o texto, a página fica como está: nunca "removida"
            self.antigos.pop(p, None)
            self.novos.pop(p, None)
        self.p_novas, self.p_alteradas, self.p_removidas, self.p_iguais = classifica(self.antigos, self.novos)
        limite = max(50, int(MAX_REMOVIDAS * len(self.antigos)))
        if len(self.p_removidas) > limite and not self.forcar:
            raise SincronizacaoAbortada(f"{len(self.p_removidas)} páginas sairiam do RAG (limite {limite}): nada foi gravado "
                                        "(confira o relatório; use --forcar se for isso mesmo)")
        self.res["rag"] = dict(novas=self.p_novas, alteradas=self.p_alteradas, removidas=self.p_removidas,
                               iguais=len(self.p_iguais))
        self.log(f"RAG: páginas novas {len(self.p_novas)}, alteradas {len(self.p_alteradas)}, removidas {len(self.p_removidas)}, "
                 f"iguais {len(self.p_iguais)}")

    # ---- 5. vetores
    def vetores(self):
        """{texto_indexado: (e5, 3-large, modelo do e5)} dos textos a gravar: reaproveitados do banco e, ao aplicar, novos da API."""
        gravar = [t for p in self.p_novas + self.p_alteradas for t in self.novos[p]]
        precisa = {t["texto_indexado"] for t in gravar}
        por_texto = {}
        for ts in self.antigos.values():
            for r in ts:
                if r["texto_indexado"] in precisa:
                    por_texto.setdefault(r["texto_indexado"], r["id"])
        vet = {}
        if por_texto:
            ids = list(por_texto.values())
            for r in self.conn.execute("SELECT texto_indexado, embedding, embedding_3l, modelo_embedding FROM apf.conhecimento_trecho "
                                       "WHERE id = ANY(%s)", (ids,)).fetchall():
                vet[r["texto_indexado"]] = (r["embedding"], r["embedding_3l"], r["modelo_embedding"])
        faltam = sorted(precisa - {k for k, v in vet.items() if v[1] is not None})
        car = sum(len(x) for x in faltam)
        estimado = precos.custo_embedding_usd(config.MODELO_EMBEDDING, car / 3.2) or 0
        v = dict(trechos_a_gravar=len(gravar), reaproveitados=len(precisa) - len(faltam), novos=len(faltam),
                 custo_estimado_usd=round(estimado, 4), custo_usd=0.0)
        if self.aplicar and faltam:
            if estimado > self.teto_usd:
                raise SincronizacaoAbortada(f"vetores novos custariam ~US$ {estimado:.2f}, acima do teto de US$ {self.teto_usd:.2f}")
            try:
                for i in range(0, len(faltam), 96):
                    lote = faltam[i:i + 96]
                    m, tok = embeddings.api_textos(lote)
                    v["custo_usd"] += precos.custo_embedding_usd(config.MODELO_EMBEDDING, tok) or 0
                    for txt, vec in zip(lote, m):
                        e5, _, mod = vet.get(txt, (None, None, None))
                        vet[txt] = (e5, vec, mod)
            except Exception as e:  # noqa: BLE001  (sem a API: grava sem vetor; a próxima execução completa)
                self.res["erros"].append(f"vetores: {type(e).__name__}: {str(e)[:200]}")
            v["custo_usd"] = round(v["custo_usd"], 4)
        self.vet = vet
        self.res["vetores"] = v
        self.log(f"vetores: {v['reaproveitados']} reaproveitados, {v['novos']} novos "
                 f"(estimado US$ {v['custo_estimado_usd']:.4f}{'; gasto US$ %.4f' % v['custo_usd'] if self.aplicar else ''})")

    # ---- datas de criação das páginas novas
    def data_criacao(self, pagina):
        c = self.cache.get(pagina)
        if c and c.get("dt_primeira_versao"):
            primeira = c["dt_primeira_versao"]
        else:
            try:
                primeira = self.wiki.primeira_versao(pagina)
            except Exception as e:  # noqa: BLE001
                self.res["erros"].append(f"versões {pagina}: {type(e).__name__}: {str(e)[:120]}")
                primeira = None
        self.primeiras[pagina] = primeira
        hist = data_historico(self.textos.get(pagina))
        datas = [d for d in (primeira, hist) if d]
        return (min(datas) if datas else None), primeira

    # ---- 6. gravação
    def grava_paginas(self):
        por_sis = collections.defaultdict(lambda: collections.Counter())
        self.primeiras = {}
        for motivo, paginas in (("alterada", self.p_alteradas), ("nova", self.p_novas), ("removida", self.p_removidas)):
            for p in paginas:
                antigos, novos = self.antigos.get(p, []), self.novos.get(p, [])
                if motivo == "nova":
                    criada, meta = self.data_criacao(p)
                else:
                    criada = min((r["dt_pagina_criada"] for r in antigos if r["dt_pagina_criada"]), default=None)
                    meta = min((r["dt_pagina_meta"] for r in antigos if r["dt_pagina_meta"]), default=None)
                versao = self.remotas.get(p, (None,))[0] or datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
                with self._tx():
                    if antigos:
                        self.conn.execute(
                            """INSERT INTO apf.wiki_trecho_arquivo (execucao_id, motivo, origem_dokuwiki, linha)
                               SELECT %s, %s, origem_dokuwiki, to_jsonb(t) - 'embedding' - 'embedding_3l' - 'tsv'
                               FROM apf.conhecimento_trecho t WHERE origem_dokuwiki = %s AND fonte = 'wiki'""",
                            (self.exec_id, "removida" if motivo == "removida" else "alterada", p))
                        self.conn.execute("DELETE FROM apf.conhecimento_trecho WHERE origem_dokuwiki = %s AND fonte = 'wiki'", (p,))
                        por_sis[antigos[0]["sistema"]]["apagados"] += len(antigos)
                    for t in novos:
                        e5, v3, mod = self.vet.get(t["texto_indexado"], (None, None, None))
                        self.conn.execute(
                            """INSERT INTO apf.conhecimento_trecho (fonte, sistema, origem_dokuwiki, arquivo_vault, tipo, peso, situacao,
                                   categoria, titulo_pagina, secao, ordem, definicoes, refs, texto, texto_indexado, n_caracteres, hash,
                                   modelo_embedding, embedding, embedding_3l, data_versao, sistemas_acesso, dt_pagina_criada,
                                   dt_pagina_meta)
                               VALUES ('wiki', %(sistema)s, %(origem_dokuwiki)s, %(arquivo_vault)s, %(tipo)s, %(peso)s, %(situacao)s,
                                   %(categoria)s, %(titulo_pagina)s, %(secao)s, %(ordem)s, %(definicoes)s::jsonb, %(refs)s, %(texto)s,
                                   %(texto_indexado)s, %(n_caracteres)s, %(hash)s, %(modelo)s, %(e5)s::vector, %(v3)s::vector,
                                   %(versao)s, %(sistemas_acesso)s, %(criada)s, %(meta)s)""",
                            {**t, "definicoes": json.dumps(t["definicoes"], ensure_ascii=False), "modelo": mod if e5 is not None else None,
                             "e5": _vetor(e5), "v3": _vetor(v3), "versao": versao, "criada": criada, "meta": meta})
                    if novos:
                        por_sis[novos[0]["sistema"]]["inseridos"] += len(novos)
                time.sleep(PAUSA_PAGINA_S)        # cada linha mexe em dois índices HNSW: dá folga ao banco, que atende a API
        self.res["rag"]["por_sistema"] = {s: dict(c) for s, c in sorted(por_sis.items())}
        self.res["rag"]["trechos_inseridos"] = sum(c["inseridos"] for c in por_sis.values())
        self.res["rag"]["trechos_apagados"] = sum(c["apagados"] for c in por_sis.values())
        self.afetados = set(por_sis)

    def ids_atuais(self):
        return {(r["origem_dokuwiki"], r["ordem"]): r["id"] for r in self.conn.execute(
            "SELECT id, origem_dokuwiki, ordem FROM apf.conhecimento_trecho WHERE fonte = 'wiki'").fetchall()}

    def atualiza_iguais(self):
        """sistemas_acesso e data_versao das páginas que não foram regravadas. Cada linha atualizada reescreve as entradas dos dois
        índices HNSW; na 1ª execução são ~40 mil linhas (todas com data_versao = epoch). Por isso a data vai em lotes curtos, com
        pausa, e no máximo MAX_DATA_VERSAO páginas por execução: o resto fica para as próximas (11/11/2026: a carga de uma vez,
        com o Windows sem memória livre, travou e derrubou o Postgres)."""
        n_acesso = 0
        with self._tx():
            for p in self.p_iguais:
                novos = {t["ordem"]: t for t in self.novos[p]}
                for r in self.antigos[p]:
                    acesso = novos[r["ordem"]]["sistemas_acesso"]
                    if set(r["sistemas_acesso"] or []) != set(acesso):
                        n_acesso += self.conn.execute("UPDATE apf.conhecimento_trecho SET sistemas_acesso = %s WHERE id = %s",
                                                      (acesso, r["id"])).rowcount
        pend = [(p, self.remotas[p][0]) for p in self.p_iguais if p in self.remotas
                and any(r["data_versao"] != self.remotas[p][0] for r in self.antigos[p])]
        n_versao, feitas = 0, pend[:MAX_DATA_VERSAO]
        for i in range(0, len(feitas), LOTE_DATA_VERSAO):
            with self._tx():
                for p, versao in feitas[i:i + LOTE_DATA_VERSAO]:
                    n_versao += self.conn.execute("UPDATE apf.conhecimento_trecho SET data_versao = %s WHERE origem_dokuwiki = %s "
                                                  "AND fonte = 'wiki' AND data_versao <> %s", (versao, p, versao)).rowcount
            time.sleep(PAUSA_LOTE_S)
        self.res["rag"].update(acesso_atualizados=n_acesso, data_versao_atualizados=n_versao,
                               data_versao_paginas_pendentes=len(pend) - len(feitas))

    def referencias(self, ids):
        """Diferença entre as referências calculadas e as gravadas (só as de origem no escopo). Devolve (desejadas, atuais)."""
        desejadas = set()
        for r in self.refs:
            o = self.trechos[r["origem"]]
            if not self.no_escopo(o["sistema"]):
                continue
            oid = ids.get((o["origem_dokuwiki"], o["ordem"]))
            if oid is None:
                continue
            d = self.trechos[r["destino"]] if r["destino"] is not None else None
            did = ids.get((d["origem_dokuwiki"], d["ordem"])) if d else None
            desejadas.add((oid, r["tipo_ref"], r["chave"], r["codigo"], did, r["texto_destino"] if did else None, did is not None))
        atuais = collections.defaultdict(list)
        escopo = {i for (p, _), i in ids.items() if p in self.novos}
        for r in self.conn.execute("""SELECT id, origem_trecho_id, tipo_ref, chave, codigo, destino_trecho_id, texto_destino, resolvido
                                      FROM apf.conhecimento_referencia""").fetchall():
            if r["origem_trecho_id"] in escopo:
                atuais[(r["origem_trecho_id"], r["tipo_ref"], r["chave"], r["codigo"], r["destino_trecho_id"], r["texto_destino"],
                        r["resolvido"])].append(r["id"])
        return desejadas, atuais

    def grava_referencias(self):
        desejadas, atuais = self.referencias(self.ids_atuais())
        apagar = [i for k, ids in atuais.items() for i in (ids if k not in desejadas else ids[1:])]
        inserir = [k for k in desejadas if k not in atuais]
        with self._tx():
            if apagar:
                self.conn.execute("DELETE FROM apf.conhecimento_referencia WHERE id = ANY(%s)", (apagar,))
            with self.conn.cursor() as cur:
                cur.executemany("""INSERT INTO apf.conhecimento_referencia (origem_trecho_id, tipo_ref, chave, codigo, destino_trecho_id,
                                       texto_destino, resolvido) VALUES (%s, %s, %s, %s, %s, %s, %s)""", inserir)
        self.res["referencias"] = dict(calculadas=len(desejadas), inseridas=len(inserir), apagadas=len(apagar))

    def grava_cache(self):
        """Texto baixado → apf.wiki_pagina_sync, logo depois do download: uma falha mais adiante não obriga a baixar tudo de novo.
        É seguro gravar antes do RAG, porque o que vai para o RAG sai da comparação do conteúdo com o banco, não do cache."""
        agora = datetime.datetime.now(datetime.timezone.utc)
        with self._tx():
            with self.conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO apf.wiki_pagina_sync (origem_dokuwiki, last_modified, tamanho, hash_texto, texto_wiki,
                           sincronizada_em, removida_em)
                       VALUES (%s, %s, %s, %s, %s, %s, NULL)
                       ON CONFLICT (origem_dokuwiki) DO UPDATE SET last_modified = EXCLUDED.last_modified, tamanho = EXCLUDED.tamanho,
                           hash_texto = EXCLUDED.hash_texto, texto_wiki = EXCLUDED.texto_wiki,
                           sincronizada_em = EXCLUDED.sincronizada_em, removida_em = NULL""",
                    [(i, self.remotas[i][0], self.remotas[i][1], hashlib.sha256(t.encode("utf-8")).hexdigest(), t, agora)
                     for i, t in self.baixadas.items()])
            if self.res["wiki"]["removidas"]:
                self.conn.execute("UPDATE apf.wiki_pagina_sync SET removida_em = %s WHERE origem_dokuwiki = ANY(%s) AND removida_em IS NULL",
                                  (agora, self.res["wiki"]["removidas"]))

    def grava_datas(self):
        """Versão mais antiga das páginas novas no RAG (consultada na gravação), para não pedir de novo à API."""
        if self.primeiras:
            with self._tx():
                with self.conn.cursor() as cur:
                    cur.executemany("UPDATE apf.wiki_pagina_sync SET dt_primeira_versao = %s WHERE origem_dokuwiki = %s",
                                    [(d, p) for p, d in self.primeiras.items() if d])

    def menus(self):
        from ..menus import catalogo
        sistemas = {s for s in self.afetados}
        if not sistemas:
            self.res["menus"] = {}
            return
        with self._tx():
            self.res["menus"] = catalogo.recarrega_wiki(self.conn, sistemas)

    def varredura(self):
        """Padrões crus (CPF, e-mail, telefone) nos trechos gravados por esta execução: tem de dar zero."""
        achados = collections.Counter()
        for p in self.p_novas + self.p_alteradas:
            for t in self.novos[p]:
                for campo in (t["texto"], t["texto_indexado"], *t["definicoes"].values()):
                    achados.update(mascara.achados(campo))
        self.res["pii_restante"] = dict(achados)
        if achados:
            self.res["erros"].append(f"varredura de dados pessoais achou {dict(achados)}")

    # ---- execução
    def roda(self):
        t0 = time.time()
        self.conn = self.conn or dbm.conectar(timeout=10)
        if not self.conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (TRAVA,)).fetchone()["ok"]:
            raise SincronizacaoAbortada("outra sincronização da wiki está em andamento")
        try:
            if self.aplicar:
                self.conn.execute(open(DDL, encoding="utf-8").read())
                self.conn.commit()
            if _existe(self.conn, "apf.wiki_sync_execucao"):
                self.exec_id = self.conn.execute("INSERT INTO apf.wiki_sync_execucao (aplicado, origem) VALUES (%s, %s) RETURNING id",
                                                 (self.aplicar, self.origem)).fetchone()["id"]
                self.conn.commit()
            try:
                self.baixa()
                self.converte()
                self.compara()
                self.varredura()
                self.vetores()
                if self.aplicar:
                    self.grava_paginas()
                    self.atualiza_iguais()
                    self.grava_referencias()
                    self.menus()
                    self.grava_datas()
                    self.log(f"gravado: {self.res['rag']['trechos_inseridos']} trechos inseridos, {self.res['rag']['trechos_apagados']} "
                             f"apagados; referências +{self.res['referencias']['inseridas']} -{self.res['referencias']['apagadas']}; "
                             f"data_versao {self.res['rag']['data_versao_atualizados']}, acesso {self.res['rag']['acesso_atualizados']}")
                else:
                    self.primeiras = {}
                    desejadas, atuais = self.referencias(self.ids_atuais())
                    self.res["referencias"] = dict(calculadas=len(desejadas), a_inserir=sum(1 for k in desejadas if k not in atuais),
                                                   a_apagar=sum(len(v) for k, v in atuais.items() if k not in desejadas))
                ok, erro = not self.res["erros"], None
            except Exception as e:
                self.conn.rollback()
                ok, erro = False, f"{type(e).__name__}: {e}"
                self.res["erros"].append(erro)
                raise
            finally:
                self.res["segundos"] = round(time.time() - t0, 1)
                if self.exec_id:
                    self.conn.execute("UPDATE apf.wiki_sync_execucao SET fim = now(), ok = %s, resumo = %s::jsonb, erro = %s WHERE id = %s",
                                      (ok, json.dumps(resumo_curto(self.res), ensure_ascii=False, default=str), erro, self.exec_id))
                    self.conn.commit()
            if self.aplicar and (self.p_novas or self.p_alteradas or self.p_removidas):
                self.conn.autocommit = True
                # só ANALYZE: o VACUUM varreria os dois índices HNSW inteiros (~300 MB cada); o autovacuum faz isso aos poucos
                self.conn.execute("ANALYZE apf.conhecimento_trecho")
                self.conn.execute("ANALYZE apf.conhecimento_referencia")
                self.conn.autocommit = False
            return self.res
        finally:
            self.conn.execute("SELECT pg_advisory_unlock(%s)", (TRAVA,))
            self.conn.commit()


def resumo_curto(res, n=200):
    """O relatório com as listas cortadas (para o banco e para o log)."""
    def corta(x):
        if isinstance(x, list):
            return x[:n] + ([f"... mais {len(x) - n}"] if len(x) > n else [])
        if isinstance(x, dict):
            return {k: corta(v) for k, v in x.items()}
        return x
    return corta(res)


def executa(**kw):
    """Uma sincronização (simulação por padrão). Devolve o relatório."""
    s = Sincronizacao(**kw)
    try:
        return s.roda()
    finally:
        if s.conn is not None and not s.conn.closed:
            s.conn.close()


def ultima(conn):
    """Data da última sincronização aplicada com sucesso (para o /saude), ou None."""
    if not _existe(conn, "apf.wiki_sync_execucao"):
        return None
    r = conn.execute("SELECT max(fim) AS fim FROM apf.wiki_sync_execucao WHERE aplicado AND ok").fetchone()
    return r["fim"]
