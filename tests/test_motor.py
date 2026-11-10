"""Testes do motor real da entrevista com LLM FALSO (sem custo) e o banco `sosia` (pulados se o banco não responder).

O RAG é trocado por um falso na maioria dos testes (rápido e previsível); test_rag_real usa a busca de verdade.
O que se testa é o que NÃO pode depender do LLM: slots revalidados, perguntas filtradas, limite de rodadas, regra documentada só
com trecho do RAG, anonimização antes do prompt, fallback sem IA, contrato com `faltando` determinístico.
"""
import os

os.environ["SOSIA_MOCK"] = "0"

import pytest  # noqa: E402

from nucleo import db, llm  # noqa: E402
from nucleo.entrevista import motor as M  # noqa: E402
from nucleo.entrevista.modelos import Intencao, NovaEntrevista, Resposta, Respostas  # noqa: E402

pytestmark = pytest.mark.skipif(not (db.ping() and db.ping_app()), reason="banco sosia ou sosia_app indisponível")

DOCS = [dict(pagina="sistemas:gai:uc010", titulo="UC010 Manter contrato", secao="Fluxo principal", tipo="caso_de_uso",
             texto="O usuário informa o contrato e salva.", cos=0.84),
        dict(pagina="sistemas:gai:regras", titulo="Regras de negócio", secao="RN021", tipo="regras_de_negocio",
             texto="RN021 - Não é permitido alterar contrato com parcela paga.", cos=0.88)]
CAND = [dict(nome="CONTRATO - ALTERAR", tipo_ifpug="EE", modulo="Contratos", similaridade=0.91)]


class LLMFalso:
    def __init__(self, respostas):
        self.respostas, self.prompts = list(respostas), []

    def __call__(self, prompt, schema, nome="resposta", papel="chat", imagens=None, **kw):
        self.prompts.append((nome, prompt))
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, dict(modelo="falso", tokens_entrada=100, tokens_saida=50, custo_usd=0.001, segundos=0.1)


def resp_entrevista(tipo="CORRETIVO", slots=None, perguntas=(), regra=0, orient="", nota=70, extra_segs=()):
    segs = [dict(tipo=tipo, resumo="Erro ao salvar", slots=[dict(codigo=c, valor=v) for c, v in (slots or {}).items()])]
    segs += [dict(tipo=t, resumo="outro", slots=[]) for t in extra_segs]
    return dict(segmentos=segs, perguntas=[dict(segmento=s, slot=sl, texto=t, dica="") for s, sl, t in perguntas],
                regra_documentada_trecho=regra, regra_documentada_orientacao=orient, nota=nota, dicas_medidor=["Dê um exemplo."])


@pytest.fixture
def ambiente(monkeypatch):
    criados = []
    monkeypatch.setattr(M, "rag", lambda conn, intencao, sid, wiki, texto: ([dict(d) for d in DOCS], [dict(c) for c in CAND]))
    monkeypatch.setattr(llm, "disponivel", lambda: True)
    mot = M.Motor()
    orig = mot.criar

    def criar(req):
        e = orig(req)
        criados.append(e.id)
        return e
    mot.criar = criar

    def usa(*respostas):
        f = LLMFalso(respostas)
        monkeypatch.setattr(llm, "json_estruturado", f)
        return f
    yield mot, usa
    with db.db_app() as c:
        c.execute("DELETE FROM app.entrevista WHERE id = ANY(%s::uuid[])", (criados,))


def nova(intencao=Intencao.PAROU_DE_FUNCIONAR, relato="Ao salvar o contrato aparece erro 500 desde ontem."):
    return NovaEntrevista(cd_sistema=1, sigla="GAI", intencao=intencao, relato=relato)


def test_slots_e_perguntas_nao_dependem_do_llm(ambiente):
    mot, usa = ambiente
    usa(resp_entrevista(slots={"onde_ocorre": "Tela de contratos, botão Salvar", "inventado": "x" * 50},
                        perguntas=[(1, "onde_ocorre", "Onde ocorre?"),          # slot já cheio: descartada
                                   (1, "como_reproduzir", "Quais passos?"),
                                   (9, "slot_que_nao_existe", "??")]))           # slot fora do roteiro: descartada
    e = mot.criar(nova())
    s = e.segmentos[0]
    assert [x.codigo for x in s.slots] == ["onde_ocorre", "o_que_aconteceu", "como_reproduzir", "desde_quando_quem"]
    assert s.slots[0].preenchido and not s.slots[1].preenchido
    slots_perg = [p.slot for p in e.perguntas]
    assert "onde_ocorre" not in slots_perg and "slot_que_nao_existe" not in slots_perg
    # o LLM só perguntou "como_reproduzir"; os outros vazios ganham a pergunta fixa do roteiro
    assert set(slots_perg) == {"o_que_aconteceu", "como_reproduzir", "desde_quando_quem"}
    assert len(e.perguntas) <= 5 and e.analise_ia and not e.pronto
    assert e.candidatos_funcionalidade[0].nome == "CONTRATO - ALTERAR"
    assert 0 <= e.nota < 100


def test_pergunta_de_gatilho_so_com_gatilho_detectado(ambiente):
    mot, usa = ambiente
    usa(resp_entrevista(perguntas=[(1, "gatilho", "É na tela de contratos?")]))      # sem gatilho no relato: descartada
    assert "gatilho" not in [p.slot for p in mot.criar(nova()).perguntas]
    usa(resp_entrevista(perguntas=[(1, "gatilho", "Só consulta o Benner ou grava?"), (1, "gatilho", "E o arquivo?"),
                                   (1, "gatilho", "Terceira pergunta de gatilho")]))
    e = mot.criar(nova(relato="Erro ao gerar o arquivo de remessa para o Benner desde ontem."))   # 2 gatilhos: integracao, arquivo
    assert [p.slot for p in e.perguntas].count("gatilho") == 1                    # 1.5.3: integração não se pergunta


def test_regra_documentada_so_com_trecho_do_rag_e_no_corretivo(ambiente):
    mot, usa = ambiente
    usa(resp_entrevista(regra=2, orient="Pela regra RN021, contrato com parcela paga não pode ser alterado."))
    e = mot.criar(nova(relato="O sistema não permite alterar o contrato 123, que já tem parcela paga."))
    assert e.regra_documentada and e.regra_documentada.secao == "RN021"
    usa(resp_entrevista(regra=7, orient="qualquer"))                               # índice fora da lista do RAG
    assert mot.criar(nova()).regra_documentada is None
    usa(resp_entrevista(tipo="EVOLUTIVO_EXISTENTE", regra=2, orient="x"))          # não é "parou de funcionar"
    assert mot.criar(nova(intencao=Intencao.MUDAR_EXISTENTE, relato="Quero mudar a regra de alteração de contrato.")).regra_documentada is None


