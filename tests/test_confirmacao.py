"""Regras puras da confirmação do Escopo do Pedido (contrato 1.2.0): sem banco, sem LLM, sem API."""
import pytest

from nucleo.entrevista import confirmacao as cf, roteiros
from nucleo.entrevista.modelos import (Contrato, DecisaoRegra, EscopoEditado, Integracao, RegraDocumentada, Segmento, SlotFaltando,
                                       VinculoChamados)


def contrato(*tipos, faltando=(), regra=None, decisao=None, valores=None):
    """valores: {n do segmento: {slot: valor}} para a seção INFORMAÇÕES DO PEDIDO."""
    valores = valores or {}
    segs = [Segmento(n=i, tipo=t, resumo=f"seg {i}", slots=roteiros.slots_vazios(t, valores.get(i))) for i, t in enumerate(tipos, 1)]
    return Contrato(id="e1", titulo="t", objetivo="o", requisitos=[], fora_escopo=[], duvidas=[], integracoes=[], segmentos=segs,
                    regra_documentada=regra, decisao_regra=decisao, nota=80, faltando=list(faltando), pode_confirmar=not faltando,
                    analise_ia=True)


def escopo(*segmentos, titulo="Ajustes no contrato de alienação", **kw):
    return EscopoEditado(titulo=titulo, objetivo="Voltar a salvar e mostrar a multa.",
                         requisitos=[dict(segmento=s, descricao=f"req do segmento {s}", criterio_aceite="" if s == 3 else f"aceite {s}")
                                     for s in segmentos], **kw)


REGRA = RegraDocumentada(pagina="sistemas:gai:regras", secao="RN021", trecho="Não é permitido alterar contrato com parcela paga.",
                         similaridade=0.9, orientacao="...")


def test_tipo_e_natureza_pelo_segmento_e_ordem_fixa():
    k = contrato("EVOLUTIVO_NOVO", "CORRETIVO", "EVOLUTIVO_EXISTENTE")
    gs = cf.monta_grupos(k, escopo(1, 2, 3))
    assert [g.tipo.value for g in gs] == ["CORRETIVO", "EVOLUTIVO"]                       # corretivo sempre primeiro
    assert [(r.n, r.natureza.value) for r in gs[0].requisitos] == [(2, "CORRECAO")]
    assert [(r.n, r.natureza.value) for r in gs[1].requisitos] == [(1, "NOVO"), (3, "ALTERACAO")]   # n do pedido inteiro
    assert gs[1].requisitos[1].criterio_aceite == "Definir com o usuário."


def test_sufixo_do_titulo_so_com_dois_grupos_e_limite():
    assert cf.monta_grupos(contrato("CORRETIVO"), escopo(1))[0].titulo == "Ajustes no contrato de alienação"
    gs = cf.monta_grupos(contrato("CORRETIVO", "EVOLUTIVO_NOVO"), escopo(1, 2, titulo="x" * 200))
    assert [len(g.titulo) for g in gs] == [200, 200]
    assert gs[0].titulo.endswith(" (correção)") and gs[1].titulo.endswith(" (evolução)")
    assert cf.titulo("  a   b ", cf.ORDEM[0], 1) == "a b"


def test_texto_com_as_secoes_e_a_referencia_ao_outro_chamado():
    k = contrato("CORRETIVO", "EVOLUTIVO_EXISTENTE", "EVOLUTIVO_NOVO", regra=REGRA, decisao=DecisaoRegra.ACEITA)
    e = escopo(1, 2, 3, fora_escopo=["Relatórios", " "], duvidas=["Vale para contratos antigos?"],
               integracoes=[Integracao(sistema="Benner", sentido="BUSCA", descricao="consulta")])
    corr, evol = cf.monta_grupos(k, e)
    t = evol.texto
    assert t.startswith("OBJETIVO\nVoltar a salvar")
    assert t.index("ALTERAÇÕES NO QUE JÁ EXISTE") < t.index("FUNCIONALIDADES NOVAS") < t.index("FORA DE ESCOPO")
    assert "2. req do segmento 2\n   Critério de aceite: aceite 2" in t
    assert "O requisito 1 deste pedido está no chamado corretivo vinculado (mesmo pacote)." in t
    assert "Os requisitos 2 e 3 deste pedido estão no chamado evolutivo vinculado (mesmo pacote)." in corr.texto
    assert "- Benner (BUSCA): consulta" in t and "- Vale para contratos antigos?" in t and "- \n" not in t
    assert "REGRA DOCUMENTADA QUE O USUÁRIO QUER MUDAR\nsistemas:gai:regras / RN021" in t
    assert "PROBLEMAS A CORRIGIR" in corr.texto and "chamado evolutivo vinculado" in corr.texto
    # regra recusada não vai para o chamado
    k2 = contrato("CORRETIVO", regra=REGRA, decisao=DecisaoRegra.RECUSADA)
    assert "REGRA DOCUMENTADA" not in cf.monta_grupos(k2, escopo(1))[0].texto and cf.regra_aceita(k2) is None


