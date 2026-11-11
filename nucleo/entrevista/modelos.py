"""Contrato da API da entrevista de abertura (Fase 1). Fonte única dos JSON Schemas em contrato_api/ (gera_schemas.py).

Textos em português, UTF-8. O SOS (proxy entrevistaApi.asp) nunca envia o nome do solicitante.
"""
import datetime
from enum import Enum
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSAO_CONTRATO = "1.6.0"
# 1.1.0 (05/11/2026, aditiva): decisão do usuário sobre a regra documentada — POST /entrevistas/{id}/regra, campo
#        `decisao_regra` no estado e no contrato, `regra_documentada` no contrato, slot pseudo "decisao_regra" em `faltando`.
# 1.2.0 (05/11/2026, aditiva): confirmação do Escopo do Pedido — POST /entrevistas/{id}/confirmacao (um chamado por tipo, no
#        máximo 2), POST /entrevistas/{id}/confirmacao/chamados (vínculo com os CD_SOLICITACAO, idempotente),
#        GET /solicitacoes/{cd}/escopo, GET /confirmacoes/pendentes; `banco_app` no /saude. Respostas, regra e contrato
#        numa entrevista CONFIRMADA → 409.
# 1.3.0 (06/11/2026, aditiva): rodada de complemento — depois das 2 rodadas, se ainda faltar item obrigatório, a entrevista não
#        se encerra: devolve só as perguntas fixas do que falta (sem IA, sem limite de rodadas). Campo `complemento` no estado.
# 1.3.1 (06/11/2026, correção, sem campo novo): `GrupoChamado.texto` ganha a seção INFORMAÇÕES DO PEDIDO (respostas da
#        entrevista); o critério de aceite do roteiro fixo deixa de ser o "como reproduzir" (roteiros.criterio_fixo).
# 1.3.2 (06/11/2026, correção, sem campo novo): concordância da frase que aponta o outro chamado ("O requisito 2 … está" /
#        "Os requisitos 2 e 3 … estão").
# 1.4.0 (08/11/2026, aditiva): abertura guiada — "Não sei" aceito em itens não bloqueantes (vira NAO_INFORMADO, dúvida no
#        contrato, nota menor); `Pergunta.opcoes` (respostas rápidas); `origem` (usuario | documentacao | inferido) em
#        `RequisitoContrato`, `Integracao` e `RequisitoEscopo`, com `RequisitoEditado.origem/n_contrato` e o evento `inferidos`;
#        `CandidatoFuncionalidade.id`, `EscopoEditado.candidatos_confirmados` e `EscopoConfirmado.funcionalidades`.
# 1.4.1 (08/11/2026, correção, sem campo novo; pedido 1.3.3 do teste com usuário): gatilhos com palavras curtas só com contexto
#        (SEI/API maiúsculas, Caixa Econômica, sem "enviar"/"retorno"); gatilho de integração não repete o item "outros
#        sistemas" nos evolutivos; item repetido cita a resposta anterior, com pergunta/dica por roteiro; número de chamado
#        reaproveitado vai para o histórico; aquecimento da busca e evento "tempos" na criação.
# 1.5.0 (08/11/2026, aditiva; pedido docs/sosia-1.5.0-menu-e-documentacao.md): GET /sistemas/{cd}/menus (catálogo de menus:
#        FORMS pelo .mmb, wiki como reserva); `NovaEntrevista.ponto_partida` (por onde o usuário entra no sistema: entra na busca
#        e preenche onde_ocorre/funcionalidade); `EstadoEntrevista.documentos` + GET /documentacao (casos de uso achados pelo
#        RAG, para ler no SOS); `EscopoEditado.documentos_lidos`. As caixas de funcionalidades saem da tela do usuário:
#        `candidatos_funcionalidade` / `candidatos_confirmados` ficam no contrato para a Sustentação.
# 1.5.1 (09/11/2026, aditiva; pedido docs/sosia-1.5.1-pontos-de-partida.md): `NovaEntrevista.pontos_partida` (até 5; o
#        `ponto_partida` da 1.5.0 vale como lista de 1): uma busca por ponto, `Documento.ponto`, `Segmento.ponto`, `Pergunta.ponto`;
#        "o que muda" perguntado por ponto, com opções do tipo de mudança montadas pela aplicação (`Pergunta.opcoes` até 7).
# 1.5.2 (10/11/2026, aditiva; teste do SOS): caminhos de pontos diferentes não se juntam mais na pergunta da IA ("A > B" vira
#        "A; B"); página de regras de negócio: na lista, título com a regra que casou e depois dos UC/US do ponto; no
#        GET /documentacao, só essa regra (+ mãe e sub-regras), com `Documentacao.parcial` e `secoes_total`.
# 1.5.3 (10/11/2026, sem mudança de esquema): "o que fica fora do escopo" e "outros sistemas" saem dos roteiros evolutivos e o
#        gatilho de integração não é mais perguntado: a Sustentação responde. O sistema citado vira dúvida no contrato
#        ("Para a Sustentação: …"); `fora_escopo` e `integracoes` do contrato vêm vazios (campos mantidos).
# 1.5.4 (10/11/2026, aditiva): `SecaoDocumentacao.html` — o texto da wiki pronto para mostrar (tabelas, listas, riscado), HTML
#        seguro gerado pelo sos-ia (`doc_html.para_html`). `texto` continua igual.
# 1.5.5 (11/11/2026, aditiva; pedido docs/pedido-sincronizacao-wiki.md): `Saude.wiki_atualizada_em` — última sincronização da
#        wiki aplicada com sucesso (o RAG passa a se atualizar sozinho, todo dia, pela API da wiki).
# 1.6.0 (11/11/2026, aditiva; pedido docs/sosia-1.6.0-consulta-documentacao.md): consulta da documentação de um sistema, fora
#        da entrevista — GET /sistemas/{cd}/documentacao (árvore), GET /documentacao?pagina=…&sigla= (página inteira, com as
#        revogadas), GET /sistemas/{cd}/documentacao/busca (busca híbrida). Campos novos: SecaoDocumentacao.revogada e
#        .referencias, Documentacao.citada_por; no html, referência resolvida vira <a class="sosia-ref" data-*>.


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Intencao(str, Enum):
    """Opção do cardápio escolhida pelo usuário (a garantia não passa pela IA)."""
    PAROU_DE_FUNCIONAR = "PAROU_DE_FUNCIONAR"   # "Algo parou de funcionar"
    MUDAR_EXISTENTE = "MUDAR_EXISTENTE"         # "Quero mudar algo que já existe"
    ALGO_NOVO = "ALGO_NOVO"                     # "Preciso de algo novo"