def test_regra_mantem_corretivo_ate_o_usuario_decidir(ambiente):
    from nucleo.entrevista.modelos import DecisaoRegraDocumentada
    mot, usa = ambiente
    relato = "O sistema não permite alterar o contrato 123, que já tem parcela paga."
    # o LLM acha a regra E tenta virar evolutivo sozinho: o motor mantém CORRETIVO (onde ocorre vem da funcionalidade)
    usa(resp_entrevista(tipo="EVOLUTIVO_EXISTENTE", slots={"funcionalidade": "Tela de alteração de contrato"}, regra=2,
                        orient="Pela regra RN021, contrato com parcela paga não pode ser alterado."),
        dict(titulo="t", objetivo="o", requisitos=[], fora_escopo=[], duvidas=[], integracoes=[]))
    e = mot.criar(nova(relato=relato))
    assert e.segmentos[0].tipo.value == "CORRETIVO" and e.decisao_regra is None
    assert {x.codigo: x.valor for x in e.segmentos[0].slots}["onde_ocorre"] == "Tela de alteração de contrato"
    k = mot.contrato(e.id)
    assert not k.pode_confirmar and "decisao_regra" in [f.slot for f in k.faltando] and k.regra_documentada.secao == "RN021"

    # aceita: vira evolutivo, o comentário vira "o que muda", e há mais uma rodada do LLM
    f = usa(resp_entrevista(tipo="EVOLUTIVO_EXISTENTE", slots={"funcionalidade": "Tela de alteração de contrato"},
                            perguntas=[(1, "exemplo_concreto", "Tem um exemplo?")]))
    e2 = mot.decidir_regra(e.id, DecisaoRegraDocumentada(aceita=True, comentario="Permitir alterar o endereço mesmo com parcela paga"))
    s = {x.codigo: x for x in e2.segmentos[0].slots}
    assert e2.segmentos[0].tipo.value == "EVOLUTIVO_EXISTENTE" and e2.decisao_regra.value == "ACEITA"
    assert s["o_que_muda"].preenchido and s["funcionalidade"].preenchido and e2.perguntas
    assert "CONFIRMOU" in f.prompts[0][1]
    with pytest.raises(ValueError):
        mot.decidir_regra(e.id, DecisaoRegraDocumentada(aceita=False))

    # recusa: segue corretivo, sem chamada ao LLM, e a decisão destrava o contrato (se os slots estiverem cheios)
    usa(resp_entrevista(regra=2, orient="RN021 ..."))
    e3 = mot.criar(nova(relato=relato))
    f = usa()
    e4 = mot.decidir_regra(e3.id, DecisaoRegraDocumentada(aceita=False, comentario="O contrato 123 não tem parcela paga."))
    assert e4.segmentos[0].tipo.value == "CORRETIVO" and e4.decisao_regra.value == "RECUSADA" and not f.prompts

    usa(resp_entrevista())
    sem = mot.criar(nova())
    with pytest.raises(ValueError):
        mot.decidir_regra(sem.id, DecisaoRegraDocumentada(aceita=True))


def test_pedido_misto_vira_dois_segmentos(ambiente):
    mot, usa = ambiente
    usa(resp_entrevista(extra_segs=["EVOLUTIVO_NOVO", "EVOLUTIVO_EXISTENTE", "EVOLUTIVO_NOVO"]))
    e = mot.criar(nova(relato="O botão salvar dá erro e também preciso de um relatório novo de contratos."))
    assert [s.tipo.value for s in e.segmentos] == ["CORRETIVO", "EVOLUTIVO_NOVO", "EVOLUTIVO_EXISTENTE"]   # máx. 3


def test_anonimiza_antes_do_prompt(ambiente):
    mot, usa = ambiente
    f = usa(resp_entrevista())
    mot.criar(nova(relato="Erro ao salvar o contrato do CPF 123.456.789-09, telefone (61) 98556-8590."))
    prompt = f.prompts[0][1]
    assert "123.456.789-09" not in prompt and "98556-8590" not in prompt and "[CPF]" in prompt


def test_rodadas_respostas_e_limite(ambiente):
    mot, usa = ambiente
    f = usa(resp_entrevista(slots={"onde_ocorre": "Tela de contratos, botão Salvar"}),
            # rodada 2: o LLM "esquece" o slot respondido; o motor mantém a resposta do usuário
            resp_entrevista(slots={"onde_ocorre": "Tela de contratos, botão Salvar"}, perguntas=[(1, "desde_quando_quem", "Desde quando?")]))
    e = mot.criar(nova())
    ids = {p.slot: p.id for p in e.perguntas}
    e2 = mot.responder(e.id, Respostas(respostas=[Resposta(id=ids["como_reproduzir"], texto="1) abrir contrato 123; 2) clicar em Salvar"),
                                                  Resposta(id=ids["o_que_aconteceu"], texto="Aparece a mensagem HTTP 500 e nada é gravado")]))
    s = {x.codigo: x for x in e2.segmentos[0].slots}
    assert s["como_reproduzir"].preenchido and s["o_que_aconteceu"].preenchido
    assert e2.rodada == 2 and [p.slot for p in e2.perguntas] == ["desde_quando_quem"]
    e3 = mot.responder(e2.id, Respostas(respostas=[Resposta(id=e2.perguntas[0].id, texto="ok")]))   # curto demais: segue vazio
    # 2 rodadas do LLM no máximo; o que falta vira complemento (pergunta fixa), sem 3ª chamada
    assert e3.rodada == 3 and e3.complemento and not e3.pronto
    assert [(p.slot, p.id) for p in e3.perguntas] == [("desde_quando_quem", "c1p1")]
    assert len(f.prompts) == 2
    assert mot.obter(e.id).complemento


def test_complemento_sem_llm_ate_completar(ambiente):
    """Depois das 2 rodadas: só o que falta, sem chamar o LLM, com evento `complemento` e analise_ia preservado."""
    mot, usa = ambiente
    f = usa(resp_entrevista(slots={"onde_ocorre": "Tela de contratos, botão Salvar"}), resp_entrevista())
    e = mot.criar(nova())
    e = mot.responder(e.id, Respostas(respostas=[Resposta(id=p.id, texto="ok") for p in e.perguntas]))
    e = mot.responder(e.id, Respostas(respostas=[Resposta(id=p.id, texto="ok") for p in e.perguntas]))
    assert e.complemento and not e.pronto and e.analise_ia and len(f.prompts) == 2
    assert {p.slot for p in e.perguntas} == {"o_que_aconteceu", "como_reproduzir", "desde_quando_quem"}
    assert all(p.id.startswith("c1p") for p in e.perguntas)
    e = mot.responder(e.id, Respostas(respostas=[Resposta(id=p.id, texto="sim") for p in e.perguntas]))   # raso de novo
    assert e.complemento and {p.slot for p in e.perguntas} == {"o_que_aconteceu", "como_reproduzir", "desde_quando_quem"}
    assert all(p.id.startswith("c2p") for p in e.perguntas)                       # ids novos a cada complemento
    resp = {"o_que_aconteceu": "Aparece a mensagem HTTP 500 e nada é gravado", "como_reproduzir": "1) abrir o contrato 123; 2) Salvar",
            "desde_quando_quem": "Desde ontem, todos os usuários"}
    e = mot.responder(e.id, Respostas(respostas=[Resposta(id=p.id, texto=resp[p.slot]) for p in e.perguntas]))
    assert e.pronto and not e.complemento and not e.perguntas and e.rodada == 3 and e.analise_ia
    assert len(f.prompts) == 2                                                     # o complemento nunca chamou o LLM
    with db.db_app() as c:
        row = c.execute("SELECT situacao, rodada FROM app.entrevista WHERE id = %s", (e.id,)).fetchone()
        tipos = [r["tipo"] for r in c.execute("SELECT tipo FROM app.entrevista_evento WHERE entrevista_id = %s ORDER BY id", (e.id,))]
    assert row["situacao"] == "PRONTA" and row["rodada"] == 3
    assert tipos.count("complemento") == 2 and tipos.count("respostas") == 2 and "fallback" not in tipos


def test_fallback_sem_llm(ambiente, monkeypatch):
    mot, usa = ambiente
    usa(RuntimeError("API fora do ar"))
    e = mot.criar(nova(relato="O sistema dá erro ao enviar o arquivo de remessa para o Benner."))
    assert not e.analise_ia and e.perguntas
    assert e.perguntas[0].slot == "gatilho"                                        # gatilho detectado por regra, sem IA
    with db.db_app() as c:
        assert c.execute("SELECT count(*) n FROM app.entrevista_evento WHERE entrevista_id = %s AND tipo = 'fallback'",
                         (e.id,)).fetchone()["n"] == 1


