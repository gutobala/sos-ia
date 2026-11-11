"""Sincronização da wiki pela linha de comando. Simula por padrão.

  python -m nucleo.wiki                      simula: baixa o que mudou, converte e mostra o que seria gravado
  python -m nucleo.wiki --aplicar            grava (só o que mudou)
  python -m nucleo.wiki --sistema gr         só os trechos do sistema gr (a conversão continua sendo da wiki inteira)
  python -m nucleo.wiki --tudo               baixa todas as páginas de novo (ignora a data e o tamanho do cache)
No container:  docker exec sosia_api python -m nucleo.wiki [--aplicar]
"""
import argparse
import json
import sys

from .sincroniza import SincronizacaoAbortada, executa, resumo_curto


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m nucleo.wiki", description=__doc__.split("\n")[0])
    ap.add_argument("--aplicar", action="store_true", help="grava no banco (sem isto, só simula)")
    ap.add_argument("--sistema", help="só os trechos deste sistema da wiki (ex.: gr)")
    ap.add_argument("--tudo", action="store_true", help="baixa todas as páginas de novo")
    ap.add_argument("--forcar", action="store_true", help="passa pelas travas de segurança (muitas páginas sumindo)")
    ap.add_argument("--teto", type=float, default=1.0, help="custo máximo dos vetores novos, em US$ (padrão 1,00)")
    ap.add_argument("--json", action="store_true", help="imprime o relatório completo em JSON")
    a = ap.parse_args(argv)
    try:
        res = executa(aplicar=a.aplicar, sistema=a.sistema, tudo=a.tudo, forcar=a.forcar, teto_usd=a.teto)
    except SincronizacaoAbortada as e:
        print(f"ABORTADA: {e}")
        return 2
    r = resumo_curto(res, 40)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    else:
        rag = r["rag"]
        print(f"\n{'APLICADO' if a.aplicar else 'SIMULAÇÃO (nada gravado; use --aplicar)'}" + (f" — sistema {a.sistema}" if a.sistema else ""))
        for k in ("novas", "alteradas", "removidas"):
            print(f"  páginas {k}: {len(res['rag'][k])}")
            for p in rag[k]:
                print(f"     {p}")
        print(f"  páginas iguais: {rag['iguais']}")
        print(f"  vetores: {r['vetores']}")
        print(f"  referências: {r.get('referencias')}")
        if a.aplicar:
            print(f"  trechos por sistema: {rag.get('por_sistema')}")
            print(f"  menus refeitos: {r.get('menus')}")
        print(f"  dados pessoais restantes nos trechos novos: {r.get('pii_restante') or 'nenhum'}")
        if r["erros"]:
            print("  ERROS:")
            for e in r["erros"]:
                print(f"     {e}")
        print(f"  {r['segundos']}s")
    return 0 if not res["erros"] else 1


if __name__ == "__main__":
    sys.exit(main())
