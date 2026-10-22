"""
Combines a Wire Log entry's (or a whole drum's) evidence files -- a mix
of photos and PDFs -- into one merged PDF, for sending to a client or
keeping as a single hard-copy-equivalent record.
"""
import io

from PIL import Image as PILImage
from pypdf import PdfReader, PdfWriter

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff")


def _image_to_pdf_bytes(file_field):
    """Converts a single image file into a one-page PDF."""
    file_field.seek(0)
    img = PILImage.open(file_field)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    buf.seek(0)
    return buf


def combine_evidence_to_pdf(evidence_list):
    """`evidence_list` is any iterable of WireLogEvidence objects. Returns
    a BytesIO of the combined PDF, or None if none of the files could be
    included (e.g. all were an unsupported type)."""
    writer = PdfWriter()
    included = 0
    for evidence in evidence_list:
        name = evidence.file.name.lower()
        evidence.file.open("rb")
        try:
            if name.endswith(IMAGE_EXTENSIONS):
                reader = PdfReader(_image_to_pdf_bytes(evidence.file))
            elif name.endswith(".pdf"):
                evidence.file.seek(0)
                reader = PdfReader(evidence.file)
            else:
                continue
            for page in reader.pages:
                writer.add_page(page)
            included += 1
        finally:
            evidence.file.close()

    if included == 0:
        return None
    output = io.BytesIO()
    writer.write(output)
    output.seek(0)
    return output