class TipoSegmento(str, Enum):
    CORRETIVO = "CORRETIVO"
    EVOLUTIVO_EXISTENTE = "EVOLUTIVO_EXISTENTE"
    EVOLUTIVO_NOVO = "EVOLUTIVO_NOVO"


class TipoRequisito(str, Enum):
    """Define o desmembramento: um chamado por tipo ao confirmar."""
    CORRETIVO = "CORRETIVO"
    EVOLUTIVO = "EVOLUTIVO"


class Imagem(Base):
    nome: str = Field(max_length=200)
    mime: str = Field(pattern=r"^image/(png|jpeg|gif|bmp|webp)$")
    base64: str = Field(description="conteúdo da imagem em base64 (sem o prefixo data:)", max_length=7_000_000)


class FonteMenu(str, Enum):
    """De onde veio o catálogo de menus de um sistema (1.5.0)."""
    FORMS = "forms"                  # .mmb do Oracle Forms (hierarquia completa)
    BENNER = "benner"
    HOPE = "hope"
    AGENDAMENTO = "agendamento"
    WIKI = "wiki"                    # caminhos citados na wiki: só folhas, com o caminho completo
    NENHUMA = "nenhuma"              # nada no catálogo: o SOS mostra um campo livre


class MenuItem(Base):
    id: str = Field(description="identificador estável do item dentro do sistema (é o que vai em PontoPartida.menu_id)")
    pai: Optional[str] = Field(default=None, description="id do pai; nulo na raiz (na fonte wiki, sempre nulo)")
    nome: str = Field(description="rótulo como o usuário vê")
    folha: bool = Field(description="true = abre uma tela; false = só agrupa outros itens")
    caminho: str = Field(description='"Pai > Filho > Item", sem a sigla do sistema')
    detalhe: Optional[str] = None
    modulo: Optional[str] = Field(default=None, description="módulo (Benner); nulo nos demais")


