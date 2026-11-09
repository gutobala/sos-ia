"""Catálogo de menus (1.5.0): leitor do .mmb do Oracle Forms, caminhos citados na wiki e o SQL de carga. Sem banco e sem rede."""
import json
import os

import pytest

from nucleo.menus import catalogo, forms, wiki

MMB_GAI = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fontes", "gai_menu.mmb")


@pytest.mark.skipif(not os.path.exists(MMB_GAI), reason="fontes/gai_menu.mmb ausente (não vai para o git)")
def test_mmb_do_gai():
    xs = forms.le_arquivo(MMB_GAI)
    assert len(xs) > 180 and len({x["id"] for x in xs}) == len(xs)
    assert [x["nome"] for x in xs if x["pai"] is None] == ["Cadastro", "Consulta", "Relatório", "Tabela"]     # "Sair" fica de fora
    por_caminho = {x["caminho"]: x for x in xs}
    p = por_caminho["Cadastro > NUGIV > Parcelamento Resíduo"]
    assert p["folha"] and p["comando"] == "gai217" and p["id"] == "CADATRO_NUNEG_MENU.PARCELAMENTO_RESÍDUO"
    carne = por_caminho["Relatório > NUGEC > Emissão de Carnê"]
    assert not carne["folha"] and [x["nome"] for x in xs if x["pai"] == carne["id"]] == ["Alienação", "Data de Vencimento", "Data Específica"]
    # rótulo cortado pelo cabeçalho de página ("Indicadores de Adimp" + "lência") sai inteiro
    assert "Relatório > GERAC > Indicadores de Adimplência" in por_caminho
    assert sum(1 for x in xs if x["comando"]) > 140
    ordem = [x["ordem"] for x in xs if x["pai"] == carne["id"]]
    assert ordem == [1, 2, 3]


def test_rotulo_tira_o_atalho():
    assert forms.rotulo("&Cadastro de  Prestamistas") == "Cadastro de Prestamistas"
    assert forms.rotulo("Imóveis Q&uitados") == "Imóveis Quitados"
    assert forms.rotulo("A && B") == "A & B"


def test_mmb_sem_menu_devolve_vazio():
    assert forms.le_mmb(b"\x00" * 9000) == []


@pytest.mark.parametrize("texto, esperado", [
    ("GAI > Relatório > NUGEC > Demonstrativo de Cálculo do Saldo Devedor", ["Relatório", "NUGEC", "Demonstrativo de Cálculo do Saldo Devedor"]),
    ("Cadastro -> NUGIV -> Parcelamento Resíduo.", ["Cadastro", "NUGIV", "Parcelamento Resíduo"]),
    ("Matrizes de Risco → Visualizar Matriz de Risco → localizar o risco → Ações", ["Matrizes de Risco", "Visualizar Matriz de Risco"]),
    ("Login no Sistema Benner → Perfil Orçamento Publico → Menu Lateral → Empenhos → Pré-Empenho", ["Empenhos", "Pré-Empenho"]),
    ("Configurações > Categoria de Risco > expandir a categoria", ["Configurações", "Categoria de Risco"]),
    ("Cadastro > NUCOR > Cadastrar Alienações (Venda", ["Cadastro", "NUCOR", "Cadastrar Alienações"]),
    ("GAI > Cadastro", []),                                               # um segmento só não é caminho
    ("Este caso de uso se inicia quando o ator aciona a opção > e depois", []),
])
def test_normaliza(texto, esperado):
    assert wiki.normaliza(texto, "gai") == esperado


def test_normaliza_tira_alias_da_sigla():
    assert wiki.normaliza("eSocial > Eventos > S-1200", "e_social", ["esocial"]) == ["Eventos", "S-1200"]


def test_caminhos_nos_trechos_e_juncao():
    ts = [dict(tipo="caso_de_uso", secao="FLUXO", origem_dokuwiki="sistemas:gai:uc021",
               texto="Este caso de uso se inicia quando o ator aciona a opção de menu: Cadastro -> NUGIV -> Parcelamento Resíduo."),
          dict(tipo="caso_de_uso", secao="INTERFACE", origem_dokuwiki="sistemas:gai:uc001",
               texto="[OBS01] Caminho de acesso à funcionalidade: - Menu: GAI > Relatório > NUGEC > Demonstrativo de Parcelas em Atraso"),
          dict(tipo="caso_de_uso", secao="FLUXO", origem_dokuwiki="sistemas:gai:uc022",
               texto="(Menu: Consulta > Consultar Alienações)."),
          dict(tipo="caso_de_uso", secao="X", origem_dokuwiki="sistemas:gai:uc099",
               texto="O menu de acesso é: “GAI > Cadastro > NUGIV > Parcelamento resíduo”."),           # repetido (caixa)
          dict(tipo="caso_de_uso", secao="X", origem_dokuwiki="sistemas:gai:uc098", texto="Menu: Cadastro > NUGIV."),   # prefixo
          dict(tipo="user_story", secao="US009 > 2. CAMINHO DE ACESSO", origem_dokuwiki="sistemas:gai:us009",
               texto="Tabela → Normas\n| Cenário 01 |"),
          dict(tipo="plano_de_teste", secao="X", origem_dokuwiki="sistemas:gai:pt1", texto="Menu: Tabela > Moedas")]   # tipo fora
    itens = wiki.extrai(ts, "gai")
    cams = {x["caminho"]: x for x in itens}
    assert set(cams) == {"Cadastro > NUGIV > Parcelamento Resíduo", "Relatório > NUGEC > Demonstrativo de Parcelas em Atraso",
                         "Consulta > Consultar Alienações", "Tabela > Normas"}
    assert cams["Cadastro > NUGIV > Parcelamento Resíduo"]["paginas"] == ["sistemas:gai:uc021", "sistemas:gai:uc099"]
    assert all(x["folha"] and x["pai"] is None and x["id"].startswith("w") for x in itens)
    assert [x["ordem"] for x in itens] == list(range(1, len(itens) + 1))


def test_sql_de_carga():
    sql = catalogo.sql_carga("gai", "forms", [dict(id="M.I", pai=None, nome="Item 'x'", folha=True, caminho="A > Item 'x'", ordem=1,
                                                   comando="gai1")], 23)
    assert sql.startswith("DELETE FROM apf.menu_sistema WHERE sistema = 'gai' AND fonte = 'forms';")
    corpo = sql.split("$menus$")[1]
    assert json.loads(corpo)[0]["nome"] == "Item 'x'" and json.loads(corpo)[0]["sistema_id"] == 23
    with pytest.raises(ValueError):
        catalogo.sql_carga("gai", "forms", [dict(id="1", nome="$menus$", folha=True, caminho="x", ordem=1)])
    assert catalogo.chave_sistema("GAC-GEATE") == "gac_geate" and catalogo.chave_sistema(" Portal ETR ") == "portal_etr"


def test_arvore_em_ordem_de_leitura():
    ms = [dict(id="b1", pai="b"), dict(id="a", pai=None), dict(id="b", pai=None), dict(id="a1", pai="a"), dict(id="orfao", pai="x")]
    assert [m["id"] for m in catalogo._em_arvore(ms)] == ["a", "a1", "b", "b1", "orfao"]
