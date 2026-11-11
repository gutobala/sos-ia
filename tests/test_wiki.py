"""Sincronização da wiki: a conversão do núcleo gera os mesmos trechos que a referência do laboratório (critério 8 do pedido
docs/pedido-sincronizacao-wiki.md), sem API e sem banco. A mini-wiki abaixo é sintética (nada da Terracap) e tem uma página de
cada tipo: caso de uso, história de usuário, regras de negócio, mensagens e plano de teste, mais índice, pasta auxiliar e
namespace excluído."""
import datetime
import importlib.util
import os
import sys

import pytest

from nucleo.wiki import agenda, conversao, mascara, sincroniza, trechos as tr

REF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migracao", "referencia_wiki")
DT = "sistemas:abc:documentacao_tecnica"
PAGINAS = {
    "sistemas:inicio": """====== Sistemas ======
===== ATIVOS =====
==== PHP ====
  * [[sistemas:abc:inicio|ABC - Sistema Exemplo]]
===== INATIVOS =====
  * [[sistemas:velho:inicio|VELHO]]
""",
    "sistemas:abc:inicio": """====== ABC – Sistema de Exemplo ======
Sistema de exemplo para os testes.
  * [[sistemas:abc:documentacao_tecnica:inicio|Documentação Técnica]]
""",
    "sistemas:velho:inicio": "====== VELHO – Sistema Antigo ======\nSem documentação nova.\n",
    f"{DT}:inicio": f"""====== Documentação Técnica ======
  * [[{DT}:caso_de_uso:uc001:inicio|UC001 - Manter Pessoa]]
  * [[{DT}:user_stories:us001:inicio|US001 - Consultar Pessoa]]
  * [[{DT}:regras_de_negocio|Regras de Negócio]]
  * [[{DT}:mensagens|Mensagens]]
  * [[{DT}:plano_de_teste:pt001|PT001 - Testar cadastro]]
  * [[{DT}:pagina_que_nao_existe|Link morto]]
""",
    f"{DT}:caso_de_uso:uc001:inicio": """===== UC001 - Manter Pessoa =====
==== BREVE DESCRIÇÃO ====
Este caso de uso permite **incluir**, //alterar// e excluir pessoas do cadastro, com a validação dos documentos informados
pelo ator e o registro de quem fez cada alteração, para a auditoria da área responsável pelo cadastro.

==== FLUXO PRINCIPAL ====
P1. O ator acessa a opção de menu Cadastro > Pessoa. [[@DT@:regras_de_negocio#RN01 - Validar documento|RN01]]
P2. O sistema apresenta a [[#Tela01 Cadastro|Tela01]] e a mensagem MN001.
P3. O ator informa os dados e confirma; em caso de erro, ver FE01.
P4. O caso de uso é encerrado.\\\\ Fim do fluxo.

==== FLUXOS DE EXCEÇÃO ====
=== FE01 - Documento inválido ===
O sistema mostra a mensagem MN002 e volta ao passo P3. Ver também [[sistemas:aux1:inicio|Apoio]].

==== INTERFACE ====
=== Tela01 Cadastro ===
{{{{:sistemas:abc:documentacao_tecnica:caso_de_uso:uc001:tela.png?400|Tela de cadastro}}
{{:sistemas:abc:documentacao_tecnica:caso_de_uso:uc001:nao_existe.png}}
^ Nome ^ Tipo ^ Descrição ^
| Nome | Texto | Nome completo \\\\ sem abreviação |
| Documento | Número | [[@DT@:regras_de_negocio#RN02|RN02]] |
{{:sistemas:abc:documentacao_tecnica:caso_de_uso:uc001:campos.xlsx|Planilha de campos}}

==== Histórico de mudanças ====
^ Versão ^ Data ^ Descrição ^
| 1.0 | 15/03/2019 | Criação |
| 1.1 | 02/08/2021 | Ajuste |
""".replace("@DT@", DT),
    f"{DT}:user_stories:us001:inicio": f"""===== US001 - Consultar Pessoa =====
==== 1. IDENTIFICAÇÃO DA HISTÓRIA ====
^ Identificador ALM ^ N/A ^
| Como | atendente |
| Quero | consultar uma pessoa pelo documento |

==== 2. CAMINHO DE ACESSO ====
Login → Menu Lateral → Cadastro → Consultar Pessoa
==== 2.1 CENÁRIOS ====
^ Cenário 01 - Consulta ^
| **Dado** que o atendente informou o documento \\\\ **Quando** pesquisar \\\\ **Então** o sistema mostra a pessoa (RN01). |
====2.2 PROTÓTIPO: Consulta====
  - primeiro item
  - segundo item
    * subitem
""",
    f"{DT}:regras_de_negocio": """====== ABC - Regras de Negócio ======
===== RN01 - Validar documento =====
O documento informado deve ter os dígitos verificadores válidos. Documento inválido gera a mensagem MN002 e o cadastro não é
gravado. A validação vale para inclusão e alteração.
===== RN02 - Documento único =====
Não pode haver duas pessoas com o mesmo documento.
<del>===== RN03 - Regra revogada =====</del>
<del>Esta regra foi revogada
em 2020.</del>
""",
    f"{DT}:mensagens": """====== Mensagens ======
^ Código ^ Mensagem ^
| MN001 | Registro gravado com sucesso. |
| MN002 | Documento inválido. |
""",
    f"{DT}:plano_de_teste:pt001": """===== PT001 - Testar cadastro =====
^ Ação do Usuário ^ Resultado Esperado ^
| Dado que o usuário informe um documento inválido | Então o sistema mostra MN002 |
| Dado que o usuário informe um documento válido | Então o sistema grava e mostra MN001 |
""",
    "sistemas:aux1:inicio": "====== Apoio ======\nPágina de apoio, alcançável só por link interno, com a regra RN01 citada.\n",
    "sistemas:teste:inicio": "====== Teste ======\nNamespace excluído (config/wiki_exclusoes.txt).\n",
}
MIDIAS = ["sistemas:abc:documentacao_tecnica:caso_de_uso:uc001:tela.png",
          "sistemas:abc:documentacao_tecnica:caso_de_uso:uc001:campos.xlsx"]