class Menus(Base):
    """Saída de GET /sistemas/{cd_sistema}/menus. Itens na ordem de leitura (cada um seguido dos filhos)."""
    fonte: FonteMenu
    modulos: list[str] = Field(default_factory=list, description="módulos do Benner; vazia nos demais")
    menus: list[MenuItem] = Field(default_factory=list)


class FontePontoPartida(str, Enum):
    FGCA = "fgca"                    # menu dos sistemas PHP, montado pelo SOS a partir do FGCA
    LIVRE = "livre"                  # o usuário digitou
    FORMS = "forms"
    BENNER = "benner"
    HOPE = "hope"
    AGENDAMENTO = "agendamento"
    WIKI = "wiki"


class PontoPartida(Base):
    """Por onde o usuário entra no sistema (1.5.0). Entra na busca do RAG junto com o relato e preenche `onde_ocorre`
    (corretivo) ou `funcionalidade` (evolutivo existente), que deixam de ser perguntados; em "algo novo" é "onde ficaria"."""
    caminho: str = Field(min_length=2, max_length=300, description='"Cadastro > NUGIV > Parcelamento Resíduo" ou o texto livre')
    detalhe: Optional[str] = Field(default=None, max_length=500)
    menu_id: Optional[str] = Field(default=None, max_length=300, description="MenuItem.id (nulo em fonte livre)")
    fonte: FontePontoPartida


class NovaEntrevista(Base):
    cd_sistema: int = Field(description="CD_SISTEMA do SOS")
    sigla: str = Field(max_length=40, description="sigla do sistema (ex.: GAI)")
    intencao: Intencao
    relato: str = Field(min_length=10, max_length=20000, description="relato livre do usuário")
    imagens: list[Imagem] = Field(default_factory=list, max_length=5)
    ponto_partida: Optional[PontoPartida] = Field(default=None, description="1.5.0: com ele, não ponha mais 'Menu: …' no relato. "
                                                                            "1.5.1: prefira pontos_partida; este vale como lista de 1")
    pontos_partida: list[PontoPartida] = Field(default_factory=list, max_length=5,
                                               description="1.5.1: as telas por onde o usuário entra (até 5, na ordem em que ele "
                                                           "escolheu); uma busca por ponto. Não mande junto com ponto_partida")

    @model_validator(mode="after")
    def _um_formato_de_ponto(self):
        if self.ponto_partida and self.pontos_partida:
            raise ValueError("mande ponto_partida (1.5.0) ou pontos_partida (1.5.1), não os dois")
        return self

    def pontos(self) -> list[PontoPartida]:
        """Os pontos de partida como lista (o ponto_partida antigo vira lista de 1)."""
        return list(self.pontos_partida) or ([self.ponto_partida] if self.ponto_partida else [])


class Slot(Base):
    codigo: str
    rotulo: str
    obrigatorio: bool
    valor: Optional[str] = None
    preenchido: bool = Field(description="valor presente e com o mínimo exigido pelo roteiro (validação determinística)")


class Segmento(Base):
    n: int = Field(ge=1)
    tipo: TipoSegmento
    resumo: str
    slots: list[Slot]
    ponto: Optional[int] = Field(default=None, ge=1, description="1.5.1: número do ponto de partida (1 = o primeiro de "
                                                                  "pontos_partida) a que o segmento se refere; nulo com 0 ou 1 ponto, "
                                                                  "ou quando o segmento vale para todos")


class Origem(str, Enum):
    """De onde veio um item do Escopo do Pedido (1.4.0). Só `usuario` é pedido dele; `inferido` é hipótese da análise, que o
    usuário confirma ou remove na tela, e que o estimador mostra à parte (não infla a contagem)."""
    USUARIO = "usuario"              # está no relato ou numa resposta
    DOCUMENTACAO = "documentacao"    # veio da regra documentada / wiki citada
    INFERIDO = "inferido"            # hipótese de solução (ex.: "criar uma rubrica")


