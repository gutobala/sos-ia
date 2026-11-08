"""Contrato da API da entrevista de abertura (Fase 1). Fonte única dos JSON Schemas em contrato_api/ (gera_schemas.py).

Textos em português, UTF-8. O SOS (proxy entrevistaApi.asp) nunca envia o nome do solicitante.
"""
from enum import Enum
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field

VERSAO_CONTRATO = "1.4.0"
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


class NovaEntrevista(Base):
    cd_sistema: int = Field(description="CD_SISTEMA do SOS")
    sigla: str = Field(max_length=40, description="sigla do sistema (ex.: GAI)")
    intencao: Intencao
    relato: str = Field(min_length=10, max_length=20000, description="relato livre do usuário")
    imagens: list[Imagem] = Field(default_factory=list, max_length=5)


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


class Origem(str, Enum):
    """De onde veio um item do Escopo do Pedido (1.4.0). Só `usuario` é pedido dele; `inferido` é hipótese da análise, que o
    usuário confirma ou remove na tela, e que o estimador mostra à parte (não infla a contagem)."""
    USUARIO = "usuario"              # está no relato ou numa resposta
    DOCUMENTACAO = "documentacao"    # veio da regra documentada / wiki citada
    INFERIDO = "inferido"            # hipótese de solução (ex.: "criar uma rubrica")


class CandidatoFuncionalidade(Base):
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
    opcoes: list[str] = Field(default_factory=list, max_length=5,
                              description="respostas rápidas (botões); o texto livre continua valendo e a opção escolhida vai em "
                                          "Resposta.texto. \"Não sei informar\" só vem nos itens que aceitam essa resposta")


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
                                                          "marcou como afetadas; só ids mostrados nesta entrevista")


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
