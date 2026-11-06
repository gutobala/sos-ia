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
    assert not roteiros.preenchido(ev, "fora_de_escopo", "ok; ok")
    assert not roteiros.preenchido(ev, "outros_sistemas", "Sim.")
    assert not roteiros.preenchido(ev, "o_que_muda", "ok, ok, não sei, ok")
    assert roteiros.preenchido(ev, "fora_de_escopo", "Nada.")
    assert roteiros.preenchido(ev, "outros_sistemas", "Não")
    assert roteiros.preenchido(ev, "outros_sistemas", "Sim, busca no Benner")
