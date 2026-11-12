"""1.7.0: texto dos documentos anexados (PDF, DOCX, TXT), anonimização e tetos. Arquivos gerados aqui, sem rede e sem banco."""
import base64
import io
import zipfile

from nucleo import anexos, config, pii

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def pdf(*paginas):
    """PDF mínimo com uma linha de texto (Helvetica) por página; sem texto = página em branco (como um PDF digitalizado)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for texto in paginas:
        conteudo = f"BT /F1 11 Tf 40 760 Td ({texto}) Tj ET" if texto else ""
        objs.append(f"<< /Length {len(conteudo)} >>\nstream\n{conteudo}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offs = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offs:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def docx(corpo_xml, imagem=False):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<w:document xmlns:w="{W_NS}"><w:body>{corpo_xml}</w:body></w:document>')
        if imagem:
            z.writestr("word/media/image1.png", b"\x89PNG")
    return b.getvalue()


def doc(nome, mime, dados):
    return dict(nome=nome, mime=mime, base64=base64.b64encode(dados).decode())


def p(texto):
    return f"<w:p><w:r><w:t>{texto}</w:t></w:r></w:p>"


def test_pdf_com_texto_paginas_e_digitalizado():
    x = anexos.extrai(doc("e.pdf", anexos.MIME_PDF, pdf("Relatorio de vendas por lote e por mes de referencia", "Segunda pagina")))
    assert x["lido"] and x["paginas"] == 2 and "Relatorio de vendas" in x["texto"] and "Segunda pagina" in x["texto"]
    y = anexos.extrai(doc("tela.pdf", anexos.MIME_PDF, pdf("", "")))
    assert not y["lido"] and y["paginas"] == 2 and y["motivo"] == anexos.MOTIVO_DIGITALIZADO and y["texto"] == ""


def test_pdf_com_senha_e_corrompido():
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for pg in PdfReader(io.BytesIO(pdf("conteudo secreto da especificacao do relatorio"))).pages:
        w.add_page(pg)
    w.encrypt(user_password="segredo", algorithm="RC4-128")
    b = io.BytesIO()
    w.write(b)
    assert anexos.extrai(doc("s.pdf", anexos.MIME_PDF, b.getvalue()))["motivo"] == anexos.MOTIVO_SENHA
    assert anexos.extrai(doc("c.pdf", anexos.MIME_PDF, b"isto nao e um pdf"))["motivo"] == anexos.MOTIVO_CORROMPIDO


def test_docx_na_ordem_com_tabela_e_controles():
    corpo = (p("Objetivo: novo relatório") + "<w:tbl><w:tr><w:tc>" + p("Campo") + "</w:tc><w:tc>" + p("Regra") + "</w:tc></w:tr>"
             "<w:tr><w:tc>" + p("Lote") + "</w:tc><w:tc>" + p("obrigatório") + "</w:tc></w:tr></w:tbl>"
             "<w:sdt><w:sdtContent>" + p("Dentro do controle") + "</w:sdtContent></w:sdt>" + p("Fim"))
    x = anexos.extrai(doc("e.docx", anexos.MIME_DOCX, docx(corpo)))
    assert x["lido"] and x["paginas"] is None
    assert x["texto"].split("\n") == ["Objetivo: novo relatório", "Campo | Regra", "Lote | obrigatório", "Dentro do controle", "Fim"]
    assert anexos.extrai(doc("v.docx", anexos.MIME_DOCX, docx("")))["motivo"] == anexos.MOTIVO_VAZIO
    assert anexos.extrai(doc("prints.docx", anexos.MIME_DOCX, docx("<w:p/>", imagem=True)))["motivo"] == anexos.MOTIVO_SO_IMAGENS
    assert anexos.extrai(doc("s.docx", anexos.MIME_DOCX, anexos.OLE + b"\0" * 100))["motivo"] == anexos.MOTIVO_SENHA
    assert anexos.extrai(doc("c.docx", anexos.MIME_DOCX, b"PK\x03\x04quebrado"))["motivo"] == anexos.MOTIVO_CORROMPIDO


def test_txt_utf8_e_cp1252():
    assert anexos.extrai(doc("a.txt", anexos.MIME_TXT, "﻿Ação  na   tela\r\n\r\n\r\n\r\nfim".encode("utf-8")))["texto"] == \
        "Ação na tela\n\nfim"
    assert anexos.extrai(doc("b.txt", anexos.MIME_TXT, "Relatório de cobrança".encode("cp1252")))["texto"] == "Relatório de cobrança"
    assert anexos.extrai(doc("c.txt", anexos.MIME_TXT, b"   \r\n "))["motivo"] == anexos.MOTIVO_VAZIO
    assert anexos.extrai(dict(nome="x", mime="text/plain", base64="***"))["lido"] is False         # base64 inválido
    assert anexos.extrai(doc("x.xlsx", "application/vnd.ms-excel", b"x"))["motivo"] == anexos.MOTIVO_TIPO


def test_prepara_documentos_anonimiza_corta_e_soma():
    assert config.MODO_PII == "local"
    grande = "Regra do relatório. " * 600                                   # ~12.000 caracteres
    docs = [doc("Pedido do gerente Joao Silva.txt", anexos.MIME_TXT, f"CPF 123.456.789-09 {grande}".encode()),
            doc("dois.txt", anexos.MIME_TXT, grande.encode()),
            doc("tres.txt", anexos.MIME_TXT, grande.encode()),
            ]
    texto, lidos = pii.prepara_documentos(docs)
    assert "123.456.789-09" not in texto and "[CPF]" in texto
    assert "Joao Silva" not in texto and lidos[0]["nome"] == "Pedido do gerente Joao Silva.txt"     # o nome original volta ao SOS
    assert lidos[0]["caracteres"] == pii.MAX_DOC and pii.MAX_DOCS - pii.MAX_DOC - 5 <= lidos[1]["caracteres"] <= pii.MAX_DOCS - pii.MAX_DOC
    assert lidos[0]["cortado"] and lidos[1]["cortado"]
    assert lidos[2] == dict(nome="tres.txt", lido=False, paginas=None, caracteres=0, cortado=False,
                            motivo="limite de texto dos anexos atingido")
    assert texto.count("[Texto do anexo") == 2 and pii.documento_util(lidos)
    curto, l2 = pii.prepara_documentos([doc("c.txt", anexos.MIME_TXT, b"so isto")])
    assert curto.startswith("[Texto do anexo c.txt]: so isto") and l2[0]["lido"] and not pii.documento_util(l2)
    assert pii.prepara_documentos([]) == ("", [])