def test_contrato_faltando_deterministico_e_cache(ambiente):
    mot, usa = ambiente
    contrato = dict(titulo="Erro ao salvar contrato", objetivo="Voltar a salvar contratos.",
                    requisitos=[dict(segmento=1, tipo="CORRETIVO", descricao="O sistema deve salvar o contrato sem erro 500.",
                                     criterio_aceite="Salvar o contrato 123 sem erro."),
                                dict(segmento=5, tipo="EVOLUTIVO", descricao="  ", criterio_aceite="")],   # vazio: descartado
                    fora_escopo=["", "Relatórios"], duvidas=[], integracoes=[dict(sistema="Benner", sentido="BUSCA", descricao="consulta")])
    f = usa(resp_entrevista(slots={"onde_ocorre": "Tela de contratos, botão Salvar"}), contrato)
    e = mot.criar(nova())
    c1 = mot.contrato(e.id)
    assert not c1.pode_confirmar and {x.slot for x in c1.faltando} == {"o_que_aconteceu", "como_reproduzir", "desde_quando_quem"}
    assert len(c1.requisitos) == 1 and c1.requisitos[0].n == 1
    assert c1.fora_escopo == [] and c1.integracoes == []                          # 1.5.3: a Sustentação responde
    c2 = mot.contrato(e.id)                                                        # cache: não chama o LLM de novo
    assert c2.titulo == c1.titulo and len(f.prompts) == 2


def test_confirmacao_ponta_a_ponta(ambiente):
    """v0 (IA) e v1 (usuário) gravadas, PENDENTE → CONFIRMADA, expira_em nulo, vínculo idempotente e chamado único."""
    from nucleo.entrevista import confirmacao as cf
    from nucleo.entrevista.modelos import EscopoEditado, VinculoChamados
    mot, usa = ambiente
    cheio = {"onde_ocorre": "Tela de contratos, botão Salvar", "o_que_aconteceu": "Aparece a mensagem HTTP 500 e nada é gravado",
             "como_reproduzir": "1) abrir o contrato 123; 2) clicar em Salvar", "desde_quando_quem": "Desde ontem, todos os usuários"}
    contrato = dict(titulo="Erro ao salvar contrato", objetivo="Voltar a salvar contratos.",
                    requisitos=[dict(segmento=1, tipo="CORRETIVO", descricao="Salvar o contrato sem erro 500.",
                                     criterio_aceite="Salvar o contrato 123 sem erro.")],
                    fora_escopo=[], duvidas=[], integracoes=[])
    usa(resp_entrevista(slots=cheio), contrato)
    e = mot.criar(nova())
    assert e.pronto
    with db.db_app() as c:
        c.execute("UPDATE app.entrevista SET expira_em = now() + interval '30 days' WHERE id = %s", (e.id,))
    esc = EscopoEditado(titulo="Erro ao salvar contrato", objetivo="Voltar a salvar contratos.", cd_usuario=55,
                        requisitos=[dict(segmento=1, descricao="Salvar o contrato sem erro 500.", criterio_aceite="Salvar o 123.")])
    with pytest.raises(cf.ConfirmacaoInvalida):                                    # contrato ainda não gerado
        mot.confirmacao(e.id, esc)
    k = mot.contrato(e.id)
    assert k.pode_confirmar
    conf = mot.confirmacao(e.id, esc)
    assert conf.situacao == "PENDENTE" and [g.tipo.value for g in conf.grupos] == ["CORRETIVO"]
    assert mot.contrato(e.id).titulo == k.titulo                                   # /contrato em PENDENTE não muda a situação
    assert e.id in [p.id for p in mot.pendentes(0)]

    cd = 990000001
    with db.db_app() as c:
        c.execute("DELETE FROM app.entrevista_chamado WHERE cd_solicitacao = %s", (cd,))
    v = VinculoChamados(chamados=[dict(tipo="CORRETIVO", cd_solicitacao=cd)])
    r1 = mot.vincular(e.id, v)
    assert r1.situacao == "CONFIRMADA" and mot.vincular(e.id, v) == r1
    with db.db_app() as c:
        row = c.execute("SELECT situacao, expira_em FROM app.entrevista WHERE id = %s", (e.id,)).fetchone()
        tipos = [r["tipo"] for r in c.execute("SELECT tipo FROM app.entrevista_evento WHERE entrevista_id = %s ORDER BY id", (e.id,))]
    assert row["situacao"] == "CONFIRMADA" and row["expira_em"] is None
    assert tipos.count("vinculo") == 1 and "confirmacao" in tipos

    s = mot.escopo_do_chamado(cd)
    assert [(x.versao, x.origem, x.cd_usuario) for x in s.versoes] == [(0, 0, None), (1, 1, 55)]
    assert s.versoes[1].conteudo["grupos"][0]["tipo"] == "CORRETIVO"
    with pytest.raises(cf.ConfirmacaoInvalida):
        mot.responder(e.id, Respostas(respostas=[Resposta(id="x", texto="y")]))
    with pytest.raises(cf.ConfirmacaoInvalida):
        mot.confirmacao(e.id, esc)

    # número reaproveitado (Oracle de teste restaurado): o vínculo antigo vai para o histórico e o novo entra
    usa(resp_entrevista(slots=cheio), contrato)
    e2 = mot.criar(nova())
    mot.contrato(e2.id)
    mot.confirmacao(e2.id, esc)
    assert mot.vincular(e2.id, v).situacao == "CONFIRMADA"
    assert mot.escopo_do_chamado(cd).entrevista_id == e2.id
    with db.db_app() as c:
        h = c.execute("SELECT entrevista_id, substituido_por, motivo FROM app.entrevista_chamado_historico WHERE cd_solicitacao = %s",
                      (cd,)).fetchall()
        ev_novo = c.execute("SELECT conteudo FROM app.entrevista_evento WHERE entrevista_id = %s AND tipo = 'vinculo_substituido'",
                            (e2.id,)).fetchone()
        ev_antigo = c.execute("SELECT count(*) n FROM app.entrevista_evento WHERE entrevista_id = %s AND tipo = 'vinculo_retirado'",
                              (e.id,)).fetchone()
        c.execute("DELETE FROM app.entrevista_chamado_historico WHERE cd_solicitacao = %s", (cd,))
    assert [(str(x["entrevista_id"]), str(x["substituido_por"])) for x in h] == [(e.id, e2.id)] and "reaproveitado" in h[0]["motivo"]
    assert ev_novo["conteudo"]["de"] == e.id and ev_antigo["n"] == 1
    with pytest.raises(KeyError):
        mot.escopo_do_chamado(cd + 1)


def test_contrato_fixo_criterio_nunca_e_como_reproduzir(ambiente):
    """LLM fora no contrato → roteiro fixo: no corretivo, frase fixa; os passos ficam só nos slots (e no texto do chamado)."""
    from nucleo.entrevista import roteiros
    mot, usa = ambiente
    passos = "1) abrir o contrato 123; 2) clicar em Salvar"
    usa(resp_entrevista(slots={"onde_ocorre": "Tela de contratos, botão Salvar", "como_reproduzir": passos,
                               "o_que_aconteceu": "Aparece a mensagem HTTP 500 e nada é gravado",
                               "desde_quando_quem": "Desde ontem, todos os usuários"}),
        RuntimeError("LLM fora do ar"))
    e = mot.criar(nova())
    k = mot.contrato(e.id)
    assert not k.analise_ia and k.requisitos[0].criterio_aceite == roteiros.CRITERIO_CORRETIVO
    assert passos not in k.requisitos[0].criterio_aceite


