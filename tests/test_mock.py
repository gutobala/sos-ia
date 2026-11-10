"""Testes do contrato da API com o motor mock (sem rede, sem LLM, sem banco)."""
import json
import os

os.environ["SOSIA_MOCK"] = "1"
os.environ.pop("SOSIA_TOKEN", None)

import jsonschema  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from contrato_api import gera_schemas  # noqa: E402
from nucleo import config  # noqa: E402
from servico.app import app  # noqa: E402

H = {"X-SOSIA-Token": config.TOKEN}      # "mock" sem .env; o SOSIA_TOKEN do .env quando ele existe
c = TestClient(app)
SCHEMAS = {n: cls.model_json_schema() for n, cls in gera_schemas.MODELOS.items()}


def valida(nome, corpo):
    jsonschema.validate(corpo, SCHEMAS[nome])
    return corpo


def nova(relato, intencao="PAROU_DE_FUNCIONAR", **kw):
    r = c.post("/entrevistas", headers=H, json=dict(cd_sistema=12, sigla="GAI", intencao=intencao, relato=relato, **kw))
    assert r.status_code == 200, r.text
    return valida("EstadoEntrevista", r.json())


def responde_tudo(e, texto="resposta detalhada o suficiente para o slot"):
    r = c.post(f"/entrevistas/{e['id']}/respostas", headers=H,
               json={"respostas": [{"id": p["id"], "texto": texto} for p in e["perguntas"]]})
    assert r.status_code == 200, r.text
    return valida("EstadoEntrevista", r.json())


def test_saude_sem_token():
    r = c.get("/saude")
    assert r.status_code == 200
    valida("Saude", r.json())
    assert r.json()["mock"] is True


def test_token_obrigatorio():
    r = c.post("/entrevistas", json=dict(cd_sistema=1, sigla="X", intencao="ALGO_NOVO", relato="preciso de um cadastro novo"))
    assert r.status_code == 401
    valida("Erro", r.json())


def test_validacao_vira_erro_do_contrato():
    r = c.post("/entrevistas", headers=H, json=dict(cd_sistema=1, sigla="X", intencao="QUALQUER", relato="curto"))
    assert r.status_code == 422
    valida("Erro", r.json())


def test_corretivo_fluxo_completo():
    e = nova("Ao salvar a alienação aparece HTTP 500 e nada é gravado.")
    assert e["rodada"] == 1 and not e["pronto"] and e["perguntas"]
    assert [s["tipo"] for s in e["segmentos"]] == ["CORRETIVO"]
    e = responde_tudo(e)
    assert e["pronto"]
    k = c.post(f"/entrevistas/{e['id']}/contrato", headers=H)
    assert k.status_code == 200
    k = valida("Contrato", k.json())
    assert k["pode_confirmar"] and not k["faltando"]
    assert k["requisitos"][0]["tipo"] == "CORRETIVO"


def test_misto_gera_dois_segmentos_e_dois_tipos():
    e = nova("O relatório de pagamentos dá erro ao gerar e também quero incluir a coluna de multa.")
    assert [s["tipo"] for s in e["segmentos"]] == ["CORRETIVO", "EVOLUTIVO_EXISTENTE"]
    for _ in range(2):
        if e["pronto"]:
            break
        e = responde_tudo(e)
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    assert {r["tipo"] for r in k["requisitos"]} == {"CORRETIVO", "EVOLUTIVO"}


def test_no_maximo_duas_rodadas():
    e = nova("Preciso de um módulo novo para controlar as vistorias.", intencao="ALGO_NOVO")
    e = c.post(f"/entrevistas/{e['id']}/respostas", headers=H, json={"respostas": [{"id": e["perguntas"][0]["id"], "texto": "x"}]}).json()
    assert e["rodada"] == 2 and not e["pronto"]
    e = c.post(f"/entrevistas/{e['id']}/respostas", headers=H, json={"respostas": [{"id": e["perguntas"][0]["id"], "texto": "x"}]}).json()
    valida("EstadoEntrevista", e)
    assert e["rodada"] == 3 and e["complemento"] and not e["pronto"]       # 1.3.0: faltando item, vira complemento


