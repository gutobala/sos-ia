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
    assert [p.slot for p in e.perguntas].count("gatilho") == 2


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
    assert e3.pronto and e3.rodada == 3 and not e3.perguntas                       # 2 rodadas no máximo, sem 3ª chamada
    assert len(f.prompts) == 2
    assert not mot.obter(e.id).perguntas


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
    assert len(c1.requisitos) == 1 and c1.requisitos[0].n == 1 and c1.fora_escopo == ["Relatórios"]
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

    # o mesmo chamado não pode ir para outra entrevista
    usa(resp_entrevista(slots=cheio), contrato)
    e2 = mot.criar(nova())
    mot.contrato(e2.id)
    mot.confirmacao(e2.id, esc)
    with pytest.raises(cf.ConfirmacaoInvalida):
        mot.vincular(e2.id, v)
    with pytest.raises(KeyError):
        mot.escopo_do_chamado(cd + 1)


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