CORRETIVO = {"desde_quando_quem": "desde segunda, toda a GEVEN", "onde_ocorre": "Tela de alienação, botão Salvar",
             "como_reproduzir": "1) abrir a alienação; 2) informar o contrato; 3) Salvar",
             "o_que_aconteceu": "aparece HTTP 500 e nada é gravado"}


def test_informacoes_do_pedido_no_corretivo_na_ordem_do_roteiro():
    t = cf.monta_grupos(contrato("CORRETIVO", valores={1: CORRETIVO}), escopo(1))[0].texto
    assert t.startswith("OBJETIVO\nVoltar a salvar e mostrar a multa.\n\nINFORMAÇÕES DO PEDIDO\n"
                        "Onde ocorre (sistema, tela ou funcionalidade): Tela de alienação, botão Salvar\n"
                        "O que aconteceu (mensagem, comportamento): aparece HTTP 500 e nada é gravado\n"
                        "Como reproduzir (passo a passo): 1) abrir a alienação; 2) informar o contrato; 3) Salvar\n"
                        "Desde quando e quem é afetado: desde segunda, toda a GEVEN\n\nPROBLEMAS A CORRIGIR\n1. ")
    assert "Parte " not in t                                                      # um segmento só: sem subtítulo


def test_informacoes_do_evolutivo_com_duas_partes_sem_fora_de_escopo():
    k = contrato("CORRETIVO", "EVOLUTIVO_EXISTENTE", "EVOLUTIVO_NOVO",
                 valores={1: CORRETIVO, 2: {"funcionalidade": "Relatório de pagamentos", "fora_de_escopo": "Não mexer no boleto",
                                            "o_que_muda": "x" * 2000},
                          3: {"quem_usa": "Gerência de vendas", "outros_sistemas": "não"}})
    corr, evol = cf.monta_grupos(k, escopo(1, 2, 3))
    t = evol.texto
    assert "Parte 1 - Alteração no que já existe: seg 2\nFuncionalidade que será alterada: Relatório de pagamentos\n" in t
    assert "Parte 2 - Funcionalidade nova: seg 3\nQuem vai usar: Gerência de vendas\n" in t
    assert "Não mexer no boleto" not in t and "O que fica fora do escopo" not in t
    linha = next(x for x in t.splitlines() if x.startswith("O que muda: "))
    assert len(linha) == len("O que muda: ") + cf.MAX_VALOR_INFO                     # 2.000 caracteres cortados em 600
    assert "Tela de alienação" not in t and "Relatório de pagamentos" not in corr.texto   # cada grupo só com os seus segmentos


def test_sem_informacoes_quando_nenhum_item_tem_valor():
    t = cf.monta_grupos(contrato("CORRETIVO"), escopo(1))[0].texto
    assert "INFORMAÇÕES DO PEDIDO" not in t and t.startswith("OBJETIVO\nVoltar a salvar e mostrar a multa.\n\nPROBLEMAS A CORRIGIR")


def test_criterio_fixo_nunca_e_como_reproduzir():
    assert roteiros.criterio_fixo("CORRETIVO", {"como_reproduzir": "1) abrir; 2) salvar"}) == roteiros.CRITERIO_CORRETIVO
    assert roteiros.criterio_fixo("EVOLUTIVO_NOVO", {"exemplo_concreto": " contrato 9 deve dar 10 "}) == \
        "Conferir com o exemplo: contrato 9 deve dar 10"
    assert roteiros.criterio_fixo("EVOLUTIVO_EXISTENTE", {"exemplo_concreto": None}) == "Definir com o usuário."