def test_trecho_riscado_nao_chega_ao_prompt(ambiente, monkeypatch):
    """Regra revogada na wiki (riscada) sai antes do prompt e não pode ser apontada como regra documentada."""
    riscada = dict(pagina="sistemas:gai:regras", titulo="Regras de negócio", secao="~~RN151 - Troca de leiloeiro~~",
                   tipo="regras_de_negocio", texto="~~Após a publicação não é permitido trocar o leiloeiro.~~", cos=0.9)
    so_texto = dict(riscada, secao="RN152", texto="~~Regra revogada inteira.~~")
    parcial = dict(riscada, secao="RN153", texto="O sistema não permite ~~excluir~~ alterar o item publicado.")
    monkeypatch.setattr(M, "rag", lambda conn, intencao, sid, wiki, texto: ([dict(riscada), dict(so_texto), dict(parcial), dict(DOCS[1])], []))
    mot, usa = ambiente
    f = usa(resp_entrevista(regra=1, orient="Pela regra, não é permitido trocar o leiloeiro depois da publicação."))
    e = mot.criar(nova(relato="O sistema não deixa trocar o leiloeiro da ordem de serviço depois da publicação."))
    prompt = f.prompts[0][1]
    assert "RN151" not in prompt and "Regra revogada inteira" not in prompt
    assert "RN153" in prompt and "RN021" in prompt                                  # risco parcial e regra vigente ficam
    assert e.regra_documentada.secao == "RN153"                                    # o [1] do prompt agora é a RN153
    assert M.riscado(dict(secao="RN1", texto="~~")) is False and M.sem_riscados([]) == []


CHEIO_MENOS_DESDE = {"onde_ocorre": "Tela de contratos, botão Salvar", "o_que_aconteceu": "Aparece a mensagem HTTP 500 e nada é gravado",
                     "como_reproduzir": "1) abrir o contrato 123; 2) clicar em Salvar"}
CONTRATO_LLM = dict(titulo="Erro ao salvar contrato", objetivo="Voltar a salvar contratos.",
                    requisitos=[dict(segmento=1, tipo="CORRETIVO", descricao="O sistema deve salvar o contrato sem erro 500.",
                                     criterio_aceite="Salvar o contrato 123 sem erro.", origem="usuario"),
                                dict(segmento=1, tipo="CORRETIVO", descricao="O sistema deve registrar o erro em log.",
                                     criterio_aceite="Definir.", origem="inferido")],
                    fora_escopo=[], duvidas=[], integracoes=[])


def test_nao_sei_em_item_opcional_encerra_sem_llm_e_vira_duvida(ambiente):
    from nucleo.entrevista import roteiros
    mot, usa = ambiente
    f = usa(resp_entrevista(slots=CHEIO_MENOS_DESDE, nota=90), CONTRATO_LLM)
    e = mot.criar(nova())
    (p,) = e.perguntas
    assert p.slot == "desde_quando_quem" and p.opcoes == ["Não sei informar"]       # pergunta fixa, com o "Não sei"
    e2 = mot.responder(e.id, Respostas(respostas=[Resposta(id=p.id, texto="Não sei informar")]))
    s = {x.codigo: x for x in e2.segmentos[0].slots}
    assert e2.pronto and not e2.complemento and len(f.prompts) == 1                 # encerrou sem nova chamada ao LLM
    assert s["desde_quando_quem"].valor == roteiros.NAO_INFORMADO and s["desde_quando_quem"].preenchido
    assert e2.nota < e.nota                                                        # "Não sei" não conta como preenchido (3 de 4)
    k = mot.contrato(e.id)
    assert k.pode_confirmar and "Não informado: Desde quando e quem é afetado (segmento 1)" in k.duvidas
    assert [r.origem.value for r in k.requisitos] == ["usuario", "inferido"]


def test_nao_sei_nao_dispara_o_gatilho_do_sei():
    assert M.detecta_gatilhos("Deu erro quando fui salvar, nao sei o que houve.") == []
    assert M.detecta_gatilhos("Não sei informar") == [] and M.detecta_gatilhos("sei lá") == []
    assert M.detecta_gatilhos("O processo vem do SEI e não sei o número") == ["integracao"]


def test_gatilhos_com_palavras_curtas_so_com_contexto():
    """1.3.3: SEI/API só em maiúsculas ou com contexto; "caixa" só Caixa Econômica; "enviar"/"retorno" sozinhos não contam."""
    for t in ("Não sei responder", "eu sei que a caixa de seleção some", "ao enviar o formulário", "o retorno da consulta veio vazio",
              "a api do sistema"):
        assert M.detecta_gatilhos(t) == [], t
    for t in ("o processo no sei não abre", "precisa ir para o SEI", "integração via API", "pagamento na Caixa Econômica"):
        assert "integracao" in M.detecta_gatilhos(t), t
    assert M.detecta_gatilhos("gerar o arquivo de retorno") == ["arquivo"]


def test_outros_sistemas_e_fora_de_escopo_ficam_para_a_sustentacao(ambiente):
    """1.5.3: o usuário não é perguntado sobre outros sistemas nem sobre o que fica fora (nem pelo item, nem pelo gatilho, em
    nenhum roteiro); o sistema citado vira dúvida no contrato, para a Sustentação."""
    mot, usa = ambiente
    for intencao, tipo, relato in (
            (Intencao.ALGO_NOVO, "EVOLUTIVO_NOVO", "Preciso de uma pesquisa que consulte os processos do SEI na intranet."),
            (Intencao.PAROU_DE_FUNCIONAR, "CORRETIVO", "Erro ao buscar os pagamentos no Benner desde ontem.")):
        usa(resp_entrevista(tipo=tipo, perguntas=[(1, "gatilho", "Vai só consultar o sistema ou também gravar?"),
                                                  (1, "outros_sistemas", "Busca ou grava dados em outro sistema?"),
                                                  (1, "fora_de_escopo", "O que não faz parte do pedido?")]),
            dict(CONTRATO_LLM, fora_escopo=["Relatórios"], integracoes=[dict(sistema="Benner", sentido="BUSCA", descricao="x")]))
        e = mot.criar(nova(intencao=intencao, relato=relato))
        assert not {"gatilho", "outros_sistemas", "fora_de_escopo"} & {p.slot for p in e.perguntas}
        assert not {"outros_sistemas", "fora_de_escopo"} & {x.codigo for s in e.segmentos for x in s.slots}
        k = mot.contrato(e.id)
        assert k.fora_escopo == [] and k.integracoes == []
        (d,) = [d for d in k.duvidas if d.startswith("Para a Sustentação")]
        assert ("SEI" if intencao == Intencao.ALGO_NOVO else "Benner") in d


def test_item_repetido_cita_a_resposta_e_usa_dica_do_roteiro(ambiente):
    """1.3.3: "HOPE" (curto) no exemplo de algo novo → a pergunta seguinte cita "HOPE" e não fala de contrato/valor."""
    mot, usa = ambiente
    novo = {"quem_usa": "Todos os funcionários", "informacoes_guardadas": "Nome do sistema ou a sigla dele",
            "o_que_faz": "Mostra o link do sistema que tem o nome digitado"}
    usa(resp_entrevista(tipo="EVOLUTIVO_NOVO", slots=novo, perguntas=[(1, "exemplo_concreto", "Pode dar um exemplo de busca?")]),
        resp_entrevista(tipo="EVOLUTIVO_NOVO", slots=novo))                # 2ª rodada: o LLM não repete; entra a pergunta fixa
    e = mot.criar(nova(intencao=Intencao.ALGO_NOVO, relato="Seria bom ter uma pesquisa dos sistemas da intranet."))
    e2 = mot.responder(e.id, Respostas(respostas=[Resposta(id=e.perguntas[0].id, texto="HOPE")]))
    (p,) = e2.perguntas
    assert p.slot == "exemplo_concreto" and "HOPE" in p.texto and "situação de uso" in p.texto
    assert "contrato" not in (p.dica or "").lower() and "HOPE" in p.dica


