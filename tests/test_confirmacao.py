"""Regras puras da confirmação do Escopo do Pedido (contrato 1.2.0): sem banco, sem LLM, sem API."""
import pytest

from nucleo.entrevista import confirmacao as cf
from nucleo.entrevista.modelos import (Contrato, DecisaoRegra, EscopoEditado, Integracao, RegraDocumentada, Segmento, SlotFaltando,
                                       VinculoChamados)


def contrato(*tipos, faltando=(), regra=None, decisao=None):
    segs = [Segmento(n=i, tipo=t, resumo=f"seg {i}", slots=[]) for i, t in enumerate(tipos, 1)]
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
    assert "Os requisitos 1 deste pedido estão no chamado corretivo vinculado (mesmo pacote)." in t
    assert "- Benner (BUSCA): consulta" in t and "- Vale para contratos antigos?" in t and "- \n" not in t
    assert "REGRA DOCUMENTADA QUE O USUÁRIO QUER MUDAR\nsistemas:gai:regras / RN021" in t
    assert "PROBLEMAS A CORRIGIR" in corr.texto and "chamado evolutivo vinculado" in corr.texto
    # regra recusada não vai para o chamado
    k2 = contrato("CORRETIVO", regra=REGRA, decisao=DecisaoRegra.RECUSADA)
    assert "REGRA DOCUMENTADA" not in cf.monta_grupos(k2, escopo(1))[0].texto and cf.regra_aceita(k2) is None


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
