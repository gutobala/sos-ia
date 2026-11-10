"""Dados pessoais antes do LLM (LGPD).

Modo (config.MODO_PII):
  local     anonimiza o texto (regras abaixo) e troca cada imagem pelo texto do OCR anonimizado; a imagem nunca sai.
            Obrigatório fora do contrato Azure (testes com a chave pessoal).
  contrato  texto e imagens vão como vieram (operador sob contrato; precedente: ANIA). Só com SOSIA_LLM=azure.
  teste     texto anonimizado como no local; imagens originais. Só para testes controlados com imagens sem dado pessoal.

Anonimização local = cópia de PRE-PROJETO/importador/anonimiza.py (máscaras por padrão, nomes por palavra-gatilho, nomes
conhecidos), com duas diferenças: (1) os nomes conhecidos vêm de um arquivo local (SOSIA_NOMES_ARQ, um por linha, fora do git e
do dump), porque o banco do núcleo não guarda nomes de pessoas; (2) telefone sem DDD ("3342-1658", "98556-8590"), que a versão
da pesquisa deixava passar em assinaturas de e-mail.
"""
import os
import re

from . import config

# ---------------------------------------------------------------- camada 1: padrões
RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
RE_CNPJ = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
RE_CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
RE_PROCESSO = re.compile(r"\b\d{5}[.\-]\d{6,8}/\d{4}-\d{2}\b")
RE_MATRICULA = re.compile(r"(?i)\bmatr[íi]cula\b[^\d\n]{0,15}\d{3,10}")
RE_TELEFONE = re.compile(r"(?<!\d)(?:\(?0?55\)?[\s.-]?)?\(?\d{2}\)?[\s.-]?\d{4,5}[\s.-]?\d{4}(?!\d)")
RE_TELEFONE_CURTO = re.compile(r"(?<![\d/.-])9?\d{4}-\d{4}(?![\d/-])")
RE_NUMEROS_LONGOS = re.compile(r"\b\d{9,14}\b|\b\d{9}-\d{2}\b|\b[MmTt]\d{5,6}\b")   # CPF sem pontuação, matrículas


def mascara_padroes(texto):
    texto = RE_EMAIL.sub("[EMAIL]", texto)
    texto = RE_CNPJ.sub("[CNPJ]", texto)
    texto = RE_CPF.sub("[CPF]", texto)
    texto = RE_PROCESSO.sub("[PROCESSO]", texto)
    texto = RE_MATRICULA.sub("[MATRICULA]", texto)
    texto = RE_TELEFONE.sub("[TELEFONE]", texto)
    texto = RE_TELEFONE_CURTO.sub("[TELEFONE]", texto)
    return texto


# ---------------------------------------------------------------- camada 2: nomes de pessoas
STOP_TOKENS = {"de", "da", "do", "das", "dos", "e", "cfps", "fatto", "consultoria", "consultoria,", "sistemas", "id", "ltda", "me"}
MESES_DIAS = {"janeiro", "fevereiro", "marco", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
              "novembro", "dezembro", "segunda", "terca", "terça", "quarta", "quinta", "sexta", "sabado", "sábado", "domingo"}
INSTITUCIONAL = {"terracap", "brasil", "brasilia", "brasília", "distrito", "federal", "governo", "secretaria", "diretoria", "gerencia",
                 "gerência", "coordenacao", "coordenação", "companhia", "imobiliaria", "imobiliária", "contrato", "sistema", "sistemas",
                 "fabrica", "fábrica", "software", "ponto", "pontos", "funcao", "função", "funcoes", "funções", "chamado", "sos",
                 "portal", "engesoftware", "capgemini", "abrantes", "ctis"}
RE_PALAVRA = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")
CONECTORES = {"de", "da", "do", "dos", "das", "e"}
# "chefe do NUVIS", "gerente da GEFIS": depois do gatilho e de "do/da/de/no/na", uma palavra só, em maiúsculas, é sigla de unidade
# (teste do SOS, 10/11). Nome em maiúsculas com sobrenome ("chefe do NUVIS, JOÃO SILVA") e nomes da lista local continuam saindo.
PREP_UNIDADE = {"de", "da", "do", "dos", "das", "no", "na", "nos", "nas"}
RE_CUE = re.compile(
    r"(?i)\b(?:solicitante\w{0,2}|requerente\w{0,2}|benefici[aá]ri[ao]s?|pensionista\w{0,2}|servidor[a]?s?|"
    r"empregad[ao]s?|funcion[aá]ri[ao]s?|titular\w{0,2}|gerente\w{0,2}|coordenador[a]?s?|supervisor[a]?s?|"
    r"chefe\w{0,2}|diretor[a]?s?|assinado\s+por|em\s+nome\s+d[eo]|senhor|senhora|atenciosamente|"
    r"cordialmente|obrigad[ao]s?)\b|"
    r"\b(?:sr|sra|dr|dra|att)[,.:]|\b(?:De|Para|Cc|Cco):")


def _titlecase(p):
    return p[0].isupper() and p[1:].islower() and len(p) >= 2


def _nomeish(p, excluir):
    if p == "PESSOA" or p.lower() in excluir:
        return False
    return (len(p) >= 2 and p.isupper()) or _titlecase(p)