def test_evento_tempos_da_criacao(ambiente):
    mot, usa = ambiente
    usa(resp_entrevista())
    e = mot.criar(nova())
    with db.db_app() as c:
        ev = c.execute("SELECT conteudo FROM app.entrevista_evento WHERE entrevista_id = %s AND tipo = 'tempos'", (e.id,)).fetchone()
    assert {"resolve_sistema_s", "pii_s", "rag_s", "llm_entrevista_s", "total_s"} <= set(ev["conteudo"])


def test_aquecimento_pre_carrega_so_a_coluna_em_uso():
    from nucleo import aquecimento, embeddings
    with db.db() as c:
        rels = aquecimento._relacoes(c)
        blocos, _ = aquecimento.prewarm(c)
    assert any("conhecimento_trecho" in r for r in rels) and any(r.startswith("pg_toast.") for r in rels)
    outra = "ix_conh_trecho_emb" if embeddings.coluna_rag() == "embedding_3l" else "ix_conh_trecho_emb3l"
    assert not any(r.split(".")[-1] == outra for r in rels)
    assert blocos is None or blocos > 0


def test_nao_sei_em_item_bloqueante_continua_pedindo(ambiente):
    mot, usa = ambiente
    slots = dict(CHEIO_MENOS_DESDE, desde_quando_quem="Desde ontem, todos os usuários")
    slots.pop("onde_ocorre")
    usa(resp_entrevista(slots=slots), resp_entrevista(slots=slots))
    e = mot.criar(nova())
    (p,) = e.perguntas
    assert p.slot == "onde_ocorre" and p.opcoes == []                               # bloqueante: sem "Não sei informar"
    e2 = mot.responder(e.id, Respostas(respostas=[Resposta(id=p.id, texto="Não sei")]))
    assert not e2.pronto and [x.slot for x in e2.perguntas] == ["onde_ocorre"]
    assert not {x.codigo: x for x in e2.segmentos[0].slots}["onde_ocorre"].preenchido


def test_opcoes_da_ia_limpas_e_nao_sei_so_onde_cabe(ambiente):
    mot, usa = ambiente
    r = resp_entrevista(slots={"onde_ocorre": "Tela de contratos, botão Salvar"},
                        perguntas=[(1, "desde_quando_quem", "Desde quando?"), (1, "como_reproduzir", "Quais passos?"),
                                   (1, "o_que_aconteceu", "O que aparece?")])
    r["perguntas"][0]["opcoes"] = ["Desde ontem", "desde ontem", "  Há mais de   um mês ", "Não sei", "x" * 100, "Só comigo",
                                   "Toda a área", "Sobrando"]
    r["perguntas"][1]["opcoes"] = []
    r["perguntas"][2]["opcoes"] = ["Mensagem de erro", "Tela em branco"]
    usa(r)
    op = {p.slot: p.opcoes for p in mot.criar(nova()).perguntas}
    assert op["desde_quando_quem"] == ["Desde ontem", "Há mais de um mês", "Só comigo", "Toda a área", "Não sei informar"]
    assert op["como_reproduzir"] == ["Não sei informar"]
    assert op["o_que_aconteceu"] == ["Mensagem de erro", "Tela em branco"]          # bloqueante: sem "Não sei"


def test_confirmacao_com_inferido_removido_e_funcionalidade_confirmada(ambiente, monkeypatch):
    from nucleo.entrevista import confirmacao as cf
    from nucleo.entrevista.modelos import EscopoEditado
    cands = [dict(id=4101, nome="CONTRATO - ALTERAR", tipo_ifpug="EE", modulo="Contratos", similaridade=0.6),
             dict(id=4102, nome="CONTRATO - CONSULTAR", tipo_ifpug="CE", modulo="Contratos", similaridade=0.5)]
    monkeypatch.setattr(M, "rag", lambda conn, intencao, sid, wiki, texto: ([dict(d) for d in DOCS], [dict(c) for c in cands]))
    mot, usa = ambiente
    usa(resp_entrevista(slots=dict(CHEIO_MENOS_DESDE, desde_quando_quem="Desde ontem, todos")), CONTRATO_LLM)
    e = mot.criar(nova())
    assert [c.id for c in e.candidatos_funcionalidade] == [4101, 4102]
    k = mot.contrato(e.id)
    base = dict(titulo=k.titulo, objetivo=k.objetivo, cd_usuario=7,
                requisitos=[dict(segmento=1, descricao=k.requisitos[0].descricao, n_contrato=1)])   # o inferido (n=2) foi removido
    with pytest.raises(cf.EscopoInvalido):
        mot.confirmacao(e.id, EscopoEditado(**base, candidatos_confirmados=[999]))
    conf = mot.confirmacao(e.id, EscopoEditado(**base, candidatos_confirmados=[4101]))
    assert [f.nome for f in conf.funcionalidades] == ["CONTRATO - ALTERAR"]
    assert "FUNCIONALIDADES INDICADAS PELO USUÁRIO\n- CONTRATO - ALTERAR (EE)" in conf.grupos[0].texto
    with db.db_app() as c:
        v1 = c.execute("SELECT conteudo FROM app.entrevista_escopo WHERE entrevista_id = %s AND versao = 1", (e.id,)).fetchone()
        ev = c.execute("SELECT conteudo FROM app.entrevista_evento WHERE entrevista_id = %s AND tipo = 'inferidos'", (e.id,)).fetchone()
    assert v1["conteudo"]["candidatos_confirmados"] == [dict(id=4101, nome="CONTRATO - ALTERAR", tipo_ifpug="EE")]
    assert ev["conteudo"] == dict(confirmados=[], removidos=[2])


# ---------------------------------------------------------------- 1.5.0: ponto de partida e documentação
def com_ponto(intencao, caminho="Cadastro > NUGIV > Parcelamento Resíduo", fonte="forms", relato=None):
    from nucleo.entrevista.modelos import PontoPartida
    return NovaEntrevista(cd_sistema=1, sigla="GAI", intencao=intencao,
                          relato=relato or "Ao salvar o parcelamento aparece erro 500 desde ontem.",
                          ponto_partida=PontoPartida(caminho=caminho, fonte=fonte, menu_id="CADATRO_NUNEG_MENU.PARCELAMENTO_RESÍDUO"))


