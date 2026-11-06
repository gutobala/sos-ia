"""Regras determinísticas do estimador, extraídas dos módulos de experimento da pesquisa (sem trazer o experimento junto):
  PESO/FATOR/conta        identifica_etapa1   (pesos NESMA × fator de movimento)
  PESOS/complexidade      complexidade_campos (tabelas IFPUG CPM 4.3)
  PESO_ALTA/FATOR_PREVIA  abertura            (referência "tudo ALTA")
  expande (R1–R3)         identifica_expansao (estilo PONTUA)
  normaliza/sem_duplicatas/aceita_mi/n_mi     abertura
"""
import re

PESO = {"ALI": 7.0, "AIE": 5.0, "EE": 4.0, "CE": 4.0, "SE": 5.0}
FATOR = {"INCLUSAO": 1.0, "ALTERACAO": 0.75, "EXCLUSAO": 0.3, "INDETERMINADO": 0.75, "SEM_ALTERACAO": 0.0, "NAO_MENSURAVEL": 0.0}
PESOS = {"EE": (3, 4, 6), "CE": (3, 4, 6), "SE": (4, 5, 7), "ALI": (7, 10, 15), "AIE": (5, 7, 10)}
CX = ("BAIXA", "MEDIA", "ALTA")
PESO_ALTA = {"ALI": 15.0, "AIE": 10.0, "EE": 6.0, "CE": 6.0, "SE": 7.0}
FATOR_PREVIA = {"INCLUSAO": 1.0, "ALTERACAO": 0.75, "EXCLUSAO": 0.4, "INDETERMINADO": 0.75}
PF_MI = 0.6     # item de manutenção cosmética (deflator MI do contrato)


def conta(funcs, chave_tipo="tipo_ifpug", chave_mov="movimento"):
    vale = [f for f in funcs if f[chave_tipo] in PESO and f[chave_mov] != "SEM_ALTERACAO"]
    return len(vale), sum(PESO[f[chave_tipo]] * FATOR.get(f[chave_mov], 0.75) for f in vale)


def complexidade(tipo, td, ar):
    """Tabelas do IFPUG (CPM 4.3). Devolve 0 (baixa), 1 (média) ou 2 (alta)."""
    td, ar = max(td, 1), max(ar, 1)
    if tipo == "EE":
        c = 0 if td <= 4 else 1 if td <= 15 else 2
        lin = 0 if ar <= 1 else 1 if ar == 2 else 2
    elif tipo in ("SE", "CE"):
        c = 0 if td <= 5 else 1 if td <= 19 else 2
        lin = 0 if ar <= 1 else 1 if ar <= 3 else 2
    else:                                           # ALI/AIE: ar = TR
        c = 0 if td <= 19 else 1 if td <= 50 else 2
        lin = 0 if ar <= 1 else 1 if ar <= 5 else 2
    return ((0, 0, 1), (0, 1, 2), (1, 2, 2))[lin][c]


def pf_alta(fs):
    vale = [f for f in fs if f["tipo_ifpug"] in PESO_ALTA and f["movimento"] in FATOR_PREVIA]
    return sum(PESO_ALTA[f["tipo_ifpug"]] * FATOR_PREVIA[f["movimento"]] for f in vale)


def n_mi(fs):
    return sum(1 for f in fs if f.get("movimento") == "NAO_MENSURAVEL")


# ------------------------------------------------------------------ expansão estilo PONTUA
RX_MANTER = re.compile(r"^\s*(manter|gerenciar|gerir|administrar|cadastrar|controlar|gest[aã]o d[eoa]s?|gerenciamento d[eoa]s?|cadastro d[eoa]s?)\b", re.I)
RX_DOIS_VERBOS = re.compile(r"\b(incluir|cadastrar|inserir|criar|adicionar)\b.*\b(e|/)\s*(alterar|editar|atualizar|modificar)\b", re.I)
RX_DOIS_FORMATOS = re.compile(r"(pdf.*(excel|xls|csv))|((excel|xls|csv).*pdf)", re.I)
RX_ALTERAR = re.compile(r"\b(alterar|editar|atualizar|modificar)\b", re.I)


