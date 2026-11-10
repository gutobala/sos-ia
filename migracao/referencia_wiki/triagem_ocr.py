"""Triagem dos blocos de OCR do vault (somente leitura): poluído, tela de código ou redundante com a tabela
logo abaixo. Gera ocr_triagem.csv e um resumo com amostras. Não altera o vault nem o cache.

Uso: python triagem_ocr.py [--vault D:\\Projetos\\Terrawiki\\sistemas]
"""
import argparse, collections, csv, os, re, unicodedata

PASTA = os.path.dirname(os.path.abspath(__file__))
RE_IMG = re.compile(r"^!\[\[([^\]|]+)")
RE_TOKEN = re.compile(r"[a-zà-ÿ]{3,}", re.I)
PALAVRAS_SQL = {"select", "from", "where", "begin", "declare", "join", "order", "group", "insert", "update",
                "delete", "create", "table", "varchar", "number", "commit", "loop", "cursor", "exception",
                "oracle", "sql", "developer", "plsql", "procedure", "function", "end"}
LIM_QUALIDADE = 0.45   # abaixo disso (fração de palavras conhecidas) o OCR é considerado poluído
LIM_SOBREPOSICAO = 0.30  # fração dos tokens do OCR presentes na tabela logo abaixo


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def tokens(texto):
    return [sem_acento(t) for t in RE_TOKEN.findall(texto)]


def blocos_ocr(linhas):
    """Gera (indice_da_imagem, caminho_midia, linhas_ocr, indice_apos_bloco) para cada bloco [!ocr]."""
    i = 0
    while i < len(linhas):
        if linhas[i].startswith("> [!ocr]"):
            j = i + 1
            while j < len(linhas) and linhas[j].startswith(">") and not linhas[j].startswith("> [!ocr]"):
                j += 1
            k = i - 1
            while k >= 0 and linhas[k].startswith("*Legenda"):
                k -= 1
            m = RE_IMG.match(linhas[k]) if k >= 0 else None
            yield k, (m.group(1) if m else "?"), [l[2:] if l.startswith("> ") else l[1:] for l in linhas[i + 1:j]], j
            i = j
        else:
            i += 1


def tabela_abaixo(linhas, depois):
    """Texto da primeira tabela logo depois do bloco (pulando linhas vazias, legenda e cabeçalhos curtos)."""
    j = depois
    while j < len(linhas) and (not linhas[j].strip() or linhas[j].startswith(("#", "*Legenda"))):
        j += 1
    if j >= len(linhas) or not linhas[j].lstrip().startswith("|"):
        return None
    t = []
    while j < len(linhas) and linhas[j].lstrip().startswith("|"):
        t.append(linhas[j])
        j += 1
    return "\n".join(t)


def analisa(texto_ocr, tab, vocab):
    toks = tokens(texto_ocr)
    conhecidos = sum(1 for t in toks if t in vocab)
    qualidade = conhecidos / len(toks) if toks else 1.0
    kw = len(PALAVRAS_SQL & set(toks))
    simb = sum(texto_ocr.count(c) for c in "();=,_") / max(len(texto_ocr), 1)
    fim_pv = sum(1 for l in texto_ocr.split("\n") if l.rstrip().endswith(";"))
    codigo = kw >= 4 or (kw >= 2 and (simb > 0.05 or fim_pv >= 2))
    sobrep = None
    if tab is not None and toks:
        tt = set(tokens(tab))
        sobrep = sum(1 for t in set(toks) if t in tt) / len(set(toks))
    return qualidade, codigo, kw, sobrep, len(toks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=r"D:\Projetos\Terrawiki\sistemas")
    args = ap.parse_args()

    paginas = {}
    freq = collections.Counter()
    for d, _, fs in os.walk(args.vault):
        for f in fs:
            if f.endswith(".md"):
                p = os.path.join(d, f)
                with open(p, encoding="utf-8") as fh:
                    linhas = fh.read().split("\n")
                paginas[p] = linhas
                freq.update(t for l in linhas if not l.startswith(">") for t in tokens(l))
    vocab = {t for t, n in freq.items() if n >= 3}

    linhas_csv, contagem, amostras = [], collections.Counter(), collections.defaultdict(list)
    total = 0
    for p, linhas in paginas.items():
        rel = os.path.relpath(p, args.vault)
        for k, midia, ocr, depois in blocos_ocr(linhas):
            total += 1
            texto = "\n".join(ocr)
            tab = tabela_abaixo(linhas, depois)
            q, cod, kw, sob, n = analisa(texto, tab, vocab)
            motivos = []
            if cod:
                motivos.append("codigo")
            if sob is not None and sob >= LIM_SOBREPOSICAO:
                motivos.append("tabela")
            if q < LIM_QUALIDADE and n >= 5:
                motivos.append("qualidade")
            chave = "+".join(motivos) or "manter"
            contagem[chave] += 1
            for m in motivos or ["manter"]:
                contagem["_" + m] += 1
            linhas_csv.append([rel, midia, n, f"{q:.2f}", kw, "" if sob is None else f"{sob:.2f}",
                               "sim" if tab else "nao", chave])
            if len(amostras[chave]) < 400:
                amostras[chave].append((rel, midia, q, sob, texto[:200].replace("\n", " | ")))

    with open(os.path.join(PASTA, "ocr_triagem.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["pagina", "midia", "tokens", "qualidade", "kw_codigo", "sobreposicao_tabela",
                    "tem_tabela_abaixo", "decisao"])
        w.writerows(linhas_csv)

    print(f"blocos OCR: {total}")
    for k, n in contagem.most_common():
        print(f"  {n:6d}  {k}")
    for k in ("codigo", "tabela", "qualidade", "manter"):
        print(f"\n--- amostras: {k}")
        for rel, midia, q, sob, t in amostras.get(k, [])[:: max(1, len(amostras.get(k, [])) // 6)][:6]:
            print(f"  {rel[-60:]} | q={q:.2f} sob={sob} | {t[:150]}")


if __name__ == "__main__":
    main()