class Documento(Base):
    """Página da wiki que o RAG achou para o pedido (1.5.0); o texto vem de GET /documentacao."""
    pagina: str = Field(description="id da página (sistemas:…); é o que vai em GET /documentacao?pagina= e em documentos_lidos")
    titulo: str
    tipo: str = Field(description="caso_de_uso, user_story ou regras_de_negocio")
    secao: Optional[str] = Field(default=None, description="seção da página que mais se parece com o pedido")
    similaridade: float
    ponto: Optional[int] = Field(default=None, ge=1, description="1.5.1: número do ponto de partida cuja busca trouxe a página "
                                                                  "(1 = o primeiro); nulo sem ponto de partida")


class ReferenciaSecao(Base):
    """1.6.0: código citado numa seção (RN257, FE01, Tela02…), resolvido pelo código na wiki do sistema."""
    codigo: str
    tipo_ref: str = Field(description="RN, MN, FE, FA, Tela, RF ou RNF")
    resolvida: bool
    pagina: Optional[str] = Field(default=None, description="página onde o código está definido (nula se não resolvida)")
    secao: Optional[str] = Field(default=None, description="seção onde o código está definido (nula se não resolvida)")


class SecaoDocumentacao(Base):
    secao: str
    texto: str
    html: str = Field(default="", description="1.5.4: o texto pronto para mostrar (tabelas, listas, riscado, referências), HTML "
                                              "seguro gerado pelo sos-ia: todo o texto da wiki vem escapado; só tags p, br, table, "
                                              "thead, tbody, tr, th, td, ul, ol, li, del, strong, span, div e a, com classes sosia-*. "
                                              "1.6.0: a referência resolvida sai como <a class=\"sosia-ref\" data-pagina=… "
                                              "data-secao=…>, sem href (o SOS põe o href e o clique)")
    revogada: bool = Field(default=False, description="1.6.0: seção revogada na wiki (riscada); só vem na consulta por sigla, já "
                                                      "com o html riscado")
    referencias: list[ReferenciaSecao] = Field(default_factory=list, description="1.6.0: o que a seção cita, uma vez por código")


class PaginaCitante(Base):
    """1.6.0: página que cita alguma seção desta página."""
    pagina: str
    titulo: str
    tipo: str


class Documentacao(Base):
    """Saída de GET /documentacao: a página inteira, seção por seção, na ordem da wiki. Com `entrevista` (1.5.0), sem as seções
    revogadas; 1.5.2: na página de regras de negócio, só as regras que a busca trouxe nesta entrevista (com a regra-mãe e as
    sub-regras). Com `sigla` (1.6.0), a página inteira, inclusive as revogadas (`revogada: true`)."""
    pagina: str
    titulo: str
    tipo: str
    secoes: list[SecaoDocumentacao]
    parcial: bool = Field(default=False, description="1.5.2: true = só parte das seções (a regra que casou); a página tem "
                                                     "secoes_total seções")
    secoes_total: Optional[int] = Field(default=None, description="1.5.2: quantas seções a página tem (sem as revogadas)")
    citada_por: list[PaginaCitante] = Field(default_factory=list, description="1.6.0: páginas que citam esta (até 50, por título)")


class PaginaArvore(Base):
    pagina: str = Field(description="id da página (sistemas:…); é o que vai em GET /documentacao?pagina=…&sigla=")
    titulo: str
    secoes: int = Field(description="seções da página, sem as revogadas")
    revogada: bool = Field(description="true quando todas as seções são revogadas (a tela mostra em cinza)")


class GrupoArvore(Base):
    tipo: str = Field(description="inicio, caso_de_uso, user_story, regras_de_negocio, mensagens, glossario, eventos, documento, "
                                  "plano_de_teste ou indice")
    titulo: str
    paginas: list[PaginaArvore]


class ArvoreDocumentacao(Base):
    """1.6.0: saída de GET /sistemas/{cd_sistema}/documentacao. Sistema sem wiki (ou sigla desconhecida): wiki nula e grupos []."""
    wiki: Optional[str] = Field(default=None, description="chave da wiki do sistema (ex.: gai)")
    titulo: Optional[str] = None
    grupos: list[GrupoArvore] = Field(default_factory=list, description="na ordem fixa dos tipos; só os que têm página")


