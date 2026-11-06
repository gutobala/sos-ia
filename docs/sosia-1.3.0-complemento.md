# sos-ia 1.3.0 — Rodada de complemento (plano de execução)

Para executar no repositório `D:\Projetos\sos-ia`. Base: contrato **1.2.0** (commit `8bf2253`). Mudança **aditiva**.
Plano geral do SOS: `C:\inetpub\wwwroot\CHAMADOS\docs\plano-abertura-ia-fase1.md`.

## Problema
Depois da 2ª rodada, a entrevista se encerra mesmo com item obrigatório faltando:
- `nucleo/entrevista/motor.py`, `responder()`: `if est["rodada"] > MAX_RODADAS or (...): est["perguntas"] = []` → `pronto=true`;
- `nucleo/entrevista/mock.py`, `_resposta()`: `pronto = st["rodada"] > MAX_RODADAS or not roteiros.faltando(...)`.

O `POST /contrato` então volta com `pode_confirmar=false`, o `POST /confirmacao` recusa (409), e o `POST /respostas` não aceita
mais nada (`if not est["perguntas"]: return ...`). O usuário fica sem saída: a tela do SOS só consegue oferecer "Recomeçar", e
tudo o que ele já respondeu se perde. Basta uma resposta rasa ("ok", "sim") num item obrigatório para cair nisso.

## Solução: rodada de complemento, sem LLM
Depois da 2ª rodada, **se ainda faltar item obrigatório**, o serviço não encerra: devolve só as perguntas fixas do roteiro para
os itens que faltam, marcadas como complemento. Repete até tudo estar preenchido. Depois, `pronto=true` e o fluxo segue igual.

- Perguntas: `PERGUNTA_SLOT` (já existe em `mock.py`, usada também pelo motor), uma por item faltando, no máximo 5 por vez
  (`MAX_PERGUNTAS`), na ordem de `roteiros.faltando(segs)`.
- Respostas: o mesmo `POST /entrevistas/{id}/respostas`, o mesmo tratamento de hoje (anonimização no modo `local`, junção com o
  valor anterior, `roteiros.revalida`). "ok", "sim", "não sei" continuam não contando.
- **Sem LLM**: nem para perguntar nem para preencher. Custo zero, resposta imediata, funciona com o LLM fora do ar.
- **Sem limite de rodadas de complemento**: o usuário sai quando responder de verdade ou quando usar "Recomeçar" no SOS. Cada
  rodada registra o evento `complemento` (ids das perguntas e tamanho das respostas, como o evento `respostas`).

## Contrato 1.3.0
`EstadoEntrevista` ganha um campo:
```python
complemento: bool = Field(default=False, description="true: as perguntas são só dos itens obrigatórios que ainda faltam, "
                                                     "depois das 2 rodadas (roteiro fixo, sem IA)")
```
Nada mais muda: mesmas rotas, mesmos corpos. `rodada` continua no máximo 3 (fica em 3 durante o complemento).
`VERSAO_CONTRATO = "1.3.0"`; regenerar `contrato_api/` com `gera_schemas.py`.

## Regras
1. Entra no complemento só quando `rodada > MAX_RODADAS` **e** `roteiros.faltando(segs)` não está vazio.
   Usar `roteiros.faltando` (só itens do roteiro), **não** `faltando_com_regra`: a decisão sobre a regra documentada não é
   pergunta, a tela do SOS trata à parte (painel da regra + `POST /regra`).
2. Pergunta de gatilho (`slot="gatilho"`) nunca entra no complemento.
3. Ids das perguntas únicos entre rodadas de complemento: por exemplo `c{n}p{i}`, com `n` = contador de complementos no estado
   (`est["complementos"]`). Hoje o id é `r{rodada}p{i}` e a rodada fica parada em 3.
4. `est["contrato"] = None` a cada resposta (já é feito em `responder()`): o Escopo do Pedido é gerado de novo com o que entrou.
5. `nota` e `dicas_medidor` recalculados por `nota(segs, est["nota"])` e pela lista do que falta, como hoje no fechamento.
6. `analise_ia` não muda por causa do complemento (ele não é falha do LLM).
7. Situação da entrevista: `EM_ANDAMENTO` enquanto houver perguntas de complemento; `PRONTA` quando acabar.
8. `CONFIRMADA`: `POST /respostas` continua 409 (regra da 1.2.0).
9. Regra documentada aceita depois da 2ª rodada: o comportamento atual (`decidir_regra` volta a rodada para 2 e chama o LLM)
   continua; se depois dessa rodada ainda faltar item, aí sim entra o complemento.
10. Mock: mesmo comportamento (o mock já usa `PERGUNTA_SLOT`).

## Arquivos
| Arquivo | Mudança |
|---|---|
| `nucleo/entrevista/modelos.py` | `EstadoEntrevista.complemento`; `VERSAO_CONTRATO = "1.3.0"` + comentário da versão |
| `nucleo/entrevista/roteiros.py` | função pura `perguntas_complemento(segs, n)` → lista de `dict(id, segmento, slot, texto, dica)` a partir de `faltando` + `PERGUNTA_SLOT` (mover `PERGUNTA_SLOT` para cá, ou importar de `mock.py` sem ciclo) |
| `nucleo/entrevista/motor.py` | `responder()`: no fechamento, se faltar item, gerar o complemento em vez de `perguntas=[]`; `_estado()` com `complemento` (`est.get("complementos_ativo")` ou equivalente); evento `complemento` |
| `nucleo/entrevista/mock.py` | `_resposta()`: mesma regra |
| `contrato_api/*` | regenerar |
| `tests/` | ver abaixo |
| `PASSAGEM_SOS.md`, `README.md` | seção "Complemento (1.3.0)"; trocar "depois `pronto=true` mesmo com slot faltando" pela regra nova |

## Testes
- `tests/test_mock.py`:
  - resposta rasa ("ok") nas 2 rodadas → 3ª resposta vem com `complemento=true`, `pronto=false`, perguntas só dos itens
    faltando, sem gatilho; responder direito → `pronto=true`, `POST /contrato` com `pode_confirmar=true`;
  - complemento respondido de novo com "ok" → continua `complemento=true` com as mesmas perguntas (ids novos);
  - entrevista completa em 1 rodada → `complemento=false` sempre;
  - regra documentada pendente + itens faltando depois da 2ª rodada → `faltando` do contrato mantém `decisao_regra`; o
    complemento não pergunta sobre a regra.
- `tests/test_motor.py` (LLM falso): o complemento **não chama o LLM** (o `LLMFalso` sem respostas na fila falharia se fosse
  chamado); evento `complemento` gravado; `analise_ia` preservado.
- `test_schemas_gerados_atualizados` depois de `gera_schemas.py`.

## Pronto quando
- Testes verdes (fora os 2 que dependem da chave de embeddings).
- Com o mock: relato curto, "ok" em todas as perguntas das 2 rodadas → o serviço pede só o que falta; respondendo, o Escopo do
  Pedido sai com `pode_confirmar=true` e o `POST /confirmacao` aceita.
- `PASSAGEM_SOS.md` atualizado (a sessão do SOS ajusta a tela a partir dele).

## O que muda no SOS depois (para referência; feito na sessão do SOS)
`novaAbertura.asp`: com `complemento=true`, o título das perguntas vira "Para concluir, faltam estas informações" (em vez de
"rodada X de 2") e o aviso do Escopo do Pedido sobre item faltando deixa de mandar recomeçar. Nenhuma mudança no proxy.
