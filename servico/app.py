"""Serviço sos-ia (FastAPI). Fase 1: entrevista de abertura do SOS.

Iniciar (na raiz do projeto):  .venv\\Scripts\\python -m uvicorn servico.app:app --host 127.0.0.1 --port 8010
Modo mock (sem LLM e sem banco):  set SOSIA_MOCK=1  (token padrão "mock")
Todas as rotas, menos /saude, exigem o cabeçalho X-SOSIA-Token.
"""
import hmac

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import logging

from nucleo import config, db, llm
from nucleo.entrevista.modelos import Contrato, DecisaoRegraDocumentada, Erro, EstadoEntrevista, NovaEntrevista, Respostas, Saude

class JSONUtf8(JSONResponse):
    """O proxy ASP (MSXML2.ServerXMLHTTP) decide a codificação pelo cabeçalho: charset explícito."""
    media_type = "application/json; charset=utf-8"


app = FastAPI(title="sos-ia", version=config.VERSAO, default_response_class=JSONUtf8,
              description="Entrevista de abertura de chamados do SOS (Fase 1). Contrato em contrato_api/.")

if config.MOCK:
    from nucleo.entrevista.mock import MotorMock
    motor = MotorMock()
else:
    from nucleo.entrevista.motor import Motor
    motor = Motor()


def exige_token(x_sosia_token: str = Header(default="")):
    if not config.TOKEN:
        raise HTTPException(503, "serviço sem SOSIA_TOKEN configurado")
    if not hmac.compare_digest(x_sosia_token.encode("utf-8"), config.TOKEN.encode("utf-8")):
        raise HTTPException(401, "token inválido")


def _motor():
    if motor is None:
        raise HTTPException(503, "motor da entrevista ainda não disponível (use SOSIA_MOCK=1)")
    return motor


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    return JSONUtf8(status_code=exc.status_code, content=Erro(erro=str(exc.status_code), detalhe=str(exc.detail)).model_dump())


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    det = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
    return JSONUtf8(status_code=422, content=Erro(erro="422", detalhe=det[:2000]).model_dump())


@app.exception_handler(Exception)
async def _erro_interno(request: Request, exc: Exception):
    logging.getLogger("sosia").exception("erro não tratado em %s", request.url.path)
    return JSONUtf8(status_code=500, content=Erro(erro="500", detalhe=f"{type(exc).__name__}").model_dump())


ERROS = {401: {"model": Erro}, 404: {"model": Erro}, 409: {"model": Erro}, 503: {"model": Erro}}


@app.get("/saude", response_model=Saude)
def saude():
    """ok=false → o SOS usa o roteiro fixo (fallback). llm=false com ok=true → o motor responde com analise_ia=false."""
    banco = config.MOCK or db.ping()
    return Saude(ok=motor is not None and banco, versao=config.VERSAO, mock=config.MOCK, llm=(not config.MOCK) and llm.disponivel(),
                 banco=banco, embeddings=config.EMB, modo_pii=config.MODO_PII)


@app.post("/entrevistas", response_model=EstadoEntrevista, responses=ERROS, dependencies=[Depends(exige_token)])
def nova_entrevista(req: NovaEntrevista):
    return _motor().criar(req)


@app.get("/entrevistas/{eid}", response_model=EstadoEntrevista, responses=ERROS, dependencies=[Depends(exige_token)])
def obter_entrevista(eid: str):
    try:
        return _motor().obter(eid)
    except KeyError:
        raise HTTPException(404, "entrevista não encontrada")


@app.post("/entrevistas/{eid}/respostas", response_model=EstadoEntrevista, responses=ERROS, dependencies=[Depends(exige_token)])
def responder(eid: str, resp: Respostas):
    try:
        return _motor().responder(eid, resp)
    except KeyError:
        raise HTTPException(404, "entrevista não encontrada")


@app.post("/entrevistas/{eid}/regra", response_model=EstadoEntrevista, responses=ERROS, dependencies=[Depends(exige_token)])
def decidir_regra(eid: str, dec: DecisaoRegraDocumentada):
    """O usuário decide se o comportamento segue a regra documentada (aceita → evolutivo) ou se é defeito (recusa → corretivo).
    409 se a entrevista não tem regra documentada ou já foi decidida."""
    try:
        return _motor().decidir_regra(eid, dec)
    except KeyError:
        raise HTTPException(404, "entrevista não encontrada")
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/entrevistas/{eid}/contrato", response_model=Contrato, responses=ERROS, dependencies=[Depends(exige_token)])
def contrato(eid: str):
    try:
        return _motor().contrato(eid)
    except KeyError:
        raise HTTPException(404, "entrevista não encontrada")
