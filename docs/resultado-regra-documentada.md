# Resultado do laboratório da regra documentada (pedido 1.3.2)

Data: 06 a 08/11/2026. Laboratório rodado na máquina pessoal sobre o `main` em `90bce60`. Motor, `rag()` e `prompts/entrevista_v1.md`
iguais aos do produto. Gasto: US$ 1,49 de US$ 2.

**Resumo: nenhuma variante passou. O prompt do produto não muda.** Há uma melhoria determinística que pode ser portada sozinha
(filtro de trecho riscado, §4) e uma hipótese nova para a próxima rodada (§5).

## 1. O que foi feito
- **Gabarito tirado da wiki do GAI** (não existem chamados reais com a resposta "conforme a regra"; ela vem por e-mail). Os relatos
  foram escritos no vocabulário do usuário, sem os termos-chave da regra. O analista conferiu todos os rótulos antes de cada rodada
  paga, com atenção especial às quase-regras, que são a única medida de falso alarme antes da produção.
  - `migracao/gabarito_regra_documentada.json` tem 22 casos: 11 com regra (P, incluindo o boleto 2023/0110 com e sem acento), 6
    quase-regras (Q), 3 falhas técnicas (T) e a parcela paga do SOS (C, com e sem acento).
  - `migracao/gabarito_regra_documentada_novos.json` tem 13 casos novos, escritos **depois** da 1ª rodada e nunca usados para
    ajustar nada: 9 quase-regras, 1 falha técnica e 3 com regra. Esses são a confirmação.
  - Os tipos de quase-regra são três: (a) o relato contradiz a condição da regra; (b) **o sistema descumpre a regra** (a regra
    manda fazer X e o usuário reclama que X não acontece); (c) a regra está riscada na wiki (revogada).
- **Bancada** `migracao/mede_regra_documentada.py` (passos `seca`, `roda` e `relatorio`; `--novos` para o 2º gabarito):
  - chama o motor como ele é (`pii.prepara` → `resolve_sistema` → `rag` → `prompt_entrevista` → `llm.json_estruturado` → `aplica`);
  - troca só a gravação (`armazem`) por memória e o `_reordena_candidatos` pela ordem da fusão (não afeta a regra);
  - faz 3 repetições por caso, decide por maioria e grava cada execução, para retomar sem pagar de novo;
  - usa `gpt-5.4-mini`, PII local e `SOSIA_MOCK=0` forçado.
- **Variantes** (em `prompts/experimentos/`, mudam só o bloco "Regra documentada"):
  - **atual**: `entrevista_v1.md`;
  - **V1** (`entrevista_v1_causa.md`): a regra pode ser a CAUSA do efeito relatado, há uma lista de quando NÃO indicar e a
    orientação é condicional;
  - **VB** (`entrevista_v1_causa_b.md`): V1 mais 2 candidatas e uma pergunta que separa as regras;
  - **V1 + filtro**: V1 com os trechos riscados retirados da saída do `rag()`.
- O caso a caso do prompt atual e da V1 + filtro está no Anexo A, e uma amostra das orientações no Anexo B. O relatório bruto da
  bancada, com todas as variantes e orientações, fica em `dados/` (fora do git). A bancada o regera com o passo `relatorio`, a
  partir das execuções gravadas.

## 2. Números
Contagem por maioria das 3 repetições.

**22 casos (1ª rodada):**

| | atual | V1 | VB | V1 + filtro |
|---|---|---|---|---|
| Falso alarme (critério ≤ 1/10) | 4/11 | 2/11 | 3/11 | **1/11** |
| Sensibilidade (critério > atual) | 6/11 | 9/11 | 8/13 | **9/11** |
| Boleto (RN13 ou RN239) e parcela paga (nenhuma) | erra o boleto | erra a parcela s/ acento | erra | **acerta os 4** |
| Instáveis (critério < atual) | 5/22 | 6/22 | 8/24 | 9/22 |

