# Para o laboratório: wiki de cada sistema (commit 22d3b8c)

Data: 11/11/2026. O que mudou no sos-ia e o que fazer depois do pull.

## O que mudou

### A wiki de cada sistema
Antes, um sistema via só a wiki com o nome da sigla. Isso deixava documentação de fora: a busca do GLP por "caução" não achava
nenhum caso de uso, porque usava a wiki `glp` (5 páginas do sistema antigo) e não a `glpv2` (295 páginas, em `sistemas:glpweb:`).

Agora a documentação de um sistema é montada em duas camadas:
- **Ligação automática:** as wikis cujo nome, sem pontuação, é a sigla oficial ou um alias do sistema (`apf.sistema_alias`).
  O GLP passa a ver a `glpv2`, o E-SOCIAL vê a `esocial` e a `esocial_v2`, e o mesmo vale para GAD, GPE e outros. A maior wiki
  é a principal.
- **`config/wiki_sistemas.yaml`:** as ligações que o nome não resolve, todas **decididas pelo usuário**. Não as altere sem ele.

| Sistema do SOS | Documentação |
|---|---|
| GSO e GSOV2 | `portal_do_cliente` (front-end do cliente externo), `gsov2` (backend) e `portal_interno` (interface dos empregados) |
| GESTAO-RISCO | `gr` |
| GPAV2 | `gpa2` |
| GAVV2 | `novogav` |
| GAI-WEB (CD_SISTEMA 803) | só as UC001–UC003 da `gai-web`, mais as mensagens e regras que elas citam |
| GFA (CD_SISTEMA 10006, em desenvolvimento) | o resto da `gai-web`, da UC004 em diante |
| nenhum | `gso` (documentação inativa), `gfov3` e `gecv2` (versões descontinuadas) e `gacv2` |

A wiki `gai-web` mistura dois sistemas: o GAI-WEB, em produção (PHP 5.2, um módulo web do GAI), e o GFA, substituto do GAI
Forms. A entrada do yaml é achada pelo `cd` (o CD_SISTEMA do SOS) e, quando não há código, pela sigla.

### `motor.resolve_sistema` mudou de formato
- A assinatura é `resolve_sistema(conn, sigla, cd_sistema=None)`. Ela devolve `(sid, Escopo | None)`, e não mais a chave da
  wiki em texto.
- O `Escopo` (`nucleo/rag/escopo.py`) tem:
  - `inteiras`: as wikis inteiras do sistema;
  - `parciais`: lista de (wiki, regex da página, incluir);
  - `citados`: ids dos trechos que as páginas parciais citam;
  - `filtro(prefixo, por_dono)`, que monta o SQL;
  - `principal`, o nome da wiki principal;
  - `para_dict()` e `Escopo.de(x)`, que gravam e leem o escopo no estado da entrevista.
- `busca.busca`, `busca_requisito`, `busca_chamado`, `pagina_da_wiki` e `arvore_documentacao` aceitam texto, lista ou Escopo.
  Chamadas com `"gai"` continuam funcionando.
- **Se algum script seu usa `resolve_sistema(...)[1]` como texto** (por exemplo, em `f"sistemas:{wiki}:"`), troque por
  `esc.principal` ou `esc.inteiras`.

### Outras mudanças
- `documentacao(entrevista=)` usa a mesma regra de página da consulta por sigla (`pagina_da_wiki`). Antes, a página precisava
  estar em `sistemas:<wiki>:`, e as páginas do GLPv2 (em `sistemas:glpweb:`) davam 404.
- **Alias:** SERVICOS ONLINE passou do GAC-GEATE para o GSO (`migracao/sql/06_alias_servicos_online.sql`). Ele tinha sido criado
  na etapa 2 do PRE-PROJETO, provavelmente porque a área gestora do GSO é a GEATE. A base de contagens não muda: a única contagem
  com esse nome continua no GAC-GEATE.
- O contrato da API continua **1.6.0**, porque nenhum formato mudou. A seção "Ligação wiki ↔ sistema" do README descreve tudo.

## Passos depois do pull

1. **Migração do alias.** Aplique o `06` no banco do laboratório; o arquivo é idempotente. Se o `05_wiki_sync.sql` (da 1.6.0)
   ainda não foi aplicado aí, aplique-o antes, do mesmo jeito.
   ```
   docker cp migracao/sql/06_alias_servicos_online.sql sosia_db:/tmp/06.sql
   docker exec sosia_db psql -U sosia -d sosia -v ON_ERROR_STOP=1 -f /tmp/06.sql
   ```
2. **Rebuild da api:** `docker compose up -d --build api`. O yaml entra na imagem (`COPY config`), então qualquer mudança nele
   também exige rebuild.
3. **Testes:**
   - os novos são `test_vinculos_do_yaml` e `test_ligacoes_do_yaml_no_banco`;
   - o segundo lê o banco e depende de as wikis `gai-web`, `gr`, `portal_do_cliente`, `gsov2`, `portal_interno`, `gpa2` e
     `novogav` existirem com esses nomes no banco do laboratório;
   - na máquina do produto, em 11/11, passaram 128 testes, mais os 2 que usam a chave de embeddings.

## Atenção

- **Medições:** os números de recall já medidos (`calibra_wiki` de 05/11, `mede_ponto_partida`) usavam a ligação antiga. Para
  GLP, E-SOCIAL, GAD, GPE, GSO, GSOV2, GESTAO-RISCO, GPAV2 e GAVV2, a busca agora cobre outras páginas. Medições novas desses
  sistemas não são comparáveis com as antigas.
- **Nomes das wikis:** se o laboratório regerar o banco da wiki por conta própria e o nome de alguma wiki mudar, o yaml deixa de
  casar com ela. Ajuste o yaml junto com o usuário.
- **Sincronização:** se a máquina do laboratório não alcança a terrawiki (rede da Terracap), deixe `SOSIA_WIKI_SYNC_HORA=0` no
  `.env` de lá. Sem isso, a sincronização das 03:00 falha todo dia. A falha não grava nada, mas suja o log.
- **Menus de fonte wiki:** os menus montados a partir da wiki (`nucleo/menus/catalogo.py`) não seguem o yaml, e isso não é
  pendência. O SOS só pede menus ao sos-ia para os sistemas Forms (pelos `.mmb`). Os sistemas PHP usam o banco do próprio SOS,
  e os demais, como o GSO, usam um campo de texto livre para o ponto de partida.
- **Busca da documentação:** os resultados "repetidos" (a mesma página duas vezes) são duas seções diferentes da página
  (`max_por_pagina=2`, do pedido 1.6.0). A correção pedida é na tela do SOS: agrupar por (`pagina`, `titulo`) e mostrar a
  `secao`.
