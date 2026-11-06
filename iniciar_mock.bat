@echo off
rem Sobe o servico sos-ia em modo MOCK (sem LLM e sem banco) na porta 8010. Token: mock
cd /d "%~dp0"
set SOSIA_MOCK=1
.venv\Scripts\python.exe -m uvicorn servico.app:app --host 127.0.0.1 --port 8010