def test_slot_raso_bloqueia_contrato():
    e = nova("Quero mudar o cálculo do desconto da venda direta.", intencao="MUDAR_EXISTENTE")
    assert e["candidatos_funcionalidade"]
    e = responde_tudo(e, texto="ok")           # respostas rasas
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    assert not k["pode_confirmar"] and k["faltando"]


def test_gatilho_e_regra_documentada():
    e = nova("O sistema não permite gerar o boleto e o Benner não recebe o pagamento.")
    assert not any(p["slot"] == "gatilho" for p in e["perguntas"])          # 1.5.3: outro sistema não se pergunta
    e = nova("O sistema não permite gerar o arquivo do boleto.")
    assert any(p["slot"] == "gatilho" for p in e["perguntas"])
    assert e["regra_documentada"] is not None


def test_regra_documentada_decisao_do_usuario():
    e = nova("O sistema não permite gerar o boleto do contrato 123.")
    assert e["regra_documentada"] and e["decisao_regra"] is None and e["segmentos"][0]["tipo"] == "CORRETIVO"
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    assert not k["pode_confirmar"] and "decisao_regra" in [f["slot"] for f in k["faltando"]]
    r = c.post(f"/entrevistas/{e['id']}/regra", headers=H, json={"aceita": True, "comentario": "liberar quando houver acordo"})
    e2 = valida("EstadoEntrevista", r.json())
    assert e2["decisao_regra"] == "ACEITA" and e2["segmentos"][0]["tipo"] == "EVOLUTIVO_EXISTENTE" and e2["perguntas"]
    r = c.post(f"/entrevistas/{e['id']}/regra", headers=H, json={"aceita": False})
    assert r.status_code == 409                                   # já decidida
    e3 = nova("O sistema não permite gerar o boleto do contrato 456.")
    e3 = c.post(f"/entrevistas/{e3['id']}/regra", headers=H, json={"aceita": False}).json()
    assert e3["decisao_regra"] == "RECUSADA" and e3["segmentos"][0]["tipo"] == "CORRETIVO"
    sem = nova("Ao salvar aparece erro 500.")
    assert c.post(f"/entrevistas/{sem['id']}/regra", headers=H, json={"aceita": True}).status_code == 409


def test_entrevista_inexistente():
    r = c.post("/entrevistas/nao-existe/contrato", headers=H)
    assert r.status_code == 404
    valida("Erro", r.json())


# ---------------------------------------------------------------- complemento (1.3.0)
def responde(e, textos):
    """textos: str para todas as perguntas, ou {slot: texto}."""
    corpo = [{"id": p["id"], "texto": textos if isinstance(textos, str) else textos[p["slot"]]} for p in e["perguntas"]]
    r = c.post(f"/entrevistas/{e['id']}/respostas", headers=H, json={"respostas": corpo})
    assert r.status_code == 200, r.text
    return valida("EstadoEntrevista", r.json())


CORRETIVO_OK = {"onde_ocorre": "Tela de alienação, botão Salvar", "como_reproduzir": "1) abrir o contrato 123; 2) clicar em Salvar",
                "desde_quando_quem": "Desde ontem, todos os usuários", "o_que_aconteceu": "Aparece HTTP 500 e nada é gravado",
                "gatilho": "só consulta"}