- A VB fez a pergunta que separa as regras só 2 vezes em 24 e foi descartada.
- A V1 + filtro só falhou na estabilidade. Por execução isolada, que é o que o produto faz, ela era melhor que o atual:
  - falso alarme: 24% (atual 42%);
  - regra certa: 82% (atual 52%).

**13 casos novos (confirmação; só atual e V1 + filtro):**

| | atual | V1 + filtro |
|---|---|---|
| Falso alarme | **6/10** | **5/10** |
| Sensibilidade | 3/3 | 3/3 |
| Instáveis | 2/13 | 6/13 |

**A V1 + filtro não se confirmou**: o falso alarme nos casos novos ficou longe do critério.

## 3. Diagnóstico
1. **O erro que sobra é quase todo do tipo "o sistema descumpre a regra"**, em regras que mandam o sistema mostrar algo: a RN17
   (aviso de dias da suspensão), a RN119 (alerta de quitação antecipada), a RN11 (parágrafo do Pró-DF na carta) e a RN114 (aviso de
   segunda via). Na 1ª rodada, foram também a RN36 (número novo ao reativar) e a RN275 (não cobrar taxa de terceiros).
   - O usuário reclama que o aviso não aparece, e o LLM aponta a regra que manda mostrá-lo.
   - **A orientação que ele escreve está certa** ("se o aviso não aparece, é defeito; se a regra precisa mudar, é melhoria"). Ou
     seja, ele entende a relação, mas o esquema só tem um campo (`regra_documentada_trecho`), que no produto quer dizer "a regra
     explica o comportamento → candidato a melhoria".
   - Proibir isso no prompt (item "o sistema descumpre a regra" da lista NÃO da V1) não bastou.
2. **Contradiz a condição:** a V1 melhorou (N3 e N6 corrigidos), mas ainda aponta às vezes (N1, N5) quando a regra fala do mesmo
   campo.
3. **Regra riscada:** todos os prompts apontavam a RN151 (riscada = revogada, confirmado pelo analista) 3 vezes em 3. O filtro
   resolveu o caso, pois é determinístico.
4. **Busca:** em 2 dos 11 casos com regra (P9 RN149 e P10 RN300), a regra certa não vem do `rag()`. Com o trecho injetado, o P10
   acerta 3/3 e o P9 continua difícil ("procure a gerência" sem a palavra da regra). Isso é assunto da busca, não do prompt.
5. **O boleto 2023/0110:** com a V1, a regra é apontada sempre (RN239 ou RN13, as duas aceitas), contra nunca no prompt atual. As
   orientações saem condicionais, mas às vezes com códigos do sistema ("tipo de baixa 50, 02…") em vez de palavras do usuário.

## 4. O que pode ser portado já (independe do prompt)
**Filtro de trecho riscado**: tirar da saída do `rag()` os trechos cuja seção ou texto inteiro está entre `~~ ~~` (seção excluída
da wiki). É determinístico, não custa nada e removeu a única regra revogada do gabarito sem efeito colateral. Na bancada:

```python
def riscado(d):
    t = (d.get("texto") or "").strip()
    return "~~" in (d.get("secao") or "") or (t.startswith("~~") and t.endswith("~~"))
```

No produto, o lugar natural é o fim do `rag()` em `nucleo/entrevista/motor.py`, ou a indexação da wiki (não indexar seção
riscada). Também falta um teste: um trecho riscado no RAG falso não chega ao prompt. No GAI estão riscadas, por exemplo, a RN151, a
RN.176, a RN.189 e alguns fluxos de relatório.

## 5. Hipótese para uma próxima rodada (não testada)
Separar no esquema a **relação** da regra com o relato, em vez de pedir ao LLM que não aponte:
- `regra_documentada_relacao`: `"explica"` (a regra causa o bloqueio/aviso → orientação melhoria/defeito como hoje) ou
  `"descumpre"` (o sistema deixou de fazer o que a regra manda → indício de defeito, e a regra vai para a fábrica como referência
  do comportamento correto);