def test_ponto_de_partida_no_corretivo_preenche_onde_ocorre_e_entra_na_busca(ambiente, monkeypatch):
    consultas = []
    monkeypatch.setattr(M, "rag", lambda conn, intencao, sid, wiki, texto: (consultas.append(texto) or [dict(d) for d in DOCS], []))
    mot, usa = ambiente
    # o LLM tenta trocar o onde_ocorre e perguntar de novo: o menu escolhido vale e a pergunta sai
    f = usa(resp_entrevista(slots={"onde_ocorre": "tela"}, perguntas=[(1, "onde_ocorre", "Em que tela?")]))
    e = mot.criar(com_ponto(Intencao.PAROU_DE_FUNCIONAR))
    assert consultas[0].startswith("Menu: Cadastro > NUGIV > Parcelamento Resíduo.\n")
    onde = next(x for x in e.segmentos[0].slots if x.codigo == "onde_ocorre")
    assert onde.valor == "Menu: Cadastro > NUGIV > Parcelamento Resíduo" and onde.preenchido
    assert "onde_ocorre" not in [p.slot for p in e.perguntas]
    assert "NÃO pergunte onde é" in f.prompts[0][1]
    with db.db_app() as c:
        ev = c.execute("SELECT conteudo FROM app.entrevista_evento WHERE entrevista_id = %s AND tipo = 'ponto_partida'", (e.id,)).fetchone()
    assert ev["conteudo"] == dict(fonte="forms", menu_id="CADATRO_NUNEG_MENU.PARCELAMENTO_RESÍDUO")


def test_ponto_de_partida_no_evolutivo_e_no_algo_novo(ambiente, monkeypatch):
    mot, usa = ambiente
    usa(resp_entrevista(tipo="EVOLUTIVO_EXISTENTE"), RuntimeError("LLM fora"))
    e = mot.criar(com_ponto(Intencao.MUDAR_EXISTENTE, relato="Quero que o parcelamento aceite 25% de desconto no 2º edital."))
    func = next(x for x in e.segmentos[0].slots if x.codigo == "funcionalidade")
    assert func.valor == "Menu: Cadastro > NUGIV > Parcelamento Resíduo" and "funcionalidade" not in [p.slot for p in e.perguntas]
    # roteiro fixo (LLM fora) também usa o ponto de partida; fonte livre fica como o usuário escreveu
    e2 = mot.criar(com_ponto(Intencao.PAROU_DE_FUNCIONAR, caminho="tela de baixa manual", fonte="livre"))
    assert not e2.analise_ia
    assert next(x for x in e2.segmentos[0].slots if x.codigo == "onde_ocorre").valor == "tela de baixa manual"
    f = usa(resp_entrevista(tipo="EVOLUTIVO_NOVO"))
    e3 = mot.criar(com_ponto(Intencao.ALGO_NOVO, caminho="Relatório > NUGEC", relato="Preciso de um relatório novo de inadimplência."))
    assert all(not (x.valor or "").startswith("Menu:") for x in e3.segmentos[0].slots)
    assert "ONDE FICARIA" in f.prompts[0][1]


def test_documentos_e_documentacao(ambiente, monkeypatch):
    from nucleo.entrevista import confirmacao as cf
    from nucleo.entrevista.modelos import EscopoEditado
    uc = "sistemas:gai:documentacao_tecnica:caso_de_uso:uc021:inicio"
    docs = [dict(pagina=uc, titulo="UC021", secao="FLUXO", tipo="caso_de_uso", texto="x", cos=0.8),
            dict(pagina=uc, titulo="UC021", secao="OBS", tipo="caso_de_uso", texto="y", cos=0.7),            # mesma página
            dict(pagina="sistemas:gai:msg", titulo="Mensagens", secao="M1", tipo="mensagens", texto="z", cos=0.7),  # tipo fora
            dict(DOCS[1])]
    monkeypatch.setattr(M, "rag", lambda conn, intencao, sid, wiki, texto: ([dict(d) for d in docs], []))
    mot, usa = ambiente
    usa(resp_entrevista(slots=dict(CHEIO_MENOS_DESDE, desde_quando_quem="Desde ontem, todos")), CONTRATO_LLM)
    e = mot.criar(nova())
    assert [(d.pagina, d.tipo) for d in e.documentos] == [(uc, "caso_de_uso"), ("sistemas:gai:regras", "regras_de_negocio")]
    assert mot.obter(e.id).documentos == e.documentos
    pag = mot.documentacao(uc, e.id)                       # página real da wiki do GAI no banco do núcleo
    assert pag.tipo == "caso_de_uso" and pag.secoes and "UC021" in pag.titulo
    for outra in ("sistemas:gop:qualquer", "wiki:gai:uc021", uc + "x"):
        with pytest.raises(KeyError):
            mot.documentacao(outra, e.id)
    with pytest.raises(KeyError):
        mot.documentacao(uc, "00000000-0000-0000-0000-000000000000")
    k = mot.contrato(e.id)
    base = dict(titulo=k.titulo, objetivo=k.objetivo, requisitos=[dict(segmento=1, descricao=k.requisitos[0].descricao, n_contrato=1)])
    with pytest.raises(cf.EscopoInvalido):
        mot.confirmacao(e.id, EscopoEditado(**base, documentos_lidos=["sistemas:gai:msg"]))
    mot.confirmacao(e.id, EscopoEditado(**base, documentos_lidos=[uc, uc]))
    with db.db_app() as c:
        v1 = c.execute("SELECT conteudo FROM app.entrevista_escopo WHERE entrevista_id = %s AND versao = 1", (e.id,)).fetchone()
    assert v1["conteudo"]["documentos_lidos"] == [uc]


def test_menus_do_catalogo(ambiente):
    """Lê o catálogo carregado no banco do núcleo (pulado sem a carga); sigla desconhecida dá "nenhuma"."""
    mot, _ = ambiente
    assert mot.menus(999, "SIGLA_QUE_NAO_EXISTE").fonte.value == "nenhuma"
    with db.db() as c:
        carregado = c.execute("SELECT to_regclass('apf.menu_sistema') IS NOT NULL AND EXISTS (SELECT 1 FROM apf.menu_sistema "
                              "WHERE sistema = 'gai' AND fonte = 'forms') AS ok").fetchone()["ok"]
    if not carregado:
        pytest.skip("catálogo de menus do GAI não carregado (migracao/carrega_menus.py)")
    m = mot.menus(1, "gai")
    assert m.fonte.value == "forms" and len(m.menus) > 180
    ids = {x.id for x in m.menus}
    assert all(x.pai is None or x.pai in ids for x in m.menus)
    vistos = set()
    for x in m.menus:                                      # ordem de leitura: o pai vem antes do filho
        assert x.pai is None or x.pai in vistos
        vistos.add(x.id)


def test_id_inexistente(ambiente):
    mot, _ = ambiente
    with pytest.raises(KeyError):
        mot.obter("00000000-0000-0000-0000-000000000000")
    with pytest.raises(KeyError):
        mot.obter("nao-e-uuid")


def test_rag_real_traz_documentacao_e_candidatos():
    from nucleo.entrevista.motor import rag, resolve_sistema
    with db.db() as c:
        sid, wiki = resolve_sistema(c, "GAI")      # o GRH não tem catálogo de funções (sem baseline)
        assert sid and wiki == "gai"
        docs, cand = rag(c, Intencao.PAROU_DE_FUNCIONAR, sid, wiki, "erro ao alterar o contrato de alienação do imóvel")
        assert docs and all(d["texto"] for d in docs)
        assert any(d["tipo"] in ("regras_de_negocio", "mensagens") for d in docs)
        assert cand and cand[0]["similaridade"] > 0.5


def test_candidatos_fusao_real_e_reordenacao(monkeypatch):
    """Catálogo enriquecido + palavra-chave + histórico no GAI; a reordenação usa só ids da lista e completa com a fusão."""
    import datetime
    from nucleo.entrevista.motor import resolve_sistema
    from nucleo.rag import catalogo
    with db.db() as c:
        sid, _ = resolve_sistema(c, "GAI")
        cands = catalogo.candidatos(c, sid, datetime.date.today(), ["erro ao alterar o endereço de correspondência da alienação"])
    assert 5 < len(cands) <= catalogo.PROFUNDIDADE and all(x["texto"] for x in cands)
    ids = [x["id"] for x in cands]
    monkeypatch.setattr(llm, "json_estruturado", LLMFalso([dict(ids=[ids[7], 999999999, ids[7], ids[2]])]))
    nova_ordem, _ = catalogo.reordena(cands, "pedido", "GAI")
    assert [x["id"] for x in nova_ordem][:3] == [ids[7], ids[2], ids[0]] and len(nova_ordem) == len(cands)


