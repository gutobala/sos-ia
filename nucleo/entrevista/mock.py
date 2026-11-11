"""Motor MOCK da entrevista (SOSIA_MOCK=1): sem LLM e sem banco, estado em memória, respostas válidas pelo contrato.

Serve para a sessão do SOS desenvolver novaAbertura.asp / entrevistaApi.asp antes do serviço real. Comportamento previsível:
  - o roteiro vem da intenção; "também" / "além disso" / "aproveitando" no relato cria um 2º segmento (pedido misto);
  - o 1º slot de conteúdo recebe o relato; os outros viram perguntas (até 5 por rodada);
  - Benner / SEI / banco / Receita / arquivo no relato geram uma pergunta de gatilho (integração);
  - "não permite" / "não deixa" / "bloqueia" num corretivo devolvem uma regra documentada de exemplo; o segmento fica CORRETIVO
    até POST /regra (aceita → EVOLUTIVO_EXISTENTE com mais uma rodada de perguntas; recusa → segue corretivo);
  - no máximo 2 rodadas; depois, se ainda faltar item obrigatório, rodadas de complemento (só o que falta, sem limite);
    quando não falta nada, pronto=true;
  - confirmação (1.2.0) com as mesmas regras do motor real (confirmacao.py), versões e vínculos em memória;
  - 1.5.0: GET /sistemas/{cd}/menus devolve um menu de exemplo de 2 níveis (fonte "wiki") para qualquer sistema; toda entrevista
    traz 2 documentos fixos, cujo texto sai em GET /documentacao; com `ponto_partida`, onde_ocorre / funcionalidade já vêm
    preenchidos e não são perguntados.
"""
import datetime
import re
import uuid

from . import confirmacao as cf, doc_html, roteiros
from .modelos import (ArvoreDocumentacao, CandidatoFuncionalidade, ChamadoGerado, Confirmacao, Contrato, DecisaoRegra,
                      DecisaoRegraDocumentada, Documentacao, Documento, EntrevistaPendente, EscopoChamado, EscopoConfirmado,
                      EscopoEditado, EstadoEntrevista, GrupoArvore, Intencao, MenuItem, Menus, NovaEntrevista, Origem, PaginaArvore,
                      PaginaCitante, Pergunta, ReferenciaSecao, RegraDocumentada, RequisitoContrato, Respostas,
                      ResultadoBuscaDocumentacao, SecaoDocumentacao, Segmento, TipoRequisito, TipoSegmento, VersaoEscopo,
                      VinculoChamados)


def _agora():
    return datetime.datetime.now(datetime.timezone.utc)

MAX_RODADAS = 2
MAX_PERGUNTAS = 5
RX_MISTO = re.compile(r"\b(tamb[eé]m|al[eé]m disso|aproveitando)\b", re.I)
RX_GATILHO = re.compile(r"\b(arquivo|planilha|e-?mail)\b", re.I)
RX_INTEGRACAO = re.compile(r"\b(benner|(?-i:SEI)|banco|receita)\b", re.I)    # 1.5.3: não pergunta; vira dúvida para a Sustentação
# 1.5.4: no formato real da wiki (passos, referências soltas, riscado, tabela), para o SOS estilizar o `html`.
TEXTO_DOC_EXEMPLO = """BREVE DESCRIÇÃO: Texto de exemplo da documentação (mock).

P1. O sistema apresenta os parâmetros para cadastro;
Tela01

P2. O ator fornece o código da alienação;
P3. O sistema recupera e apresenta os demais dados da alienação;
RN01,
~~RN149~~,
FE01
P4. O caso de uso é encerrado.

---
Voltar"""
TABELA_DOC_EXEMPLO = """| Nome | Tipo | Descrição | Ob. |
| --- | --- | --- | --- |
| Código da alienação | Numérico | Código <obrigatório> da alienação | SIM |
| Data do edital | Data | Data de publicação | NÃO |"""
RX_REGRA = re.compile(r"n[aã]o (permite|deixa)|bloque", re.I)
RX_CALCULO = re.compile(r"c[aá]lcul", re.I)
SLOT_PRINCIPAL = {TipoSegmento.CORRETIVO: "o_que_aconteceu", TipoSegmento.EVOLUTIVO_EXISTENTE: "o_que_muda",
                  TipoSegmento.EVOLUTIVO_NOVO: "o_que_faz"}
