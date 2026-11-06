"""Confirmação do Escopo do Pedido (contrato 1.2.0): regras puras, usadas pelo motor real e pelo mock.

  1. POST /confirmacao: o SOS manda o escopo editado. O serviço confere `faltando` (regra determinística, não o navegador),
     define o tipo de cada requisito pelo segmento de origem e devolve os grupos: um chamado por tipo, no máximo 2
     (CORRETIVO; EVOLUTIVO = alterações no que já existe + funcionalidades novas). A entrevista fica PENDENTE.
  2. O SOS grava os chamados no Oracle (e o TS_PACOTE quando são 2).
  3. POST /confirmacao/chamados: o SOS informa os CD_SOLICITACAO. Idempotente; a entrevista fica CONFIRMADA.
"""
from .modelos import (DecisaoRegra, EscopoEditado, GrupoChamado, Natureza, RequisitoEscopo, TipoRequisito, TipoSegmento,
                      VinculoChamados)


class ConfirmacaoInvalida(Exception):
    """Estado não permite a operação (409)."""


class EscopoInvalido(Exception):
    """Corpo coerente com o schema, mas inválido para esta entrevista (422)."""


NATUREZA = {TipoSegmento.CORRETIVO: Natureza.CORRECAO, TipoSegmento.EVOLUTIVO_EXISTENTE: Natureza.ALTERACAO,
            TipoSegmento.EVOLUTIVO_NOVO: Natureza.NOVO}
ORDEM = (TipoRequisito.CORRETIVO, TipoRequisito.EVOLUTIVO)
SUFIXO = {TipoRequisito.CORRETIVO: " (correção)", TipoRequisito.EVOLUTIVO: " (evolução)"}
SECOES = {TipoRequisito.CORRETIVO: [("PROBLEMAS A CORRIGIR", Natureza.CORRECAO)],
          TipoRequisito.EVOLUTIVO: [("ALTERAÇÕES NO QUE JÁ EXISTE", Natureza.ALTERACAO), ("FUNCIONALIDADES NOVAS", Natureza.NOVO)]}
NOME_TIPO = {TipoRequisito.CORRETIVO: "corretivo", TipoRequisito.EVOLUTIVO: "evolutivo"}
SITUACOES_ABERTAS = ("EM_ANDAMENTO", "PRONTA", "CONTRATO_GERADO", "PENDENTE")
SITUACOES_TRAVADAS = ("PENDENTE", "CONFIRMADA")     # /contrato não muda a situação; /respostas e /regra: só CONFIRMADA dá 409


def tipo_requisito(tipo_segmento):
    return TipoRequisito.CORRETIVO if TipoSegmento(tipo_segmento) == TipoSegmento.CORRETIVO else TipoRequisito.EVOLUTIVO


def verifica_aberta(situacao):
    """Respostas, regra e confirmação só numa entrevista ainda não confirmada."""
    if situacao == "CONFIRMADA":
        raise ConfirmacaoInvalida("a entrevista já foi confirmada")


def verifica_confirmavel(contrato):
    if contrato.faltando:
        itens = "; ".join(f"segmento {f.segmento}: {f.rotulo}" for f in contrato.faltando)
        raise ConfirmacaoInvalida(f"o Escopo do Pedido ainda não pode ser confirmado. Falta: {itens}")


