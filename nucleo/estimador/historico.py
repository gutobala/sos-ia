"""Complexidade pelo HISTÓRICO de contagens (cópia do runtime de PRE-PROJETO/app/complexidade_historico.py, 03–04/11/2026).

Para cada função identificada, busca a mesma função (mesmo sistema e tipo, nome parecido por embeddings, cosseno >= LIMIAR,
mesmo verbo e mesmo objeto) em contagens DETALHADA e BASELINE feitas ANTES da data de referência (sem as do próprio chamado).
Achou: usa a complexidade gravada; não achou: reserva (texto, perfil do sistema ou NESMA). Funções de dados também casam pela tabela
física citada no pedido (contagem_funcao_ar_tr) e o ALI implícito entra quando o pedido manda gravar campo na tabela.
PF = peso IFPUG × fator de movimento NESMA. O modelo segue SOSIA_EMB (universo.vetores), e o limiar padrão depende dele: 0,95 no e5,
0,78 no 3-large (06/11/2026, migracao/calibra_historico.py: menor corte com acerto de complexidade >= o do e5; 3.239 funções,
cobertura 64,4% × 65,1% e acerto 83,2% × 82,9%; nos 83 chamados da paridade MAE 19,85 × 19,84, 78 iguais, p = 0,69).
"""
import datetime
import os
import re
import unicodedata

import numpy as np

from .. import config
from . import regras as cc
from . import regras as e1
from . import universo as uf

# e5: 0,93 casava funções diferentes (03/11/2026). 3-large: escala de cosseno diferente (ver docstring).
LIMIAR = float(os.environ.get("APF_HIST_LIMIAR", "0.95" if config.EMB == "e5" else "0.78"))
# Filtro de casamento (03/11/2026, pedido do analista): além do cosseno, exige o MESMO verbo e o MESMO objeto (palavras de conteúdo).
# APF_HIST_FILTRO=0 volta à regra só por cosseno. APF_HIST_ESCOLHA=recente escolhe a contagem mais recente entre as que passam no
# filtro (testado: igual em 2023+ e pior antes; padrão = a mais parecida, empate fica a mais recente).
FILTRO = os.environ.get("APF_HIST_FILTRO", "1") == "1"
ESCOLHA = os.environ.get("APF_HIST_ESCOLHA", "similar")
OBJ_MIN = float(os.environ.get("APF_HIST_OBJ_MIN", "0.6"))   # 0,5 aceitava "Controle Operação - Venda Direta" = "... - Concessão de Uso"
# Reserva pelo PERFIL do sistema (03/11/2026): sem casamento, usa o peso médio IFPUG das funções do mesmo sistema e tipo contadas nos
# PERFIL_ANOS anteriores (mín. PERFIL_MIN funções), em vez do peso NESMA. APF_HIST_PERFIL: 0 (desliga), alt (só alteração/exclusão),
# todas (também inclusões).
PERFIL = os.environ.get("APF_HIST_PERFIL", "alt")
PERFIL_ANOS = int(os.environ.get("APF_HIST_PERFIL_ANOS", "5"))
PERFIL_MIN = int(os.environ.get("APF_HIST_PERFIL_MIN", "5"))
CX = {"BAIXA": 0, "MEDIA": 1, "ALTA": 2}

# classes de verbo (as mesmas famílias de ajusta_e5, por palavra); verbo fora das classes vale por si (ex.: "encaminhar", "efetivar")
CLASSES_VERBO = (("incluir", re.compile(r"^(inclu|cadastr|criar|registr|inser|adicion)")),
                 ("alterar", re.compile(r"^(alter|edit|atualiz|modific|ajust|corrig|manter)")),
                 ("excluir", re.compile(r"^(exclu|remov|apag|delet|desativ|inativ)")),
                 ("consultar", re.compile(r"^(consult|pesquis|visualiz|list|detalh|exib|busc)")),
                 ("emitir", re.compile(r"^(emit|imprim|gera|relat)")))
VAZIAS = {"de", "da", "do", "das", "dos", "por", "para", "pelo", "pela", "pelos", "pelas", "e", "a", "o", "as", "os", "em", "no", "na",
          "nos", "nas", "com", "ao", "aos", "um", "uma"}
