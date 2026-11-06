"""Motor MOCK da entrevista (SOSIA_MOCK=1): sem LLM e sem banco, estado em memória, respostas válidas pelo contrato.

Serve para a sessão do SOS desenvolver novaAbertura.asp / entrevistaApi.asp antes do serviço real. Comportamento previsível:
  - o roteiro vem da intenção; "também" / "além disso" / "aproveitando" no relato cria um 2º segmento (pedido misto);
  - o 1º slot de conteúdo recebe o relato; os outros viram perguntas (até 5 por rodada);
  - Benner / SEI / banco / Receita / arquivo no relato geram uma pergunta de gatilho (integração);
  - "não permite" / "não deixa" / "bloqueia" num corretivo devolvem uma regra documentada de exemplo; o segmento fica CORRETIVO
    até POST /regra (aceita → EVOLUTIVO_EXISTENTE com mais uma rodada de perguntas; recusa → segue corretivo);
  - no máximo 2 rodadas; depois, se ainda faltar item obrigatório, rodadas de complemento (só o que falta, sem limite);
    quando não falta nada, pronto=true;
  - confirmação (1.2.0) com as mesmas regras do motor real (confirmacao.py), versões e vínculos em memória.
"""
import datetime
import re
import uuid

from . import confirmacao as cf, roteiros
from .modelos import (CandidatoFuncionalidade, ChamadoGerado, Confirmacao, Contrato, DecisaoRegra, DecisaoRegraDocumentada,
                      EntrevistaPendente, EscopoChamado, EscopoConfirmado, EscopoEditado, EstadoEntrevista, Integracao, Intencao,
                      NovaEntrevista, Pergunta, RegraDocumentada, RequisitoContrato, Respostas, Segmento, TipoRequisito, TipoSegmento,
                      VersaoEscopo, VinculoChamados)


def _agora():
    return datetime.datetime.now(datetime.timezone.utc)

MAX_RODADAS = 2
MAX_PERGUNTAS = 5
RX_MISTO = re.compile(r"\b(tamb[eé]m|al[eé]m disso|aproveitando)\b", re.I)
RX_GATILHO = re.compile(r"\b(benner|sei|banco|receita|arquivo|planilha|e-?mail)\b", re.I)
RX_REGRA = re.compile(r"n[aã]o (permite|deixa)|bloque", re.I)
SLOT_PRINCIPAL = {TipoSegmento.CORRETIVO: "o_que_aconteceu", TipoSegmento.EVOLUTIVO_EXISTENTE: "o_que_muda",
                  TipoSegmento.EVOLUTIVO_NOVO: "o_que_faz"}
PERGUNTA_SLOT = roteiros.PERGUNTA_SLOT      # mantido aqui: o motor importa daqui