PERGUNTA_SLOT = roteiros.PERGUNTA_SLOT      # mantido aqui: o motor importa daqui
MENU_EXEMPLO = [("m1", None, "Cadastro", False, "Cadastro"),
                ("m11", "m1", "Alienação", True, "Cadastro > Alienação"),
                ("m12", "m1", "Parcelamento", True, "Cadastro > Parcelamento"),
                ("m2", None, "Relatório", False, "Relatório"),
                ("m21", "m2", "Extrato da Alienação", True, "Relatório > Extrato da Alienação")]


def _documentos_exemplo(sigla, n_pontos=0):
    """2 documentos fixos; 1.5.1: com 2+ pontos, o 2º vem do ponto 2 (e mais um UC de exemplo por ponto a partir do 3º)."""
    s = sigla.lower()
    p1, p2 = (1, 2) if n_pontos > 1 else (1, 1) if n_pontos else (None, None)
    docs = [Documento(pagina=f"sistemas:{s}:documentacao_tecnica:caso_de_uso:uc001:inicio", titulo="UC001 - Cadastrar Alienação (exemplo)",
                      tipo="caso_de_uso", secao="FLUXO PRINCIPAL", similaridade=0.81, ponto=p1),
            Documento(pagina=f"sistemas:{s}:documentacao_tecnica:regras_de_negocio:inicio", titulo="Regras de Negócio (exemplo)",
                      tipo="regras_de_negocio", secao="RN010", similaridade=0.77, ponto=p2)]
    docs += [Documento(pagina=f"sistemas:{s}:documentacao_tecnica:caso_de_uso:uc00{k}:inicio", titulo=f"UC00{k} - Caso de uso do ponto {k} (exemplo)",
                       tipo="caso_de_uso", secao="FLUXO PRINCIPAL", similaridade=0.7, ponto=k) for k in range(3, n_pontos + 1)]
    return docs


MAX_TRECHO_BUSCA = 900
SECAO_REGRA_EXEMPLO = "REGRAS DE NEGÓCIO > RN01 - Regra de exemplo"


def _paginas_consulta(s):
    """1.6.0: as 2 páginas da árvore do mock: (id, título) do caso de uso e da história de usuário."""
    return [(f"sistemas:{s}:documentacao_tecnica:caso_de_uso:uc001:inicio", "UC001 - Cadastrar Alienação (exemplo)"),
            (f"sistemas:{s}:documentacao_tecnica:user_stories:us001:inicio", "US001 - Consultar Alienação (exemplo)")]