class ResultadoBuscaDocumentacao(Base):
    """1.6.0: item de GET /sistemas/{cd_sistema}/documentacao/busca."""
    pagina: str
    titulo: str = Field(description="título da página; na página de regras, \"Regras de negócio: RNxx. …\" (como na 1.5.2)")
    tipo: str
    secao: Optional[str] = None
    trecho_html: str = Field(description="o trecho que casou, em HTML seguro (como SecaoDocumentacao.html), até ~900 caracteres")
    similaridade: float = Field(ge=0, le=1, description="cosseno entre a pesquisa e o trecho (0 quando só a palavra casou)")


class CandidatoFuncionalidade(Base):
    """Função do catálogo de PF parecida com o pedido. 1.5.0: a tela do usuário não mostra mais (os nomes são os das contagens,
    não os da tela); fica no contrato para a Sustentação."""
    id: Optional[int] = Field(default=None, description="id da função no catálogo; é o que vai em EscopoEditado.candidatos_confirmados")
    nome: str
    tipo_ifpug: Optional[str] = None
    pagina_wiki: Optional[str] = None
    trecho: Optional[str] = None
    similaridade: float


class RegraDocumentada(Base):
    pagina: str
    secao: Optional[str] = None
    trecho: str
    similaridade: float
    orientacao: str = Field(description="texto para o usuário: o comportamento parece seguir esta regra; se quiser mudá-la, é evolutivo")


class DecisaoRegra(str, Enum):
    """Escolha do usuário diante da regra documentada (o segmento fica CORRETIVO até ele decidir)."""
    ACEITA = "ACEITA"       # "o sistema segue a regra; quero mudar a regra" → o segmento vira EVOLUTIVO_EXISTENTE
    RECUSADA = "RECUSADA"   # "não é essa regra / é defeito mesmo" → continua CORRETIVO; o comentário vai para as dúvidas


class DecisaoRegraDocumentada(Base):
    """Entrada de POST /entrevistas/{id}/regra."""
    aceita: bool = Field(description="true = o comportamento segue a regra e o usuário quer mudá-la (vira evolutivo); "
                                     "false = o usuário mantém que é defeito")
    comentario: Optional[str] = Field(default=None, max_length=2000, description="por que discorda, ou o que quer mudar na regra")


class Pergunta(Base):
    id: str
    segmento: int
    slot: str = Field(description="código do slot que a resposta preenche ('gatilho' quando é pergunta de complexidade oculta)")
    texto: str
    dica: Optional[str] = None
    opcoes: list[str] = Field(default_factory=list, max_length=7,
                              description="respostas rápidas (botões); o texto livre continua valendo e a opção escolhida vai em "
                                          "Resposta.texto. \"Não sei informar\" vem nos itens que aceitam essa resposta e, na "
                                          "1.5.1, na pergunta \"o que muda\" de um ponto de partida (até 6 tipos de mudança + ele)")
    ponto: Optional[int] = Field(default=None, ge=1, description="1.5.1: número do ponto de partida a que a pergunta se refere "
                                                                  "(com 2 ou mais pontos); nulo nas demais")


class EstadoEntrevista(Base):
    """Resposta de POST /entrevistas, POST /entrevistas/{id}/respostas e GET /entrevistas/{id}."""
    id: str
    versao_contrato: str = VERSAO_CONTRATO
    rodada: int = Field(ge=1, le=3, description="rodada atual de perguntas (máx. 2; 3 = encerrada ou em complemento)")
    pronto: bool = Field(description="true: não há mais perguntas; chamar POST /contrato")
    complemento: bool = Field(default=False, description="true: as perguntas são só dos itens obrigatórios que ainda faltam, "
                                                         "depois das 2 rodadas (roteiro fixo, sem IA)")
    segmentos: list[Segmento]
    candidatos_funcionalidade: list[CandidatoFuncionalidade] = Field(default_factory=list)
    documentos: list[Documento] = Field(default_factory=list, max_length=25,
                                        description="1.5.0: casos de uso, user stories e regras achados pelo RAG (até 5, uma vez por "
                                                    "página, sem os revogados); vazia quando o sistema não tem wiki. 1.5.1: até 5 por "
                                                    "ponto de partida, agrupados pelo `ponto`, sem repetir página")
    regra_documentada: Optional[RegraDocumentada] = None
    decisao_regra: Optional[DecisaoRegra] = Field(default=None, description="null com regra_documentada presente = o SOS deve "
                                                  "perguntar ao usuário e chamar POST /entrevistas/{id}/regra")
    perguntas: list[Pergunta] = Field(default_factory=list)
    nota: int = Field(ge=0, le=100, description="medidor de qualidade do pedido")
    dicas_medidor: list[str] = Field(default_factory=list)
    analise_ia: bool = Field(description="false quando a resposta veio do roteiro fixo (sem IA)")


