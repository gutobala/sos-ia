"""Entrevista ponta a ponta com o LLM de verdade (passo 5): os 6 cenários do plano do SOS, pela API (TestClient, sem subir servidor).

Cada cenário: POST /entrevistas → responde as perguntas com respostas roteirizadas (por slot) → até 2 rodadas → POST /contrato.
Mostra perguntas, slots, nota, regra documentada, candidatos, contrato e o custo (apf.entrevista_evento). Banco `sosia`, modelo do
papel "chat" (gpt-5.4-mini), PII em modo local. Custo esperado: ~US$ 0,01–0,03 por entrevista.

Uso (na raiz):  .venv\\Scripts\\python migracao\\testa_entrevista.py [--cenario N] [--manter]
Saída: dados\\entrevista_e2e.txt   (--manter não apaga as entrevistas de teste do banco)
"""
import argparse
import base64
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ["SOSIA_MOCK"] = "0"
os.environ["SOSIA_TOKEN"] = "teste-e2e-local"
from fastapi.testclient import TestClient  # noqa: E402

from nucleo import config, db  # noqa: E402
from servico.app import app  # noqa: E402

H = {"X-SOSIA-Token": "teste-e2e-local"}
GENERICAS = {
    "onde_ocorre": "Na tela de cadastro, ao clicar no botão Salvar.",
    "o_que_aconteceu": "Aparece uma tela de erro e nada é gravado.",
    "como_reproduzir": "1) Abrir o cadastro; 2) preencher os campos; 3) clicar em Salvar.",
    "desde_quando_quem": "Desde segunda-feira, todos os usuários da área.",
    "funcionalidade": "A tela que vocês indicaram.",
    "exemplo_concreto": "Por exemplo, no registro 1234 o valor deveria ser R$ 1.500,00 e hoje aparece R$ 1.000,00.",
    "fora_de_escopo": "Nada.",
    "outros_sistemas": "Não.",
    "quem_usa": "A equipe da gerência, cerca de 10 pessoas.",
    "informacoes_guardadas": "Número do processo, data, valor, situação e observação.",
    "o_que_faz": "Cadastra, consulta e gera um relatório mensal com o total por situação.",
    "o_que_muda": "Deve passar a considerar a nova regra informada.",
    "gatilho": "Só consulta, não grava nada no outro sistema.",
}


def png_erro_500():
    try:
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (520, 90), "white")
        ImageDraw.Draw(im).text((10, 30), "HTTP Status 500 - Internal Server Error  /gai/alienacao/salvar.do", fill="black")
        b = io.BytesIO()
        im.save(b, "PNG")
        return [dict(nome="erro500.png", mime="image/png", base64=base64.b64encode(b.getvalue()).decode())]
    except ImportError:
        return []


CENARIOS = [
    dict(nome="1. Corretivo com print de HTTP 500", sigla="GAI", intencao="PAROU_DE_FUNCIONAR", imagens=png_erro_500(),
         relato="Ao salvar a alteração de uma alienação aparece a tela de erro do print e a alteração não é gravada. Preciso "
                "atualizar o endereço de correspondência do comprador.",
         respostas={"desde_quando_quem": "Desde ontem à tarde. Acontece com todos da GEPAI."}),
    dict(nome="2. Evolutivo existente (funcionalidade pelo RAG)", sigla="GAI", intencao="MUDAR_EXISTENTE",
         relato="Na consulta de alienações, preciso que apareça também a data da escritura e que dê para filtrar por ela.",
         respostas={"funcionalidade": "Sim, é a consulta de alienações (menu Alienação > Consultar).",
                    "exemplo_concreto": "Filtrar as alienações com escritura entre 01/01/2026 e 31/03/2026 e ver a data na lista."}),
    dict(nome="3. Evolutivo novo com integração Benner", sigla="GAI", intencao="ALGO_NOVO",
         relato="Precisamos de um cadastro de acordos de parcelamento de dívida dos prestamistas, que busque no Benner os "
                "pagamentos já feitos para calcular o saldo.",
         respostas={"gatilho": "Só busca os pagamentos no Benner; não grava nada lá.",
                    "outros_sistemas": "Busca os pagamentos no Benner."}),
    dict(nome="4. Pedido misto (corretivo + evolutivo)", sigla="GAI", intencao="PAROU_DE_FUNCIONAR",
         relato="O relatório de imóveis por leilão está saindo em branco desde a semana passada. Aproveitando, também queria "
                "incluir nesse relatório a coluna com o valor de avaliação do imóvel.",
         respostas={}),
    dict(nome="5. 'Erro' que é regra documentada", sigla="GAI", intencao="PAROU_DE_FUNCIONAR",
         relato="O sistema não permite emitir o boleto da alienação de um prestamista. Clico em gerar boleto e ele bloqueia. "
                "O prestamista está inscrito no SERASA, mas mesmo assim precisamos emitir o boleto para ele negociar.",
         respostas={}, regra=dict(aceita=True, comentario="Liberar a emissão do boleto para prestamista no SERASA quando ele "
                                                           "estiver negociando a dívida.")),
    dict(nome="6. Slot raso bloqueia o contrato", sigla="GAI", intencao="MUDAR_EXISTENTE",
         relato="Ajustar a tela de leiloeiros, está errado.", respostas="raso"),
]


