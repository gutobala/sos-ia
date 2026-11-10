"""1.5.4: texto da wiki → HTML seguro para o SOS mostrar (sem banco, sem LLM)."""
import re

from nucleo.entrevista.doc_html import para_html

TAGS_PERMITIDAS = {"p", "br", "table", "thead", "tbody", "tr", "th", "td", "ul", "ol", "li", "del", "strong", "span", "div"}


def tags(h):
    return set(re.findall(r"</?([a-z]+)", h))


def test_tabela_com_cabecalho_e_texto_escapado():
    h = para_html("| Nome | Descrição |\n| --- | --- |\n| Ano | Texto fixo: <ano de referência> & <script>x</script> |\n|  |  |")
    assert h.startswith('<div class="sosia-tabela"><table><thead><tr><th>Nome</th><th>Descrição</th></tr></thead>')
    assert "<td>Texto fixo: &lt;ano de referência&gt; &amp; &lt;script&gt;x&lt;/script&gt;</td>" in h
    assert h.count("<tr>") == 2                                    # linha vazia some
    assert tags(h) <= TAGS_PERMITIDAS


def test_passos_referencias_soltas_e_sobras():
    t = ("BREVE DESCRIÇÃO: cadastra o parcelamento.\n\nP1. O sistema apresenta os parâmetros;\nTela01\n\n"
         "P3. O sistema recupera os dados;\nRN01,\nRN68\n~~RN149~~,\nFE01\nP6. O ator informa a mídia (Opção: Vídeo);FA01\n"
         "P12. O caso de uso é encerrado.\n\n---\nVoltar\n#####")
    h = para_html(t)
    assert "<strong>BREVE DESCRIÇÃO:</strong> cadastra" in h
    assert '<strong>P1.</strong> O sistema apresenta os parâmetros <span class="sosia-refs">Tela01</span>' in h
    assert '<span class="sosia-refs">RN01, RN68, <del>RN149</del>, FE01</span>' in h
    assert '(Opção: Vídeo) <span class="sosia-refs">FA01</span>' in h
    assert "Voltar" not in h and "---" not in h and "#" not in h
    assert tags(h) <= TAGS_PERMITIDAS


def test_lista_links_e_negrito():
    t = ("Fórmulas:\n\n  - Valor hectare = Valor pago / Área fazenda\n    (duas casas)\n    - sub item\n  -\n"
         "Ver [Gov.br](https://www.gov.br) e [[sistemas:gai:uc021|UC021 - Cadastrar]] e [[sistemas:gai:uc022]]; **atenção**")
    h = para_html(t)
    assert '<ul class="sosia-lista"><li>Valor hectare = Valor pago / Área fazenda (duas casas)</li>' in h
    assert '<li class="sosia-nivel1">sub item</li></ul>' in h
    assert "Ver Gov.br e UC021 - Cadastrar e uc022; <strong>atenção</strong>" in h
    assert "http" not in h and "[[" not in h


def test_lista_numerada_da_wiki():
    t = "A regra:\n1. Se primeiro edital: 25%;\n1. Se segundo edital: sem os 25%;\n1. Se terceiro: sem desconto.\nDepois."
    h = para_html(t)
    assert h == ('<p>A regra:</p><ol class="sosia-lista"><li>Se primeiro edital: 25%;</li><li>Se segundo edital: sem os 25%;</li>'
                 "<li>Se terceiro: sem desconto.</li></ol><p>Depois.</p>")


def test_vazio_e_texto_simples():
    assert para_html("") == "" and para_html(None) == ""
    assert para_html("Linha 1\nLinha 2\n\nOutro parágrafo") == "<p>Linha 1<br>Linha 2</p><p>Outro parágrafo</p>"