def expande(funcs, variante="R3", divide_formatos=False):
    """R1 'manter X' → incluir/alterar/excluir/consultar (+ALI se inclusão); R2 dois verbos → 2 EE (e formatos, se pedido);
    R3 + consulta implícita para cada EE de alteração. divide_formatos=False: mesmo conteúdo em PDF/Excel = uma SE (03/11/2026)."""
    out = []
    for f in funcs:
        t, m, n = f["tipo_ifpug"], f["movimento"], f["nome_funcao"]
        if m in ("SEM_ALTERACAO", "NAO_MENSURAVEL"):
            out.append(f)
            continue
        if variante != "orig" and t in ("EE", "CE", "SE", "INDETERMINADO") and RX_MANTER.search(n):
            novas = [("EE", "incluir"), ("EE", "alterar"), ("EE", "excluir"), ("CE", "consultar")]
            if m == "INCLUSAO":
                novas.append(("ALI", "dados"))
            out += [dict(tipo_ifpug=tt, movimento=m, nome_funcao=f"{v} | {n}") for tt, v in novas]
            continue
        if variante in ("R2", "R3") and t == "EE" and RX_DOIS_VERBOS.search(n):
            out += [dict(tipo_ifpug="EE", movimento=m, nome_funcao=f"incluir | {n}"), dict(tipo_ifpug="EE", movimento=m, nome_funcao=f"alterar | {n}")]
            continue
        if divide_formatos and variante in ("R2", "R3") and t in ("SE", "CE") and RX_DOIS_FORMATOS.search(n):
            out += [dict(f, nome_funcao=f"pdf | {n}"), dict(f, nome_funcao=f"planilha | {n}")]
            continue
        out.append(f)
    if variante == "R3":
        out += [dict(tipo_ifpug="CE", movimento=x["movimento"], nome_funcao=f"consulta implícita | {x['nome_funcao']}")
                for x in list(out) if x["tipo_ifpug"] == "EE" and x["movimento"] not in ("SEM_ALTERACAO", "NAO_MENSURAVEL")
                and RX_ALTERAR.search(x["nome_funcao"])]
    return out


def normaliza(fs):
    """consulta como EE → CE; relatório/exportação como EE → SE; CE/SE do mesmo objeto = uma; cadastro novo sem ALI ganha um ALI."""
    rx_cons = re.compile(r"^\s*(listar|consultar|pesquisar|visualizar|filtrar|buscar|exibir)\b", re.I)
    rx_rel = re.compile(r"^\s*(emitir|gerar|exportar|imprimir)\b|relat[óo]rio", re.I)
    rx_verbo = re.compile(r"^\s*\w+\s+", re.I)
    out, vistos = [], set()
    for f in fs:
        f = dict(f)
        if f["tipo_ifpug"] == "EE" and rx_cons.search(f["nome_funcao"]):
            f["tipo_ifpug"] = "CE"
        elif f["tipo_ifpug"] == "EE" and rx_rel.search(f["nome_funcao"]):
            f["tipo_ifpug"] = "SE"
        if f["tipo_ifpug"] in ("CE", "SE") and f["movimento"] not in ("SEM_ALTERACAO", "NAO_MENSURAVEL"):
            chave = (f["tipo_ifpug"], rx_verbo.sub("", f["nome_funcao"].casefold()).strip())
            if chave in vistos:
                continue
            vistos.add(chave)
        out.append(f)
    if not any(f["tipo_ifpug"] in ("ALI", "AIE") for f in out):
        for f in list(out):
            m = re.match(r"^\s*(incluir|cadastrar|criar|registrar)\s+(.+)", f["nome_funcao"], re.I)
            if f["tipo_ifpug"] == "EE" and f["movimento"] == "INCLUSAO" and m:
                out.append(dict(f, tipo_ifpug="ALI", nome_funcao=f"Dados de {m.group(2)}", status="HIPOTESE",
                                justificativa="Cadastro novo: grupo lógico de dados acrescentado por regra fixa da prévia."))
    return out


def sem_duplicatas(fs):
    vistos, out = set(), []
    for f in fs:
        nome = re.sub(r"^consulta implícita \| ", "consulta implícita ", f["nome_funcao"].casefold())
        nome = re.sub(r"\s+", " ", nome.replace("consulta implícita de ", "consulta implícita ")).strip()
        chave = (f["tipo_ifpug"], nome)
        if chave not in vistos:
            vistos.add(chave)
            out.append(f)
    return out


def aceita_mi(schema):
    """Acrescenta NAO_MENSURAVEL ao enum de movimento (em memória)."""
    def anda(o):
        if isinstance(o, dict):
            mv = o.get("properties", {}).get("movimento")
            if isinstance(mv, dict) and "enum" in mv and "NAO_MENSURAVEL" not in mv["enum"]:
                mv["enum"] = mv["enum"] + ["NAO_MENSURAVEL"]
            for v in o.values():
                anda(v)
        elif isinstance(o, list):
            for v in o:
                anda(v)
    anda(schema)
    return schema
