"""Slots obrigatórios por roteiro e validação determinística (a mesma regra vale no serviço e no confirmarAbertura.asp).

Regra de "preenchido": tira do valor as partes sem conteúdo (TRIVIAIS: "ok", "sim", "não sei"...; partes separadas por ; , . ! ?)
e exige o mínimo de caracteres do roteiro no que sobra. "não" e "nada" NÃO são triviais (são respostas válidas para fora de
escopo e outros sistemas). Assim "ok; ok" ou "sim" (sem dizer qual sistema) não destravam o contrato.
"""
import re

from .modelos import Segmento, Slot, SlotFaltando, TipoSegmento

# (código, rótulo, mínimo de caracteres). Mínimo baixo onde "não"/"nada" é resposta válida.
ROTEIROS = {
    TipoSegmento.CORRETIVO: [
        ("onde_ocorre", "Onde ocorre (sistema, tela ou funcionalidade)", 10),
        ("o_que_aconteceu", "O que aconteceu (mensagem, comportamento)", 15),
        ("como_reproduzir", "Como reproduzir (passo a passo)", 15),
        ("desde_quando_quem", "Desde quando e quem é afetado", 8),
    ],
    TipoSegmento.EVOLUTIVO_EXISTENTE: [
        ("funcionalidade", "Funcionalidade que será alterada", 8),
        ("o_que_muda", "O que muda", 15),
        ("exemplo_concreto", "Exemplo concreto", 15),
        ("fora_de_escopo", "O que fica fora do escopo", 3),
        ("outros_sistemas", "Busca ou grava dados de outros sistemas?", 2),
    ],
    TipoSegmento.EVOLUTIVO_NOVO: [
        ("quem_usa", "Quem vai usar", 5),
        ("informacoes_guardadas", "Que informações vai guardar", 15),
        ("o_que_faz", "O que faz com essas informações", 15),
        ("exemplo_concreto", "Exemplo concreto", 15),
        ("fora_de_escopo", "O que fica fora do escopo", 3),
        ("outros_sistemas", "Busca ou grava dados de outros sistemas?", 2),
    ],
}


# Pergunta fixa de cada slot (texto, dica): roteiro sem IA, slot que o LLM esqueceu e rodada de complemento.
PERGUNTA_SLOT = {
    "onde_ocorre": ("Em qual tela ou funcionalidade isso acontece?", "Ex.: menu Cadastro > Alienação, botão Salvar."),
    "o_que_aconteceu": ("O que aconteceu exatamente? Copie a mensagem de erro, se houver.", None),
    "como_reproduzir": ("Quais passos levam ao problema?", "Ex.: 1) abrir a tela X; 2) informar Y; 3) clicar em Z."),
    "desde_quando_quem": ("Desde quando acontece e quem é afetado?", "Só você, sua área ou todos os usuários?"),
    "funcionalidade": ("Qual funcionalidade será alterada?", "Use o nome do menu ou da tela."),
    "o_que_muda": ("O que deve mudar no comportamento atual?", None),
    "exemplo_concreto": ("Dê um exemplo concreto, com valores.", "Ex.: para o contrato X, o valor deveria ser Y e hoje é Z."),
    "fora_de_escopo": ("O que NÃO faz parte deste pedido?", "Se nada, responda 'nada'."),
    "outros_sistemas": ("As informações vêm de outro sistema ou precisam ser gravadas em outro sistema?",
                        "Ex.: Benner, SEI, banco. Se não, responda 'não'."),
    "quem_usa": ("Quem vai usar a nova funcionalidade?", "Área ou perfil de usuário."),
    "informacoes_guardadas": ("Que informações a funcionalidade vai guardar?", "Liste os campos principais."),
    "o_que_faz": ("O que a funcionalidade faz com essas informações?", None),
}
MAX_PERGUNTAS_COMPLEMENTO = 5

