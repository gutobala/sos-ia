"""Contrato da API da entrevista de abertura (Fase 1). Fonte única dos JSON Schemas em contrato_api/ (gera_schemas.py).

Textos em português, UTF-8. O SOS (proxy entrevistaApi.asp) nunca envia o nome do solicitante.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

VERSAO_CONTRATO = "1.1.0"
# 1.1.0 (05/11/2026, aditiva): decisão do usuário sobre a regra documentada — POST /entrevistas/{id}/regra, campo
#        `decisao_regra` no estado e no contrato, `regra_documentada` no contrato, slot pseudo "decisao_regra" em `faltando`.


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


class CandidatoFuncionalidade(Base):
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


class EstadoEntrevista(Base):
    """Resposta de POST /entrevistas, POST /entrevistas/{id}/respostas e GET /entrevistas/{id}."""
    id: str
    versao_contrato: str = VERSAO_CONTRATO
    rodada: int = Field(ge=1, le=3, description="rodada atual de perguntas (máx. 2; 3 = encerrada)")
    pronto: bool = Field(description="true: não há mais perguntas; chamar POST /contrato")
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


class Integracao(Base):
    sistema: str
    sentido: str = Field(pattern=r"^(BUSCA|GRAVA|AMBOS)$")
    descricao: str


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
    embeddings: str
    modo_pii: str


class Erro(Base):
    erro: str
    detalhe: Optional[str] = None
