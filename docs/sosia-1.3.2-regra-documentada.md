# sos-ia 1.3.2 — Regra documentada no motor real + concordância no texto (investigação e correção)

Para executar no repositório `D:\Projetos\sos-ia`. Base: contrato **1.3.1**. Origem: passo 6 do plano do SOS (motor real pelo
proxy do SOS, 06/11/2026, `SOSIA_PII_MODO=local`, sistema GAI, `cd_sistema=1`). Sem mudança de contrato prevista.

## 1. Regra documentada não aparece no motor real

### Evidência
Em 4 entrevistas `PAROU_DE_FUNCIONAR` com relato de bloqueio, `regra_documentada` veio `null` em todas (o mock, os testes com LLM
falso e a tela do SOS funcionam):

| Relato (resumo) | Regra esperada na wiki do GAI? |
|---|---|
| "O sistema não deixa alterar o contrato de alienação depois que a primeira parcela foi paga…" (×2) | Não há RN sobre isso — `null` está certo |
| "Não consigo emitir o boleto bancário da alienação do prestamista 2023/0110. O sistema bloqueia a emissão do boleto e não gera nada." (×2) | **Sim: RN13 — Bloqueio de emissão de Boleto Bancário para Alienação de Prestamista inscrito no SERASA** (`sistemas:gai:documentacao_tecnica:regras_de_negocio:inicio`) |

Nas duas entrevistas do boleto, os candidatos de funcionalidade vieram certos ("BOLETO POR ALIENACAO EMITIR"), mas nenhuma
regra foi apontada. As entrevistas estão no `sosia_app` como rascunho (descartadas sem confirmar):
```sql
SELECT id, criado_em, estado->>'texto' AS relato
  FROM app.entrevista
 WHERE estado->>'texto' ILIKE '%boleto%' AND criado_em::date = DATE '2026-11-06'
 ORDER BY criado_em;
```

### O que investigar (nesta ordem)
1. **O RAG trouxe a RN13?** Em `estado->'docs'` das duas entrevistas: a RN13 está entre os trechos? Com que `tipo` e `cos`?
   Hipótese principal: o relato do usuário fala do **efeito** ("bloqueia a emissão do boleto") e a regra fala da **causa**
   ("prestamista inscrito no SERASA"); a busca de `rag()` para regras/mensagens no corretivo pega só as 3 mais próximas por
   vetor (`LIMIT 3`) e pode estar deixando a RN13 de fora.
2. **Se trouxe, o LLM indicou?** No evento `llm_entrevista` (`app.entrevista_evento.conteudo`): `regra_documentada_trecho` e
   `regra_documentada_orientacao`. Se o trecho veio 0, o prompt (`prompts/entrevista_v1.md`) está conservador demais.
3. **Se indicou, a regra fixa descartou?** `aplica()` só aceita se o trecho é do RAG, de tipo `regras_de_negocio` /
   `mensagens` / `caso_de_uso`, na intenção `PAROU_DE_FUNCIONAR` e com orientação não vazia. Conferir qual condição falhou.

### Correção esperada
Depende do que a investigação mostrar; possibilidades:
- **Busca:** para o corretivo, buscar regras/mensagens também com a palavra-chave (BM25) além do vetor, e/ou aumentar o `LIMIT`
  só para esse tipo (o prompt já recebe os trechos numerados; manter o total de trechos razoável).
- **Prompt:** deixar explícito que a regra pode descrever a **causa** de um efeito relatado (bloqueio, mensagem, botão
  desabilitado) e que, nesse caso, deve ser apontada com uma orientação que explique a ligação.
- Manter o que já é determinístico: só trecho vindo do RAG, só no corretivo, só uma vez, decisão sempre do usuário.

### Como medir
Montar um gabarito pequeno (10 a 20 relatos de "bloqueio/mensagem" do GAI, metade com regra na wiki e metade sem) a partir de
chamados reais anonimizados, e medir: quantas regras certas aparecem (sensibilidade) e quantas aparecem sem existir (falso
alarme). Falso alarme é pior que omissão — atrapalha o usuário com um corretivo legítimo —, então preferir precisão.
Registrar o resultado em `dados/` como os outros experimentos.

## 2. Concordância no texto do chamado
`confirmacao.texto()` gera "Os requisitos **2** deste pedido estão no chamado evolutivo vinculado" quando é um requisito só.
Corrigir para singular/plural:
- 1 requisito: "O requisito 2 deste pedido está no chamado evolutivo vinculado (mesmo pacote)."
- 2+: "Os requisitos 2 e 3 deste pedido estão…" (último separado por "e").

## Testes
- `tests/test_confirmacao.py`: singular e plural da frase do outro chamado.
- `tests/test_motor.py` (LLM falso): relato de efeito + trecho de regra de causa no RAG falso → regra apontada quando o LLM
  indica; descartada quando o trecho não é do RAG (já existe; manter).
- Teste com o banco real (pula sem banco), como `test_rag_real_…`: o relato do boleto acima traz a RN13 entre os trechos de
  regras passados ao prompt.

## Pronto quando
- O relato do boleto (2023/0110) devolve `regra_documentada` apontando a RN13, com orientação que explique a ligação com o
  SERASA; o relato da "parcela paga" continua sem regra.
- Gabarito medido e registrado.
- `PASSAGEM_SOS.md` com uma linha sobre a 1.3.2. O SOS não muda nada: a tela já trata a regra documentada (aceita/recusa).