# "Não sei" (1.4.0): estes itens aceitam a resposta e não bloqueiam a abertura; o valor vira NAO_INFORMADO, o item sai de
# `faltando`, entra nas dúvidas do contrato e baixa a nota. Os demais (onde ocorre, o que aconteceu, funcionalidade, o que muda,
# quem usa, informações guardadas, o que faz) continuam bloqueando: sem eles não se sabe o que fazer.
ACEITA_NAO_SEI = {"desde_quando_quem", "como_reproduzir", "exemplo_concreto", "fora_de_escopo", "outros_sistemas"}
NAO_INFORMADO = "Não informado (o usuário não soube responder)"
OPCAO_NAO_SEI = "Não sei informar"
_FRASES_NAO_SEI = {"não sei", "nao sei", "não sei informar", "nao sei informar", "não sei dizer", "nao sei dizer", "sei lá",
                   "sei la", "não lembro", "nao lembro", "desconheço", "desconheco", "não tenho essa informação",
                   "nao tenho essa informacao", "não sei responder", "nao sei responder"}


def e_nao_sei(texto):
    """True se a resposta inteira é um "não sei" (com ou sem pontuação). "Não sei se é no GAI ou no GPZ" NÃO é."""
    t = " ".join(re.sub(r"[^\w\s]", " ", (texto or "").lower()).split())
    return t in _FRASES_NAO_SEI


RX_NAO_SEI_FRASE = re.compile(r"\b(n[aã]o|nem) sei\b|\bsei l[aá]\b", re.I)


def sem_nao_sei(texto):
    """O texto sem "não sei" / "sei lá", para buscar gatilhos: o "sei" casava com o gatilho de integração (sistema SEI)."""
    return RX_NAO_SEI_FRASE.sub(" ", texto or "")


def aceita_nao_sei(codigo):
    return codigo in ACEITA_NAO_SEI


def opcoes_fixas(codigo):
    """Opções das perguntas fixas (roteiro sem IA, slot esquecido, complemento): só o "Não sei informar", quando o item aceita."""
    return [OPCAO_NAO_SEI] if aceita_nao_sei(codigo) else []


def nao_informado(valor):
    return (valor or "").strip() == NAO_INFORMADO


TRIVIAIS = {"ok", "okay", "sim", "s", "certo", "correto", "isso", "isso mesmo", "beleza", "blz", "entendi", "não sei", "nao sei",
            "sei lá", "sei la", "talvez", "?", "-", "x", "a", "teste"}


def conteudo(valor):
    """O valor sem as partes triviais (minúsculas, sem espaços duplicados)."""
    partes = [p.strip() for p in re.split(r"[;,.!?\n]+", (valor or "").lower())]
    return " ".join(p for p in partes if p and p not in TRIVIAIS)


def preenchido(tipo, codigo, valor):
    if nao_informado(valor):
        return aceita_nao_sei(codigo)
    return len(conteudo(valor)) >= minimo(tipo, codigo)


def minimo(tipo, codigo):
    return next((m for c, _, m in ROTEIROS[tipo] if c == codigo), 1)


def slots_vazios(tipo, valores=None):
    """Lista de Slot do roteiro, preenchendo com valores {codigo: texto} quando houver."""
    valores = valores or {}
    out = []
    for codigo, rotulo, m in ROTEIROS[tipo]:
        v = (valores.get(codigo) or "").strip() or None
        out.append(Slot(codigo=codigo, rotulo=rotulo, obrigatorio=True, valor=v, preenchido=preenchido(tipo, codigo, v)))
    return out


def revalida(seg: Segmento) -> Segmento:
    """Recalcula 'preenchido' de cada slot pela regra determinística (não confia no LLM)."""
    slots = [s.model_copy(update={"preenchido": preenchido(seg.tipo, s.codigo, s.valor)})
             for s in seg.slots]
    return seg.model_copy(update={"slots": slots})


def faltando(segmentos):
    return [SlotFaltando(segmento=s.n, slot=x.codigo, rotulo=x.rotulo)
            for s in segmentos for x in s.slots if x.obrigatorio and not x.preenchido]


def nao_informados(segmentos):
    """Itens respondidos com "Não sei" (1.4.0): vão para as dúvidas do contrato e não contam na nota."""
    return [SlotFaltando(segmento=s.n, slot=x.codigo, rotulo=x.rotulo)
            for s in segmentos for x in s.slots if nao_informado(x.valor)]