def mascara_nomes_por_gatilho(texto, excluir):
    """Depois de uma palavra-gatilho (solicitante, gerente, 'Sr.', 'Att,' ...), mascara a sequência de palavras capitalizadas."""
    matches = list(RE_PALAVRA.finditer(texto))
    n = len(matches)
    trechos = []
    for cue in RE_CUE.finditer(texto):
        j = next((i for i, m in enumerate(matches) if m.start() >= cue.end()), None)
        if j is None:
            continue
        inicio, fim_busca = None, cue.end()
        if texto[fim_busca:fim_busca + 1] == ".":
            fim_busca += 1
        for k in range(j, min(j + 4, n)):
            if re.search(r"[.!?]", texto[fim_busca:matches[k].start()]):
                break
            fim_busca = matches[k].end()
            if _nomeish(matches[k].group(), excluir):
                inicio = k
                break
        if inicio is None:
            continue
        ini = matches[inicio].start()
        fim, k = matches[inicio].end(), inicio + 1
        while k < n:
            prox = matches[k]
            if texto[fim:prox.start()] not in (" ", ", ", "; "):
                break
            p = prox.group()
            if _nomeish(p, excluir):
                fim, k = prox.end(), k + 1
                continue
            if p.lower() in CONECTORES and k + 1 < n:
                prox2 = matches[k + 1]
                if texto[prox.end():prox2.start()] in (" ", ", ") and _nomeish(prox2.group(), excluir):
                    fim, k = prox2.end(), k + 2
                    continue
            break
        palavra = matches[inicio].group()
        prep = texto[cue.end():ini].strip(" .,:").lower()
        if k == inicio + 1 and palavra.isupper() and prep in PREP_UNIDADE:
            continue                                      # sigla de unidade, não nome
        trechos.append((ini, fim))
    if not trechos:
        return texto
    trechos.sort()
    partes, cursor = [], 0
    for ini, fim in trechos:
        if ini < cursor:
            continue
        partes += [texto[cursor:ini], "[PESSOA]"]
        cursor = fim
    partes.append(texto[cursor:])
    return "".join(partes)


_cache = {}


def _nomes():
    """(regex de nomes completos, regex de tokens) do arquivo local de nomes; (None, None) sem arquivo."""
    if "nomes" in _cache:
        return _cache["nomes"]
    arq = config.env("SOSIA_NOMES_ARQ", os.path.join(config.RAIZ, "dados", "nomes_anonimizacao.txt"))
    completos, tokens = set(), set()
    if os.path.exists(arq):
        for linha in open(arq, encoding="utf-8"):
            nome = re.sub(r"\s+", " ", linha).strip()
            if " " not in nome or len(nome) < 5:
                continue
            completos.add(nome)
            for tok in nome.split():
                t = tok.strip(".,").lower()
                if len(t) >= 4 and t not in STOP_TOKENS and t not in INSTITUCIONAL:
                    tokens.add(t)
    rc = re.compile("|".join(sorted((re.escape(n) for n in completos), key=len, reverse=True)), re.I) if completos else None
    rt = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)) + r")\b", re.I) if tokens else None
    _cache["nomes"] = (rc, rt)
    return _cache["nomes"]


def _exclusoes(conn):
    if "excl" not in _cache:
        excl = set(STOP_TOKENS) | set(MESES_DIAS) | set(INSTITUCIONAL)
        if conn is not None:
            for tabela, col in (("sistema", "nome"), ("sistema", "sigla"), ("sistema_alias", "alias")):
                for r in conn.execute(f"SELECT DISTINCT {col} v FROM apf.{tabela} WHERE {col} IS NOT NULL").fetchall():
                    v = r["v"] if isinstance(r, dict) else r[0]
                    excl |= {t.lower() for t in RE_PALAVRA.findall(v) if len(t) >= 3}
        _cache["excl"] = excl
    return _cache["excl"]


def anonimizar(texto, conn=None, max_car=30000):
    """Texto -> sem CPF/CNPJ/e-mail/telefone/processo/matrícula e com nomes mascarados (tudo local)."""
    if not texto:
        return texto or ""
    t = mascara_padroes(texto)
    t = mascara_nomes_por_gatilho(t, _exclusoes(conn))
    rc, rt = _nomes()
    if rc:
        t = rc.sub("[PESSOA]", t)
    if rt:
        t = rt.sub("[PESSOA]", t)
    t = RE_NUMEROS_LONGOS.sub("[NUM]", t)
    return t[:max_car]


# ---------------------------------------------------------------- entrada da entrevista
def _ocr(imagem):
    """Texto da imagem por OCR local (easyocr, opcional). Sem o pacote: None."""
    try:
        import base64
        import easyocr  # noqa: F401
    except ImportError:
        return None
    if "ocr" not in _cache:
        import easyocr
        _cache["ocr"] = easyocr.Reader(["pt", "en"], gpu=False, verbose=False)
    return " ".join(_cache["ocr"].readtext(base64.b64decode(imagem["base64"]), detail=0))


def prepara(texto, imagens=(), conn=None):
    """Devolve (texto para o LLM, imagens para o LLM, notas). Ver o docstring do módulo para os dois modos."""
    if config.MODO_PII == "contrato":
        return texto, list(imagens), []
    if config.MODO_PII == "teste":
        notas = ["modo de PII 'teste': imagens enviadas sem anonimização"] if imagens else []
        return anonimizar(texto, conn), list(imagens), notas
    partes, notas = [anonimizar(texto, conn)], []
    for im in imagens:
        ocr = _ocr(im)
        if ocr:
            partes.append(f"[Texto da imagem {im['nome']}]: {anonimizar(ocr, conn, 4000)}")
        else:
            notas.append(f"imagem {im['nome']} não enviada (modo local sem OCR disponível)")
    return "\n\n".join(partes), [], notas
