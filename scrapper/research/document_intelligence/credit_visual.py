"""Explicit visual review bindings. Never OCR, replace native text, or infer support.

A visual correction is a new reviewer-derived atom, with retained parent identity.
The trusted review pins the entire atom AND its original-page rendering/crop.
"""
import hashlib
import io
import math
from pathlib import Path

from .evidence import canonical_hash


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def render_page(pdf, page):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(pdf) as doc:
        if type(page) is not int or not 1 <= page <= len(doc):
            raise ValueError('Invalid physical page')
        p = doc[page - 1]
        size = list(p.get_size())
        bitmap = p.render(scale=2)
        image = bitmap.to_pil().copy()
        bitmap.close(); p.close()
    stream = io.BytesIO(); image.save(stream, format='PNG')
    return stream.getvalue(), image, size


def bind_visual(pdf_path, context, extraction, native_support, page, bbox, image_path):
    """Freeze a page image; bbox uses rendered-page top-left points, at 2 px/pt.

    native_support retains the exact damaged locator, even when it has no bbox.
    Choosing the visual crop is an explicit source-review operation.
    """
    from .credit_v1 import attach_evidence
    native = attach_evidence(context, extraction, native_support)
    if not native or any(e['page'] != page for e in native):
        raise ValueError('Visual/native page association mismatch')
    body = Path(pdf_path).read_bytes()
    if _sha(body) != context.content_sha256:
        raise ValueError('Original PDF changed')
    png, image, size = render_page(body, page)
    if (len(bbox) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in bbox)
            or not 0 <= bbox[0] < bbox[2] <= size[0] or not 0 <= bbox[1] < bbox[3] <= size[1]):
        raise ValueError('Invalid original-page coordinates')
    crop = image.crop(tuple(round(x * 2) for x in bbox))
    stream = io.BytesIO(); crop.save(stream, format='PNG')
    target = Path(image_path)
    if target.exists():
        if target.read_bytes() != png:
            raise ValueError('Page image changed')
    else:
        with target.open('xb') as f: f.write(png)
    binding = {'source_document_sha256': context.content_sha256, 'canonical_sha256': canonical_hash(extraction),
        'pdf_path': str(Path(pdf_path).resolve()), 'page': page, 'page_size_points': size,
        'bbox': list(bbox), 'coordinate_system': 'rendered-page top-left points', 'scale': 2,
        'renderer': 'pypdfium2', 'renderer_version': __import__('pypdfium2').PYPDFIUM_INFO.version,
        'image_path': str(target.resolve()), 'image_sha256': _sha(png), 'crop_sha256': _sha(stream.getvalue()),
        'native_support': native, 'verification_method': 'visual_pdf_inspection'}
    binding['visual_id'] = canonical_hash(binding)
    return binding


def validate_visual(binding, context, extraction, support):
    from .credit_v1 import attach_evidence
    expected = dict(binding); visual_id = expected.pop('visual_id')
    if canonical_hash(expected) != visual_id:
        raise ValueError('Visual identity changed')
    native = attach_evidence(context, extraction, support)
    if not binding['native_support'] or any(e not in native for e in binding['native_support']):
        raise ValueError('Visual evidence not associated with atomic native support')
    # Read-only validation: do not recreate missing artifacts during admission.
    if _sha(Path(binding['image_path']).read_bytes()) != binding['image_sha256']:
        raise ValueError('Page image changed')
    rebuilt = bind_visual(binding['pdf_path'], context, extraction, binding['native_support'],
                          binding['page'], binding['bbox'], binding['image_path'])
    if rebuilt != binding:
        raise ValueError('Original page/crop binding changed')
    return binding