- o `aplica()` só trata como regra documentada o `"explica"`. O `"descumpre"` pode virar uma informação útil no corretivo.

Pela regra do laboratório, uma variante nova precisa de **outro conjunto de relatos novos**, conferido pelo analista antes de
rodar. A V1 + filtro também pode entrar como base dessa variante. Custo estimado: ~US$ 0,50.

## 6. Limitações
- Quem escreveu os prompts também escreveu os gabaritos. Os casos novos reduzem esse viés, mas não o eliminam.
- São poucos casos (22 + 13) e só do GAI.
- A medida de falso alarme vem toda de quase-regras escritas à mão. A confirmação real vem da produção (aceite ou recusa da
  orientação pelo usuário).

## Anexo A. Caso a caso

Decisões das 3 repetições (`-` = nenhuma regra apontada) e acerto por maioria. Só o prompt atual e a V1 + filtro, que são
o que interessa ao produto; as outras variantes estão no relatório bruto da bancada (`dados/`, fora do git).

### 22 casos (1ª rodada)

| id | tipo | relato | certo | atual | V1 + filtro |
|---|---|---|---|---|---|
| P1 | P | Não consigo emitir o boleto bancário da alienação do prestamista 2023/0110. O sistema bloqueia a emissão do boleto e não gera nada. | FE5 Alienação Bloqueada ou RN13 ou RN239 | - - - ✘ | RN239 RN239 RN13 ✔ |
| P1s | P | Nao consigo emitir o boleto bancario da alienacao do prestamista 2023/0110. O sistema bloqueia a emissao do boleto e nao gera nada. | FE5 Alienação Bloqueada ou RN13 ou RN239 | - - - ✘ | RN13 RN239 RN13 ✔ |
| P2 | P | Fui desfazer o acordo de dívida que fiz para um cliente e a tela não deixa apagar. | FE08 Erro ao Excluir Renegociação ou RN234 | RN234 RN234 RN234 ✔ | RN234 RN234 RN234 ✔ |
| P3 | P | Lancei uma parcela errada do TerraFácil e quis apagar só ela, mas o sistema avisa que vai apagar todas. | RN194 | RN194 RN194 RN194 ✔ | RN194 RN194 RN194 ✔ |
| P4 | P | Não consigo lançar a nova venda da modalidade 129: preencho tudo e na hora de salvar o sistema recusa. | FE09 Alienação sem Data de Lavratura ou RN296 | RN296 RN296 RN296 ✔ | RN296 RN296 RN296 ✔ |
| P5 | P | Tentei trocar o índice de correção de uma venda com data retroativa e o sistema não aceita. | RN262 ou RN266 | RN266 RN266 RN266 ✔ | RN266 RN266 RN266 ✔ |
| P6 | P | Um leiloeiro saiu e eu preciso apagar o cadastro dele, mas não existe a opção de apagar, só de desativar. | RN36 | RN36 RN36 RN36 ✔ | RN36 RN36 RN36 ✔ |
| P7 | P | Ao mudar a situação de um imóvel do leilão, tento juntar a ata digitalizada e o sistema recusa o arquivo. | RN45 | - - - ✘ | RN45 RN45 RN45 ✔ |
| P8 | P | Uma colega tentou tirar da lista um modelo antigo de carta de cobrança e não conseguiu. | FA3 Excluir Modelo de Carta de Cobrança ou RN106 | RN106 RN106 - ✔ | RN106 RN106 RN106 ✔ |
| P9 | P | Na hora de lançar o parcelamento aparece um aviso mandando procurar a gerência e não dá para continuar. | RN149 | - - - ✘ | FE01 FE01 - ✘ |
| P10 | P | Não consigo cadastrar uma alienação da modalidade 131 para um contrato novo; o sistema barra o cadastro. | RN300 | - - - ✘ | - - - ✘ |
| Q1 | Q | Tento excluir a renegociação que fiz hoje numa alienação; é a única que ela tem e ainda não tem nenhum pagamento, mas não consigo. | nenhuma | - - - ✔ | - RN234 - ✔ |
| Q2 | Q | O boleto da alienação está saindo com a taxa de cobrança de serviços de terceiros somada ao valor. | nenhuma | RN144 - RN144 ✘ | RN145 RN144 RN145 ✘ |
| Q3 | Q | Reativei um leiloeiro que estava inativo e ele voltou com o mesmo número de credenciamento de antes. | nenhuma | RN36 RN36 RN36 ✘ | - RN36 - ✔ |
| Q4 | Q | Na alteração de situação do item de leilão, anexo um PDF de 1 MB chamado ata.pdf e o sistema dá erro ao salvar. | nenhuma | - - RN45 ✔ | - - RN45 ✔ |
| Q5 | Q | O cadastro da venda da modalidade 129 não salva, mesmo com a data de lavratura já registrada no GPZ. | nenhuma | RN296 - - ✔ | - - - ✔ |
| Q6 | Q | Depois que preenchemos a data de publicação, o sistema não deixa mais trocar o leiloeiro da ordem de serviço do leilão. | nenhuma | RN151 RN151 RN151 ✘ | - - - ✔ |
| T1 | T | A tela de emissão de boleto da alienação fica carregando e não termina; até semana passada funcionava. | nenhuma | - - - ✔ | - - - ✔ |
| T2 | T | O relatório de saldo a receber por modalidade dá erro 500 quando escolho o período de 2024. | nenhuma | - - - ✔ | - - - ✔ |
| T3 | T | Depois da atualização de ontem, o botão Gravar do cadastro de alienação não faz nada. | nenhuma | - - - ✔ | - - - ✔ |
| C1 | C | O sistema não deixa alterar o contrato de alienação depois que a primeira parcela foi paga. | nenhuma | - - FE4 ✔ | - FE4 - ✔ |
| C1s | C | O sistema nao deixa alterar o contrato de alienacao depois que a primeira parcela foi paga. | nenhuma | FE4 FE4 FE4 ✘ | - FE4 - ✔ |