def test_motor_mostra_5_candidatos_e_falha_do_llm_mantem_fusao(monkeypatch):
    from nucleo.entrevista.motor import Motor
    cands = [dict(id=i, nome=f"F{i}", tipo_ifpug="EE", modulo=None, similaridade=0.5, texto=f"EE f{i}") for i in range(12)]
    monkeypatch.setattr(llm, "disponivel", lambda: True)
    monkeypatch.setattr(llm, "json_estruturado", LLMFalso([RuntimeError("fora do ar")]))
    with db.db_app() as c:
        eid = "00000000-0000-0000-0000-0000000000aa"
        from nucleo.entrevista import armazem
        armazem.garante(c)
        c.execute("DELETE FROM app.entrevista WHERE id = %s::uuid", (eid,))
        armazem.criar(c, eid, nova(), None, "local", True, dict(x=1), 0, None)
        est = dict(candidatos=cands, texto="t", sigla="GAI", rodada=1)
        out = Motor._reordena_candidatos(c, eid, est)
        ev = c.execute("SELECT tipo FROM app.entrevista_evento WHERE entrevista_id = %s::uuid", (eid,)).fetchall()
        c.execute("DELETE FROM app.entrevista WHERE id = %s::uuid", (eid,))
        c.commit()
    assert [x["id"] for x in out] == [0, 1, 2, 3, 4] and all("texto" not in x for x in out)
    assert [e["tipo"] for e in ev] == ["fallback_candidatos"]


# ---------------------------------------------------------------- 1.5.1: vários pontos de partida
def com_pontos(intencao, caminhos, relato="Quero que o parcelamento aceite 25% de desconto no 2º edital."):
    from nucleo.entrevista.modelos import PontoPartida
    return NovaEntrevista(cd_sistema=1, sigla="GAI", intencao=intencao, relato=relato,
                          pontos_partida=[PontoPartida(caminho=c, fonte="forms", menu_id=f"M.{i}") for i, c in enumerate(caminhos, 1)])


def rag_por_ponto(consultas, catalogos):
    """RAG falso: cada busca devolve uma página própria + uma página comum a todas (para testar a junção sem repetir)."""
    def f(conn, intencao, sid, wiki, texto, com_catalogo=True):
        consultas.append(texto)
        catalogos.append(com_catalogo)
        n = len(consultas)
        docs = [dict(pagina=f"sistemas:gai:uc0{n}", titulo=f"UC0{n}", secao="Fluxo", tipo="caso_de_uso", texto=f"uc{n}", cos=0.8),
                dict(pagina="sistemas:gai:comum", titulo="Comum", secao="Fluxo", tipo="caso_de_uso", texto="comum", cos=0.7)]
        return docs, ([dict(c) for c in CAND] if com_catalogo else [])
    return f


RELATO_25 = "Quero que o parcelamento aceite 25% de desconto no 2º edital."


def test_dois_pontos_duas_buscas_documentos_por_ponto_e_o_que_muda_por_ponto(ambiente, monkeypatch):
    consultas, catalogos = [], []
    monkeypatch.setattr(M, "rag", rag_por_ponto(consultas, catalogos))
    mot, usa = ambiente
    cam = ["Cadastro > NUGIV > Parcelamento Resíduo", "Relatório > NUGEC > Demonstrativo"]
    # o LLM não pergunta o que muda (e tenta trocar a funcionalidade): a aplicação pergunta por ponto, com os tipos de mudança
    f = usa(resp_entrevista(tipo="EVOLUTIVO_EXISTENTE", slots={"funcionalidade": "outra tela"}), resp_entrevista(tipo="EVOLUTIVO_EXISTENTE"))
    e = mot.criar(com_pontos(Intencao.MUDAR_EXISTENTE, cam))
    assert consultas == [f"Menu: {cam[0]}.\n{RELATO_25}", f"Menu: {cam[1]}.\n{RELATO_25}"]
    assert catalogos == [True, False] and e.candidatos_funcionalidade             # candidatos só da 1ª busca
    assert [(d.pagina, d.ponto) for d in e.documentos] == [("sistemas:gai:uc01", 1), ("sistemas:gai:comum", 1),
                                                          ("sistemas:gai:uc02", 2)]
    func = next(x for x in e.segmentos[0].slots if x.codigo == "funcionalidade")
    assert func.valor == f"Menu: {cam[0]}; Menu: {cam[1]}" and e.segmentos[0].ponto is None
    pq = [p for p in e.perguntas if p.slot == "o_que_muda"]
    assert [p.ponto for p in pq] == [1, 2] and pq[0].texto.startswith(f"Em {cam[0]},")
    assert pq[0].opcoes[:2] == ["Incluir ou cadastrar", "Alterar"] and pq[0].opcoes[-1] == "Não sei informar" and len(pq[0].opcoes) == 7
    prompt = f.prompts[0][1]
    assert f'1. "{cam[0]}"' in prompt and f'2. "{cam[1]}"' in prompt and "(ponto 2)" in prompt

    # respostas por ponto: o item guarda de qual tela é; "Alterar" sozinho não basta e "Não sei informar" não conta
    e2 = mot.responder(e.id, Respostas(respostas=[Resposta(id=pq[0].id, texto="Alterar"),
                                                  Resposta(id=pq[1].id, texto="Não sei informar")]))
    muda = next(x for x in e2.segmentos[0].slots if x.codigo == "o_que_muda")
    assert muda.valor == f"[{cam[0]}] Alterar; [{cam[1]}] Não sei informar" and not muda.preenchido
    assert "(ponto 1)" in f.prompts[1][1]


def test_segmento_por_ponto_e_texto_do_chamado_um_caminho_por_linha(ambiente, monkeypatch):
    from nucleo.entrevista.modelos import EscopoEditado
    monkeypatch.setattr(M, "rag", rag_por_ponto([], []))
    mot, usa = ambiente
    cam = ["Cadastro > Parcelamento", "Relatório > Demonstrativo"]
    cheio = dict(o_que_muda="Aceitar desconto de 25% no segundo edital", exemplo_concreto="Edital 2/2026 com 25% de desconto")
    # um segmento por ponto: cada um recebe só o seu caminho
    r = resp_entrevista(tipo="EVOLUTIVO_EXISTENTE", slots=cheio, extra_segs=["EVOLUTIVO_EXISTENTE"])
    r["segmentos"][0]["ponto"], r["segmentos"][1]["ponto"] = 1, 2
    r["segmentos"][1]["slots"] = [dict(codigo=c, valor=v) for c, v in dict(cheio, o_que_muda="Mostrar o desconto no demonstrativo").items()]
    usa(r)
    e = mot.criar(com_pontos(Intencao.MUDAR_EXISTENTE, cam))
    got = [(s.ponto, next(x.valor for x in s.slots if x.codigo == "funcionalidade")) for s in e.segmentos]
    assert got == [(1, f"Menu: {cam[0]}"), (2, f"Menu: {cam[1]}")]
    # ponto inválido (ou um ponto sem segmento): o 1º segmento volta a valer para todos
    r2 = resp_entrevista(tipo="EVOLUTIVO_EXISTENTE", slots=cheio)
    r2["segmentos"][0]["ponto"] = 9
    usa(r2, CONTRATO_LLM)
    e2 = mot.criar(com_pontos(Intencao.MUDAR_EXISTENTE, cam))
    assert e2.segmentos[0].ponto is None
    assert next(x.valor for x in e2.segmentos[0].slots if x.codigo == "funcionalidade") == f"Menu: {cam[0]}; Menu: {cam[1]}"
    k = mot.contrato(e2.id)
    conf = mot.confirmacao(e2.id, EscopoEditado(titulo=k.titulo, objetivo=k.objetivo,
                                                requisitos=[dict(segmento=1, descricao="Aceitar o desconto", n_contrato=1)]))
    assert f"Funcionalidades que serão alteradas:\n- Menu: {cam[0]}\n- Menu: {cam[1]}" in conf.grupos[0].texto
    with db.db_app() as c:
        v1 = c.execute("SELECT conteudo FROM app.entrevista_escopo WHERE entrevista_id = %s AND versao = 1", (e2.id,)).fetchone()
        ev = c.execute("SELECT conteudo FROM app.entrevista_evento WHERE entrevista_id = %s AND tipo = 'ponto_partida'", (e2.id,)).fetchone()
    assert [p["caminho"] for p in v1["conteudo"]["pontos_partida"]] == cam and v1["conteudo"]["ponto_partida"]["caminho"] == cam[0]
    assert ev["conteudo"]["pontos"] == [dict(fonte="forms", menu_id="M.1"), dict(fonte="forms", menu_id="M.2")]


