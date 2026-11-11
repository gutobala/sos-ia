"""API XML-RPC da DokuWiki (só leitura). DokuWiki "Jack Jackrum" 2023-04-04a, API 11.

SOSIA_WIKI_URL: padrão o nome completo, que resolve dentro do container (o "terrawiki" curto só resolve na rede do Windows).
SOSIA_WIKI_USUARIO / SOSIA_WIKI_SENHA: opcionais (hoje a leitura é sem senha), só no .env; vão como autenticação básica do HTTP.
"""
import datetime
import socket
import xmlrpc.client
from urllib.parse import quote, urlsplit, urlunsplit

from .. import config

URL_PADRAO = "http://terrawiki.terracapnet.local/lib/exe/xmlrpc.php"
ZW = "​"


def url():
    return config.env("SOSIA_WIKI_URL", URL_PADRAO)


def _com_credenciais(u):
    usuario, senha = config.env("SOSIA_WIKI_USUARIO"), config.env("SOSIA_WIKI_SENHA")
    if not usuario:
        return u
    p = urlsplit(u)
    return urlunsplit(p._replace(netloc=f"{quote(usuario, safe='')}:{quote(senha or '', safe='')}@{p.hostname}"
                                        + (f":{p.port}" if p.port else "")))


def data(dt):
    """DateTime do xmlrpc ('2017-09-21T19:33:50+00:00' ou '20170921T19:33:50') -> datetime com fuso UTC."""
    v = dt.value if hasattr(dt, "value") else str(dt)
    try:
        d = datetime.datetime.fromisoformat(v)
    except ValueError:
        d = datetime.datetime.strptime(v, "%Y%m%dT%H:%M:%S")
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


class Wiki:
    def __init__(self, endereco=None, timeout=60):
        socket.setdefaulttimeout(timeout)
        self.endereco = endereco or url()
        self.api = xmlrpc.client.ServerProxy(_com_credenciais(self.endereco), allow_none=True)

    def versao(self):
        return self.api.dokuwiki.getVersion()

    def paginas(self):
        """{id: (última alteração, tamanho em bytes)} das páginas 'sistemas:*'. Fica de fora o rascunho com espaço invisível."""
        return {p["id"]: (data(p["lastModified"]), int(p["size"])) for p in self.api.wiki.getAllPages()
                if p["id"].startswith("sistemas:") and ZW not in p["id"]}

    def pagina(self, id_):
        return self.api.wiki.getPage(id_)

    def midias(self):
        """Ids de toda a mídia da wiki (só para saber se o arquivo existe; nada é baixado)."""
        return [m["id"] for m in self.api.wiki.getAttachments("", {})]

    def primeira_versao(self, id_, limite=40):
        """Data da versão mais antiga do histórico da página (a lista vem paginada, das mais novas para as mais antigas)."""
        menor, offset = None, 0
        for _ in range(limite):
            vs = self.api.wiki.getPageVersions(id_, offset)
            if not vs:
                break
            for v in vs:
                if v.get("version"):
                    d = datetime.datetime.fromtimestamp(int(v["version"]), datetime.timezone.utc)
                    menor = d if menor is None or d < menor else menor
            offset += len(vs)
        return menor
