"""Testes do contrato da API com o motor mock (sem rede, sem LLM, sem banco)."""
import json
import os

os.environ["SOSIA_MOCK"] = "1"
os.environ.pop("SOSIA_TOKEN", None)

import jsonschema  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from contrato_api import gera_schemas  # noqa: E402
from servico.app import app  # noqa: E402

H = {"X-SOSIA-Token": "mock"}
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
    assert e["pronto"] and not e["perguntas"]


def test_slot_raso_bloqueia_contrato():
    e = nova("Quero mudar o cálculo do desconto da venda direta.", intencao="MUDAR_EXISTENTE")
    assert e["candidatos_funcionalidade"]
    e = responde_tudo(e, texto="ok")           # respostas rasas
    k = valida("Contrato", c.post(f"/entrevistas/{e['id']}/contrato", headers=H).json())
    assert not k["pode_confirmar"] and k["faltando"]


def test_gatilho_e_regra_documentada():
    e = nova("O sistema não permite gerar o boleto e o Benner não recebe o pagamento.")
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
