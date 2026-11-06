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


TRIVIAIS = {"ok", "okay", "sim", "s", "certo", "correto", "isso", "isso mesmo", "beleza", "blz", "entendi", "não sei", "nao sei",
            "sei lá", "sei la", "talvez", "?", "-", "x", "a", "teste"}


def conteudo(valor):
    """O valor sem as partes triviais (minúsculas, sem espaços duplicados)."""
    partes = [p.strip() for p in re.split(r"[;,.!?\n]+", (valor or "").lower())]
    return " ".join(p for p in partes if p and p not in TRIVIAIS)


def preenchido(tipo, codigo, valor):
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


def perguntas_complemento(segmentos, n):
    """Rodada de complemento (1.3.0), depois das 2 rodadas: só as perguntas fixas dos itens obrigatórios que faltam, na ordem de
    `faltando`, no máximo 5. Sem gatilho e sem a decisão da regra documentada. `n` (contador de complementos) deixa os ids únicos."""
    out = []
    for i, f in enumerate(faltando(segmentos)[:MAX_PERGUNTAS_COMPLEMENTO], 1):
        texto, dica = PERGUNTA_SLOT[f.slot]
        out.append(dict(id=f"c{n}p{i}", segmento=f.segmento, slot=f.slot, texto=texto, dica=dica))
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