def test_complemento_depois_das_duas_rodadas():
    e = nova("Ao salvar a alienação aparece HTTP 500 e nada é gravado.")
    assert not e["complemento"]
    e = responde(e, "ok")
    assert e["rodada"] == 2 and not e["complemento"]
    e = responde(e, "ok")
    assert e["rodada"] == 3 and e["complemento"] and not e["pronto"]
    slots = [p["slot"] for p in e["perguntas"]]
    assert slots == ["onde_ocorre", "como_reproduzir", "desde_quando_quem"] and "gatilho" not in slots
    ids = {p["id"] for p in e["perguntas"]}
    e = responde(e, "sim")                                                   # raso de novo: mesmas perguntas, ids novos
    assert e["complemento"] and [p["slot"] for p in e["perguntas"]] == slots and not ids & {p["id"] for p in e["perguntas"]}
    e = responde(e, CORRETIVO_OK)
    assert e["pronto"] and not e["complemento"] and not e["perguntas"] and e["rodada"] == 3
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    assert k["pode_confirmar"] and not k["faltando"]
    assert confirma(k["id"], escopo_de(k)).status_code == 200


def test_complemento_nunca_em_entrevista_completa():
    e = nova("Ao salvar a alienação aparece HTTP 500 e nada é gravado.")
    e = responde(e, CORRETIVO_OK)
    assert e["pronto"] and not e["complemento"]
    assert not valida("EstadoEntrevista", c.get(f"/entrevistas/{e['id']}", headers=H).json())["complemento"]


def test_complemento_nao_pergunta_sobre_a_regra():
    e = nova("O sistema não permite gerar o boleto do contrato 123.")
    assert e["regra_documentada"] and e["decisao_regra"] is None
    e = responde(responde(e, "ok"), "ok")
    assert e["complemento"] and "decisao_regra" not in [p["slot"] for p in e["perguntas"]]
    e = responde(e, CORRETIVO_OK)
    assert e["pronto"] and not e["complemento"]
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    assert [f["slot"] for f in k["faltando"]] == ["decisao_regra"]           # a regra continua pendente, tratada à parte


# ---------------------------------------------------------------- texto do chamado (1.3.1)
def test_criterio_fixo_e_informacoes_do_pedido_no_texto():
    e = responde(nova("Ao salvar a alienação aparece HTTP 500 e nada é gravado."), CORRETIVO_OK)
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    crit = k["requisitos"][0]["criterio_aceite"]
    assert crit == "Ao repetir os passos informados, a operação é concluída sem o erro relatado."
    assert CORRETIVO_OK["como_reproduzir"] not in crit
    g = valida("EscopoConfirmado", confirma(k["id"], escopo_de(k)).json())["grupos"][0]
    t = g["texto"]
    assert "INFORMAÇÕES DO PEDIDO\nOnde ocorre (sistema, tela ou funcionalidade): Tela de alienação, botão Salvar\n" in t
    assert "Como reproduzir (passo a passo): 1) abrir o contrato 123; 2) clicar em Salvar" in t
    assert t.index("INFORMAÇÕES DO PEDIDO") < t.index("PROBLEMAS A CORRIGIR")

    e = nova("Quero mudar o cálculo do desconto da venda direta.", intencao="MUDAR_EXISTENTE")
    e = responde(e, {"funcionalidade": "Venda direta, cálculo do desconto", "exemplo_concreto": "Contrato 9: desconto de 10% e não 5%",
                     "o_que_muda": "desconto passa a ser de 10%"})
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    assert k["requisitos"][0]["criterio_aceite"] == "Conferir com o exemplo: Contrato 9: desconto de 10% e não 5%"


def test_fora_de_escopo_e_outros_sistemas_ficam_para_a_sustentacao_no_mock():
    """1.5.3: sem as duas perguntas; o sistema citado vira dúvida; fora_escopo e integracoes vazios."""
    e = nova("Quero mudar o cálculo do desconto que vem do Benner.", intencao="MUDAR_EXISTENTE")
    slots = {p["slot"] for p in e["perguntas"]} | {x["codigo"] for s in e["segmentos"] for x in s["slots"]}
    assert not {"fora_de_escopo", "outros_sistemas", "gatilho"} & slots
    e = responde(e, {"funcionalidade": "Venda direta, cálculo do desconto", "exemplo_concreto": "Contrato 9: desconto de 10% e não 5%",
                     "o_que_muda": "desconto passa a ser de 10%"})
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    assert k["pode_confirmar"] and k["fora_escopo"] == [] and k["integracoes"] == []
    assert any(d.startswith("Para a Sustentação") and "Benner" in d for d in k["duvidas"])


