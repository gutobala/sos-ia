# Contrato 1.5.1: vários pontos de partida e opções do "o que muda"

Para a sessão do SOS. Data: 09/11/2026. Base: contrato 1.5.0 (576bf09). **Só acréscimos:** o `ponto_partida` da 1.5.0 continua
aceito, e a tela atual continua funcionando sem mudança.

## Por quê
Medição do laboratório (`migracao/mede_ponto_partida.py`, 699 chamados SOS, gabarito = casos de uso citados como insumo na contagem
detalhada, busca do motor sem LLM):
- **O menu certo na busca ajuda.** Nos 269 chamados com caminho na wiki, as páginas certas entre as 5 mostradas subiram de 40% para
  56%, e os chamados com pelo menos 1 página certa, de 60% para 77%.
- **Um ponto só perde as outras telas.** Fora a página do menu escolhido, a busca acha 29% das outras páginas certas com 1 ponto, e
  44% com uma busca por ponto (5 documentos por ponto). Nos chamados que passam por 6 ou mais casos de uso, 12% → 24%.
- Os chamados de 5 ou mais funções são 34% do total e 75% do PF, e passam por 3 a 6 casos de uso. Um ponto só acerta os pequenos
  e perde os que pesam.

## O que muda para o SOS

### 1. Vários pontos de partida (`POST /entrevistas`)
`NovaEntrevista.pontos_partida`: lista de até 5, no mesmo formato do `ponto_partida` (`caminho`, `detalhe`, `menu_id`, `fonte`), na
ordem em que o usuário escolheu.
```json
{ "cd_sistema": 1, "sigla": "GAI", "intencao": "MUDAR_EXISTENTE",
  "relato": "O parcelamento deve aceitar desconto de 25% também no segundo edital e o demonstrativo deve mostrar o desconto.",
  "pontos_partida": [
    { "caminho": "Cadastro > NUGIV > Parcelamento Resíduo", "menu_id": "CADATRO_NUNEG_MENU.PARCELAMENTO_RESÍDUO", "fonte": "forms", "detalhe": null },
    { "caminho": "Relatório > NUGEC > Demonstrativo", "menu_id": "RELATORIO_NUGEC_MENU.DEMONSTRATIVO", "fonte": "forms", "detalhe": null } ] }
```
- **Um ponto só:** pode continuar mandando `ponto_partida`, que vale como lista de 1. **Não mande os dois campos juntos (422).**
  Mais de 5 pontos também dá 422. Ponto repetido (mesmo `menu_id`, ou o mesmo caminho em fonte livre) é ignorado.
- **Busca:** uma busca por ponto ("Menu: <caminho>." + relato). O relato sozinho só é buscado quando não há ponto. Com 3 pontos,
  a busca levou 1,5 s aqui, contra 1,1 s com um ponto.
- **Itens preenchidos:** no corretivo, `onde_ocorre`; no evolutivo existente, `funcionalidade`. Com vários pontos, o valor traz os
  caminhos unidos por "; " (`"Menu: Cadastro > NUGIV > Parcelamento Resíduo; Menu: Relatório > NUGEC > Demonstrativo"`).
  Em "algo novo", os caminhos continuam só no contexto da IA.

### 2. Documentos por ponto (`EstadoEntrevista.documentos`)
- Até 5 por ponto (no máximo 25), agrupados por ponto, sem repetir página.
- Cada documento ganha **`ponto`**: o número do ponto cuja busca trouxe a página (1 = o primeiro da lista). Vem nulo quando não
  houve ponto de partida; com um ponto só, vem 1.
- Sugestão de tela: um grupo por ponto, com o caminho como título.
```json
"documentos": [
  { "pagina": "sistemas:gai:…:us024…", "titulo": "US024 - Manter Parcelamento de Resíduo", "tipo": "user_story", "secao": "…", "similaridade": 0.629, "ponto": 1 },
  { "pagina": "sistemas:gai:…:uc054…", "titulo": "UC054 - Gerar Demonstrativo de Desconto - REFIS PRÓ-DF 2016", "tipo": "caso_de_uso", "secao": "…", "similaridade": 0.647, "ponto": 2 } ]
```
`documentos_lidos` na confirmação continua igual: ids de página, até 10.

### 3. "O que muda" por ponto, com opções
No evolutivo existente com ponto de partida, a pergunta do "o que muda" tem **opções montadas pela aplicação** (sem IA): o tipo de
mudança, mais "Não sei informar". O texto livre continua valendo.
```json
{ "id": "r1p1", "segmento": 1, "slot": "o_que_muda", "ponto": 1,
  "texto": "Em Cadastro > NUGIV > Parcelamento Resíduo, o que deve mudar?",
  "dica": "Escolha o tipo de mudança e descreva o que deve passar a acontecer nessa tela ou nos passos seguintes.",
  "opcoes": ["Incluir ou cadastrar", "Alterar", "Excluir", "Consultar ou visualizar", "Gerar relatório ou documento",
             "Gerar arquivo ou integração", "Não sei informar"] }
```
- **`Pergunta.ponto`:** com 2 ou mais pontos, vem uma pergunta por ponto, com o número dele. Com 0 ou 1 ponto, vem nulo.
- **`Pergunta.opcoes`** passa de até 5 para **até 7** itens. Ajuste a tela para caber 7 botões (podem quebrar linha).
- **O item continua obrigatório.** Uma opção sozinha ("Alterar") ou "Não sei informar" não preenche o "o que muda": a próxima rodada
  pede o detalhe ("Você respondeu “Alterar”, mas faltou detalhe…"). Se a tela permitir, junte a opção e o texto numa resposta só
  ("Alterar: aceitar 25% de desconto no 2º edital").
