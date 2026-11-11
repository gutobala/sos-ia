"""Máscara de dados pessoais da wiki, antes de gravar e de gerar o vetor (já houve CPF real em páginas do eSocial).

As regras são as de migracao/exporta_nucleo.mascara, com que o `sosia` foi gerado:
  - CPF, e-mail e telefone viram [CPF], [EMAIL] e [TELEFONE];
  - nomes conhecidos (dados/nomes_anonimizacao.txt, ou SOSIA_NOMES_ARQ) viram [PESSOA].
`bruto` vale para o texto DokuWiki do cache e preserva a sintaxe que a conversão precisa: mídia `{{...}}` e alvo de link `[[alvo|`.
Sem o arquivo de nomes, só os padrões são mascarados.
"""
import os
import re

from .. import config

RX_PII = {"cpf": re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}"),
          "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
          # (DDD) opcional + 4/5 dígitos-4 dígitos; não pega nº de processo (0070-000451/2012) nem datas/códigos
          "telefone": re.compile(r"(?<![0-9/.-])(\(?0?[0-9]{2}\)?\s?)?9?[0-9]{4}-[0-9]{4}(?![0-9/-])")}
SUBST = {"cpf": "[CPF]", "email": "[EMAIL]", "telefone": "[TELEFONE]"}
RX_SINTAXE = re.compile(r"\{\{[^{}]*\}\}|\[\[[^\]|]*")      # mídia inteira e alvo de link: não mexer
_cache = {}


def arquivo_nomes():
    return config.env("SOSIA_NOMES_ARQ", os.path.join(config.RAIZ, "dados", "nomes_anonimizacao.txt"))


def regex_nomes(arquivo=None):
    """Regex dos nomes completos do arquivo (2+ palavras, 8+ letras, como no exporta_nucleo), ou None sem arquivo."""
    arquivo = arquivo or arquivo_nomes()
    if arquivo not in _cache:
        nomes = set()
        if os.path.exists(arquivo):
            for linha in open(arquivo, encoding="utf-8"):
                n = re.sub(r"\s+", " ", linha).strip()
                if re.search(r"\S+\s+\S+", n) and len(n) >= 8:
                    nomes.add(n)
        partes = [r"\s+".join(re.escape(p) for p in n.split(" ")) for n in sorted(nomes, key=len, reverse=True)]
        _cache[arquivo] = re.compile(r"(?<!\w)(?:" + "|".join(partes) + r")(?!\w)", re.I) if partes else None
    return _cache[arquivo]


def texto(t, nomes=None):
    """Texto já convertido (trecho, definição, texto_destino)."""
    if not t:
        return t
    for k, rx in RX_PII.items():
        t = rx.sub(SUBST[k], t)
    rx = nomes if nomes is not None else regex_nomes()
    return rx.sub("[PESSOA]", t) if rx else t


def bruto(t, nomes=None):
    """Texto DokuWiki: mascara tudo menos a mídia `{{...}}` e o alvo dos links `[[alvo|rótulo]]` (o rótulo é mascarado)."""
    if not t:
        return t
    partes, ini = [], 0
    for m in RX_SINTAXE.finditer(t):
        partes += [texto(t[ini:m.start()], nomes), m.group(0)]
        ini = m.end()
    partes.append(texto(t[ini:], nomes))
    return "".join(partes)


def achados(t):
    """{padrão: quantidade} dos padrões crus que sobraram (a varredura do exporta_nucleo)."""
    return {k: len(rx.findall(t or "")) for k, rx in RX_PII.items() if rx.search(t or "")}