def test_ponto_antigo_vale_como_lista_de_um(ambiente, monkeypatch):
    from pydantic import ValidationError
    from nucleo.entrevista.modelos import PontoPartida
    consultas = []
    monkeypatch.setattr(M, "rag", rag_por_ponto(consultas, []))
    mot, usa = ambiente
    usa(resp_entrevista(tipo="EVOLUTIVO_EXISTENTE", slots={"o_que_muda": ""}, perguntas=[(1, "o_que_muda", "O que muda no parcelamento?")]))
    e = mot.criar(com_ponto(Intencao.MUDAR_EXISTENTE))          # ponto_partida da 1.5.0
    assert len(consultas) == 1 and e.documentos and all(d.ponto == 1 for d in e.documentos)
    p = next(p for p in e.perguntas if p.slot == "o_que_muda")
    assert p.ponto is None and p.texto == "O que muda no parcelamento?"
    assert p.opcoes[-1] == "Não sei informar" and "Alterar" in p.opcoes      # opções da aplicação, não da IA
    pp = PontoPartida(caminho="Cadastro > X", fonte="forms")
    base = dict(cd_sistema=1, sigla="GAI", intencao=Intencao.MUDAR_EXISTENTE, relato="relato longo o bastante")
    with pytest.raises(ValidationError):
        NovaEntrevista(**base, ponto_partida=pp, pontos_partida=[pp])
    with pytest.raises(ValidationError):
        NovaEntrevista(**base, pontos_partida=[pp] * 6)


def test_pergunta_nao_junta_os_caminhos_de_pontos_diferentes(ambiente, monkeypatch):
    """Teste do SOS (10/11): corretivo com 3 pontos, o LLM juntou os caminhos com " > " como se fossem uma tela só."""
    from nucleo.entrevista.modelos import PontoPartida
    monkeypatch.setattr(M, "rag", rag_por_ponto([], []))
    mot, usa = ambiente
    cam = ["Venda Direta", "Edital Venda por Licitação > Cadastrar Controle Edital", "Tela de impressao do controle"]
    junto = " > ".join(cam)
    f = usa(resp_entrevista(perguntas=[(1, "como_reproduzir", f"Em {junto}, o que você faz até aparecer o erro?")]))
    e = mot.criar(NovaEntrevista(cd_sistema=666, sigla="GOP", intencao=Intencao.PAROU_DE_FUNCIONAR,
                                 relato="O desconto sai errado na impressão do controle de operação.",
                                 pontos_partida=[PontoPartida(caminho=cam[0], fonte="fgca", menu_id="4996"),
                                                 PontoPartida(caminho=cam[1], fonte="fgca", menu_id="824"),
                                                 PontoPartida(caminho=cam[2], fonte="livre")]))
    p = next(p for p in e.perguntas if p.slot == "como_reproduzir")
    assert p.texto == f"Em {'; '.join(cam)}, o que você faz até aparecer o erro?"
    assert "nunca junte dois caminhos" in f.prompts[0][1]


def test_regras_de_negocio_so_a_regra_que_casou(ambiente, monkeypatch):
    """Teste do SOS (10/11): a página geral de regras (368 seções no GAI) vinha em 1º e inteira na leitura."""
    rn = "sistemas:gai:documentacao_tecnica:regras_de_negocio:inicio"
    sec = "DOCUMENTO DE REGRAS DE NEGÓCIO > 3. REGRAS DO NEGÓCIO > RN02. Cálculo da Correção e do Valor Corrigido"
    uc = "sistemas:gai:documentacao_tecnica:caso_de_uso:uc021:inicio"
    docs = [dict(pagina=rn, titulo="gai - regras de negocio", secao=sec, tipo="regras_de_negocio", texto="RN02", cos=0.7),
            dict(pagina=uc, titulo="UC021", secao="FLUXO", tipo="caso_de_uso", texto="x", cos=0.6)]
    monkeypatch.setattr(M, "rag", lambda conn, intencao, sid, wiki, texto: ([dict(d) for d in docs], []))
    mot, usa = ambiente
    usa(resp_entrevista())
    e = mot.criar(nova())
    assert [d.pagina for d in e.documentos] == [uc, rn]                  # UC/US antes da página geral de regras
    assert e.documentos[1].titulo == "Regras de negócio: RN02. Cálculo da Correção e do Valor Corrigido"
    pag = mot.documentacao(rn, e.id)
    assert pag.parcial and pag.secoes_total > 300 and pag.secoes[0].secao == sec and len(pag.secoes) <= M.MAX_SECOES_REGRA
    assert pag.titulo == e.documentos[1].titulo
    inteira = mot.documentacao(uc, e.id)                                 # as outras páginas continuam inteiras
    assert not inteira.parcial and inteira.secoes_total == len(inteira.secoes)


def test_secoes_da_regra_com_mae_e_sub_regras():
    raiz = "DOC > 3. REGRAS"
    s = [f"{raiz}", f"{raiz} > RN96. Emitir", f"{raiz} > RN96. Emitir > RN99. Cálculo", f"{raiz} > RN96. Emitir > RN99. Cálculo > a",
         f"{raiz} > RN97. Outra", "DOC"]
    assert M.secoes_da_regra(s, [s[2]]) == [1, 2, 3]                     # mãe, a que casou e a sub-regra; sem a raiz
    assert M.secoes_da_regra(s, ["outra seção"]) is None and M.secoes_da_regra(s, [None]) is None


def test_sem_llm_com_dois_pontos_pergunta_por_ponto(ambiente, monkeypatch):
    consultas = []
    monkeypatch.setattr(M, "rag", rag_por_ponto(consultas, []))
    mot, usa = ambiente
    usa(RuntimeError("LLM fora"))
    e = mot.criar(com_pontos(Intencao.MUDAR_EXISTENTE, ["Cadastro > A", "Cadastro > B", "cadastro > a"]))
    assert not e.analise_ia and len(consultas) == 3              # menu_id diferente: são 3 pontos
    assert [(p.slot, p.ponto) for p in e.perguntas][:3] == [("o_que_muda", 1), ("o_que_muda", 2), ("o_que_muda", 3)]