def _modulo(nome):
    spec = importlib.util.spec_from_file_location(f"ref_{nome}", os.path.join(REF, f"{nome}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def referencia(tmp_path_factory):
    """Roda os scripts do laboratório, sem mudança, sobre a mini-wiki gravada em disco como o dump."""
    if not os.path.isdir(REF):
        pytest.skip("migracao/referencia_wiki não está no repositório")
    raiz = tmp_path_factory.mktemp("ref")
    for i, t in PAGINAS.items():
        p = raiz.joinpath("dump", *i.split(":")).with_suffix(".txt")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t, encoding="utf-8")
    for m in MIDIAS:
        p = raiz.joinpath("dump", "media", *m.split(":")[1:])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    sys.path.insert(0, REF)
    try:
        conv = _modulo("converte_dokuwiki")
        conv.PAGINAS_DIR, conv.MEDIA_DIR = str(raiz / "dump" / "sistemas"), str(raiz / "dump" / "media")
        conv.SAIDA, conv.CACHE_OCR = str(raiz / "vault"), str(raiz / "ocr.json")
        argv = sys.argv
        sys.argv = ["converte_dokuwiki.py", "--sem-ocr"]
        try:
            conv.main()
        finally:
            sys.argv = argv
        idx = _modulo("indexa_wiki")
        idx.VAULT = str(raiz / "vault" / "sistemas")
        trechos = idx.carrega_vault()
        return trechos, idx.resolve(trechos), idx.mapa_acesso()
    finally:
        sys.path.remove(REF)


@pytest.fixture(scope="module")
def nucleo():
    notas = conversao.Conversor(PAGINAS, MIDIAS).converte()
    trechos = tr.carrega(notas)
    refs = tr.resolve(trechos)
    return notas, trechos, refs


def _por_chave(trechos):
    return {(t["origem_dokuwiki"], t["ordem"]): t for t in trechos}


def test_mesmos_trechos_da_referencia(referencia, nucleo):
    ref, novo = _por_chave(referencia[0]), _por_chave(nucleo[1])
    assert set(ref) == set(novo)
    tipos = set()
    for k, r in ref.items():
        n = novo[k]
        for c in ("secao", "texto", "texto_indexado", "hash", "sistema", "tipo", "peso", "situacao", "categoria", "titulo_pagina",
                  "definicoes", "refs"):
            assert n[c] == r[c], (k, c)
        assert n["arquivo_vault"].replace("\\", "/") == r["arquivo_vault"].replace(os.sep, "/")
        tipos.add(n["tipo"])
    assert {"caso_de_uso", "user_story", "regras_de_negocio", "mensagens", "plano_de_teste"} <= tipos


def test_mesmas_referencias_e_acesso(referencia, nucleo):
    def mapa(trechos, refs):
        out = {}
        for r in refs:
            o = trechos[r["origem"]]
            d = trechos[r["destino"]] if r["destino"] is not None else None
            out[(o["origem_dokuwiki"], o["ordem"], r["chave"])] = ((d["origem_dokuwiki"], d["ordem"]) if d else None, r["texto_destino"])
        return out
    assert mapa(*referencia[:2]) == mapa(nucleo[1], nucleo[2])
    assert any(v[0] for v in mapa(nucleo[1], nucleo[2]).values())       # há referência resolvida (RN01, MN002, FE01...)
    assert tr.mapa_acesso(nucleo[0]) == referencia[2] == {"aux1": ["abc"]}


def test_conversao_detalhes(nucleo):
    notas, trechos, _ = nucleo
    ids = set(notas)
    assert f"sistemas/ATIVOS/PHP/abc/documentacao_tecnica/caso_de_uso/UC001 - Manter Pessoa" in ids    # nome pelo índice
    assert any(i.startswith("sistemas/INATIVOS/velho/") for i in ids)
    assert any(i.startswith("sistemas/_auxiliares/aux1/") for i in ids)
    assert not any("/teste/" in i for i in ids)                                                       # namespace excluído
    textos = "\n".join(t["texto"] for t in trechos)
    assert "mídia não encontrada: :sistemas:abc:documentacao_tecnica:caso_de_uso:uc001:nao_existe.png" in textos
    assert "Planilha de campos" in textos and "Link morto" not in textos and "15/03/2019" not in textos   # histórico fica fora
    assert "2.2 PROTÓTIPO: Consulta" in {s for t in trechos for s in t["secao"].split(" > ")}         # "====" com espaço duro


def test_midia_fora_de_sistemas_e_rascunho_nao_contam():
    paginas = {"sistemas:abc:inicio": "{{:sistemas:abc:a.png}} {{:outro:b.png}} {{:sistemas:abc:c.png}}"}
    c = conversao.Conversor(paginas, ["sistemas:abc:a.png", "outro:b.png", "sistemas:​abc:c.png"])
    md = c.converte_pagina(paginas["sistemas:abc:inicio"], ["abc"], {"sistemas/abc/inicio"})
    assert "![[media/abc/a.png]]" in md and "não encontrada: :outro:b.png" in md and "não encontrada: :sistemas:abc:c.png" in md


def test_ordem_walk_do_windows():
    assert conversao.ordem_walk(["b/x.txt", "a.txt", "a/z.txt", "B.txt", "a/b/c.txt", "a/a.txt"]) == \
        ["a.txt", "B.txt", "a/a.txt", "a/z.txt", "a/b/c.txt", "b/x.txt"]


# ------------------------------------------------------------------ máscara
def test_mascara_padroes_e_nomes(tmp_path):
    arq = tmp_path / "nomes.txt"
    arq.write_text("Fulano de Tal Silva\nCurto\n", encoding="utf-8")
    rx = mascara.regex_nomes(str(arq))
    t = "CPF 123.456.789-09, e-mail fulano.tal@terracap.df.gov.br, tel (61) 3342-1234; chefe FULANO  DE TAL SILVA."
    # a regra do exporta_nucleo para e-mail para no 1º ponto do domínio; o resto (".gov.br") não é dado pessoal
    assert mascara.texto(t, rx) == "CPF [CPF], e-mail [EMAIL].gov.br, tel [TELEFONE]; chefe [PESSOA]."
    assert mascara.texto("processo 0070-000451/2012 de 21/09/2017", rx) == "processo 0070-000451/2012 de 21/09/2017"
    assert mascara.achados("ok [CPF]") == {} and mascara.achados("111.222.333-44") == {"cpf": 1}


def test_mascara_bruto_preserva_sintaxe(tmp_path):
    rx = mascara.regex_nomes(str(tmp_path / "nao_existe.txt"))
    assert rx is None
    t = "{{:sistemas:x:tela_1234-5678.png|foto 1234-5678}} [[sistemas:x:a@b.com|fale com a@b.com]] tel 9999-8888"
    assert mascara.bruto(t, rx) == "{{:sistemas:x:tela_1234-5678.png|foto 1234-5678}} [[sistemas:x:a@b.com|fale com [EMAIL]]] tel [TELEFONE]"


# ------------------------------------------------------------------ sincronização (partes sem banco)
def test_data_do_historico_de_mudancas():
    d = sincroniza.data_historico(PAGINAS[f"{DT}:caso_de_uso:uc001:inicio"], hoje=datetime.date(2026, 11, 11))
    assert d == datetime.datetime(2019, 3, 15, tzinfo=datetime.timezone.utc)
    assert sincroniza.data_historico("==== Histórico de mudanças ====\n| 1.0 | 01/01/2009 |\n| 2.0 | 31/02/2020 |") is None
    assert sincroniza.data_historico("sem histórico 01/01/2020") is None


def test_classifica_paginas():
    def t(ordem, texto, **kw):
        base = dict(secao="S", ordem=ordem, texto=texto, texto_indexado="X\n" + texto, sistema="abc", arquivo_vault="a.md",
                    tipo="caso_de_uso", peso=1.0, situacao="ATIVO", categoria="PHP", titulo_pagina="UC001", definicoes={}, refs=[])
        return {**base, **kw}
    antigos = {"igual": [t(0, "a"), t(1, "b")], "muda": [t(0, "a")], "sai": [t(0, "a")], "peso": [t(0, "a", peso=0.85)]}
    novos = {"igual": [t(1, "b"), t(0, "a")], "muda": [t(0, "a2")], "entra": [t(0, "a")], "peso": [t(0, "a", peso=0.6)]}
    assert sincroniza.classifica(antigos, novos) == (["entra"], ["muda", "peso"], ["sai"], ["igual"])


def test_vetor_lido_do_banco_e_da_api():
    """O vetor lido do banco vem como pgvector.Vector, que não é iterável (a 1ª gravação na cópia caiu por isso, 11/11/2026)."""
    import numpy as np
    from pgvector import Vector
    assert sincroniza._vetor(Vector([0.5, -1.0])) == "[0.500000,-1.000000]"
    assert sincroniza._vetor(np.array([0.25], dtype=np.float32)) == "[0.250000]"
    assert sincroniza._vetor(None) is None


def test_proxima_execucao_na_hora_de_brasilia():
    agora = datetime.datetime(2026, 11, 11, 5, 0, tzinfo=datetime.timezone.utc)          # 02:00 em Brasília
    assert agenda.proxima(agora, (3, 0)) == datetime.datetime(2026, 11, 11, 3, 0, tzinfo=agenda.BRASILIA)
    agora = datetime.datetime(2026, 11, 11, 7, 0, tzinfo=datetime.timezone.utc)          # 04:00 em Brasília
    assert agenda.proxima(agora, (3, 0)) == datetime.datetime(2026, 11, 12, 3, 0, tzinfo=agenda.BRASILIA)
