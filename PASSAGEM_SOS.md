# sos-ia → sessão do SOS: o que mudou e o que o SOS precisa fazer (05/11/2026)

O serviço sos-ia (núcleo da abertura de chamados com IA) está pronto para a integração. O motor real da entrevista funciona de
ponta a ponta, e o modo mock continua disponível para desenvolver as telas. Contrato da API: **1.1.0**. Os JSON Schemas de cada
endpoint ficam em `contrato_api/`, com o `openapi.json`.

## 1. Como subir
- `docker compose up -d --build` na raiz do sos-ia. Na primeira vez, o banco é restaurado de `dados\sosia.dump` e demora alguns minutos.
- A API fica em `http://<servidor>:8010`.
- `SOSIA_MOCK=1` no `.env`: respostas fixas, sem LLM e sem banco, para testar as telas. `SOSIA_MOCK=0`: motor real.
- Depois de mudar o `.env`, recrie o container com `docker compose up -d api`.

## 2. Autenticação (mudou)
- Todo endpoint, exceto `/saude`, exige o cabeçalho **`X-SOSIA-Token`** com o valor de `SOSIA_TOKEN` do `.env` do sos-ia.
- O token já está definido. **O valor "mock" não é mais aceito, nem no modo mock.**
- O token fica **só no servidor**, no proxy ASP. Nunca vai para o JavaScript nem para o navegador, e não entra no git.
- Token errado: 401. Serviço sem token configurado: 503.

## 3. Endpoints
| Método e rota | Corpo | Resposta |
|---|---|---|
| `GET /saude` | — | `{ok, versao, versao_contrato, mock, llm, banco, embeddings, modo_pii}` |
| `POST /entrevistas` | `{cd_sistema, sigla, intencao, relato, imagens[]}` | `EstadoEntrevista` |
| `GET /entrevistas/{id}` | — | `EstadoEntrevista` (retomar) |
| `POST /entrevistas/{id}/respostas` | `{respostas:[{id, texto}]}` | `EstadoEntrevista` |
| `POST /entrevistas/{id}/regra` **(novo, 1.1.0)** | `{aceita: bool, comentario?: string}` | `EstadoEntrevista` |
| `POST /entrevistas/{id}/contrato` | — | `Contrato` |

Detalhes dos campos:
- `intencao`: `PAROU_DE_FUNCIONAR` | `MUDAR_EXISTENTE` | `ALGO_NOVO`.
- `relato`: de 10 a 20.000 caracteres.
- `imagens`: até 5, cada uma `{nome, mime: image/png|jpeg|gif|bmp|webp, base64}`, com o base64 sem o prefixo `data:`.

Erros, sempre no formato `{erro, detalhe}`:
| Código | Quando |
|---|---|
| 401 | token errado |
| 404 | entrevista não existe |
| 409 | `/regra` sem regra documentada, ou já decidida |
| 422 | corpo inválido |
| 500 | erro interno |
| 503 | sem token, ou motor real indisponível |

## 4. O que o SOS precisa implementar ou ajustar

### 4.1 Regra documentada (novo na 1.1.0)
1. Num `PAROU_DE_FUNCIONAR`, o sos-ia pode concluir que o comportamento relatado é uma **regra de negócio documentada** na wiki. Nesse caso, `EstadoEntrevista.regra_documentada` vem preenchida com `{pagina, secao, trecho, similaridade, orientacao}`.
2. Se `regra_documentada` vier preenchida e `decisao_regra` vier `null`, o SOS **mostra ao usuário** a `orientacao` e o `trecho` e pergunta: *"É isso que está acontecendo e você quer mudar a regra, ou é defeito mesmo?"*
3. Conforme a resposta, o SOS chama `POST /entrevistas/{id}/regra`:
   - `{aceita: true, comentario}`: o segmento 1 vira **EVOLUTIVO_EXISTENTE**, o comentário entra em `o_que_muda` e pode vir uma nova rodada de perguntas;
   - `{aceita: false, comentario}`: o segmento continua **CORRETIVO** e o comentário vai para as dúvidas.