class Resposta(Base):
    id: str = Field(description="id da pergunta respondida")
    texto: str = Field(max_length=5000)


class Respostas(Base):
    respostas: list[Resposta] = Field(min_length=1, max_length=10)


class RequisitoContrato(Base):
    n: int = Field(ge=1)
    tipo: TipoRequisito
    segmento: int
    descricao: str
    criterio_aceite: str
    origem: Origem = Field(default=Origem.USUARIO, description="inferido = hipótese da análise: mostrar destacado para o usuário "
                                                              "confirmar ou remover")


class Integracao(Base):
    sistema: str
    sentido: str = Field(pattern=r"^(BUSCA|GRAVA|AMBOS)$")
    descricao: str
    origem: Origem = Origem.USUARIO


class SlotFaltando(Base):
    segmento: int
    slot: str
    rotulo: str


class Contrato(Base):
    """Resposta de POST /entrevistas/{id}/contrato. O usuário edita no SOS; confirmarAbertura.asp revalida os slots."""
    id: str
    versao_contrato: str = VERSAO_CONTRATO
    titulo: str = Field(max_length=200)
    objetivo: str
    requisitos: list[RequisitoContrato]
    fora_escopo: list[str]
    duvidas: list[str]
    integracoes: list[Integracao]
    segmentos: list[Segmento]
    regra_documentada: Optional[RegraDocumentada] = Field(default=None, description="trecho a anexar ao chamado quando aceita")
    decisao_regra: Optional[DecisaoRegra] = None
    nota: int = Field(ge=0, le=100)
    faltando: list[SlotFaltando] = Field(description="inclui o slot 'decisao_regra' enquanto a regra documentada estiver sem decisão")
    pode_confirmar: bool = Field(description="false enquanto houver slot obrigatório faltando")
    analise_ia: bool


class Saude(Base):
    ok: bool
    versao: str
    versao_contrato: str = VERSAO_CONTRATO
    mock: bool
    llm: bool
    banco: bool
    banco_app: bool = Field(description="banco da aplicação (entrevistas e Escopo do Pedido confirmado)")
    embeddings: str
    modo_pii: str
    wiki_atualizada_em: Optional[datetime.datetime] = Field(
        default=None, description="1.5.5: última sincronização da wiki aplicada com sucesso (nula no mock ou antes da 1ª)")


class Erro(Base):
    erro: str
    detalhe: Optional[str] = None


# ---------------------------------------------------------------- confirmação (1.2.0)
class Natureza(str, Enum):
    """De onde vem o requisito; separa as seções do texto e orienta a contagem (função alterada × incluída)."""
    CORRECAO = "CORRECAO"     # segmento CORRETIVO
    ALTERACAO = "ALTERACAO"   # segmento EVOLUTIVO_EXISTENTE
    NOVO = "NOVO"             # segmento EVOLUTIVO_NOVO


class RequisitoEditado(Base):
    segmento: int = Field(ge=1, description="segmento de origem (Contrato.segmentos[].n); define o tipo e a natureza")
    descricao: str = Field(min_length=1, max_length=2000)
    criterio_aceite: str = Field(default="", max_length=2000)
    origem: Origem = Field(default=Origem.USUARIO, description="a do requisito do contrato de onde veio (o usuário pode ter editado)")
    n_contrato: Optional[int] = Field(default=None, ge=1, description="Contrato.requisitos[].n de onde veio; nulo = criado pelo "
                                                                      "usuário na tela. Requisito inferido do contrato que não volta "
                                                                      "aqui foi removido pelo usuário")


