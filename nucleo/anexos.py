"""Texto dos documentos anexados ao pedido (contrato 1.7.0; pedido docs/sosia-1.7.0-documentos-anexados.md).

  PDF   camada de texto (pypdf). PDF digitalizado (sem texto) não é lido: a imagem não tem OCR de página (sem renderizador de PDF);
        volta `lido: false` com o motivo, e a pergunta do anexo continua.
  DOCX  parágrafos e tabelas na ordem do documento, direto do word/document.xml (sem dependência).
  TXT   UTF-8 (com ou sem BOM); se não decodificar, CP1252 (arquivos de usuário no Windows).

`extrai` nunca levanta exceção: arquivo que não abre volta `lido: false` com o motivo em texto curto para o usuário.
A anonimização e os tetos de tamanho ficam em pii.prepara_documentos.
"""
import base64
import binascii
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile

logging.getLogger("pypdf").setLevel(logging.ERROR)     # avisos de fonte e de xref de PDFs reais: muitos e inofensivos

MIME_PDF = "application/pdf"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_TXT = "text/plain"
MIMES = (MIME_PDF, MIME_DOCX, MIME_TXT)
MIN_PDF = 50                    # menos que isso de texto num PDF: digitalizado
MAX_BRUTO = 40_000              # para de ler páginas depois disso (o teto por documento é bem menor)
MAX_XML = 50_000_000            # word/document.xml descompactado (proteção contra zip-bomba)
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # DOCX protegido por senha (e .doc antigo) é um arquivo OLE, não um zip

MOTIVO_DIGITALIZADO = "sem texto (PDF digitalizado)"
MOTIVO_SENHA = "arquivo protegido por senha"
MOTIVO_CORROMPIDO = "arquivo corrompido"
MOTIVO_VAZIO = "arquivo sem texto"
MOTIVO_SO_IMAGENS = "só imagens (sem texto)"      # Word com prints colados (10 de 55 DOCX reais do SOS)
MOTIVO_TIPO = "tipo não lido"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
RE_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def limpa(texto):
    """Sem caracteres de controle, espaços repetidos e mais de uma linha em branco seguida."""
    t = RE_CONTROLE.sub(" ", (texto or "").replace("\r\n", "\n").replace("\r", "\n"))
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _resultado(lido, texto="", paginas=None, motivo=None):
    return dict(lido=lido, texto=texto if lido else "", paginas=paginas, motivo=None if lido else motivo)


def _pdf(dados):
    from pypdf import PdfReader
    try:
        r = PdfReader(io.BytesIO(dados))
        if r.is_encrypted:
            try:
                if not r.decrypt(""):                 # muitos PDFs só têm senha de dono (abrem sem senha)
                    return _resultado(False, motivo=MOTIVO_SENHA)
            except Exception:  # noqa: BLE001  (inclui a falta do pacote de criptografia para AES)
                return _resultado(False, motivo=MOTIVO_SENHA)
        paginas, partes, total = len(r.pages), [], 0
        for p in r.pages:
            t = p.extract_text() or ""
            partes.append(t)
            total += len(t)
            if total > MAX_BRUTO:
                break
    except Exception:  # noqa: BLE001
        return _resultado(False, motivo=MOTIVO_CORROMPIDO)
    texto = limpa("\n\n".join(partes))
    if len(texto) < MIN_PDF:
        return _resultado(False, paginas=paginas, motivo=MOTIVO_DIGITALIZADO)
    return _resultado(True, texto, paginas)


def _texto_docx(el):
    """Texto de um elemento do corpo, na ordem: parágrafo -> linha; tabela -> uma linha por linha da tabela, células com " | "."""
    if el.tag == W + "p":
        out = []
        for x in el.iter():
            if x.tag == W + "t" and x.text:
                out.append(x.text)
            elif x.tag == W + "tab":
                out.append(" ")
            elif x.tag in (W + "br", W + "cr"):
                out.append("\n")
        return ["".join(out)]
    if el.tag == W + "tbl":
        linhas = []
        for tr in el.findall(W + "tr"):
            celulas = [" ".join(l for p in tc for l in _texto_docx(p) if l.strip()) for tc in tr.findall(W + "tc")]
            linhas.append(" | ".join(celulas))
        return linhas
    return [l for filho in el for l in _texto_docx(filho)]       # sdt, sdtContent, customXml…: o que houver dentro


def _docx(dados):
    if dados[:8] == OLE:
        return _resultado(False, motivo=MOTIVO_SENHA)
    try:
        z = zipfile.ZipFile(io.BytesIO(dados))
        info = z.getinfo("word/document.xml")
        if info.file_size > MAX_XML:
            return _resultado(False, motivo=MOTIVO_CORROMPIDO)
        corpo = ET.fromstring(z.read(info)).find(W + "body")
        linhas = [l for el in (corpo if corpo is not None else []) for l in _texto_docx(el)]
        imagens = any(n.startswith("word/media/") for n in z.namelist())
    except Exception:  # noqa: BLE001
        return _resultado(False, motivo=MOTIVO_CORROMPIDO)
    texto = limpa("\n".join(linhas))
    return _resultado(True, texto) if texto else _resultado(False, motivo=MOTIVO_SO_IMAGENS if imagens else MOTIVO_VAZIO)


def _txt(dados):
    try:
        bruto = dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        bruto = dados.decode("cp1252", errors="replace")
    texto = limpa(bruto)
    return _resultado(True, texto) if texto else _resultado(False, motivo=MOTIVO_VAZIO)


def extrai(doc):
    """{nome, mime, base64} -> {lido, texto (limpo, sem corte), paginas (só PDF), motivo}."""
    try:
        dados = base64.b64decode(doc["base64"], validate=False)
    except (binascii.Error, ValueError):
        return _resultado(False, motivo=MOTIVO_CORROMPIDO)
    if not dados:
        return _resultado(False, motivo=MOTIVO_VAZIO)
    leitor = {MIME_PDF: _pdf, MIME_DOCX: _docx, MIME_TXT: _txt}.get(doc.get("mime"))
    return leitor(dados) if leitor else _resultado(False, motivo=MOTIVO_TIPO)