# ---------------------------------------------------------------- abertura guiada (1.4.0)
def test_nao_sei_informar_encerra_e_vira_duvida():
    e = nova("Ao salvar a alienação aparece HTTP 500 e nada é gravado.")
    op = {p["slot"]: p["opcoes"] for p in e["perguntas"]}
    assert op["desde_quando_quem"] == ["Não sei informar"] and op["como_reproduzir"] == ["Não sei informar"]
    assert op["onde_ocorre"] == []                                               # bloqueante
    e = responde(e, {"onde_ocorre": "Tela de alienação, botão Salvar", "como_reproduzir": "Não sei informar",
                     "desde_quando_quem": "não sei"})
    assert e["pronto"] and not e["complemento"] and e["nota"] < 100
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    assert k["pode_confirmar"] and len([d for d in k["duvidas"] if d.startswith("Não informado:")]) == 2


def test_nao_sei_em_item_bloqueante_vai_para_o_complemento():
    e = nova("Ao salvar a alienação aparece HTTP 500 e nada é gravado.")
    e = responde(responde(e, {"onde_ocorre": "não sei", "como_reproduzir": "1) abrir; 2) salvar a alienação",
                              "desde_quando_quem": "Desde ontem, todos"}), "não sei")
    assert e["complemento"] and [p["slot"] for p in e["perguntas"]] == ["onde_ocorre"]


def test_inferido_e_candidatos_confirmados_no_mock():
    e = nova("Quero mudar o cálculo do desconto da venda direta.", intencao="MUDAR_EXISTENTE")
    assert [c["id"] for c in e["candidatos_funcionalidade"]] == [9001, 9002]
    e = responde(e, {"funcionalidade": "Venda direta, cálculo do desconto", "exemplo_concreto": "Não sei informar",
                     "o_que_muda": "desconto passa a ser de 10%"})
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    inf = [r for r in k["requisitos"] if r["origem"] == "inferido"]
    assert len(inf) == 1 and k["pode_confirmar"]
    corpo = escopo_de(k, candidatos_confirmados=[9002])
    for r, rk in zip(corpo["requisitos"], k["requisitos"]):
        r["n_contrato"] = rk["n"]
    assert confirma(k["id"], dict(corpo, candidatos_confirmados=[1])).status_code == 422
    g = valida("EscopoConfirmado", confirma(k["id"], corpo).json())
    assert [f["id"] for f in g["funcionalidades"]] == [9002]
    t = g["grupos"][0]["texto"]
    assert "FUNCIONALIDADES INDICADAS PELO USUÁRIO" in t and "(sugerido pela análise e confirmado pelo usuário)" in t


# ---------------------------------------------------------------- menus e documentação (1.5.0)
def test_menus_do_mock():
    m = valida("Menus", c.get("/sistemas/666/menus", headers=H, params=dict(sigla="GOP")).json())
    assert m["fonte"] == "wiki" and m["modulos"] == []
    ids = {x["id"] for x in m["menus"]}
    assert any(not x["folha"] for x in m["menus"]) and all(x["pai"] is None or x["pai"] in ids for x in m["menus"])
    assert c.get("/sistemas/666/menus", headers=H).status_code == 422             # sigla obrigatória
    assert c.get("/sistemas/666/menus", params=dict(sigla="GOP")).status_code == 401


