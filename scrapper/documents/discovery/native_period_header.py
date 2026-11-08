"""Isolated local PDF header reader. No OCR, network, or document writes."""
import hashlib
import json
from pathlib import Path
import sys


def read_headers(item):
    import pypdfium2 as pdfium
    path = Path(item['local_path'])
    if not path.is_file() or path.stat().st_size != item['byte_size']:
        return []
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != item['content_sha256']:
        return []
    texts = []
    with pdfium.PdfDocument(data) as pdf:
        for number in range(min(3, len(pdf))):
            page = pdf[number]
            try:
                textpage = page.get_textpage()
                try:
                    texts.append((f"verified_content:{item['content_id']}:page:{number+1}",
                                  textpage.get_text_range(count=min(16000, textpage.count_chars()))))
                finally:
                    textpage.close()
            finally:
                page.close()
    return texts


if __name__ == '__main__':
    try:
        print(json.dumps(read_headers(json.load(sys.stdin))))
    except Exception:
        # A malformed/unreadable document stays unresolved for a later pass.
        print('[]')
