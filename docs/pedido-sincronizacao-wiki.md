# Pedido ao sos-ia: sincronização da wiki no produto

Para a sessão do sos-ia. Data: 10/11/2026. Base: contrato 1.5.4 (508f923). Origem: laboratório (`D:\Projetos\PRE-PROJETO`).
**O contrato da API não muda.** Só há um campo opcional novo no `/saude` (item 6), que seria uma 1.5.5 aditiva.

## Por quê
O RAG do `sosia` é uma cópia da wiki de **23/10/2026**: 40.657 trechos de 4.682 páginas, exportados da base `apf` pelo
`exporta_nucleo.py`.

A wiki não parou. Em 10/11, o laboratório baixou pela API o que tinha mudado:

| O que mudou | Quantidade |
|---|---|
| Páginas novas | 46 |
| Páginas alteradas | 69 |
| Páginas removidas | 1 (GIA PT046) |
| Imagens novas | 262 |

Os sistemas afetados foram GR, GLA, GFV, GAI, GIA, ANIA, Benner, GPEv2 e Intranet. Exemplos:
- a US031 do GAI;
- as US036 a US040 do GFV;
- a EU010 do GR, que mudou de "Detalhar" para "Visualizar Categoria".

Nada disso aparece hoje na documentação relacionada nem no prompt da entrevista.

O produto roda na rede da Terracap e não pode depender da máquina do laboratório. Por isso, a atualização tem de rodar no próprio
sos-ia, contra a API da wiki.

## Como o laboratório faz hoje (referência)
Os arquivos estão em `migracao/referencia_wiki/`, copiados da `PRE-PROJETO` sem nenhuma mudança:

| Arquivo | Papel |
|---|---|
| `atualiza_dump.py` | **Novo, de 10/11.** Lista as páginas `sistemas:*` (`wiki.getAllPages`), compara a data e o tamanho com a cópia local, baixa só o que mudou (`wiki.getPage`), separa as removidas e baixa as imagens citadas. Simula por padrão; `--aplicar` grava, com backup. |
| `converte_dokuwiki.py` | Converte a sintaxe da DokuWiki para Markdown (as notas do vault): tipo, peso, situação e categoria da página, links, âncoras, nomes das notas e a junção `glpweb`→`glpv2`. Ignora o namespace com espaço invisível (`​gcf`, rascunho antigo) e o que está em `wiki_exclusoes.txt`. **O OCR está desligado no vault** (`OCR_NO_VAULT = False`), então a porta não precisa de OCR. |
| `indexa_wiki.py` | Divide a nota em trechos (`monta_pagina`, `secoes`, `divide_corpo`, máximo de 1.500 caracteres), extrai os códigos definidos e citados (RN, FE, FA, MN, Tela, P) e resolve as citações dentro do sistema. Calcula o `hash` do texto e o `sistemas_acesso` (`mapa_acesso`). Grava em `conhecimento_trecho` e `conhecimento_referencia`, com DDL em `15_conhecimento_trecho.sql`. É incremental pelo `hash`. |
| `resolve_refs_cruzadas.py` | Resolve as RN/MN que apontam para o documento de regras de **outro** sistema e acrescenta esse sistema ao `sistemas_acesso` do trecho de destino. |
| `datas_paginas.py` | Calcula o `dt_pagina_criada`: a menor data entre o `created` do `.meta` e a primeira data do histórico de mudanças da página. A busca usa essa data como filtro contra vazamento (`dt_pagina_criada < ate`). |
| `triagem_ocr.py` | Dependência do `converte_dokuwiki.py` (só a função `tokens`). |

API:
- **Na rede da Terracap:** `http://terrawiki/lib/exe/xmlrpc.php`.
- **Pela VPN:** `http://terrawiki.terracapnet.local/lib/exe/xmlrpc.php`.
- **Versão:** DokuWiki "Jack Jackrum" 2023-04-04a, API XML-RPC 11.
- **Acesso:** só leitura, **sem senha** hoje.
- **Datas:** a `lastModified` vem como `DateTime` com fuso (`2026-11-03T17:14:27+00:00`).

## O que fazer

### 1. `migracao/sincroniza_wiki.py` (ou `nucleo/wiki/`)
**Uso:** `python -m migracao.sincroniza_wiki [--aplicar] [--sistema gr] [--tudo]`. Simula por padrão, como o resto do projeto.

1. **Descobrir o que mudou:**
   - pedir `wiki.getAllPages()`, só `sistemas:*`;
   - comparar com o que está gravado no `sosia`: `lastModified` e tamanho por página, guardados numa tabela nova (item 5);
   - na primeira execução não há nada gravado; aí vale comparar o `hash` do texto convertido.
   - Nas páginas com data nova, baixar e só considerar alterada se o texto mudou de fato. No laboratório, 19 páginas tinham data
     nova e texto igual.
2. **Converter e dividir em trechos:**
   - usar a mesma lógica de `converte_dokuwiki.converte_pagina` e de `indexa_wiki.monta_pagina`, levada para módulos do núcleo;
   - os índices globais (páginas, nomes das notas, categorias da página inicial) saem da lista completa da API, não do disco;
   - a mídia não é necessária, porque o OCR está desligado. A imagem vira só uma referência no texto, como hoje.
3. **Mascarar antes de gravar e antes de gerar o vetor:**
   - as mesmas regras do `exporta_nucleo.mascara`: CPF, e-mail, telefone e os nomes de `dados/nomes_anonimizacao.txt`;
   - a máscara vale em `texto`, `texto_indexado` e `texto_destino`.
   - Motivo: já houve CPF real em páginas do eSocial.
