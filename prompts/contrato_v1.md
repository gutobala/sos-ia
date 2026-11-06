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
5. `fora_escopo`: o que o usuário disse que NÃO faz parte (lista vazia se ele disse "nada" ou não disse).
6. `integracoes`: cada outro sistema citado, com `sentido` BUSCA (só consulta dados dele), GRAVA (envia ou grava dados nele) ou
   AMBOS, e uma descrição curta. Lista vazia se o usuário disse que não há.
7. `duvidas`: o que ficou em aberto e a equipe vai precisar confirmar (itens vagos, respostas contraditórias, gatilhos sem resposta
   clara). Lista vazia se nada ficou em aberto.

## Saída

Somente o JSON do esquema.
