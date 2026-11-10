# CONTRATO DO CHAMADO — SOS / TERRACAP (v1)

A entrevista de abertura de um chamado do sistema **{{SISTEMA}}** terminou. Escreva o contrato do pedido: o texto que o usuário vai
revisar, editar e confirmar, e que a equipe de sistemas vai usar para fazer e testar o trabalho.

RELATO ORIGINAL (dados pessoais já retirados):
{{RELATO}}

SEGMENTOS E ITENS COLETADOS NA ENTREVISTA:
{{SEGMENTOS}}

RESPOSTAS ÀS PERGUNTAS DE ATENÇÃO (gatilhos):
{{GATILHOS}}

REGRA DOCUMENTADA MOSTRADA AO USUÁRIO (se houver):
{{REGRA}}

## Regras

1. Use só o que está acima. Não invente telas, campos, regras, valores, sistemas nem prazos. Linguagem simples, sem jargão de TI e
   sem pontos de função.
2. `titulo`: até 100 caracteres, começando pelo que muda ou pelo problema (ex.: "Erro ao salvar o contrato de alienação").
3. `objetivo`: 1 a 3 frases com o porquê do pedido (o problema que resolve, quem ganha).
4. `requisitos`: numerados, **um requisito = uma mudança ou um defeito** que pode ser testado sozinho. Divida quando o usuário juntou
   telas, relatórios, formatos ou operações diferentes. Cada requisito tem:
   - `segmento`: o número do segmento de onde veio;
   - `tipo`: CORRETIVO se veio de segmento CORRETIVO; EVOLUTIVO nos demais;
   - `descricao`: o que deve ser feito, na forma "O sistema deve ..." (corretivo: "O sistema deve ... sem apresentar o erro ...");
   - `criterio_aceite`: como o usuário vai conferir que ficou pronto, de preferência com o exemplo dado por ele (ex.: "Ao salvar o
     contrato 123 com a data 10/05, a mensagem X não aparece e o contrato fica gravado.").
5. `fora_escopo`: sempre lista vazia (o que fica fora do pedido é definido pela Sustentação).
6. `integracoes`: sempre lista vazia (a Sustentação confirma os outros sistemas; os citados pelo usuário já entram nas dúvidas
   automaticamente).
7. `duvidas`: o que ficou em aberto e a equipe vai precisar confirmar (itens vagos, respostas contraditórias, gatilhos sem resposta
   clara). Lista vazia se nada ficou em aberto. Itens com o valor "Não informado (o usuário não soube responder)" já entram nas
   dúvidas automaticamente: não repita.
8. `origem` de cada requisito e de cada integração — diga de onde ele veio, com honestidade:
   - `usuario`: o usuário pediu isso, com estas ou outras palavras, no relato ou numa resposta;
   - `documentacao`: vem da regra documentada mostrada acima (por exemplo, o usuário quer mudar o que ela diz);
   - `inferido`: é uma hipótese sua sobre como resolver ou sobre o que mais seria afetado, que o usuário NÃO disse (ex.: "criar uma
     nova rubrica", "alterar a rotina de fechamento", "incluir também no relatório X"). O usuário vai ver o item destacado e
     confirmar ou remover. Prefira não incluir hipóteses; quando incluir, marque `inferido`. Na dúvida entre `usuario` e
     `inferido`, use `inferido`.

## Saída

Somente o JSON do esquema.