def _pagina_consulta(pagina, sigla):
    """1.6.0: página inteira por sigla. As 2 da árvore trazem o exemplo da 1.5.4 com a RN01 resolvida (link para a página de
    regras, que também abre aqui) e uma seção revogada."""
    s = sigla.lower()
    regras = f"sistemas:{s}:documentacao_tecnica:regras_de_negocio:inicio"
    if pagina == regras:
        texto = "O sistema não permite cadastrar a alienação sem o código do edital (texto de exemplo)."
        return Documentacao(pagina=pagina, titulo="Regras de Negócio (exemplo)", tipo="regras_de_negocio", secoes_total=1,
                            secoes=[SecaoDocumentacao(secao=SECAO_REGRA_EXEMPLO, texto=texto, html=doc_html.para_html(texto))],
                            citada_por=[PaginaCitante(pagina=p, titulo=t, tipo=tp) for (p, t), tp in
                                        zip(_paginas_consulta(s), ("caso_de_uso", "user_story"))])
    titulos = dict(_paginas_consulta(s))
    if pagina not in titulos:
        raise KeyError(pagina)
    ref = ReferenciaSecao(codigo="RN01", tipo_ref="RN", resolvida=True, pagina=regras, secao=SECAO_REGRA_EXEMPLO)
    nao = [ReferenciaSecao(codigo=c, tipo_ref=t, resolvida=False) for c, t in (("Tela01", "Tela"), ("FE01", "FE"))]
    revogado = "~~RN149 - Regra revogada em 2020 (exemplo).~~"
    secoes = [SecaoDocumentacao(secao="FLUXO PRINCIPAL", texto=TEXTO_DOC_EXEMPLO, referencias=[nao[0], ref, nao[1]],
                                html=doc_html.para_html(TEXTO_DOC_EXEMPLO, {"RN:1": (regras, SECAO_REGRA_EXEMPLO)})),
              SecaoDocumentacao(secao="Observações", texto=TABELA_DOC_EXEMPLO, html=doc_html.para_html(TABELA_DOC_EXEMPLO)),
              SecaoDocumentacao(secao="~~REGRA ANTIGA~~", texto=revogado, revogada=True,
                                html=f"<del>{doc_html.para_html(revogado)}</del>")]
    tipo = "caso_de_uso" if ":caso_de_uso:" in pagina else "user_story"
    return Documentacao(pagina=pagina, titulo=titulos[pagina], tipo=tipo, secoes=secoes, secoes_total=2)


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
        segs, ps = [], req.pontos()
        for n, t in enumerate(tipos, 1):
            valores = {SLOT_PRINCIPAL[t]: req.relato.strip()[:500]} if n == 1 else {}
            if n == 1 and len(ps) > 1 and roteiros.slot_por_ponto(t):
                valores = {}                                  # 1.5.1: o que muda é perguntado por ponto
            if n == 1 and ps and roteiros.slot_do_ponto(t):
                valores[roteiros.slot_do_ponto(t)] = roteiros.valor_dos_pontos(
                    [roteiros.valor_do_ponto(p.caminho, p.fonte.value, p.detalhe) for p in ps])
            segs.append(Segmento(n=n, tipo=t, resumo=(req.relato.strip()[:120] if n == 1 else "Segunda parte do pedido (mock)"),
                                 slots=roteiros.slots_vazios(t, valores)))
        return segs

    def _perguntas(self, st):
        ps = []
        if st["rodada"] == 1 and RX_GATILHO.search(roteiros.sem_nao_sei(st["req"].relato)):
            ps.append(Pergunta(id=f"r1g", segmento=1, slot="gatilho",
                               texto="Você citou um arquivo. O sistema vai gerar, enviar ou ler esse arquivo?",
                               dica="Isso costuma mudar bastante o tamanho do trabalho.",
                               opcoes=["Gerar", "Enviar", "Ler"]))
        pontos = st["req"].pontos()
        for s in st["segs"]:
            for x in s.slots:
                if x.preenchido or len(ps) >= MAX_PERGUNTAS:
                    continue
                if pontos and x.codigo == roteiros.slot_por_ponto(s.tipo):     # 1.5.1: "o que muda" por ponto, com os tipos
                    if not roteiros.conteudo(x.valor):
                        varios = len(pontos) > 1
                        for k, p in enumerate(pontos, 1):
                            texto, dica = roteiros.pergunta_do_ponto(p.caminho)
                            ps.append(Pergunta(id=f"r{st['rodada']}s{s.n}{x.codigo}" + (f"p{k}" if varios else ""), segmento=s.n,
                                               slot=x.codigo, texto=texto, dica=dica, ponto=k if varios else None,
                                               opcoes=roteiros.opcoes_do_ponto()))
                        continue
                texto, dica = roteiros.pergunta_fixa(s.tipo, x.codigo, x.valor)
                ps.append(Pergunta(id=f"r{st['rodada']}s{s.n}{x.codigo}", segmento=s.n, slot=x.codigo, texto=texto, dica=dica,
                                   opcoes=roteiros.opcoes_do_ponto() if pontos and x.codigo == roteiros.slot_por_ponto(s.tipo)
                                   else roteiros.opcoes_fixas(x.codigo)))
        return ps[:MAX_PERGUNTAS]

    def _nota(self, segs):
        return round(100 * roteiros.fracao_preenchida(segs))

    @staticmethod
    def _candidatos(req):
        if req.intencao != Intencao.MUDAR_EXISTENTE:
            return []
        return [CandidatoFuncionalidade(id=9001, nome=f"{req.sigla} - Consultar Cadastro (exemplo)", tipo_ifpug="CE",
                                        pagina_wiki=f"sistemas:{req.sigla.lower()}:uc001", trecho="Trecho de exemplo da wiki.",
                                        similaridade=0.82),
                CandidatoFuncionalidade(id=9002, nome=f"{req.sigla} - Alterar Cadastro (exemplo)", tipo_ifpug="EE", similaridade=0.79)]

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
        return EstadoEntrevista(id=eid, rodada=min(st["rodada"], 3), pronto=pronto, segmentos=st["segs"],
                                candidatos_funcionalidade=self._candidatos(req), documentos=_documentos_exemplo(req.sigla, len(req.pontos())),
                                regra_documentada=self._regra(req), decisao_regra=st.get("decisao"),
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
                                candidatos_funcionalidade=self._candidatos(st["req"]), documentos=_documentos_exemplo(st["req"].sigla, len(st["req"].pontos())),
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
            valor = roteiros.NAO_INFORMADO if roteiros.e_nao_sei(r.texto) and roteiros.aceita_nao_sei(p.slot) else r.texto
            pontos = st["req"].pontos()
            if p.ponto and len(pontos) > 1:                   # 1.5.1: resposta de um ponto, marcada com o caminho dele
                atual = next((x.valor for x in seg.slots if x.codigo == p.slot), None)
                valor = roteiros.resposta_no_slot(p.slot, atual, roteiros.marca_ponto(pontos[p.ponto - 1].caminho) + r.texto)
            slots = [x.model_copy(update={"valor": valor}) if x.codigo == p.slot else x for x in seg.slots]
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
                                          criterio_aceite=roteiros.criterio_fixo(s.tipo, v)[:500]))
        if RX_CALCULO.search(req.relato):        # 1.4.0: exemplo de item inferido, para a tela do SOS mostrar e confirmar/remover
            s1 = segs[0]
            reqs.append(RequisitoContrato(
                n=len(reqs) + 1, tipo=TipoRequisito.CORRETIVO if s1.tipo == TipoSegmento.CORRETIVO else TipoRequisito.EVOLUTIVO,
                segmento=s1.n, descricao="O sistema deve guardar o histórico dos valores calculados (exemplo de hipótese do mock).",
                criterio_aceite=roteiros.CRITERIO_INDEFINIDO, origem=Origem.INFERIDO))
        regra = self._regra(req)
        falt = roteiros.faltando_com_regra(segs, regra is not None, st.get("decisao"))
        integ = roteiros.duvida_integracao(m.group(0) for m in RX_INTEGRACAO.finditer(roteiros.sem_nao_sei(req.relato)))
        k = Contrato(id=eid, titulo=f"{req.sigla} - {req.relato.strip()[:80]}", objetivo=req.relato.strip()[:300],
                     requisitos=reqs, fora_escopo=[], integracoes=[],                # 1.5.3: a Sustentação responde
                     duvidas=[f"Gatilho: {g}" for g in st["gatilhos"]] + roteiros.duvidas_nao_informado(segs) + ([integ] if integ else []),
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
        funcs = cf.funcionalidades_confirmadas(escopo, self._candidatos(st["req"]))
        lidos = cf.documentos_lidos(escopo, _documentos_exemplo(st["req"].sigla, len(st["req"].pontos())))
        pts = [p.model_dump(mode="json") for p in st["req"].pontos()]
        grupos = cf.monta_grupos(k, escopo, funcs, n_pontos=len(pts))
        agora = _agora()
        st["versoes"][0] = dict(origem=0, cd_usuario=None, criado_em=agora, conteudo=k.model_dump(mode="json"))
        st["versoes"][1] = dict(origem=1, cd_usuario=escopo.cd_usuario, criado_em=agora,
                                conteudo=dict(escopo=escopo.model_dump(mode="json"), grupos=[g.model_dump(mode="json") for g in grupos],
                                              candidatos_confirmados=[dict(id=f.id, nome=f.nome, tipo_ifpug=f.tipo_ifpug) for f in funcs],
                                              documentos_lidos=lidos,
                                              ponto_partida=pts[0] if pts else None, pontos_partida=pts))
        st["inferidos"] = cf.inferidos(k, escopo)
        st["situacao"], st["atualizado"] = "PENDENTE", agora
        return EscopoConfirmado(id=eid, situacao="PENDENTE", grupos=grupos, analise_ia=k.analise_ia, nota=k.nota, funcionalidades=funcs,
                                regra_documentada=cf.regra_aceita(k))

    def vincular(self, eid, vinculo: VinculoChamados) -> Confirmacao:
        st = self.estado[eid]
        if st["situacao"] not in cf.SITUACOES_TRAVADAS:
            raise cf.ConfirmacaoInvalida("a entrevista não tem Escopo do Pedido confirmado: chame POST /entrevistas/{id}/confirmacao antes")
        tipos = [g["tipo"] for g in st["versoes"][1]["conteudo"]["grupos"]]
        existentes = [dict(tipo=t, cd_solicitacao=cd, cd_pacote=st["cd_pacote"]) for t, cd in st["chamados"]]
        if not cf.valida_vinculo(tipos, vinculo, existentes):
            for c in vinculo.chamados:          # número reaproveitado: o vínculo antigo sai (como no motor, que guarda histórico)
                antiga = self.por_chamado.get(c.cd_solicitacao)
                if antiga and antiga != eid:
                    self.estado[antiga]["chamados"] = [x for x in self.estado[antiga]["chamados"] if x[1] != c.cd_solicitacao]
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

    # ---- menus e documentação (1.5.0)
    def menus(self, cd_sistema, sigla) -> Menus:
        return Menus(fonte="wiki", modulos=[], menus=[MenuItem(id=i, pai=p, nome=n, folha=f, caminho=c) for i, p, n, f, c in MENU_EXEMPLO])

    def documentacao(self, pagina, eid=None, sigla=None) -> Documentacao:
        if sigla is not None:                                  # 1.6.0: consulta fora da entrevista
            return _pagina_consulta(pagina, sigla)
        st = self.estado[eid]
        doc = next((d for d in _documentos_exemplo(st["req"].sigla, len(st["req"].pontos())) if d.pagina == pagina), None)
        if doc is None:
            raise KeyError(pagina)
        return Documentacao(pagina=doc.pagina, titulo=doc.titulo, tipo=doc.tipo, secoes=[
            SecaoDocumentacao(secao=s, texto=t, html=doc_html.para_html(t)) for s, t in ((doc.secao, TEXTO_DOC_EXEMPLO),
                                                                                        ("Observações", TABELA_DOC_EXEMPLO))])

    # ---- consulta da documentação (1.6.0)
    def arvore_documentacao(self, cd_sistema, sigla) -> ArvoreDocumentacao:
        s = sigla.lower()
        uc, us = _paginas_consulta(s)
        return ArvoreDocumentacao(wiki=s, titulo=sigla.upper(), grupos=[
            GrupoArvore(tipo="caso_de_uso", titulo="Casos de uso", paginas=[PaginaArvore(pagina=uc[0], titulo=uc[1], secoes=2, revogada=False)]),
            GrupoArvore(tipo="user_story", titulo="Histórias de usuário", paginas=[PaginaArvore(pagina=us[0], titulo=us[1], secoes=2,
                                                                                                 revogada=False)])])

    def busca_documentacao(self, cd_sistema, sigla, q, n=10, testes=False) -> list[ResultadoBuscaDocumentacao]:
        s = sigla.lower()
        return [ResultadoBuscaDocumentacao(pagina=p, titulo=t, tipo=tipo, secao="FLUXO PRINCIPAL", similaridade=sim,
                                           trecho_html=doc_html.para_html(TEXTO_DOC_EXEMPLO[:MAX_TRECHO_BUSCA]))
                for (p, t), tipo, sim in zip(_paginas_consulta(s), ("caso_de_uso", "user_story"), (0.81, 0.74))][:n]

    def pendentes(self, horas) -> list[EntrevistaPendente]:
        limite = _agora() - datetime.timedelta(hours=horas)
        return [EntrevistaPendente(id=eid, cd_sistema=st["req"].cd_sistema, sigla=st["req"].sigla, atualizado_em=st["atualizado"].isoformat(),
                                   grupos=[g["tipo"] for g in st["versoes"][1]["conteudo"]["grupos"]])
                for eid, st in sorted(self.estado.items(), key=lambda x: x[1]["atualizado"])
                if st["situacao"] == "PENDENTE" and st["atualizado"] < limite]