GENERICAS = {"relatorio", "relatorios", "pdf", "xls", "xlsx", "excel", "csv", "planilha", "consulta", "implicita", "ci", "tela",
             "dados", "arquivo", "web", "funcionalidade"}


def _sa(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", "", s)            # pré-edital = preedital
    return re.findall(r"[a-z0-9+]+", s)


def _sem_prefixo(n):
    """Tira o prefixo de sistema/módulo ("GAI - ", "GIU_WEB >", "GPE WEB - ")."""
    return re.sub(r"^\s*[A-Z][A-Z0-9_ ]{1,11}\s*(-|>)\s*", "", n or "")


def _classe(w):
    for k, rx in CLASSES_VERBO:
        if rx.match(w):
            return k
    return None


NAO_VERBO = {"titular", "familiar", "auxiliar", "particular", "lugar", "militar", "celular", "escolar", "similar", "popular",
             "regular", "mulher", "poder", "dever", "exemplar", "preliminar", "complementar", "modular", "singular", "anterior",
             "superior", "inferior", "exterior", "interior"}


def _e_verbo(w):
    """Infinitivo (-ar/-er/-ir) ou "relatório" (= emitir). Assim "edital" ou "listagem" não viram verbo."""
    return w.startswith("relat") or (len(w) > 4 and w not in NAO_VERBO and re.search(r"(ar|er|ir)$", w) is not None)


def verbo(nome):
    """Classe do verbo do processo: 1ª palavra, ou a última (nomes antigos: "IMOVEL ATUALIZAR"). None se não houver verbo."""
    ws = [w for w in _sa(_sem_prefixo(nome)) if (w not in GENERICAS or w.startswith("relat")) and w not in VAZIAS]
    for w in ws[:1] + ws[-1:]:
        if _e_verbo(w):
            return _classe(w) or w
    return None


def _singular(w):
    for a, b in (("coes", "cao"), ("oes", "ao"), ("aes", "ao"), ("ais", "al"), ("eis", "el"), ("ns", "m")):
        if w.endswith(a) and len(w) > len(a) + 2:
            return w[: -len(a)] + b
    return w[:-1] if w.endswith("s") and len(w) > 3 else w


def objeto(nome):
    """Palavras de conteúdo do nome, sem verbos, preposições e termos genéricos (relatório, PDF, consulta implícita...)."""
    return {_singular(w) for w in _sa(_sem_prefixo(nome)) if w not in VAZIAS and w not in GENERICAS and not _e_verbo(w)}


def mesmo_processo(a, b):
    """Mesmo verbo (quando os dois têm verbo) e objetos que se sobrepõem em pelo menos OBJ_MIN do maior."""
    va, vb = verbo(a), verbo(b)
    if va and vb and va != vb:
        return False
    oa, ob = objeto(a), objeto(b)
    if not oa or not ob:
        return True
    return len(oa & ob) / max(len(oa), len(ob)) >= OBJ_MIN


# Funções de DADOS (04/11/2026, pedido do analista no 3871: "criar campo na tabela de controle de operações" tem de trazer o ALI com a
# complexidade do histórico). (1) o nome perde o rótulo descritivo ("Grupo lógico de dados X" = "X"); (2) se o texto cita a tabela
# física (FGIU.TS_CONTROLE_OPERACAO), casa com a função de dados do sistema que tem essa tabela entre os AR/TR; (3) se o texto manda
# criar/gravar campo nessa tabela e a prévia não trouxe a função, ela entra como ALI ALTERACAO. APF_HIST_TABELA=0 desliga (2) e (3).
TABELA = os.environ.get("APF_HIST_TABELA", "1") == "1"
RX_ROTULO_DADOS = re.compile(r"^\s*((grupo\s+l[oó]gico(\s+de\s+dados)?|arquivo\s+l[oó]gico\s+(interno|externo)|ali|aie|entidade|tabela)"
                             r"\s*[:\-|]?\s*(de\s+|do\s+|da\s+)?)", re.I)
RX_TABELA = re.compile(r"\b(?:[A-Za-z][A-Za-z0-9]{1,15}\.)?([A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]{3,})\b")
RX_GRAVA = re.compile(r"(cri|inclu|adicion|acrescent|grav|insir|nov[ao]s?\s+(campo|coluna)|alter)", re.I)


def tabela_norm(t):
    t = re.sub(r"^[A-Za-z][A-Za-z0-9]{1,15}\.", "", (t or "").strip())
    return re.sub(r"[\s_]+", "_", t).upper()


def tabelas_do_texto(texto):
    """Tabelas físicas citadas no texto ({TABELA: grava?}); grava = verbo de criação/gravação até 200 caracteres antes."""
    out = {}
    for m in RX_TABELA.finditer(texto or ""):
        nome = tabela_norm(m.group(1))
        if not re.search(r"[A-Z]", m.group(1)) or len(nome) < 6:
            continue
        grava = bool(RX_GRAVA.search((texto or "")[max(0, m.start() - 200): m.start()]))
        out[nome] = out.get(nome, False) or grava
    return out


def nome_dados(n):
    return RX_ROTULO_DADOS.sub("", n or "", count=1).strip() or (n or "")


def nome_base(n):
    """Tira os prefixos da expansão R3 para buscar no histórico."""
    n = n or ""
    if n.startswith("consulta implícita | "):
        return n[len("consulta implícita | "):] + " - consulta implícita"
    for pre in ("pdf | ", "planilha | ", "incluir | ", "alterar | "):
        if n.startswith(pre):
            return n[len(pre):]
    return n


class Historico:
    def __init__(self, conn):
        _, self.vet = uf.carrega(conn)
        rows = conn.execute(
            """SELECT ct.sistema_id, coalesce(ct.dt_contagem, c.dt_abertura::date) dt, c.numero chamado, cf.tipo_funcao tipo,
                      cf.nome_funcao nome, cf.qtd_td td, cf.qtd_ar_tr ar, cf.complexidade cx,
                      CASE WHEN cf.tipo_funcao IN ('ALI', 'AIE') THEN
                        (SELECT array_agg(DISTINCT t.nome) FROM apf.contagem_funcao_ar_tr t WHERE t.contagem_funcao_id = cf.id) END trs
               FROM apf.contagem_funcao cf JOIN apf.contagem ct ON ct.id = cf.contagem_id LEFT JOIN apf.chamado c ON c.id = ct.chamado_id
               WHERE ct.tipo IN ('DETALHADA', 'BASELINE') AND cf.tipo_funcao = ANY(%s)
                 AND cf.qtd_td BETWEEN 1 AND 98 AND cf.qtd_ar_tr BETWEEN 0 AND 98
                 AND coalesce(ct.dt_contagem, c.dt_abertura::date) IS NOT NULL""", (list(uf.TIPOS),)).fetchall()
        self.por, self.perf, self.tab = {}, {}, {}
        for r in rows:
            for t in r["trs"] or []:          # tabela física -> funções de dados (ALI/AIE) que a têm entre os AR/TR
                self.tab.setdefault((r["sistema_id"], tabela_norm(t)), []).append(r)
            k = CX.get(r["cx"], cc.complexidade(r["tipo"], r["td"], r["ar"]))
            if r["tipo"] in cc.PESOS:
                self.perf.setdefault((r["sistema_id"], r["tipo"]), []).append((r["dt"], str(r["chamado"]), cc.PESOS[r["tipo"]][k]))
            k = (r["sistema_id"], r["tipo"], uf.norm(r["nome"]))
            if k in self.vet:
                self.por.setdefault((r["sistema_id"], r["tipo"]), []).append((r, self.vet[k]))
        self.mat = {k: np.stack([v for _, v in lst]) for k, lst in self.por.items()}
        self._q = {}

    def _vq(self, tipo, nome):
        k = (tipo, uf.norm(nome))
        if k not in self._q:
            self._q[k] = uf.vetores([(tipo, nome)])[0]
        return self._q[k]

    def prepara(self, funcs):
        """Gera de uma vez (uma chamada à API no 3-large) os vetores dos nomes que pf() vai buscar."""
        falta = {}
        for f in funcs:
            t = f.get("tipo_ifpug")
            if t in uf.TIPOS and f.get("movimento") not in ("INCLUSAO", "SEM_ALTERACAO", "NAO_MENSURAVEL"):
                n = nome_base(nome_dados(f["nome_funcao"]) if t in ("ALI", "AIE") else f["nome_funcao"])
                if (t, uf.norm(n)) not in self._q:
                    falta[(t, uf.norm(n))] = (t, n)
        if falta:
            for k, v in zip(falta, uf.vetores(list(falta.values()))):
                self._q[k] = v

    def perfil(self, sistema_id, data_ref, excluir, tipo):
        """Peso IFPUG médio das funções do mesmo sistema e tipo contadas nos PERFIL_ANOS antes de data_ref (None se poucas)."""
        if isinstance(data_ref, datetime.datetime):
            data_ref = data_ref.date()
        ini = data_ref.replace(year=data_ref.year - PERFIL_ANOS) if not (data_ref.month == 2 and data_ref.day == 29) else data_ref.replace(
            year=data_ref.year - PERFIL_ANOS, day=28)
        ps = [w for dt, ch, w in self.perf.get((sistema_id, tipo), []) if ini <= dt < data_ref and ch != str(excluir)]
        return sum(ps) / len(ps) if len(ps) >= PERFIL_MIN else None

    def por_tabela(self, sistema_id, data_ref, excluir, tabelas, tipo=None, nome=None):
        """Função de dados do histórico (antes de data_ref) que tem alguma das tabelas citadas; prefere a de nome mais parecido
        (objeto em comum) e, empatando, a mais recente. Devolve (linha, tabela) ou None."""
        if isinstance(data_ref, datetime.datetime):
            data_ref = data_ref.date()
        ob = objeto(nome_dados(nome)) if nome else set()
        cand = []
        for t in tabelas:
            for r in self.tab.get((sistema_id, t), []):
                if (tipo and r["tipo"] != tipo) or r["dt"] >= data_ref or (excluir and str(r["chamado"]) == str(excluir)):
                    continue
                cand.append((len(ob & objeto(r["nome"])), r["dt"], r, t))
        if not cand:
            return None
        if ob and max(c[0] for c in cand) == 0 and len({uf.norm(c[2]["nome"]) for c in cand}) > 1:
            return None                       # várias funções com a tabela e nenhuma com o nome da prévia: não chuta
        _, _, r, t = max(cand, key=lambda c: (c[0], c[1]))
        return r, t

    def busca(self, sistema_id, data_ref, excluir, tipo, nome):
        lst = self.por.get((sistema_id, tipo))
        if not lst:
            return None
        if isinstance(data_ref, datetime.datetime):
            data_ref = data_ref.date()
        nb = nome_base(nome_dados(nome) if tipo in ("ALI", "AIE") else nome)
        s = self.mat[(sistema_id, tipo)] @ self._vq(tipo, nb)
        cand = []
        for j in np.argsort(-s)[:40]:
            if s[j] < LIMIAR:
                break
            r = lst[j][0]
            if r["dt"] >= data_ref or (excluir and str(r["chamado"]) == str(excluir)):
                continue
            if FILTRO and not mesmo_processo(nb, r["nome"]):
                continue
            cand.append((r, float(s[j])))
        if not cand:
            return None
        if FILTRO and ESCOLHA == "recente":
            return max(cand, key=lambda x: (x[0]["dt"], x[1]))
        # regra anterior: o mais parecido; empate (até 0,005) fica a contagem mais recente
        topo = cand[0][1]
        return max((x for x in cand if x[1] >= topo - 0.005), key=lambda x: (x[0]["dt"], x[1]))

    def pf(self, funcs, sistema_id, data_ref, excluir=None, reserva=None, texto=None):
        """funcs: dicts com tipo_ifpug, movimento, nome_funcao. reserva: {índice: k (0/1/2)} da estimativa pelo texto.
        texto: pedido (+ anexos), para casar funções de dados pelas tabelas citadas e acrescentar o ALI implícito.
        Devolve (pf, linhas, nº achadas no histórico)."""
        tabs = tabelas_do_texto(texto) if (TABELA and texto) else {}
        self.prepara(funcs)
        pf, linhas, achou = self._pf(funcs, sistema_id, data_ref, excluir, reserva, tabs)
        if not tabs:
            return pf, linhas, achou
        # ALI implícito: o texto manda criar/gravar campo numa tabela citada e nenhuma função de dados da lista corresponde a ela
        cobertas = {uf.norm(x.get("hist_nome") or "") for x in linhas} | {uf.norm(nome_dados(x["nome_funcao"])) for x in linhas
                                                                          if x["tipo_ifpug"] in ("ALI", "AIE")}
        obj_lista = [objeto(nome_dados(x["nome_funcao"])) for x in linhas if x["tipo_ifpug"] in ("ALI", "AIE")]
        for t, grava in tabs.items():
            h = self.por_tabela(sistema_id, data_ref, excluir, [t], tipo="ALI") if grava else None
            if not h:
                continue
            r = h[0]
            if uf.norm(r["nome"]) in cobertas or any(o and len(o & objeto(r["nome"])) / len(objeto(r["nome"]) or {1}) >= OBJ_MIN
                                                     for o in obj_lista):
                continue
            k = CX.get(r["cx"], cc.complexidade("ALI", r["td"], r["ar"]))
            fator = e1.FATOR.get("ALTERACAO", 0.75)
            pf += cc.PESOS["ALI"][k] * fator
            achou += 1
            cobertas.add(uf.norm(r["nome"]))
            linhas.append(dict(tipo_ifpug="ALI", movimento="ALTERACAO", nome_funcao=f"{r['nome']} (tabela {t} citada no pedido)",
                               cx=("BAIXA", "MEDIA", "ALTA")[k], fonte=f"TABELA {t} -> {r['nome'][:30]} ({r['dt']})",
                               td=r["td"], ar=r["ar"], pf=cc.PESOS["ALI"][k] * fator, hist_nome=r["nome"], implicita=True))
        return pf, linhas, achou

    def _pf(self, funcs, sistema_id, data_ref, excluir, reserva, tabs):
        # itens MI (NAO_MENSURAVEL na prévia): 0,6 PF fixo cada (a lista oficial do benchmark usa SEM_ALTERACAO e não entra aqui)
        pf, linhas, achou = float(os.environ.get("APF_PF_MI", "0.6")) * sum(1 for f in funcs if f.get("movimento") == "NAO_MENSURAVEL"), [], 0
        vale = [f for f in funcs if f["tipo_ifpug"] in cc.PESOS and f["movimento"] not in ("SEM_ALTERACAO", "NAO_MENSURAVEL")]
        for i, f in enumerate(vale, 1):
            t, fator = f["tipo_ifpug"], e1.FATOR.get(f["movimento"], 0.75)
            # função nova (inclusão) não existe no histórico: vai direto à reserva
            h = None if f["movimento"] == "INCLUSAO" else self.busca(sistema_id, data_ref, excluir, t, f["nome_funcao"])
            fonte_t = None
            if not h and tabs and t in ("ALI", "AIE") and f["movimento"] != "INCLUSAO":
                pt = self.por_tabela(sistema_id, data_ref, excluir, list(tabs), tipo=t, nome=f["nome_funcao"])
                if pt:
                    h, fonte_t = (pt[0], 1.0), f"TABELA {pt[1]} -> {pt[0]['nome'][:30]} ({pt[0]['dt']})"
            if h:
                r, sim = h
                k = CX.get(r["cx"], cc.complexidade(t, r["td"], r["ar"]))
                fonte, achou = fonte_t or f"HIST {sim:.2f} {r['nome'][:40]} ({r['dt']})", achou + 1
                f = dict(f, hist_nome=r["nome"])
                peso, td, ar = cc.PESOS[t][k], r["td"], r["ar"]
            elif reserva and i in reserva:
                k, fonte, td, ar = reserva[i], "TEXTO", None, None
                peso = cc.PESOS[t][k]
            else:
                pp = None
                if PERFIL == "todas" or (PERFIL == "alt" and f["movimento"] != "INCLUSAO"):
                    pp = self.perfil(sistema_id, data_ref, excluir, t)
                if pp is not None:
                    peso, k, fonte, td, ar = pp, None, f"PERFIL {pp:.1f}", None, None
                else:
                    peso, k, fonte, td, ar = e1.PESO[t], None, "NESMA", None, None
            pf += peso * fator
            linhas.append(dict(f, cx=("BAIXA", "MEDIA", "ALTA")[k] if k is not None else fonte.split()[0], fonte=fonte, td=td, ar=ar, pf=peso * fator))
        return pf, linhas, achou