def test_ponto_de_partida_e_documentos_no_mock():
    ponto = dict(caminho="Cadastro > Alienação", menu_id="m11", fonte="wiki")
    e = nova("Ao salvar a alienação aparece HTTP 500 e nada é gravado.", ponto_partida=ponto)
    onde = next(x for x in e["segmentos"][0]["slots"] if x["codigo"] == "onde_ocorre")
    assert onde["valor"] == "Menu: Cadastro > Alienação" and onde["preenchido"]
    assert "onde_ocorre" not in [p["slot"] for p in e["perguntas"]]
    e2 = nova("Quero mudar o cálculo do desconto da venda direta.", intencao="MUDAR_EXISTENTE",
              ponto_partida=dict(caminho="tela de venda direta", fonte="livre"))
    assert next(x for x in e2["segmentos"][0]["slots"] if x["codigo"] == "funcionalidade")["valor"] == "tela de venda direta"
    assert len(e["documentos"]) == 2
    pag = e["documentos"][0]["pagina"]
    d = valida("Documentacao", c.get("/documentacao", headers=H, params=dict(pagina=pag, entrevista=e["id"])).json())
    assert d["pagina"] == pag and d["secoes"]
    h = "".join(s["html"] for s in d["secoes"])                                  # 1.5.4: pronto para mostrar
    assert "<strong>P1.</strong>" in h and "<th>Nome</th>" in h and "&lt;obrigatório&gt;" in h and "Voltar" not in h
    r = c.get("/documentacao", headers=H, params=dict(pagina="sistemas:outro:x", entrevista=e["id"]))
    assert r.status_code == 404 and valida("Erro", r.json())
    assert c.get("/documentacao", headers=H, params=dict(pagina=pag, entrevista="nao-existe")).status_code == 404
    r = c.post("/entrevistas", headers=H, json=dict(cd_sistema=1, sigla="GAI", intencao="ALGO_NOVO", relato="preciso de um cadastro novo",
                                                    ponto_partida=dict(caminho="x" * 301, fonte="livre")))
    assert r.status_code == 422


def test_varios_pontos_no_mock():
    pts = [dict(caminho="Cadastro > Alienação", menu_id="m11", fonte="wiki"), dict(caminho="Relatório > Extrato da Alienação",
                                                                                   menu_id="m21", fonte="wiki")]
    e = nova("Quero mostrar a data do edital na alienação e no extrato.", intencao="MUDAR_EXISTENTE", pontos_partida=pts)
    func = next(x for x in e["segmentos"][0]["slots"] if x["codigo"] == "funcionalidade")
    assert func["valor"] == "Menu: Cadastro > Alienação; Menu: Relatório > Extrato da Alienação" and func["preenchido"]
    pq = [p for p in e["perguntas"] if p["slot"] == "o_que_muda"]
    assert [p["ponto"] for p in pq] == [1, 2] and pq[0]["opcoes"][-1] == "Não sei informar" and len(pq[0]["opcoes"]) == 7
    assert [d["ponto"] for d in e["documentos"]] == [1, 2]
    e2 = valida("EstadoEntrevista", c.post(f"/entrevistas/{e['id']}/respostas", headers=H, json={"respostas": [
        {"id": pq[0]["id"], "texto": "Mostrar a data do edital"}, {"id": pq[1]["id"], "texto": "Incluir a coluna da data"}]}).json())
    muda = next(x for x in e2["segmentos"][0]["slots"] if x["codigo"] == "o_que_muda")
    assert muda["valor"] == "[Cadastro > Alienação] Mostrar a data do edital; [Relatório > Extrato da Alienação] Incluir a coluna da data"
    r = c.post("/entrevistas", headers=H, json=dict(cd_sistema=1, sigla="GAI", intencao="MUDAR_EXISTENTE", relato="relato longo o bastante",
                                                    ponto_partida=pts[0], pontos_partida=pts))
    assert r.status_code == 422                                   # os dois formatos juntos


def test_documentos_lidos_no_mock():
    k = pronta("Ao salvar a alienação aparece HTTP 500 e nada é gravado.")
    st = c.get(f"/entrevistas/{k['id']}", headers=H).json()
    pag = st["documentos"][1]["pagina"]
    assert confirma(k["id"], escopo_de(k, documentos_lidos=["sistemas:gai:nao_mostrada"])).status_code == 422
    assert confirma(k["id"], escopo_de(k, documentos_lidos=[pag])).status_code == 200