### 13 casos novos (confirmação)

| id | tipo | relato | certo | atual | V1 + filtro |
|---|---|---|---|---|---|
| N1 | Q | No cadastro da alienação marquei S no campo de proprietário e mesmo assim o sistema não deixa salvar. | nenhuma | - RN6 - ✔ | RN6 - RN6 ✘ |
| N2 | Q | Gerei de novo o boleto de uma alienação que já tinha boleto e não apareceu nenhum aviso de segunda via; saiu como se fosse o primeiro. | nenhuma | RN114 RN114 RN114 ✘ | - 2. LISTA DE MENSAGENS RN114 ✔ |
| N3 | Q | Faltam 10 dias para a publicação do leilão na internet e o sistema não deixa excluir um item da ordem de serviço. | nenhuma | RN163 RN163 RN163 ✘ | RN163 - - ✔ |
| N4 | Q | Consulto uma alienação com recebimento suspenso e prazo determinado preenchido, e não aparece o aviso de quantos dias faltam para acabar a suspensão. | nenhuma | RN17 RN17 RN17 ✘ | RN17 RN17 RN17 ✘ |
| N5 | Q | No refinanciamento, lanço o IPTU como taxa acessória pela primeira vez e o sistema diz que a taxa já foi informada. | nenhuma | - - - ✔ | RN256 RN256 - ✘ |
| N6 | Q | A migração de índice de uma venda com data retroativa não é aceita pelo sistema, mas na data escolhida a venda estava em dia e depois dela não houve nenhum pagamento nem refinanciamento. | nenhuma | RN266 RN266 RN266 ✘ | - - - ✔ |
| N7 | Q | Não consigo alterar o telefone de contato de um leiloeiro que está ativo; a alteração não fica gravada. | nenhuma | - - - ✔ | - FA2 - ✔ |
| N8 | Q | No relatório, a alienação da modalidade 117 que foi quitada antes do prazo não traz nenhum alerta sobre isso. | nenhuma | RN119 RN119 RN119 ✘ | - RN119 RN119 ✘ |
| N9 | Q | A carta de cobrança da modalidade 50 está saindo sem o parágrafo que avisa que o contrato pode ser rescindido. | nenhuma | RN11 RN11 RN11 ✘ | RN11 RN11 RN11 ✘ |
| N10 | T | Ao gerar a carta de cobrança aparece a mensagem 'Erro inesperado, contate o administrador' e nada é gerado. | nenhuma | - - - ✔ | - - - ✔ |
| NP1 | P | No refinanciamento, tento lançar de novo o ITBI, que eu já tinha lançado antes nessa alienação, e o sistema não aceita. | RN256 | RN260 RN256 RN256 ✔ | RN256 RN256 RN256 ✔ |
| NP2 | P | Algumas cartas de cobrança estão saindo com um parágrafo dizendo que o contrato será rescindido sem devolver o que foi pago, e o cliente reclamou. | RN11 | RN11 RN11 RN11 ✔ | RN11 RN11 RN11 ✔ |
| NP3 | P | Ao gerar o boleto aparece um aviso dizendo que é segunda via, mas o cliente diz que nunca recebeu o primeiro. | RN114 | RN114 RN114 RN114 ✔ | RN114 RN114 RN114 ✔ |

