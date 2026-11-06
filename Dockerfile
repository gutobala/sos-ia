# API do sos-ia. Imagem enxuta: sem modelos locais (LLM e embeddings 3-large pela Azure/OpenAI). O e5 só entra na Fase 3.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY nucleo ./nucleo
COPY servico ./servico
COPY contrato_api ./contrato_api
COPY prompts ./prompts
COPY config ./config
COPY migracao/sql ./migracao/sql

RUN useradd --create-home --uid 10001 sosia
USER sosia

EXPOSE 8010
CMD ["python", "-m", "uvicorn", "servico.app:app", "--host", "0.0.0.0", "--port", "8010"]