4. **Gravar só o que mudou, página a página, numa transação:**
   - apagar os trechos e as referências da página e inserir os novos;
   - gravar `data_versao` = `lastModified` da página (hoje está em 1970-01-01 em todos os trechos). O `Motor.documentacao`
     lê `max(data_versao)`, mas **a busca não filtra versão**. Por isso, nunca deixar duas versões da mesma página na
     `conhecimento_trecho`, ou a página sai repetida no `rag()`. Se for preciso guardar o histórico, usar uma tabela à parte;
   - o vetor 3-large (`embedding_3l`) é gerado só para os `hash` novos;
   - a coluna `embedding` (e5) fica NULL nas linhas novas, porque o produto não tem e5 (ver o primeiro cuidado abaixo).
5. **Páginas removidas da wiki:** saem do `conhecimento_trecho`. Guardar o que saiu numa tabela de removidos ou num arquivo de log,
   para dar para voltar atrás.
6. **Refazer, só nos sistemas afetados:**
   - a resolução de referências, inclusive a cruzada;
   - o `sistemas_acesso`;
   - o `dt_pagina_criada` das páginas novas. Usar a versão mais antiga de `wiki.getPageVersions` e o histórico de mudanças do
     texto.
7. **Menus da wiki:** rodar `carrega_menus` com a fonte `wiki`, só nos sistemas afetados. Os menus do `.mmb` (FORMS) não mudam.
8. **Relatório de cada execução:**
   - páginas novas, alteradas, iguais (só a data mudou) e removidas;
   - trechos inseridos e apagados, por sistema;
   - custo dos vetores;
   - erros.

### 2. Configuração
- `SOSIA_WIKI_URL`, com padrão `http://terrawiki/lib/exe/xmlrpc.php`.
- `SOSIA_WIKI_USUARIO` e `SOSIA_WIKI_SENHA`: opcionais, só no `.env`, para o caso de a Segurança exigir um usuário somente leitura.
- `wiki_exclusoes.txt` vai para `config/`, com a mesma regra do laboratório.

### 3. Quando roda
- Uma vez por dia no container, de madrugada, ou à mão.
- Não pode bloquear a API: a entrevista continua respondendo durante a sincronização, porque cada página é gravada numa transação.

### 4. Custo
- O corpus inteiro no 3-large custou US$ 1,22.
- Uma atualização como a de 10/11 (cerca de 115 páginas) deve sair por menos de US$ 0,05.

### 5. Tabela de controle
`apf.wiki_pagina_sync`:

| Campo | Conteúdo |
|---|---|
| `origem_dokuwiki` | identificador da página |
| `last_modified` | data da última alteração na wiki |
| `tamanho` | tamanho da página |
| `hash_texto` | hash do texto convertido |
| `sincronizada_em` | quando a página foi sincronizada |
| `removida_em` | quando a página saiu da wiki, se saiu |

Mais `apf.wiki_sync_execucao`, com o resumo de cada execução.

### 6. `/saude` (opcional e aditivo)
- `wiki_atualizada_em`: data da última sincronização com sucesso. Ajuda o SOS e a Sustentação a ver se a documentação está em dia.

## Critérios de aceite
1. **Paridade da conversão e dos trechos:**
   - rodar a nova conversão sobre as páginas que **não mudaram** desde 23/10 e comparar o `hash` dos trechos com o `sosia` atual;
   - meta: ≥ 99% iguais, com a lista das diferenças explicada.
   - É isso que garante que a busca calibrada (pesos de 05/11) continua valendo.
2. **Primeira execução real:** detecta pelo menos o delta acima (46 novas, 69 alteradas, PT046 removida), mais o que tiver mudado
   depois de 10/11.
3. **Segunda execução seguida:** zero mudanças.
4. **Busca:** o `rag()` acha uma página nova. Por exemplo, um relato do GR sobre "visualizar categoria" deve trazer a EU010 com o
   título novo.
5. **Página removida:** `GET /documentacao?pagina=sistemas:gia:documentacao_tecnica:plano_de_teste:pt046` dá 404.
6. **Dados pessoais:** a varredura do `exporta_nucleo` (CPF, e-mail e telefone) não acha nada nos trechos novos.
7. **Documentação em HTML (1.5.4):** `GET /documentacao` de uma página nova devolve as seções com `html` correto
   (`doc_html.para_html`): tabelas, listas e regras riscadas, como nas páginas antigas.
8. **Testes do pytest:** a conversão de uma página de exemplo de cada tipo (UC, US, RN, MN, plano de teste) gera os mesmos trechos
   da referência. Esses testes não precisam da API.

## Cuidados
- **Laboratório:**
  - a base `apf` do laboratório **não** é tocada e continua com a wiki de 23/10, para os experimentos seguirem comparáveis;
  - os scripts de medição (`mede_*`, `calibra_wiki`) com `SOSIA_EMB=e5` não enxergam os trechos novos do `sosia`, que não têm e5;
  - para medir no `sosia` atualizado, use o 3-large.
- **Dump:** o `sosia.dump` passa a ser só para distribuição. O banco vivo é o do container, e o dump deve ser gerado de novo depois
  das sincronizações, quando for preciso levar uma cópia.
- **Página inicial da wiki:** se ela mudar um sistema de grupo ou de situação (ativo/inativo), a `categoria` e a `situacao` dos
  trechos daquele sistema mudam. Nesse caso, sincronizar o sistema inteiro, não só as páginas alteradas.