# ---------------------------------------------------------------- confirmação (1.2.0)
def pronta(relato, intencao="PAROU_DE_FUNCIONAR"):
    """Entrevista respondida até o fim e com o contrato gerado e confirmável."""
    e = nova(relato, intencao)
    for _ in range(2):
        if e["pronto"]:
            break
        e = responde_tudo(e)
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    assert k["pode_confirmar"], k["faltando"]
    return k


def escopo_de(k, **kw):
    corpo = dict(titulo=k["titulo"], objetivo=k["objetivo"], fora_escopo=["Relatórios"], duvidas=[],
                 integracoes=[dict(sistema="Benner", sentido="BUSCA", descricao="consulta o pagamento")], cd_usuario=321,
                 requisitos=[dict(segmento=r["segmento"], descricao=r["descricao"], criterio_aceite=r["criterio_aceite"])
                             for r in k["requisitos"]])
    corpo.update(kw)
    return valida("EscopoEditado", corpo)


def confirma(eid, escopo):
    return c.post(f"/entrevistas/{eid}/confirmacao", headers=H, json=escopo)


def vincula(eid, chamados, cd_pacote=None):
    return c.post(f"/entrevistas/{eid}/confirmacao/chamados", headers=H,
                  json=dict(chamados=[dict(tipo=t, cd_solicitacao=cd) for t, cd in chamados], cd_pacote=cd_pacote))


def test_confirmacao_misto_gera_dois_chamados_e_vinculo_com_pacote():
    k = pronta("O relatório de pagamentos dá erro ao gerar e também quero incluir a coluna de multa.")
    r = confirma(k["id"], escopo_de(k))
    assert r.status_code == 200, r.text
    e = valida("EscopoConfirmado", r.json())
    assert e["situacao"] == "PENDENTE" and [g["tipo"] for g in e["grupos"]] == ["CORRETIVO", "EVOLUTIVO"]
    assert e["grupos"][0]["titulo"].endswith(" (correção)") and e["grupos"][1]["titulo"].endswith(" (evolução)")
    assert e["grupos"][1]["requisitos"][0]["natureza"] == "ALTERACAO"
    assert "chamado evolutivo vinculado" in e["grupos"][0]["texto"] and "INTEGRAÇÕES" in e["grupos"][0]["texto"]

    assert vincula(k["id"], [("CORRETIVO", 91001), ("EVOLUTIVO", 91002)]).status_code == 422          # 2 chamados sem pacote
    assert vincula(k["id"], [("CORRETIVO", 91001)], 7).status_code == 409                              # falta o grupo evolutivo
    assert vincula(k["id"], [("CORRETIVO", 91001), ("CORRETIVO", 91002)], 7).status_code == 422        # tipo repetido
    r1 = vincula(k["id"], [("EVOLUTIVO", 91002), ("CORRETIVO", 91001)], 7)
    assert r1.status_code == 200, r1.text
    conf = valida("Confirmacao", r1.json())
    assert conf["situacao"] == "CONFIRMADA" and conf["cd_pacote"] == 7
    r2 = vincula(k["id"], [("CORRETIVO", 91001), ("EVOLUTIVO", 91002)], 7)                            # repetição idêntica
    assert r2.status_code == 200 and r2.json() == r1.json()
    assert vincula(k["id"], [("CORRETIVO", 91003), ("EVOLUTIVO", 91002)], 7).status_code == 409        # outros números

    # depois de confirmada: nada muda a entrevista
    assert confirma(k["id"], escopo_de(k)).status_code == 409
    assert c.post(f"/entrevistas/{k['id']}/respostas", headers=H, json={"respostas": [{"id": "x", "texto": "y"}]}).status_code == 409
    k2 = c.post(f"/entrevistas/{k['id']}/contrato", headers=H)
    assert k2.status_code == 200 and k2.json()["titulo"] == k["titulo"]

    s = c.get("/solicitacoes/91002/escopo", headers=H)
    assert s.status_code == 200, s.text
    s = valida("EscopoChamado", s.json())
    assert s["tipo"] == "EVOLUTIVO" and s["cd_pacote"] == 7 and [v["versao"] for v in s["versoes"]] == [0, 1]
    assert s["versoes"][1]["origem"] == 1 and s["versoes"][1]["cd_usuario"] == 321
    assert c.get("/solicitacoes/123456789/escopo", headers=H).status_code == 404

    # número reaproveitado (Oracle de teste restaurado): o vínculo antigo sai e o novo entra
    k3 = pronta("Ao salvar a alienação aparece HTTP 500 e nada é gravado.")
    assert confirma(k3["id"], escopo_de(k3)).status_code == 200
    assert vincula(k3["id"], [("CORRETIVO", 91001)]).status_code == 200
    assert c.get("/solicitacoes/91001/escopo", headers=H).json()["entrevista_id"] == k3["id"]