def resposta_para(cen, p):
    if cen["respostas"] == "raso":
        return "ok"
    return cen["respostas"].get(p["slot"]) or GENERICAS.get(p["slot"]) or "Não sei."


def roda(cli, cen, out):
    out(f"\n{'=' * 100}\n{cen['nome']}  [{cen['sigla']} / {cen['intencao']}]\nRelato: {cen['relato']}")
    r = cli.post("/entrevistas", headers=H, json=dict(cd_sistema=0, sigla=cen["sigla"], intencao=cen["intencao"],
                                                      relato=cen["relato"], imagens=cen.get("imagens", [])))
    assert r.status_code == 200, r.text
    e = r.json()
    while True:
        out(f"\n-- rodada {e['rodada']}  nota {e['nota']}  IA={e['analise_ia']}  pronto={e['pronto']}")
        for s in e["segmentos"]:
            out(f"   segmento {s['n']} {s['tipo']}: {s['resumo']}")
            for x in s["slots"]:
                out(f"      [{'x' if x['preenchido'] else ' '}] {x['codigo']}: {(x['valor'] or '')[:110]}")
        if e["candidatos_funcionalidade"]:
            out("   candidatos: " + "; ".join(f"{c['nome']} ({c['similaridade']})" for c in e["candidatos_funcionalidade"]))
        if e["regra_documentada"]:
            rd = e["regra_documentada"]
            out(f"   REGRA DOCUMENTADA: {rd['secao']} — {rd['orientacao']}  [decisão: {e['decisao_regra']}]")
        if e["dicas_medidor"]:
            out("   dicas: " + " | ".join(e["dicas_medidor"]))
        for p in e["perguntas"]:
            out(f"   ? ({p['slot']}) {p['texto']}" + (f"  [dica: {p['dica']}]" if p.get("dica") else ""))
        if e["regra_documentada"] and not e["decisao_regra"] and cen.get("regra"):
            out(f"   >> usuário decide sobre a regra: {cen['regra']}")
            r = cli.post(f"/entrevistas/{e['id']}/regra", headers=H, json=cen["regra"])
            assert r.status_code == 200, r.text
            e = r.json()
            continue
        if e["pronto"]:
            break
        resp = [dict(id=p["id"], texto=resposta_para(cen, p)) for p in e["perguntas"]]
        for p, x in zip(e["perguntas"], resp):
            out(f"   > {p['slot']}: {x['texto']}")
        r = cli.post(f"/entrevistas/{e['id']}/respostas", headers=H, json=dict(respostas=resp))
        assert r.status_code == 200, r.text
        e = r.json()
    r = cli.post(f"/entrevistas/{e['id']}/contrato", headers=H)
    assert r.status_code == 200, r.text
    c = r.json()
    out(f"\n-- CONTRATO  pode_confirmar={c['pode_confirmar']}  nota {c['nota']}  IA={c['analise_ia']}")
    out(f"   título: {c['titulo']}\n   objetivo: {c['objetivo']}")
    for q in c["requisitos"]:
        out(f"   R{q['n']} [{q['tipo']}, seg {q['segmento']}] {q['descricao']}\n        aceite: {q['criterio_aceite']}")
    for k in ("fora_escopo", "duvidas"):
        if c[k]:
            out(f"   {k}: " + " | ".join(c[k]))
    for i in c["integracoes"]:
        out(f"   integração: {i['sistema']} {i['sentido']} — {i['descricao']}")
    if c["faltando"]:
        out("   FALTANDO: " + ", ".join(f"seg {f['segmento']} {f['rotulo']}" for f in c["faltando"]))
    with db.db() as conn:
        ev = conn.execute("""SELECT count(*) FILTER (WHERE tipo LIKE 'llm%%') n, coalesce(sum(custo_usd), 0)::float custo,
                                    coalesce(sum(segundos), 0)::float seg, count(*) FILTER (WHERE tipo = 'fallback') fb
                             FROM apf.entrevista_evento WHERE entrevista_id = %s""", (e["id"],)).fetchone()
    out(f"   custo: {ev['n']} chamadas, US$ {ev['custo']:.4f}, {ev['seg']:.0f} s de LLM" + (f", {ev['fb']} FALLBACK" if ev["fb"] else ""))
    return e["id"], ev["custo"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cenario", type=int)
    ap.add_argument("--manter", action="store_true")
    a = ap.parse_args()
    linhas = []

    def out(t):
        print(t, flush=True)
        linhas.append(t)
    out(f"modelo {config.MODELO_CHAT}, PII {config.MODO_PII}, embeddings {config.EMB}")
    cli = TestClient(app)
    out("saúde: " + cli.get("/saude").text)
    ids, total = [], 0.0
    for i, cen in enumerate(CENARIOS, 1):
        if a.cenario and a.cenario != i:
            continue
        eid, custo = roda(cli, cen, out)
        ids.append(eid)
        total += custo
    out(f"\nTOTAL: {len(ids)} entrevistas, US$ {total:.4f} (média US$ {total / max(len(ids), 1):.4f})")
    if not a.manter:
        with db.db() as conn:
            conn.execute("DELETE FROM apf.entrevista WHERE id = ANY(%s::uuid[])", (ids,))
    open(os.path.join(RAIZ, "dados", "entrevista_e2e.txt"), "w", encoding="utf-8").write("\n".join(linhas) + "\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