def test_origem_inferidos_e_funcionalidades_confirmadas():
    """1.4.0: origem vem do contrato (não da tela); inferido mantido ganha marca; removido vai para o evento; funções marcadas
    entram nos dois chamados; id fora dos mostrados é erro."""
    from nucleo.entrevista.modelos import CandidatoFuncionalidade, RequisitoContrato
    k = contrato("CORRETIVO", "EVOLUTIVO_EXISTENTE").model_copy(update={"requisitos": [
        RequisitoContrato(n=1, tipo="CORRETIVO", segmento=1, descricao="a", criterio_aceite="c"),
        RequisitoContrato(n=2, tipo="EVOLUTIVO", segmento=2, descricao="criar rubrica", criterio_aceite="c", origem="inferido"),
        RequisitoContrato(n=3, tipo="EVOLUTIVO", segmento=2, descricao="alterar o fechamento", criterio_aceite="c", origem="inferido")]})
    e = EscopoEditado(titulo="t", objetivo="o", candidatos_confirmados=[7, 5, 7], requisitos=[
        dict(segmento=1, descricao="corrigir a", n_contrato=1),
        dict(segmento=2, descricao="criar a rubrica de reembolso", n_contrato=2, origem="usuario"),   # a tela não muda a origem
        dict(segmento=2, descricao="pedido novo do usuário")])
    assert cf.inferidos(k, e) == dict(confirmados=[2], removidos=[3])
    cands = [CandidatoFuncionalidade(id=5, nome="REEMBOLSO CALCULAR", tipo_ifpug="EE", similaridade=0.5),
             CandidatoFuncionalidade(id=6, nome="X", similaridade=0.4),
             CandidatoFuncionalidade(id=7, nome="FOLHA FECHAR", similaridade=0.4)]
    funcs = cf.funcionalidades_confirmadas(e, cands)
    assert [f.id for f in funcs] == [5, 7]                                        # ordem dos mostrados, sem repetir
    corr, evol = cf.monta_grupos(k, e, funcs)
    assert [r.origem.value for r in evol.requisitos] == ["inferido", "usuario"]
    assert "2. criar a rubrica de reembolso" + cf.MARCA_INFERIDO in evol.texto and "3. pedido novo do usuário\n" in evol.texto
    for g in (corr, evol):
        assert "FUNCIONALIDADES INDICADAS PELO USUÁRIO\n- REEMBOLSO CALCULAR (EE)\n- FOLHA FECHAR\n" in g.texto
    with pytest.raises(cf.EscopoInvalido, match="99"):
        cf.funcionalidades_confirmadas(e.model_copy(update={"candidatos_confirmados": [5, 99]}), cands)
    assert "FUNCIONALIDADES INDICADAS" not in cf.monta_grupos(k, e)[0].texto


def test_frase_do_outro_chamado_singular_e_plural():
    assert cf.frase_outro_chamado("evolutivo", [2]) == "O requisito 2 deste pedido está no chamado evolutivo vinculado (mesmo pacote)."
    assert cf.frase_outro_chamado("evolutivo", [2, 3]) == \
        "Os requisitos 2 e 3 deste pedido estão no chamado evolutivo vinculado (mesmo pacote)."
    assert cf.frase_outro_chamado("corretivo", [1, 4, 5]).startswith("Os requisitos 1, 4 e 5 deste pedido estão")


def test_segmento_inexistente_e_faltando():
    with pytest.raises(cf.EscopoInvalido):
        cf.monta_grupos(contrato("CORRETIVO"), escopo(1, 2))
    with pytest.raises(cf.EscopoInvalido):
        cf.monta_grupos(contrato("CORRETIVO"), EscopoEditado(titulo="t", objetivo="o", requisitos=[dict(segmento=1, descricao="  ")]))
    with pytest.raises(cf.ConfirmacaoInvalida, match="segmento 1: Exemplo"):
        cf.verifica_confirmavel(contrato("CORRETIVO", faltando=[SlotFaltando(segmento=1, slot="exemplo", rotulo="Exemplo")]))
    cf.verifica_confirmavel(contrato("CORRETIVO"))
    with pytest.raises(cf.ConfirmacaoInvalida):
        cf.verifica_aberta("CONFIRMADA")
    cf.verifica_aberta("PENDENTE")