- **Resposta por ponto:** o sos-ia guarda de qual tela ela é, no formato `"[Cadastro > NUGIV > Parcelamento Resíduo] Alterar: …"`. A
  marca entre colchetes não conta para o mínimo de caracteres.
- **Próxima etapa:** quando o item de menu estiver ligado a um caso de uso, as opções passam a ser os títulos dos fluxos dele (ex.:
  "Incluir parcelamento", "Imprimir demonstrativo"). O formato não muda.

### 4. Segmento por ponto (`Segmento.ponto`)
Quando o relato deixa claro que cada tela é uma mudança à parte, a IA pode criar um segmento por ponto. Nesse caso o segmento traz
`ponto` com o número, e a `funcionalidade` dele tem só aquele caminho. Senão, `ponto` vem nulo e o segmento 1 vale para todos. Os
chamados continuam sendo por tipo, no máximo 2.

### 5. Texto do chamado
Com 2 ou mais pontos, INFORMAÇÕES DO PEDIDO mostra um caminho por linha, e o "o que muda" uma resposta por ponto:
```
Funcionalidades que serão alteradas:
- Menu: Cadastro > NUGIV > Parcelamento Resíduo
- Menu: Relatório > NUGEC > Demonstrativo
O que muda:
- [Cadastro > NUGIV > Parcelamento Resíduo] Alterar: aceitar 25% de desconto no 2º edital
- [Relatório > NUGEC > Demonstrativo] Incluir o desconto do 2º edital no demonstrativo
```
No corretivo o rótulo continua "Onde ocorre (sistema, tela ou funcionalidade)", também com um caminho por linha. Com 1 ponto, o texto
fica como na 1.5.0. A versão 1 do escopo guarda `pontos_partida` (lista) e continua com `ponto_partida` (o primeiro).

## Mock
- `pontos_partida` funciona como no real: `funcionalidade` / `onde_ocorre` com os caminhos unidos por "; ";
- no `MUDAR_EXISTENTE`, uma pergunta de "o que muda" por ponto, com as 7 opções;
- os 2 documentos de exemplo vêm com `ponto` 1 e 2, e mais um por ponto a partir do 3º;
- os dois campos juntos dão 422.

## Não muda
Rotas, proxy, autenticação, `GET /sistemas/{cd}/menus`, `GET /documentacao`, a confirmação e o vínculo dos chamados.

## Correção 1.5.2 (10/11/2026, teste do SOS)
- **Telas juntadas:** no corretivo com 3 pontos, a IA juntou os caminhos como se fossem uma tela só ("Em Venda Direta > Edital
  Venda por Licitação > Cadastrar Controle Edital > Tela de impressao do controle, …"). Agora o prompt proíbe isso, e a aplicação
  troca o " > " entre dois caminhos por "; " (`motor.separa_caminhos`), sem depender da IA.
- **Página geral de regras de negócio:** é uma só para o sistema todo (368 seções no GAI, 111 no GOP).
  - Na lista, o título vira o nome da regra que casou, e ela vem depois dos UC/US do mesmo ponto.
  - No `GET /documentacao`, vem só a regra que a busca trouxe nesta entrevista, com a regra-mãe e as sub-regras, até 10 seções.
  ```json
  { "pagina": "sistemas:gop:documentacao_tecnica:regras_de_negocio:inicio",
    "titulo": "Regras de negócio: RN99. Cálculo para 1°, 2° e 3° Edital", "tipo": "regras_de_negocio",
    "secoes": [ { "secao": "… > RN96. Emitir Controle de Operação - Leilão", "texto": "…" },
                { "secao": "… > RN96. Emitir Controle de Operação - Leilão > RN99. Cálculo para 1°, 2° e 3° Edital", "texto": "…" } ],
    "parcial": true, "secoes_total": 111 }
  ```
  `parcial` e `secoes_total` são campos novos, também nas outras páginas (com `parcial: false`).

## Próximas etapas do sos-ia (sem mudança de contrato)
- **Ligar o item de menu ao caso de uso.** Na carga dos menus, ligar cada folha do FORMS (e do FGCA, se o SOS mandar o rótulo) ao
  caso de uso ou user story pelo nome, por similaridade e sem LLM. Ligação duvidosa fica de fora e vai para o relatório de cobertura,
  para a Sustentação conferir. Depois, os pares confirmados pelo uso: documento marcado como "é isto que eu uso" e caso de uso citado
  no insumo da contagem. Com o item ligado, a busca do ponto traz as páginas ligadas primeiro, e as opções do "o que muda" passam a
  ser os fluxos do caso de uso.
- **Para a Sustentação (Fase 3):** candidatos com evidência ("alterada em N chamados anteriores via UCxxx"), só na prévia. O usuário
  não vê.
