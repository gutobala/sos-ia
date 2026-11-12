# Pedido ao sos-ia — contrato 1.7.0: ler PDF e Word anexados ao pedido

Da sessão do SOS. Data: 11/11/2026. Base: contrato 1.6.0. **Só acréscimos:** nenhuma rota ou campo existente muda.

## Por quê
Teste do usuário (11/11): o pedido foi aberto com as especificações num PDF bem detalhado e o relato curto ("segue em anexo…").
A entrevista perguntou "Você citou um anexo. Resuma aqui o que nele é o pedido…" (gatilho `conforme_anexo`) e a qualidade
ficou em 35/100. O sos-ia não leu o PDF: o SOS só manda `imagens[]` no `POST /entrevistas`. PDF, Word e planilhas ficam no
rascunho do SOS e só viram anexo do chamado na confirmação.

O caminho já existe para as imagens: no modo `local`, `pii.prepara` troca cada imagem pelo texto do OCR anonimizado, e esse
texto vai para `est["texto"]`, que alimenta tudo depois (`texto_do_usuario`, perguntas, nota, Escopo). O pedido é fazer o
mesmo com documentos.

O SOS não consegue extrair texto de PDF (ASP clássico, sem biblioteca), por isso a extração fica no sos-ia.

## 1. Entrada — `POST /entrevistas` ganha `documentos`
```json
{ "cd_sistema": 1, "sigla": "GAI", "intencao": "MUDAR_EXISTENTE", "relato": "Segue em anexo a especificação do relatório.",
  "imagens": [],
  "documentos": [ { "nome": "Especificacao relatorio.pdf", "mime": "application/pdf", "base64": "JVBERi0xLjcK…" } ] }
```
- Opcional, lista vazia por padrão. Até 3 documentos.
- `mime`: `application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document` (.docx) e `text/plain`.
  Outros tipos → 422 (o SOS só manda esses três).
- Tamanho: sugestão de até 10 MB por arquivo (base64 de até ~14.000.000 caracteres). Se preferirem outro limite, digam qual;
  o SOS filtra antes de mandar e não manda o que passar.
- Sem `documentos`, nada muda.

## 2. Extração e anonimização (mesmo tratamento das imagens)
- Texto do PDF pela camada de texto (pypdf ou similar), do DOCX pelos parágrafos e tabelas (python-docx), TXT como UTF-8 ou
  CP1252 (o SOS é Windows; arquivos .txt dos usuários costumam vir em CP1252).
- PDF digitalizado (sem camada de texto): se for simples, OCR com o easyocr de `pii._ocr`, por página e com limite de páginas;
  senão, `lido: false` com o motivo (abaixo). Fica a critério de vocês.
- Modo `local` e `teste`: `anonimizar()` no texto do documento, como no OCR das imagens. Modo `contrato`: o texto vai como veio
  (mesmo critério do relato).
- O texto entra no que vai para o LLM e em `est["texto"]`, como as imagens: `[Texto do anexo Especificacao relatorio.pdf]: …`.
  Assim ele vale também nas rodadas seguintes, na nota e no Escopo, não só na 1ª chamada.
- Teto de tamanho: sugestão de 8.000 caracteres por documento e 15.000 no total, cortando no fim (e marcando `cortado`).
  Ajustem ao que o prompt aguenta.

## 3. Gatilho `conforme_anexo`
Quando ao menos um documento foi lido com texto útil (sugestão: 300 caracteres ou mais depois de limpo), o gatilho
`conforme_anexo` não vira pergunta. Hoje ele casa com o relato ("em anexo") mesmo quando o conteúdo já está disponível.
Se nenhum documento foi lido, a pergunta continua como hoje.

## 4. Saída — `EstadoEntrevista.anexos_lidos`
```json
"anexos_lidos": [
  { "nome": "Especificacao relatorio.pdf", "lido": true,  "paginas": 12, "caracteres": 8000, "cortado": true,  "motivo": null },
  { "nome": "Tela digitalizada.pdf",       "lido": false, "paginas": 3,  "caracteres": 0,    "cortado": false, "motivo": "sem texto (PDF digitalizado)" } ]
```
- Lista vazia por padrão; vem em todas as respostas da entrevista (criar, retomar, responder), para a tela mostrar
  "Lemos: Especificacao relatorio.pdf (12 páginas)" ou "Não conseguimos ler…: resuma no texto o que nele é o pedido".
- `motivo` em texto curto para o usuário: "sem texto (PDF digitalizado)", "arquivo protegido por senha", "arquivo corrompido",
  "tipo não lido". `paginas` pode ser null para DOCX/TXT.
- Arquivo que não abre não derruba a criação: entra com `lido: false`.

## 5. Mock
Com `SOSIA_MOCK=1`: documento cujo nome tem "digitalizado" volta `lido: false`; os demais `lido: true` com `paginas: 1` e
`caracteres` do base64 decodificado, e o gatilho `conforme_anexo` deixa de ser perguntado. Basta para a tela.

## O que o SOS faz quando a 1.7.0 estiver no ar
- Já feito (11/11): aviso no bloco de anexos da abertura. Com o serviço abaixo de 1.7.0: "PDF, Word e planilhas vão para a
  equipe com o chamado, mas ainda não são lidos pela análise automática". Com 1.7.0 ou mais: "PDF e Word anexados antes de
  Analisar pedido são lidos pela análise. Planilhas, outros arquivos e o que for anexado depois vão só para a equipe".
- `documentos` no `POST /entrevistas` só quando `/saude` responder `versao_contrato` ≥ 1.7.0 (o serviço recusa campo
  desconhecido com 422), com pdf/docx/txt do rascunho dentro dos limites.
- Mostrar `anexos_lidos` nas perguntas e no Escopo do Pedido.
- `.doc` (Word antigo), `.odt`, planilhas e apresentações continuam só como anexo do chamado. Se quiserem ler algum deles
  também (xlsx/csv, por exemplo), digam e o SOS passa a mandar.