Trecho injetado quando a busca não traz a regra (P9 e P10, V1 + filtro): P10 → RN300 nas 3; P9 → `- - RN149` (erra por maioria).

## Anexo B. Orientações da V1 + filtro (amostra)

Para ver o formato e o erro dominante: as do boleto, as dos falsos alarmes "o sistema descumpre a regra" (repare que a
orientação já diz "é defeito") e as de contradição de condição.

- **P1** RN239: Se a alienação estiver bloqueada para emissão de boleto, a regra RN239 faz o sistema bloquear a emissão. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **P1** RN239: Se ao emitir boleto da alienação o tipo de baixa for 50, 02, 01, 03 ou 09, a regra RN239 faz o sistema bloquear a emissão. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **P1** RN13: Se a alienação estiver com cadastro de inscrição no SERASA, a regra de bloqueio de boleto faz o sistema não permitir a emissão. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **P1s** RN13: Se o prestamista estiver inscrito no SERASA, a regra RN13 faz o sistema bloquear a emissão do boleto bancário. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **P1s** RN239: Se a alienação estiver bloqueada ou liberada conforme a regra de emissão de boleto, o sistema verifica isso antes de emitir. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **P1s** RN13: Se a alienação do prestamista tiver cadastro de inscrição no SERASA, a regra de bloqueio de emissão de boleto faz o sistema não permitir a emissão. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **Q2** RN145: Se for emitir boleto da alienação e houver valor de taxa no boleto, a regra RN145 faz o sistema somar essa taxa ao valor total. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **Q2** RN144: Se ao emitir o boleto houver valor do campo VL_TAC, a regra Taxa de Administração de Boleto faz o sistema somar esse valor ao boleto. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **Q2** RN145: Se ao gerar o boleto da alienação a taxa de cobrança de serviços de terceiros estiver sendo somada no total, a regra Soma total da parcela a pagar faz o sistema incluir esse valor. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **Q3** RN36: Se o leiloeiro estava inativo e for reativado, a regra de reativação diz que ele volta para a lista com um novo número de credenciamento. Se no seu caso ele voltou com o mesmo número e isso precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **C1** FE4 Não Permitido Alterar Data de Vencim: Se já existir parcela paga depois da parcela que você quer alterar, a regra de alterar data de vencimento faz o sistema bloquear. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **N1** RN6: Se no cadastro da alienação o campo de proprietário estiver com S, a regra de proprietário faz o sistema considerar o prestamista como proprietário. Se mesmo assim ele não deixa salvar, parece defeito; se a regra precisa mudar, o pedido é uma melhoria.
- **N1** RN6: Se você marcou S no campo de proprietário, a regra de cadastro de alienação diz que o prestamista deve ser considerado proprietário. Se mesmo assim o sistema não salva, isso indica defeito; se a regra precisar mudar, o pedido é uma melhoria.
- **N4** RN17: Se a alienação estiver com a situação 9 - Recebimento Suspenso e o prazo determinado estiver preenchido, a regra da mensagem faz o sistema mostrar quantos dias faltam para o fim da suspensão. Se for o seu caso e isso não aparece, é um defeito; se a regra precisar mudar, o pedido é uma melhoria.
- **N4** RN17: Se a alienação estiver com situação 9 - Recebimento Suspenso e o prazo determinado estiver preenchido, a regra RN17 faz o sistema mostrar a mensagem de quantos dias faltam para o fim da suspensão. Se for o seu caso e isso não aparece, é defeito; se a regra precisar mudar, o pedido é uma melhoria.
- **N4** RN17: Se a alienação estiver com situação 9 - Recebimento Suspenso e o campo Prazo determinado estiver preenchido, a regra da mensagem faz o sistema mostrar que faltam dias para o fim da suspensão. Se no seu caso isso não aparece, é defeito; se a regra precisa mudar, o pedido é uma melhoria.
- **N5** RN256: Se o IPTU já tiver sido cadastrado para essa alienação, a regra Incluir Taxas Acessórias faz o sistema avisar que a taxa já foi informada. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **N5** RN256: Se o IPTU já tiver sido cadastrado antes na mesma alienação, a regra RN256 de taxas acessórias faz o sistema avisar que a taxa já foi informada. Se for o seu caso e a regra precisa mudar, o pedido é uma melhoria; se não for, é um defeito.
- **N8** RN119: Se a alienação for da modalidade 117 e estiver quitada antes do prazo, a regra de mensagem para alienação quitada antecipadamente faz o sistema alertar no relatório. Se for o seu caso e o alerta não aparece, o pedido é um defeito; se não for, a regra precisa mudar.
- **N8** RN119: Se a alienação for da modalidade 117 e estiver quitada antes do prazo, a regra de mensagem para alienação quitada antecipadamente faz o sistema alertar no relatório. Se for o seu caso e esse alerta não aparece, é defeito; se a regra precisa mudar, o pedido é uma melhoria.
- **N9** RN11: Se a carta for da modalidade Pró-DF 35, 50, 53, 64 ou 530, a regra de mensagem faz o sistema incluir esse aviso sobre a rescisão do contrato. Se for o seu caso e isso não aparece, é defeito; se a regra precisar mudar, o pedido é uma melhoria.
- **N9** RN11: Se for carta de cobrança da modalidade Pró-DF 50, a regra da mensagem diz que o sistema deve mostrar esse parágrafo sobre rescisão. Se ele não aparece, é defeito; se a regra precisa mudar, o pedido é uma melhoria.
- **N9** RN11: Se a carta for da modalidade Pró-DF 50, a regra de mensagem para modalidades do Pró-DF faz o sistema mostrar esse aviso sobre rescisão. Se for o seu caso e isso não aparece, é um defeito; se a regra precisa mudar, o pedido é uma melhoria.

## Anexo C. Como repetir

```
python migracao/mede_regra_documentada.py seca                       # sem LLM: trechos do rag(), posição da regra certa
python migracao/mede_regra_documentada.py roda --variantes atual,v1r --reps 3 --teto 1.0
python migracao/mede_regra_documentada.py relatorio                  # gera dados/regra_documentada_gabarito.txt
# --novos troca para o gabarito de confirmação; precisa de SOSIA_PII_MODO=local e da chave do LLM no ambiente
```