class EscopoEditado(Base):
    """Entrada de POST /entrevistas/{id}/confirmacao: o Escopo do Pedido como o usuário confirmou na tela.
    O tipo de cada requisito NÃO vem daqui: é sempre o do segmento de origem."""
    titulo: str = Field(min_length=1, max_length=200)
    objetivo: str = Field(min_length=1, max_length=4000)
    requisitos: list[RequisitoEditado] = Field(min_length=1, max_length=30)
    fora_escopo: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=30)
    duvidas: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=30)
    integracoes: list[Integracao] = Field(default_factory=list, max_length=20)
    cd_usuario: Optional[int] = Field(default=None, description="session(\"CD_USUARIO\") do SOS, para auditoria (nunca o nome)")
    candidatos_confirmados: list[int] = Field(default_factory=list, max_length=5,
                                              description="ids (CandidatoFuncionalidade.id) das funcionalidades que o usuário "
                                                          "marcou como afetadas; só ids mostrados nesta entrevista (1.5.0: a "
                                                          "tela do usuário não manda mais; fica para a Sustentação)")
    documentos_lidos: list[Annotated[str, Field(max_length=300)]] = Field(
        default_factory=list, max_length=10, description="1.5.0: Documento.pagina das páginas que o usuário abriu ou marcou como "
                                                         "'é isto que eu uso'; só páginas de EstadoEntrevista.documentos")


class RequisitoEscopo(Base):
    n: int = Field(ge=1, description="número no Escopo do Pedido inteiro (o mesmo nos dois chamados)")
    tipo: TipoRequisito
    natureza: Natureza
    segmento: int
    descricao: str
    criterio_aceite: str
    origem: Origem = Origem.USUARIO


class GrupoChamado(Base):
    """Um chamado a criar no SOS."""
    tipo: TipoRequisito
    titulo: str = Field(max_length=200, description="para DS_RESUMO; com 2 grupos, termina em (correção) ou (evolução)")
    texto: str = Field(description="Escopo do Pedido do grupo em texto corrido, para DS_SOLICITACAO (o SOS corta em bytes)")
    requisitos: list[RequisitoEscopo]


class EscopoConfirmado(Base):
    """Saída de POST /entrevistas/{id}/confirmacao. A entrevista fica PENDENTE até POST /confirmacao/chamados."""
    id: str
    versao_contrato: str = VERSAO_CONTRATO
    situacao: str = Field(description="PENDENTE")
    grupos: list[GrupoChamado] = Field(min_length=1, max_length=2, description="CORRETIVO antes de EVOLUTIVO")
    analise_ia: bool
    nota: int = Field(ge=0, le=100)
    regra_documentada: Optional[RegraDocumentada] = Field(default=None, description="só quando aceita: trecho a anexar ao chamado")
    funcionalidades: list[CandidatoFuncionalidade] = Field(default_factory=list,
                                                           description="as funcionalidades confirmadas pelo usuário (vão no texto)")


class ChamadoGerado(Base):
    tipo: TipoRequisito
    cd_solicitacao: int = Field(gt=0, description="TS_SOLICITACAO.CD_SOLICITACAO")


class VinculoChamados(Base):
    """Entrada de POST /entrevistas/{id}/confirmacao/chamados: o que o SOS gravou no Oracle."""
    chamados: list[ChamadoGerado] = Field(min_length=1, max_length=2, description="um por grupo da confirmação")
    cd_pacote: Optional[int] = Field(default=None, gt=0, description="TS_PACOTE.CD_PACOTE; obrigatório com 2 chamados")


class Confirmacao(Base):
    """Saída de POST /entrevistas/{id}/confirmacao/chamados (idempotente)."""
    id: str
    versao_contrato: str = VERSAO_CONTRATO
    situacao: str = Field(description="CONFIRMADA")
    chamados: list[ChamadoGerado]
    cd_pacote: Optional[int] = None


class VersaoEscopo(Base):
    versao: int
    origem: int = Field(description="0 = IA, 1 = usuário, 2 = Sustentação")
    cd_usuario: Optional[int] = None
    criado_em: str
    conteudo: dict


class EscopoChamado(Base):
    """Saída de GET /solicitacoes/{cd}/escopo."""
    cd_solicitacao: int
    entrevista_id: str
    tipo: TipoRequisito
    cd_pacote: Optional[int] = None
    versoes: list[VersaoEscopo]


class EntrevistaPendente(Base):
    """Item de GET /confirmacoes/pendentes: confirmação iniciada e não concluída (para reconciliar com o Oracle)."""
    id: str
    cd_sistema: Optional[int] = None
    sigla: str
    atualizado_em: str
    grupos: list[TipoRequisito]
