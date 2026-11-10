"""Testes de unidade do núcleo portado (sem banco, sem LLM, sem modelo de embeddings)."""
import os

os.environ.setdefault("SOSIA_MOCK", "1")

import pytest  # noqa: E402

from nucleo import config, llm, pii  # noqa: E402
from nucleo.estimador import regras  # noqa: E402


def test_anonimiza_padroes_e_assinatura():
    t = pii.anonimizar("Att,\nMaria Souza Lima\n3342-1658 / (61) 98556-8590 CPF 123.456.789-09 fulano@terracap.df.gov.br")
    for cru in ("Maria", "3342-1658", "98556-8590", "123.456.789-09", "fulano@"):
        assert cru not in t
    assert "[PESSOA]" in t and "[TELEFONE]" in t and "[CPF]" in t and "[EMAIL]" in t


def test_anonimiza_preserva_numero_de_processo_e_texto_funcional():
    t = pii.anonimizar("Processo 0070-000451/2012 no menu Cadastro > Alienação, campo Edital.")
    assert "0070-000451/2012" in t and "Cadastro > Alienação" in t


def test_sigla_de_unidade_depois_do_gatilho_nao_e_nome():
    """Teste do SOS (10/11): "chefe do NUVIS" virava "chefe do [PESSOA]"."""
    for t in ("O sistema também deve permitir o chefe do NUVIS assinar as vistorias",
              "Só o gerente da GEFIS aprova.", "o diretor de DICOM pediu"):
        assert "[PESSOA]" not in pii.anonimizar(t), t
    for t, cru in (("O chefe do NUVIS, JOÃO SILVA, assina.", "JOÃO"), ("Falei com o chefe João Pereira.", "João"),
                   ("Pedido do gerente MARIA", "MARIA"), ("chefe da Maria Souza", "Maria")):
        a = pii.anonimizar(t)
        assert cru not in a and "[PESSOA]" in a, a


def test_modo_local_nao_envia_imagem():
    assert config.MODO_PII == "local"
    texto, imgs, notas = pii.prepara("relato com CPF 123.456.789-09", [{"nome": "print.png", "mime": "image/png", "base64": "AAAA"}])
    assert imgs == [] and "123.456.789-09" not in texto
    assert notas or "[Texto da imagem" in texto


def test_llm_recusa_imagem_fora_do_modo_contrato():
    with pytest.raises(llm.LLMIndisponivel):
        llm._conteudo("p", [{"mime": "image/png", "base64": "AAAA"}])


def test_modo_teste_envia_imagem_e_anonimiza_texto(monkeypatch):
    import importlib
    monkeypatch.setenv("SOSIA_PII_MODO", "teste")
    monkeypatch.setenv("SOSIA_LLM", "openai")
    importlib.reload(config)
    try:
        im = {"nome": "print.png", "mime": "image/png", "base64": "AAAA"}
        texto, imgs, notas = pii.prepara("relato com CPF 123.456.789-09", [im])
        assert imgs == [im] and "123.456.789-09" not in texto and notas
        assert llm._conteudo("p", imgs)[1]["image_url"]["detail"] == "high"
    finally:
        monkeypatch.setenv("SOSIA_PII_MODO", "local")
        importlib.reload(config)


def test_contrato_exige_azure(monkeypatch):
    import importlib
    monkeypatch.setenv("SOSIA_PII_MODO", "contrato")
    monkeypatch.setenv("SOSIA_LLM", "openai")
    with pytest.raises(RuntimeError):
        importlib.reload(config)
    monkeypatch.setenv("SOSIA_PII_MODO", "local")
    importlib.reload(config)


def test_regras_nesma_ifpug_e_expansao():
    fs = [dict(tipo_ifpug="EE", movimento="ALTERACAO", nome_funcao="Alterar contrato"),
          dict(tipo_ifpug="EE", movimento="INCLUSAO", nome_funcao="Listar contratos"),
          dict(tipo_ifpug="SE", movimento="NAO_MENSURAVEL", nome_funcao="Corrigir rótulo")]
    fs2 = regras.sem_duplicatas(regras.expande(regras.normaliza(fs), "R3"))
    tipos = sorted(f["tipo_ifpug"] for f in fs2)
    assert tipos == ["CE", "CE", "EE", "SE"]          # listar vira CE; alteração ganha consulta implícita; MI fica
    n, pf = regras.conta(fs2)
    assert n == 4 and pf == pytest.approx(4 * 0.75 + 4 * 1.0 + 4 * 0.75 + 0)
    assert regras.n_mi(fs2) == 1
    assert regras.complexidade("SE", 51, 4) == 2 and regras.complexidade("EE", 6, 1) == 0


def test_precos():
    from nucleo import precos
    assert precos.custo_usd("gpt-5.4-mini", 1_000_000, 1_000_000) == pytest.approx(0.75 + 4.50)
    assert precos.custo_usd("modelo-desconhecido", 10, 10) is None
    assert precos.custo_embedding_usd("text-embedding-3-large", 37_000_000) == pytest.approx(4.81)


