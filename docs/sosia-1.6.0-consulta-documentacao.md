# Pedido ao sos-ia — contrato 1.6.0: consulta da documentação de um sistema

Da sessão do SOS. Data: 10/11/2026. Base: contrato 1.5.4. **Só acréscimos:** nenhuma rota ou campo existente muda.

## Por quê
O SOS ganha a tela **Consultar Documentação de Sistema** (menu Chamados e menu Sustentação, para todos os perfis): o usuário
escolhe o sistema e navega pela wiki inteira, ou pesquisa por texto. Hoje só se lê uma página por vez e só durante a entrevista
(`GET /documentacao?entrevista=`). A tela precisa de três coisas que o banco já tem (`apf.conhecimento_trecho` e
`apf.conhecimento_referencia`): a lista de páginas do sistema, a página inteira com as referências resolvidas, e a busca híbrida
por sistema (`nucleo/rag/busca.py: busca()`).

Decisões do usuário (10/11): árvore **e** busca; planos de teste aparecem na árvore e ficam fora da busca por padrão; seções
revogadas aparecem riscadas (não ocultas); qualquer usuário logado consulta qualquer sistema.

## 1. Árvore do sistema — `GET /sistemas/{cd_sistema}/documentacao?sigla=GAI`
A sigla identifica a wiki como em `/menus` (`resolve_sistema`); o `cd_sistema` do SOS fica só na rota.
```json
{ "wiki": "gai", "titulo": "GAI",
  "grupos": [
    { "tipo": "inicio",       "titulo": "Sobre o sistema",      "paginas": [ { "pagina": "sistemas:gai:inicio", "titulo": "GAI", "secoes": 2, "revogada": false } ] },
    { "tipo": "caso_de_uso",  "titulo": "Casos de uso",         "paginas": [ { "pagina": "sistemas:gai:documentacao_tecnica:caso_de_uso:uc001:inicio", "titulo": "UC001 - Gerar Demonstrativo …", "secoes": 14, "revogada": false }, … ] },
    { "tipo": "user_story",   "titulo": "Histórias de usuário", "paginas": [ … ] },
    { "tipo": "regras_de_negocio", "titulo": "Regras de negócio", "paginas": [ … ] },
    { "tipo": "mensagens",    "titulo": "Mensagens",            "paginas": [ … ] },
    { "tipo": "glossario",    "titulo": "Glossário",            "paginas": [ … ] },
    { "tipo": "eventos",      "titulo": "Eventos",              "paginas": [ … ] },
    { "tipo": "documento",    "titulo": "Documentos",           "paginas": [ … ] },
    { "tipo": "plano_de_teste", "titulo": "Planos de teste",    "paginas": [ … ] },
    { "tipo": "indice",       "titulo": "Índices",              "paginas": [ … ] } ] }
```
- Grupos nessa ordem; só os que têm página. Páginas em ordem de título (UC001, UC002…; ordenação natural, para UC0035 não
  cair antes de UC004 se for simples de fazer).
- `secoes`: quantidade de seções da versão mais recente (`data_versao = max`, como em `documentacao()`), sem contar as revogadas.
- `revogada`: true quando **todas** as seções da página são revogadas (`riscado()`), para a tela mostrar em cinza.
- Sistema sem wiki → 200 com `"wiki": null` e `"grupos": []`. Sigla desconhecida também.
- O GAI tem 198 páginas; o Benner, 475. A resposta cabe numa chamada; o SOS guarda em memória da página.

## 2. Página inteira sem entrevista — `GET /documentacao?pagina=…&sigla=GAI`
Mesma rota de hoje; `entrevista` e `sigla` são **excludentes** (os dois ou nenhum → 422). Com `sigla`:
- devolve a página **inteira**: todas as seções, inclusive as revogadas, com `SecaoDocumentacao.revogada: bool` (campo novo,
  padrão false) e o `html` já riscado (`<del>`); `parcial` sempre false (sem o recorte 1.5.2 da página de regras);
- só páginas `sistemas:<wiki da sigla>:…` (mesma checagem de hoje, pela sigla em vez da entrevista); outras → 404;
- cada seção ganha `referencias` (campo novo, lista vazia por padrão): o que ela cita, resolvido em
  `conhecimento_referencia`:
  ```json
  "referencias": [ { "codigo": "RN257", "tipo_ref": "RN", "resolvida": true,
                     "pagina": "sistemas:gai:documentacao_tecnica:regras_de_negocio:inicio", "secao": "REGRAS DE NEGÓCIO > RN257. Desconto de 25% para Quitação" },
                   { "codigo": "Tela02", "tipo_ref": "Tela", "resolvida": false, "pagina": null, "secao": null } ]
  ```
- a página ganha `citada_por` (campo novo, lista vazia por padrão): quem cita esta página, como em `busca.expande()`, sem
  limite de 8 (ou até 50): `[ { "pagina": "…uc053:inicio", "titulo": "UC053 - …", "tipo": "caso_de_uso" } ]`;
- no `html`, a referência **resolvida** sai como link navegável dentro do SOS:
  `<a class="sosia-ref" data-pagina="sistemas:gai:…:regras_de_negocio:inicio" data-secao="REGRAS DE NEGÓCIO > RN257. …">RN257</a>`.
  A tag `a` entra na lista de permitidas, **só com `class` e `data-*`, sem `href`** (o SOS põe `href="#"` e o clique depois
  da limpeza). Não resolvida continua como texto no `<span class="sosia-refs">`.
- Com `entrevista=` nada muda (o `html` pode trazer os mesmos links; a tela da entrevista ignora `a`).

## 3. Busca por texto — `GET /sistemas/{cd_sistema}/documentacao/busca?sigla=GAI&q=…&n=10&testes=0`
```json
[ { "pagina": "sistemas:gai:documentacao_tecnica:regras_de_negocio:inicio", "titulo": "Regras de negócio: RN257. Desconto de 25% para Quitação",
    "tipo": "regras_de_negocio", "secao": "REGRAS DE NEGÓCIO > RN257. Desconto de 25% para Quitação",
    "trecho_html": "<p>O desconto de 25% deverá ser aplicado…</p>", "similaridade": 0.71 }, … ]
```
- `busca(conn, q, sistema=wiki, n=n, incluir_teste=(testes == 1), expandir=False, max_por_pagina=2)`; `q` de 3 a 500
  caracteres; `n` de 1 a 30 (padrão 10). Sem wiki → `[]`.
- `trecho_html`: `doc_html.para_html(texto)` do trecho, cortado em ~900 caracteres antes da conversão (como `wiki_trechos`).
  `similaridade`: a pontuação final normalizada em 0–1 (o SOS mostra em %), ou o cosseno se for mais simples.
- Páginas de regras de negócio: o título como na 1.5.2 ("Regras de negócio: RNxx. …").

## 4. Mock
`SOSIA_MOCK=1`: árvore com os 2 documentos de exemplo (grupos `caso_de_uso` e `user_story`); busca devolve os dois com
`trecho_html`; página devolve o exemplo da 1.5.4 com uma seção `revogada: true` e uma referência resolvida (`<a class="sosia-ref">`).

## 5. Versão
`versao_contrato` = **1.6.0**. O SOS só mostra a tela quando `/saude` responder ≥ 1.6.0; antes disso, avisa que o serviço
ainda não oferece a consulta. Rotas, autenticação (`X-SOSIA-Token`) e as demais respostas não mudam.

## Não muda
`/entrevistas`, `/menus`, `/documentacao?entrevista=` (resposta igual, mais os campos novos opcionais), confirmação e vínculo.