def vinculo(*chamados, cd_pacote=None):
    return VinculoChamados(chamados=[dict(tipo=t, cd_solicitacao=cd) for t, cd in chamados], cd_pacote=cd_pacote)


def test_valida_vinculo():
    dois = ["CORRETIVO", "EVOLUTIVO"]
    assert cf.valida_vinculo(dois, vinculo(("EVOLUTIVO", 2), ("CORRETIVO", 1), cd_pacote=7)) is False
    assert cf.valida_vinculo(["CORRETIVO"], vinculo(("CORRETIVO", 1))) is False
    with pytest.raises(cf.ConfirmacaoInvalida):                                          # tipos divergentes
        cf.valida_vinculo(dois, vinculo(("CORRETIVO", 1)))
    with pytest.raises(cf.ConfirmacaoInvalida):
        cf.valida_vinculo(["CORRETIVO"], vinculo(("EVOLUTIVO", 1)))
    with pytest.raises(cf.EscopoInvalido):                                               # repetido
        cf.valida_vinculo(dois, vinculo(("CORRETIVO", 1), ("CORRETIVO", 2), cd_pacote=7))
    with pytest.raises(cf.EscopoInvalido):                                               # sem pacote
        cf.valida_vinculo(dois, vinculo(("CORRETIVO", 1), ("EVOLUTIVO", 2)))
    gravado = [dict(tipo="CORRETIVO", cd_solicitacao=1, cd_pacote=7), dict(tipo="EVOLUTIVO", cd_solicitacao=2, cd_pacote=7)]
    assert cf.valida_vinculo(dois, vinculo(("EVOLUTIVO", 2), ("CORRETIVO", 1), cd_pacote=7), gravado) is True   # repetição idêntica
    with pytest.raises(cf.ConfirmacaoInvalida):                                          # outros números
        cf.valida_vinculo(dois, vinculo(("CORRETIVO", 1), ("EVOLUTIVO", 3), cd_pacote=7), gravado)
    with pytest.raises(cf.ConfirmacaoInvalida):                                          # outro pacote
        cf.valida_vinculo(dois, vinculo(("CORRETIVO", 1), ("EVOLUTIVO", 2), cd_pacote=8), gravado)


# ---------------------------------------------------------------- 1.5.1: vários pontos de partida
def test_conteudo_ignora_marca_do_ponto_e_nao_sei_informar():
    assert roteiros.conteudo("[Cadastro > Parcelamento] Alterar") == "alterar"
    assert roteiros.conteudo("[A] Não sei informar; [B] não sei") == ""
    assert not roteiros.preenchido("EVOLUTIVO_EXISTENTE", "o_que_muda", "Não sei informar")
    assert roteiros.preenchido("EVOLUTIVO_EXISTENTE", "o_que_muda", "[A] Incluir a data do edital no cadastro")
    assert roteiros.marca_ponto("Relatório > [x]") == "[Relatório > (x)] "
    assert roteiros.opcoes_do_ponto(["Incluir parcelamento", "Alterar", "Alterar"]) == ["Incluir parcelamento", "Alterar", "Não sei informar"]
    assert len(roteiros.opcoes_do_ponto()) == 7


def test_texto_com_varios_pontos_um_por_linha():
    k = contrato("EVOLUTIVO_EXISTENTE", valores={1: dict(funcionalidade="Menu: A > B; Menu: C",
                                                         o_que_muda="[A > B] Incluir o campo; com data; [C] Mostrar o campo")})
    t1 = cf.monta_grupos(k, escopo(1), n_pontos=2)[0].texto
    assert "Funcionalidades que serão alteradas:\n- Menu: A > B\n- Menu: C\n" in t1
    assert "O que muda:\n- [A > B] Incluir o campo; com data\n- [C] Mostrar o campo" in t1
    t0 = cf.monta_grupos(k, escopo(1))[0].texto                       # sem pontos (ou com 1): como na 1.5.0
    assert "Funcionalidade que será alterada: Menu: A > B; Menu: C" in t0
