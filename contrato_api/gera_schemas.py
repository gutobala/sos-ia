"""Gera os JSON Schemas do contrato da API a partir de nucleo/entrevista/modelos.py (fonte única).

Uso (na raiz):  .venv\\Scripts\\python contrato_api\\gera_schemas.py
Saída: contrato_api\\<Modelo>.schema.json e contrato_api\\openapi.json. A sessão do SOS copia estes arquivos.
"""
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
os.environ.setdefault("SOSIA_MOCK", "1")

from nucleo.entrevista import modelos as m  # noqa: E402
from servico.app import app  # noqa: E402

MODELOS = {
    "NovaEntrevista": m.NovaEntrevista,       # entrada de POST /entrevistas
    "Respostas": m.Respostas,                 # entrada de POST /entrevistas/{id}/respostas
    "DecisaoRegraDocumentada": m.DecisaoRegraDocumentada,   # entrada de POST /entrevistas/{id}/regra (1.1.0)
    "EstadoEntrevista": m.EstadoEntrevista,   # saída de POST /entrevistas, /respostas e GET /entrevistas/{id}
    "Contrato": m.Contrato,                   # saída de POST /entrevistas/{id}/contrato
    "EscopoEditado": m.EscopoEditado,         # entrada de POST /entrevistas/{id}/confirmacao (1.2.0)
    "EscopoConfirmado": m.EscopoConfirmado,   # saída de POST /entrevistas/{id}/confirmacao (1.2.0)
    "VinculoChamados": m.VinculoChamados,     # entrada de POST /entrevistas/{id}/confirmacao/chamados (1.2.0)
    "Confirmacao": m.Confirmacao,             # saída de POST /entrevistas/{id}/confirmacao/chamados (1.2.0)
    "EscopoChamado": m.EscopoChamado,         # saída de GET /solicitacoes/{cd}/escopo (1.2.0)
    "EntrevistaPendente": m.EntrevistaPendente,   # item de GET /confirmacoes/pendentes (1.2.0)
    "Saude": m.Saude,                         # saída de GET /saude
    "Erro": m.Erro,                           # corpo de qualquer erro (401, 404, 409, 422, 503)
}

if __name__ == "__main__":
    for nome, cls in MODELOS.items():
        sch = cls.model_json_schema()
        sch["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        sch["$id"] = f"sos-ia/{nome}.schema.json"
        sch["x-versao-contrato"] = m.VERSAO_CONTRATO
        with open(os.path.join(AQUI, f"{nome}.schema.json"), "w", encoding="utf-8") as f:
            json.dump(sch, f, ensure_ascii=False, indent=2)
    with open(os.path.join(AQUI, "openapi.json"), "w", encoding="utf-8") as f:
        json.dump(app.openapi(), f, ensure_ascii=False, indent=2)
    print(f"{len(MODELOS)} schemas + openapi.json em {AQUI} (contrato {m.VERSAO_CONTRATO})")
