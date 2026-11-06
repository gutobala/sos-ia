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