4. Enquanto o usuário não decide, o segmento fica CORRETIVO. O `Contrato` volta com `pode_confirmar=false`, e `faltando` traz o slot `decisao_regra`.
5. Quando a regra é aceita, o `Contrato` traz `regra_documentada`. É o trecho a anexar ao chamado.

### 4.2 Candidatos de funcionalidade (mudou o significado)
- `candidatos_funcionalidade` traz até 5 itens **já em ordem de relevância**. Mostre nessa ordem.
- **Não reordene nem filtre pela `similaridade`.** Ela é só informativa e mudou de escala: agora fica em torno de 0,4–0,7, antes era cerca de 0,85. Qualquer corte fixo antigo esconderia tudo.
- Em testes, a função certa ficou entre as 5 em 43% dos casos (antes, 19%).
- Não há candidatos em `ALGO_NOVO`.

### 4.3 Respostas sem conteúdo (replicar no `confirmarAbertura.asp`)
- Respostas como "ok", "sim", "não sei", "?" e "-" **não preenchem** o slot. "Não" e "nada" **contam** como resposta.
- Depois de tirar essas partes, o que sobra precisa ter o mínimo de caracteres do slot.
- O sos-ia já aplica a regra. Se o `confirmarAbertura.asp` revalida os slots por conta própria, precisa aplicar a mesma regra; a referência é `nucleo/entrevista/roteiros.py` (`TRIVIAIS`, `conteudo`, `preenchido`).

### 4.4 Tempo de resposta e fallback
- No modo real, `POST /entrevistas` leva de **8 a 11 s**: busca, reordenação dos candidatos (2 a 4 s) e LLM (cerca de 5 s). As outras chamadas levam de 5 a 10 s.
- Use **timeout de 60 s** no proxy e mostre "analisando…" na tela.
- `analise_ia=false` significa que a resposta veio do roteiro fixo, sem IA, porque o LLM falhou ou está fora. O fluxo continua igual, e o chamado pode ser marcado como "sem análise de IA".
- Para decidir entre o fluxo com IA e o formulário antigo: `/saude` com `ok=true` e `llm=true` → IA.

## 5. Regras do fluxo (determinísticas, não dependem do LLM)
- No máximo **2 rodadas** de perguntas e **5 perguntas** por rodada. `pronto=true` → chamar `/contrato`.
- `rodada=3` significa entrevista encerrada.
- Pedido misto vem com até 3 segmentos; cada segmento corresponde a um chamado.
- `faltando` e `pode_confirmar` do contrato são calculados por regra fixa, por roteiro:

| Roteiro | Slots obrigatórios |
|---|---|
| Corretivo | onde ocorre, o que aconteceu, como reproduzir, desde quando/quem |
| Evolutivo de algo existente | funcionalidade, o que muda, exemplo, fora de escopo, outros sistemas |
| Evolutivo novo | quem usa, o que guarda, o que faz, exemplo, fora de escopo, outros sistemas |

- Pergunta com `slot="gatilho"` é sobre uma complexidade oculta detectada no texto: integração (Benner, SEI, banco), arquivo, rotina, layout legal, "conforme anexo" e similares.

## 6. Mock (para as telas)
O mock reage a palavras do relato:
- "também", "além disso" ou "aproveitando" → 2 segmentos;
- Benner, SEI, banco, Receita, arquivo, planilha ou e-mail → pergunta de gatilho;
- "não permite", "não deixa" ou "bloque…" num `PAROU_DE_FUNCIONAR` → regra documentada de exemplo, e o `/regra` funciona como no real;
- `MUDAR_EXISTENTE` → 2 candidatos de exemplo.

No mock, `analise_ia` é sempre `false`.

## 7. LGPD
- `modo_pii=local`, o padrão nos testes com a chave pessoal: o texto é anonimizado no sos-ia antes do LLM e as imagens não são enviadas.
- O SOS **não deve enviar o nome do solicitante** no relato.
- O modo `contrato` (Azure) depende de aprovação da Segurança.