def test_confirmacao_um_grupo_sem_sufixo_e_validacoes():
    e = nova("Ao gerar a guia aparece HTTP 500 e nada é gravado.")
    assert confirma(e["id"], dict(titulo="t", objetivo="o", requisitos=[dict(segmento=1, descricao="d")])).status_code == 409  # sem contrato
    assert vincula(e["id"], [("CORRETIVO", 92001)]).status_code == 409                            # sem confirmação
    k = pronta("Ao emitir o boleto aparece HTTP 500 e nada é gravado.")
    assert confirma(k["id"], escopo_de(k, requisitos=[dict(segmento=9, descricao="x")])).status_code == 422
    e = valida("EscopoConfirmado", confirma(k["id"], escopo_de(k)).json())
    assert len(e["grupos"]) == 1 and e["grupos"][0]["titulo"] == k["titulo"]
    assert "vinculado" not in e["grupos"][0]["texto"]
    assert confirma(k["id"], escopo_de(k, titulo="Novo título")).json()["grupos"][0]["titulo"] == "Novo título"   # repetível em PENDENTE
    assert vincula(k["id"], [("CORRETIVO", 92002)]).status_code == 200


def test_confirmacao_bloqueada_com_faltando():
    e = nova("Quero mudar o cálculo do desconto da venda direta.", intencao="MUDAR_EXISTENTE")
    e = responde_tudo(e, texto="ok")
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    assert k["faltando"]
    r = confirma(k["id"], escopo_de(k))
    assert r.status_code == 409 and "Falta" in valida("Erro", r.json())["detalhe"]


def test_confirmacoes_pendentes():
    k = pronta("Ao imprimir o termo aparece HTTP 500 e nada é gravado.")
    confirma(k["id"], escopo_de(k))
    r = c.get("/confirmacoes/pendentes?horas=0", headers=H)
    assert r.status_code == 200
    itens = [valida("EntrevistaPendente", x) for x in r.json()]
    assert any(x["id"] == k["id"] and x["grupos"] == ["CORRETIVO"] for x in itens)
    assert all(x["id"] != k["id"] for x in c.get("/confirmacoes/pendentes?horas=1", headers=H).json())
    assert c.get("/confirmacoes/pendentes?horas=721", headers=H).status_code == 422


def test_schemas_gerados_atualizados():
    """Os arquivos em contrato_api/ têm de bater com os modelos (rodar gera_schemas.py após mudar o contrato)."""
    aqui = os.path.dirname(gera_schemas.__file__)
    for nome, sch in SCHEMAS.items():
        arq = os.path.join(aqui, f"{nome}.schema.json")
        assert os.path.exists(arq), f"falta {arq}: rode contrato_api/gera_schemas.py"
        salvo = json.load(open(arq, encoding="utf-8"))
        for k in ("$schema", "$id", "x-versao-contrato"):
            salvo.pop(k, None)
        assert salvo == json.loads(json.dumps(sch)), f"{nome}.schema.json desatualizado: rode gera_schemas.py"