def duvidas_nao_informado(segmentos):
    """Dúvidas determinísticas do contrato para os itens "Não sei"."""
    return [f"Não informado: {f.rotulo} (segmento {f.segmento})" for f in nao_informados(segmentos)]


def resposta_no_slot(codigo, atual, texto):
    """Valor do slot depois de uma resposta. "Não sei" num item que aceita vira NAO_INFORMADO (substitui o que havia de
    trivial); num item que bloqueia, junta como qualquer resposta (e continua não contando: "não sei" é trivial)."""
    if e_nao_sei(texto) and aceita_nao_sei(codigo):
        return NAO_INFORMADO
    if nao_informado(atual):
        return texto
    return f"{atual}; {texto}" if atual else texto


def fracao_preenchida(segmentos):
    """Para a nota do medidor: itens preenchidos de verdade ("Não sei" não conta) / total."""
    tot = sum(len(s.slots) for s in segmentos)
    cheio = sum(x.preenchido and not nao_informado(x.valor) for s in segmentos for x in s.slots)
    return cheio / max(tot, 1)


CRITERIO_CORRETIVO = "Ao repetir os passos informados, a operação é concluída sem o erro relatado."
CRITERIO_INDEFINIDO = "Definir com o usuário."


def criterio_fixo(tipo, valores):
    """Critério de aceite do roteiro fixo (sem LLM). Passos para reproduzir não são critério: no corretivo, frase fixa (os passos
    vão na seção INFORMAÇÕES DO PEDIDO do chamado); no evolutivo, o exemplo concreto, quando houver."""
    if TipoSegmento(tipo) == TipoSegmento.CORRETIVO:
        return CRITERIO_CORRETIVO
    ex = (valores.get("exemplo_concreto") or "").strip()
    return f"Conferir com o exemplo: {ex}" if ex and not nao_informado(ex) else CRITERIO_INDEFINIDO


def perguntas_complemento(segmentos, n):
    """Rodada de complemento (1.3.0), depois das 2 rodadas: só as perguntas fixas dos itens obrigatórios que faltam, na ordem de
    `faltando`, no máximo 5. Sem gatilho e sem a decisão da regra documentada. `n` (contador de complementos) deixa os ids únicos."""
    out = []
    for i, f in enumerate(faltando(segmentos)[:MAX_PERGUNTAS_COMPLEMENTO], 1):
        texto, dica = PERGUNTA_SLOT[f.slot]
        out.append(dict(id=f"c{n}p{i}", segmento=f.segmento, slot=f.slot, texto=texto, dica=dica, opcoes=opcoes_fixas(f.slot)))
    return out


def roteiro_da_intencao(intencao):
    from .modelos import Intencao
    return {Intencao.PAROU_DE_FUNCIONAR: TipoSegmento.CORRETIVO, Intencao.MUDAR_EXISTENTE: TipoSegmento.EVOLUTIVO_EXISTENTE,
            Intencao.ALGO_NOVO: TipoSegmento.EVOLUTIVO_NOVO}[intencao]


SLOT_DECISAO_REGRA = SlotFaltando(segmento=1, slot="decisao_regra", rotulo="Confirmar se o comportamento segue a regra documentada")


def faltando_com_regra(segmentos, tem_regra, decisao):
    """`faltando` + a decisão da regra documentada pendente (bloqueia a confirmação até o usuário escolher)."""
    return roteiros_faltando(segmentos) + ([SLOT_DECISAO_REGRA] if tem_regra and not decisao else [])


roteiros_faltando = faltando


def converte_para_evolutivo(seg: Segmento) -> Segmento:
    """Usuário aceitou a regra documentada: o segmento corretivo vira EVOLUTIVO_EXISTENTE (onde ocorria = funcionalidade)."""
    v = {x.codigo: x.valor for x in seg.slots}
    t = TipoSegmento.EVOLUTIVO_EXISTENTE
    return revalida(Segmento(n=seg.n, tipo=t, resumo=seg.resumo, slots=slots_vazios(t, {"funcionalidade": v.get("onde_ocorre")})))