def monta_grupos(contrato, escopo: EscopoEditado):
    """Grupos (chamados) do escopo editado. O tipo vem do segmento; segmento inexistente é erro."""
    segs = {s.n: s for s in contrato.segmentos}
    reqs = []
    for i, r in enumerate(escopo.requisitos, 1):
        s = segs.get(r.segmento)
        if s is None:
            raise EscopoInvalido(f"requisito {i}: o segmento {r.segmento} não existe nesta entrevista")
        if not r.descricao.strip():
            raise EscopoInvalido(f"requisito {i}: descrição vazia")
        reqs.append(RequisitoEscopo(n=i, tipo=tipo_requisito(s.tipo), natureza=NATUREZA[TipoSegmento(s.tipo)], segmento=s.n,
                                    descricao=r.descricao.strip(), criterio_aceite=r.criterio_aceite.strip() or "Definir com o usuário."))
    tipos = [t for t in ORDEM if any(r.tipo == t for r in reqs)]
    regra = regra_aceita(contrato)
    grupos = []
    for t in tipos:
        meus = [r for r in reqs if r.tipo == t]
        outros = [r for r in reqs if r.tipo != t]
        grupos.append(GrupoChamado(tipo=t, titulo=titulo(escopo.titulo, t, len(tipos)), requisitos=meus,
                                   texto=texto(t, escopo, meus, outros, regra)))
    return grupos


def regra_aceita(contrato):
    """A regra documentada só vai para o chamado quando o usuário confirmou que quer mudá-la."""
    return contrato.regra_documentada if contrato.decisao_regra == DecisaoRegra.ACEITA else None


def titulo(base, tipo, n_grupos):
    base = " ".join(base.split())
    if n_grupos < 2:
        return base[:200]
    suf = SUFIXO[tipo]
    return base[:200 - len(suf)].rstrip() + suf


def texto(tipo, escopo, reqs, outros, regra=None):
    """Texto corrido para o DS_SOLICITACAO. O que mais importa vem primeiro: o SOS corta em ~3900 bytes."""
    L = ["OBJETIVO", escopo.objetivo.strip(), ""]
    for nome, nat in SECOES[tipo]:
        rs = [r for r in reqs if r.natureza == nat]
        if rs:
            L.append(nome)
            for r in rs:
                L += [f"{r.n}. {r.descricao}", f"   Critério de aceite: {r.criterio_aceite}"]
            L.append("")
    if outros:
        outro = NOME_TIPO[outros[0].tipo]
        ns = ", ".join(str(r.n) for r in outros)
        L += [f"Os requisitos {ns} deste pedido estão no chamado {outro} vinculado (mesmo pacote).", ""]
    for nome, itens in (("FORA DE ESCOPO", [x.strip() for x in escopo.fora_escopo if x.strip()]),
                        ("INTEGRAÇÕES", [f"{i.sistema} ({i.sentido}): {i.descricao}" for i in escopo.integracoes]),
                        ("DÚVIDAS EM ABERTO", [x.strip() for x in escopo.duvidas if x.strip()])):
        if itens:
            L += [nome] + [f"- {x}" for x in itens] + [""]
    if regra is not None:
        L += ["REGRA DOCUMENTADA QUE O USUÁRIO QUER MUDAR", f"{regra.pagina}" + (f" / {regra.secao}" if regra.secao else ""),
              regra.trecho.strip(), ""]
    return "\n".join(L).strip()


def valida_vinculo(tipos_grupos, vinculo: VinculoChamados, existentes=None):
    """Confere o que o SOS gravou contra os grupos da confirmação. `existentes`: vínculo já gravado (idempotência).
    Devolve True se é uma repetição idêntica (nada a gravar)."""
    tipos = [c.tipo.value for c in vinculo.chamados]
    if len(set(tipos)) != len(tipos):
        raise EscopoInvalido("tipo de chamado repetido")
    if set(tipos) != set(tipos_grupos):
        raise ConfirmacaoInvalida(f"a confirmação tem os grupos {sorted(tipos_grupos)}; recebidos {sorted(tipos)}")
    if len(tipos) == 2 and not vinculo.cd_pacote:
        raise EscopoInvalido("cd_pacote é obrigatório com 2 chamados")
    if existentes:
        atual = {(e["tipo"], int(e["cd_solicitacao"])) for e in existentes}
        pac = {e["cd_pacote"] for e in existentes}
        if atual == {(c.tipo.value, c.cd_solicitacao) for c in vinculo.chamados} and pac == {vinculo.cd_pacote}:
            return True
        raise ConfirmacaoInvalida("a entrevista já foi confirmada com outros chamados")
    return False