def test_env_com_comentario_e_valor_invalido(tmp_path, monkeypatch):
    import importlib
    import re
    linha = "SOSIA_PII_MODO=local         # local | contrato"
    v = re.split(r"\s+#", linha.split("=", 1)[1], maxsplit=1)[0].strip()
    assert v == "local"
    # o docker compose entrega o comentário junto: o núcleo limpa ("VAR= # x" vira vazio, "local # x" vira "local")
    monkeypatch.setenv("SOSIA_PII_MODO", "local # comentário")
    assert importlib.reload(config).MODO_PII == "local"
    monkeypatch.setenv("SOSIA_TOKEN", "# token compartilhado com o proxy")
    monkeypatch.setenv("SOSIA_MOCK", "0")
    assert importlib.reload(config).TOKEN is None
    monkeypatch.setenv("SOSIA_PII_MODO", "localx")          # valor inválido continua barrando a subida
    with pytest.raises(RuntimeError):
        importlib.reload(config)
    monkeypatch.setenv("SOSIA_PII_MODO", "local")
    monkeypatch.delenv("SOSIA_TOKEN")
    monkeypatch.delenv("SOSIA_MOCK")
    importlib.reload(config)


def test_slot_com_resposta_trivial_nao_conta():
    from nucleo.entrevista import roteiros
    from nucleo.entrevista.modelos import TipoSegmento as T
    ev = T.EVOLUTIVO_EXISTENTE
    assert not roteiros.preenchido(ev, "exemplo_concreto", "ok; ok")
    assert not roteiros.preenchido(ev, "o_que_muda", "ok, ok, não sei, ok")
    assert roteiros.preenchido(ev, "exemplo_concreto", "Contrato 123: valor 10, hoje sai 12")


def test_fora_de_escopo_e_outros_sistemas_sairam_do_roteiro():
    """1.5.3: a Sustentação responde; uma entrevista começada antes perde os dois itens na revalidação e não fica bloqueada."""
    from nucleo.entrevista import roteiros as R
    from nucleo.entrevista.modelos import Segmento, Slot, TipoSegmento as T
    for t in (T.EVOLUTIVO_EXISTENTE, T.EVOLUTIVO_NOVO):
        assert not {"fora_de_escopo", "outros_sistemas"} & {c for c, _, _ in R.ROTEIROS[t]}
    cheio = R.slots_vazios(T.EVOLUTIVO_EXISTENTE, dict(funcionalidade="Menu: Cadastro > Parcelamento",
                                                       o_que_muda="Aceitar 25% de desconto no 2º edital",
                                                       exemplo_concreto="Edital 2/2026 com 25% de desconto"))
    antigo = Segmento(n=1, tipo=T.EVOLUTIVO_EXISTENTE, resumo="x", slots=cheio + [
        Slot(codigo="fora_de_escopo", rotulo="O que fica fora do escopo", obrigatorio=True, valor=None, preenchido=False)])
    assert R.faltando([antigo]) and not R.faltando([R.revalida(antigo)])
    assert R.duvida_integracao([]) is None
    d = R.duvida_integracao(["Benner", "SEI", "benner ", "Benner"])
    assert d.startswith("Para a Sustentação") and "(Benner, SEI)" in d


def test_nao_sei_so_conta_nos_itens_que_aceitam():
    """1.4.0: "Não sei" preenche (como NAO_INFORMADO) só os itens não bloqueantes; não conta na nota."""
    from nucleo.entrevista import roteiros as R
    from nucleo.entrevista.modelos import Segmento, TipoSegmento as T
    for t in ("não sei", "Não sei.", "NAO SEI", "Não sei informar", "sei lá!", "não lembro"):
        assert R.e_nao_sei(t), t
    for t in ("Não sei se é no GAI ou no GPZ", "", "não", "nada"):
        assert not R.e_nao_sei(t), t
    assert R.preenchido(T.CORRETIVO, "desde_quando_quem", R.NAO_INFORMADO)
    assert R.preenchido(T.CORRETIVO, "como_reproduzir", R.NAO_INFORMADO)
    assert not R.preenchido(T.CORRETIVO, "onde_ocorre", R.NAO_INFORMADO)          # bloqueante
    assert not R.preenchido(T.EVOLUTIVO_NOVO, "o_que_faz", R.NAO_INFORMADO)
    assert R.resposta_no_slot("desde_quando_quem", "ok", "Não sei informar") == R.NAO_INFORMADO
    assert R.resposta_no_slot("onde_ocorre", "ok", "não sei") == "ok; não sei"     # bloqueante: segue como resposta comum
    assert R.resposta_no_slot("desde_quando_quem", R.NAO_INFORMADO, "Desde ontem") == "Desde ontem"
    assert R.opcoes_fixas("exemplo_concreto") == [R.OPCAO_NAO_SEI] and R.opcoes_fixas("o_que_muda") == []
    assert R.opcoes_fixas("gatilho") == []
    seg = R.revalida(Segmento(n=1, tipo=T.CORRETIVO, resumo="r", slots=R.slots_vazios(T.CORRETIVO, {
        "onde_ocorre": "Tela de contratos, botão Salvar", "o_que_aconteceu": "Aparece HTTP 500 e nada é gravado",
        "como_reproduzir": "1) abrir o contrato; 2) Salvar", "desde_quando_quem": R.NAO_INFORMADO})))
    assert not R.faltando([seg]) and R.fracao_preenchida([seg]) == 0.75
    assert R.duvidas_nao_informado([seg]) == ["Não informado: Desde quando e quem é afetado (segmento 1)"]
    assert R.criterio_fixo(T.EVOLUTIVO_NOVO, {"exemplo_concreto": R.NAO_INFORMADO}) == R.CRITERIO_INDEFINIDO
