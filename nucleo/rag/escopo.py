"""Escopo da wiki de um sistema: quais trechos de apf.conhecimento_trecho o sistema enxerga (busca, árvore, página).

  inteiras   chaves de wiki que valem inteiras (sistemas_acesso && inteiras); a maior primeiro (ver motor.resolve_sistema)
  parciais   [(chave, regex, incluir)]: só as páginas da wiki `chave` cujo id casa (incluir=True) ou não casa (False) com a regex.
             Ex.: o GAI-WEB (SOS 803) fica só com as UC001–UC003 da wiki gai-web; o GFA (SOS 10006), com o resto.
  citados    ids dos trechos que as páginas parciais citam (mensagens e regras das UC001–UC003): o link para eles abre.

Gravado no estado da entrevista como dicionário (`para_dict`); `Escopo.de` aceita também a chave em texto ou a lista de chaves
(entrevistas e scripts de migracao/ anteriores).
"""
import dataclasses
import re


@dataclasses.dataclass
class Escopo:
    inteiras: list
    parciais: list = dataclasses.field(default_factory=list)
    citados: list = dataclasses.field(default_factory=list)

    @classmethod
    def de(cls, x):
        """Escopo, dicionário, chave ("gai") ou lista de chaves -> Escopo; vazio -> None."""
        if not x:
            return None
        if isinstance(x, Escopo):
            return x
        if isinstance(x, str):
            return cls([x])
        if isinstance(x, dict):
            return cls(list(x.get("inteiras") or []), [tuple(p) for p in x.get("parciais") or []], list(x.get("citados") or []))
        return cls(list(x))

    def para_dict(self):
        return dict(inteiras=list(self.inteiras), parciais=[list(p) for p in self.parciais], citados=list(self.citados))

    @property
    def principal(self):
        """Chave que dá nome ao escopo (ArvoreDocumentacao.wiki)."""
        return self.inteiras[0] if self.inteiras else self.parciais[0][0]

    @property
    def prefixos(self):
        return [f"sistemas:{k}:" for k in self.inteiras]

    def filtro(self, prefixo="", por_dono=False):
        """(SQL, parâmetros nomeados wk_*) para o WHERE. `prefixo`: alias da tabela ("o."). `por_dono`: as wikis inteiras pela
        coluna `sistema` (página da própria wiki) em vez de `sistemas_acesso` (o que o sistema alcança)."""
        p, partes, par = prefixo, [], {}
        if self.inteiras:
            partes.append(f"{p}sistema = ANY(%(wk_i)s)" if por_dono else f"{p}sistemas_acesso && %(wk_i)s::text[]")
            par["wk_i"] = list(self.inteiras)
        for i, (k, rx, incluir) in enumerate(self.parciais):
            partes.append(f"({p}sistema = %(wk_k{i})s AND ({p}origem_dokuwiki ~ %(wk_r{i})s) = %(wk_s{i})s)")
            par.update({f"wk_k{i}": k, f"wk_r{i}": rx, f"wk_s{i}": bool(incluir)})
        if self.citados:
            partes.append(f"{p}id = ANY(%(wk_c)s)")
            par["wk_c"] = list(self.citados)
        return "(" + (" OR ".join(partes) or "false") + ")", par

    def tem_pagina_parcial(self, pagina):
        """A página é de uma wiki parcial e passa na regra (sem consultar o banco)."""
        return any(pagina.startswith(f"sistemas:{k}:") and bool(re.search(rx, pagina)) == bool(inc) for k, rx, inc in self.parciais)
