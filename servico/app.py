"""Serviço sos-ia (FastAPI). Fase 1: entrevista de abertura do SOS.

Iniciar (na raiz do projeto):  .venv\\Scripts\\python -m uvicorn servico.app:app --host 127.0.0.1 --port 8010
Modo mock (sem LLM e sem banco):  set SOSIA_MOCK=1  (token padrão "mock")
Todas as rotas, menos /saude, exigem o cabeçalho X-SOSIA-Token.
"""
import hmac

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import logging

from nucleo import config, db, llm
from nucleo.entrevista.confirmacao import ConfirmacaoInvalida, EscopoInvalido
from nucleo.entrevista.modelos import (Confirmacao, Contrato, DecisaoRegraDocumentada, EntrevistaPendente, Erro, EscopoChamado,
                                       EscopoConfirmado, EscopoEditado, EstadoEntrevista, NovaEntrevista, Respostas, Saude,
                                       VinculoChamados)

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


@app.exception_handler(ConfirmacaoInvalida)
async def _erro_confirmacao(request: Request, exc: ConfirmacaoInvalida):
    return JSONUtf8(status_code=409, content=Erro(erro="409", detalhe=str(exc)).model_dump())


@app.exception_handler(EscopoInvalido)
async def _erro_escopo(request: Request, exc: EscopoInvalido):
    return JSONUtf8(status_code=422, content=Erro(erro="422", detalhe=str(exc)).model_dump())


@app.exception_handler(Exception)
async def _erro_interno(request: Request, exc: Exception):
    logging.getLogger("sosia").exception("erro não tratado em %s", request.url.path)
    return JSONUtf8(status_code=500, content=Erro(erro="500", detalhe=f"{type(exc).__name__}").model_dump())


ERROS = {401: {"model": Erro}, 404: {"model": Erro}, 409: {"model": Erro}, 422: {"model": Erro}, 503: {"model": Erro}}


@app.get("/saude", response_model=Saude)
def saude():
    """ok=false → o SOS usa o roteiro fixo (fallback). llm=false com ok=true → o motor responde com analise_ia=false."""
    banco = config.MOCK or db.ping()
    banco_app = config.MOCK or db.ping_app()
    return Saude(ok=motor is not None and banco and banco_app, versao=config.VERSAO, mock=config.MOCK,
                 llm=(not config.MOCK) and llm.disponivel(), banco=banco, banco_app=banco_app, embeddings=config.EMB,
                 modo_pii=config.MODO_PII)


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
    """Gera (ou devolve o já gerado) Escopo do Pedido. Numa entrevista PENDENTE/CONFIRMADA não muda a situação."""
    try:
        return _motor().contrato(eid)
    except KeyError:
        raise HTTPException(404, "entrevista não encontrada")


@app.post("/entrevistas/{eid}/confirmacao", response_model=EscopoConfirmado, responses=ERROS, dependencies=[Depends(exige_token)])
def confirmacao(eid: str, escopo: EscopoEditado):
    """Passo 1 da confirmação: o Escopo do Pedido editado vira 1 ou 2 grupos (um chamado por tipo). A entrevista fica PENDENTE.
    409: já confirmada, contrato ainda não gerado ou `faltando` não vazio. 422: requisito com segmento inexistente."""
    try:
        return _motor().confirmacao(eid, escopo)
    except KeyError:
        raise HTTPException(404, "entrevista não encontrada")


@app.post("/entrevistas/{eid}/confirmacao/chamados", response_model=Confirmacao, responses=ERROS, dependencies=[Depends(exige_token)])
def vincular_chamados(eid: str, vinculo: VinculoChamados):
    """Passo 3: o SOS informa os CD_SOLICITACAO gravados no Oracle. Idempotente (repetição idêntica → 200 com o mesmo corpo).
    409: sem confirmação, tipos diferentes dos grupos ou outros números numa entrevista já confirmada. Número já ligado a OUTRA
    entrevista é tratado como reaproveitado (Oracle restaurado): o vínculo antigo vai para o histórico e o novo é aceito.
    422: tipo repetido ou 2 chamados sem cd_pacote."""
    try:
        return _motor().vincular(eid, vinculo)
    except KeyError:
        raise HTTPException(404, "entrevista não encontrada")


@app.get("/solicitacoes/{cd}/escopo", response_model=EscopoChamado, responses=ERROS, dependencies=[Depends(exige_token)])
def escopo_do_chamado(cd: int):
    """Versões do Escopo do Pedido de um chamado aberto pelo fluxo novo (0 = IA, 1 = usuário). 404 se o chamado não veio dele."""
    try:
        return _motor().escopo_do_chamado(cd)
    except KeyError:
        raise HTTPException(404, "chamado sem Escopo do Pedido (não veio da abertura com IA)")


@app.get("/confirmacoes/pendentes", response_model=list[EntrevistaPendente], responses=ERROS, dependencies=[Depends(exige_token)])
def confirmacoes_pendentes(horas: int = Query(default=1, ge=0, le=720)):
    """Entrevistas PENDENTE há mais de `horas`: o SOS pode ter gravado o chamado sem conseguir chamar /confirmacao/chamados."""
    return _motor().pendentes(horas)