class MotorMock:
    analise_ia = False

    def __init__(self):
        self.estado = {}
        self.por_chamado = {}          # cd_solicitacao -> entrevista (único, como no banco)

    # ---- interno
    def _segmentos_iniciais(self, req: NovaEntrevista):
        tipos = [roteiros.roteiro_da_intencao(req.intencao)]
        if RX_MISTO.search(req.relato):
            tipos.append(TipoSegmento.EVOLUTIVO_EXISTENTE if tipos[0] != TipoSegmento.EVOLUTIVO_EXISTENTE
                         else TipoSegmento.EVOLUTIVO_NOVO)
        segs = []
        for n, t in enumerate(tipos, 1):
            valores = {SLOT_PRINCIPAL[t]: req.relato.strip()[:500]} if n == 1 else {}
            segs.append(Segmento(n=n, tipo=t, resumo=(req.relato.strip()[:120] if n == 1 else "Segunda parte do pedido (mock)"),
                                 slots=roteiros.slots_vazios(t, valores)))
        return segs

    def _perguntas(self, st):
        ps = []
        if st["rodada"] == 1 and RX_GATILHO.search(st["req"].relato):
            ps.append(Pergunta(id=f"r1g", segmento=1, slot="gatilho",
                               texto="Você citou outro sistema ou arquivo. Os dados serão só consultados, ou também gravados/enviados?",
                               dica="Isso costuma mudar bastante o tamanho do trabalho."))
        for s in st["segs"]:
            for x in s.slots:
                if not x.preenchido and len(ps) < MAX_PERGUNTAS:
                    texto, dica = PERGUNTA_SLOT[x.codigo]
                    ps.append(Pergunta(id=f"r{st['rodada']}s{s.n}{x.codigo}", segmento=s.n, slot=x.codigo, texto=texto, dica=dica))
        return ps

    def _nota(self, segs):
        tot = sum(len(s.slots) for s in segs)
        return round(100 * sum(x.preenchido for s in segs for x in s.slots) / max(tot, 1))

    def _resposta(self, eid):
        st = self.estado[eid]
        falta = roteiros.faltando(st["segs"])
        st["complemento"] = st["rodada"] > MAX_RODADAS and bool(falta)
        if st["complemento"]:                     # 1.3.0: depois das 2 rodadas, só o que falta, sem limite de rodadas
            st["complementos"] = st.get("complementos", 0) + 1
            st["perguntas"] = [Pergunta(**p) for p in roteiros.perguntas_complemento(st["segs"], st["complementos"])]
        else:
            st["perguntas"] = [] if st["rodada"] > MAX_RODADAS or not falta else self._perguntas(st)
        pronto = not st["perguntas"]
        dicas = [f"Falta: {f.rotulo}" for f in roteiros.faltando(st["segs"])][:3]
        req = st["req"]
        cand = []
        if req.intencao == Intencao.MUDAR_EXISTENTE:
            cand = [CandidatoFuncionalidade(nome=f"{req.sigla} - Consultar Cadastro (exemplo)", tipo_ifpug="CE",
                                            pagina_wiki=f"sistemas:{req.sigla.lower()}:uc001", trecho="Trecho de exemplo da wiki.",
                                            similaridade=0.82),
                    CandidatoFuncionalidade(nome=f"{req.sigla} - Alterar Cadastro (exemplo)", tipo_ifpug="EE",
                                            similaridade=0.79)]
        return EstadoEntrevista(id=eid, rodada=min(st["rodada"], 3), pronto=pronto, segmentos=st["segs"],
                                candidatos_funcionalidade=cand, regra_documentada=self._regra(req), decisao_regra=st.get("decisao"),
                                perguntas=st["perguntas"], complemento=st["complemento"],
                                nota=self._nota(st["segs"]), dicas_medidor=dicas, analise_ia=self.analise_ia)

    @staticmethod
    def _regra(req):
        if req.intencao == Intencao.PAROU_DE_FUNCIONAR and RX_REGRA.search(req.relato):
            return RegraDocumentada(pagina=f"sistemas:{req.sigla.lower()}:regras_de_negocio", secao="RN010 (exemplo)",
                                    trecho="O sistema não permite a operação quando ... (trecho de exemplo).", similaridade=0.88,
                                    orientacao="O comportamento parece seguir esta regra documentada. Se você quer mudar a regra, "
                                               "o pedido será tratado como evolutivo.")
        return None

    # ---- API
    def decidir_regra(self, eid, dec: DecisaoRegraDocumentada) -> EstadoEntrevista:
        st = self.estado[eid]
        cf.verifica_aberta(st["situacao"])
        if not self._regra(st["req"]):
            raise ValueError("a entrevista não tem regra documentada")
        if st.get("decisao"):
            raise ValueError("a regra documentada já foi decidida")
        st["contrato"], st["situacao"] = None, "EM_ANDAMENTO"
        if dec.aceita:
            st["decisao"] = DecisaoRegra.ACEITA
            st["segs"] = [roteiros.converte_para_evolutivo(s) if s.n == 1 else s for s in st["segs"]]
            st["rodada"] = min(st["rodada"], MAX_RODADAS)          # o segmento mudou de roteiro: mais uma rodada de perguntas
        else:
            st["decisao"] = DecisaoRegra.RECUSADA
            if dec.comentario:
                st["gatilhos"].append(f"Regra documentada recusada: {dec.comentario}")
        return self._resposta(eid)

    def criar(self, req: NovaEntrevista) -> EstadoEntrevista:
        eid = str(uuid.uuid4())
        self.estado[eid] = dict(req=req, rodada=1, segs=self._segmentos_iniciais(req), perguntas=[], gatilhos=[], contrato=None,
                                situacao="EM_ANDAMENTO", atualizado=_agora(), versoes={}, chamados=[], cd_pacote=None)
        return self._resposta(eid)

    def obter(self, eid) -> EstadoEntrevista:
        st = self.estado[eid]
        return EstadoEntrevista(id=eid, rodada=min(st["rodada"], 3), pronto=not st["perguntas"], segmentos=st["segs"],
                                regra_documentada=self._regra(st["req"]), decisao_regra=st.get("decisao"),
                                perguntas=st["perguntas"], complemento=bool(st.get("complemento") and st["perguntas"]),
                                nota=self._nota(st["segs"]), dicas_medidor=[], analise_ia=self.analise_ia)

    def responder(self, eid, resp: Respostas) -> EstadoEntrevista:
        st = self.estado[eid]
        cf.verifica_aberta(st["situacao"])
        st["contrato"], st["situacao"] = None, "EM_ANDAMENTO"
        por_id = {p.id: p for p in st["perguntas"]}
        for r in resp.respostas:
            p = por_id.get(r.id)
            if not p:
                continue
            if p.slot == "gatilho":
                st["gatilhos"].append(r.texto)
                continue
            seg = next(s for s in st["segs"] if s.n == p.segmento)
            slots = [x.model_copy(update={"valor": r.texto}) if x.codigo == p.slot else x for x in seg.slots]
            st["segs"] = [roteiros.revalida(seg.model_copy(update={"slots": slots})) if s.n == seg.n else s for s in st["segs"]]
        st["rodada"] = min(st["rodada"] + 1, MAX_RODADAS + 1)   # fica em 3 durante o complemento
        return self._resposta(eid)

    def contrato(self, eid) -> Contrato:
        st = self.estado[eid]
        req, segs = st["req"], [roteiros.revalida(s) for s in st["segs"]]
        reqs = []
        for s in segs:
            v = {x.codigo: x.valor for x in s.slots}
            tipo = TipoRequisito.CORRETIVO if s.tipo == TipoSegmento.CORRETIVO else TipoRequisito.EVOLUTIVO
            desc = v.get(SLOT_PRINCIPAL[s.tipo]) or s.resumo
            reqs.append(RequisitoContrato(n=len(reqs) + 1, tipo=tipo, segmento=s.n, descricao=desc[:500],
                                          criterio_aceite=(v.get("exemplo_concreto") or v.get("como_reproduzir")
                                                           or "Definir com o usuário.")[:500]))
        fora = [x.valor for s in segs for x in s.slots if x.codigo == "fora_de_escopo" and x.valor
                and x.valor.strip().lower() not in ("nada", "não", "nao")]
        integ = []
        for s in segs:
            v = next((x.valor for x in s.slots if x.codigo == "outros_sistemas"), None)
            if v and v.strip().lower() not in ("não", "nao", "n"):
                integ.append(Integracao(sistema="(a identificar)", sentido="AMBOS", descricao=v))
        regra = self._regra(req)
        falt = roteiros.faltando_com_regra(segs, regra is not None, st.get("decisao"))
        k = Contrato(id=eid, titulo=f"{req.sigla} - {req.relato.strip()[:80]}", objetivo=req.relato.strip()[:300],
                     requisitos=reqs, fora_escopo=fora, duvidas=[f"Gatilho: {g}" for g in st["gatilhos"]], integracoes=integ,
                     segmentos=segs, regra_documentada=regra, decisao_regra=st.get("decisao"), nota=self._nota(segs),
                     faltando=falt, pode_confirmar=not falt, analise_ia=self.analise_ia)
        st["contrato"] = k
        if st["situacao"] not in cf.SITUACOES_TRAVADAS:
            st["situacao"] = "CONTRATO_GERADO"
        return k

    # ---- confirmação (1.2.0)
    def confirmacao(self, eid, escopo: EscopoEditado) -> EscopoConfirmado:
        st = self.estado[eid]
        cf.verifica_aberta(st["situacao"])
        k = st["contrato"]
        if k is None:
            raise cf.ConfirmacaoInvalida("o Escopo do Pedido ainda não foi gerado: chame POST /entrevistas/{id}/contrato antes")
        cf.verifica_confirmavel(k)
        grupos = cf.monta_grupos(k, escopo)
        agora = _agora()
        st["versoes"][0] = dict(origem=0, cd_usuario=None, criado_em=agora, conteudo=k.model_dump(mode="json"))
        st["versoes"][1] = dict(origem=1, cd_usuario=escopo.cd_usuario, criado_em=agora,
                                conteudo=dict(escopo=escopo.model_dump(mode="json"), grupos=[g.model_dump(mode="json") for g in grupos]))
        st["situacao"], st["atualizado"] = "PENDENTE", agora
        return EscopoConfirmado(id=eid, situacao="PENDENTE", grupos=grupos, analise_ia=k.analise_ia, nota=k.nota,
                                regra_documentada=cf.regra_aceita(k))

    def vincular(self, eid, vinculo: VinculoChamados) -> Confirmacao:
        st = self.estado[eid]
        if st["situacao"] not in cf.SITUACOES_TRAVADAS:
            raise cf.ConfirmacaoInvalida("a entrevista não tem Escopo do Pedido confirmado: chame POST /entrevistas/{id}/confirmacao antes")
        tipos = [g["tipo"] for g in st["versoes"][1]["conteudo"]["grupos"]]
        existentes = [dict(tipo=t, cd_solicitacao=cd, cd_pacote=st["cd_pacote"]) for t, cd in st["chamados"]]
        if not cf.valida_vinculo(tipos, vinculo, existentes):
            if any(self.por_chamado.get(c.cd_solicitacao, eid) != eid for c in vinculo.chamados):
                raise cf.ConfirmacaoInvalida("um dos chamados informados já está vinculado a outra entrevista")
            st["chamados"] = sorted((c.tipo.value, c.cd_solicitacao) for c in vinculo.chamados)
            st["cd_pacote"] = vinculo.cd_pacote
            self.por_chamado.update({c.cd_solicitacao: eid for c in vinculo.chamados})
            st["situacao"], st["atualizado"] = "CONFIRMADA", _agora()
        return Confirmacao(id=eid, situacao="CONFIRMADA", chamados=[ChamadoGerado(tipo=t, cd_solicitacao=cd) for t, cd in st["chamados"]],
                           cd_pacote=st["cd_pacote"])

    def escopo_do_chamado(self, cd_solicitacao) -> EscopoChamado:
        eid = self.por_chamado[cd_solicitacao]
        st = self.estado[eid]
        tipo = next(t for t, cd in st["chamados"] if cd == cd_solicitacao)
        vs = [VersaoEscopo(versao=n, origem=v["origem"], cd_usuario=v["cd_usuario"], criado_em=v["criado_em"].isoformat(),
                           conteudo=v["conteudo"]) for n, v in sorted(st["versoes"].items())]
        return EscopoChamado(cd_solicitacao=cd_solicitacao, entrevista_id=eid, tipo=tipo, cd_pacote=st["cd_pacote"], versoes=vs)

    def pendentes(self, horas) -> list[EntrevistaPendente]:
        limite = _agora() - datetime.timedelta(hours=horas)
        return [EntrevistaPendente(id=eid, cd_sistema=st["req"].cd_sistema, sigla=st["req"].sigla, atualizado_em=st["atualizado"].isoformat(),
                                   grupos=[g["tipo"] for g in st["versoes"][1]["conteudo"]["grupos"]])
                for eid, st in sorted(self.estado.items(), key=lambda x: x[1]["atualizado"])
                if st["situacao"] == "PENDENTE" and st["atualizado"] < limite]
