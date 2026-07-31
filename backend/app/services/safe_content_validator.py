"""Resource-bounded structural validation for untrusted document and image bytes."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
import re
import warnings
import zlib

from app.config.settings import settings

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:  # Trusted decoder is optional; callers must fail closed.
    Image = None
    UnidentifiedImageError = OSError

try:
    from pypdf import PdfReader
except ImportError:  # Trusted parser is optional; callers must fail closed.
    PdfReader = None

if Image is not None:
    Image.MAX_IMAGE_PIXELS = 50_000_000

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}
_JPEG_STANDALONE_MARKERS = {0x01, *range(0xD0, 0xD8)}
_PDF_HEADER = re.compile(rb"%PDF-(?:1\.[0-7]|2\.0)(?:\r?\n|\r)")
_PDF_OBJECT = re.compile(rb"(?<!\d)(\d+)\s+(\d+)\s+obj(?=[\s<\[])")
_PDF_STARTXREF = re.compile(rb"startxref\s+(\d+)\s+%%EOF\s*\Z")
_MINIMUM_PILLOW_VERSION = (10, 3, 0)
_MINIMUM_PYPDF_VERSION = (4, 2, 0)
_STABLE_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:\.post\d+)?$")


def _has_valid_png_structure(content: bytes) -> bool:
    if len(content) < 57 or not content.startswith(_PNG_SIGNATURE):
        return False
    offset = len(_PNG_SIGNATURE)
    saw_header = False
    saw_data = False
    saw_end = False
    while offset + 12 <= len(content):
        chunk_length = int.from_bytes(content[offset:offset + 4], "big")
        chunk_type = content[offset + 4:offset + 8]
        data_start = offset + 8
        data_end = data_start + chunk_length
        chunk_end = data_end + 4
        if chunk_end > len(content):
            return False
        expected_crc = int.from_bytes(content[data_end:chunk_end], "big")
        actual_crc = zlib.crc32(chunk_type + content[data_start:data_end]) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            return False
        if not saw_header:
            if chunk_type != b"IHDR" or chunk_length != 13:
                return False
            width = int.from_bytes(content[data_start:data_start + 4], "big")
            height = int.from_bytes(content[data_start + 4:data_start + 8], "big")
            if width == 0 or height == 0:
                return False
            saw_header = True
        elif chunk_type == b"IHDR":
            return False
        if chunk_type == b"IDAT":
            if saw_end or chunk_length == 0:
                return False
            saw_data = True
        elif chunk_type == b"IEND":
            if chunk_length != 0 or not saw_data:
                return False
            saw_end = True
            return chunk_end == len(content)
        offset = chunk_end
    return saw_header and saw_data and saw_end


def _has_valid_jpeg_structure(content: bytes) -> bool:
    if len(content) < 16 or not content.startswith(b"\xff\xd8"):
        return False
    offset = 2
    saw_frame = False
    saw_scan = False
    while offset < len(content):
        if content[offset] != 0xFF:
            return False
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        if offset >= len(content):
            return False
        marker = content[offset]
        offset += 1
        if marker == 0xD9:
            return saw_frame and saw_scan and offset == len(content)
        if marker in _JPEG_STANDALONE_MARKERS:
            continue
        if offset + 2 > len(content):
            return False
        segment_length = int.from_bytes(content[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(content):
            return False
        segment_start = offset + 2
        segment_end = offset + segment_length
        if marker in _JPEG_SOF_MARKERS:
            if segment_length < 8:
                return False
            height = int.from_bytes(
                content[segment_start + 1:segment_start + 3],
                "big",
            )
            width = int.from_bytes(
                content[segment_start + 3:segment_start + 5],
                "big",
            )
            if width == 0 or height == 0:
                return False
            saw_frame = True
        if marker != 0xDA:
            offset = segment_end
            continue
        if not saw_frame:
            return False
        saw_scan = True
        offset = segment_end
        while offset < len(content):
            marker_start = content.find(b"\xff", offset)
            if marker_start < 0 or marker_start + 1 >= len(content):
                return False
            next_byte = content[marker_start + 1]
            if next_byte == 0x00 or next_byte in range(0xD0, 0xD8):
                offset = marker_start + 2
                continue
            offset = marker_start
            break
    return False


def _has_valid_webp_structure(content: bytes) -> bool:
    if (
        len(content) < 20
        or content[:4] != b"RIFF"
        or content[8:12] != b"WEBP"
        or int.from_bytes(content[4:8], "little") + 8 != len(content)
    ):
        return False
    chunk_type = content[12:16]
    chunk_length = int.from_bytes(content[16:20], "little")
    padded_length = chunk_length + (chunk_length & 1)
    if 20 + padded_length > len(content):
        return False
    if chunk_type == b"VP8X":
        return chunk_length == 10
    return chunk_type in {b"VP8 ", b"VP8L"} and chunk_length > 0


def _has_valid_pdf_structure(content: bytes) -> bool:
    if len(content) < 64 or _PDF_HEADER.match(content) is None:
        return False
    startxref_match = _PDF_STARTXREF.search(content)
    if startxref_match is None:
        return False
    xref_offset = int(startxref_match.group(1))
    if xref_offset <= 0 or xref_offset + 4 > startxref_match.start():
        return False
    if content[xref_offset:xref_offset + 4] != b"xref":
        return False
    xref_section = content[xref_offset:startxref_match.start()]
    trailer_offset = xref_section.find(b"trailer")
    if trailer_offset < 0:
        return False
    trailer = xref_section[trailer_offset:]
    root_match = re.search(rb"/Root\s+(\d+)\s+(\d+)\s+R", trailer)
    if root_match is None or re.search(rb"/Size\s+\d+", trailer) is None:
        return False
    objects = {
        (int(number), int(generation))
        for number, generation in _PDF_OBJECT.findall(content[:xref_offset])
    }
    root_reference = (int(root_match.group(1)), int(root_match.group(2)))
    return bool(objects) and root_reference in objects


def _package_version_satisfies(
    distribution: str,
    minimum: tuple[int, int, int],
) -> bool:
    try:
        installed = version(distribution)
    except PackageNotFoundError:
        return False
    match = _STABLE_VERSION.fullmatch(installed)
    if match is None:
        return False
    return tuple(int(part) for part in match.groups()) >= minimum


def trusted_media_pipeline_available() -> bool:
    """Enable only through an explicit flag and pinned minimum parser versions."""
    return bool(
        settings.ENABLE_TRUSTED_MEDIA_PIPELINE
        and Image is not None
        and PdfReader is not None
        and _package_version_satisfies("Pillow", _MINIMUM_PILLOW_VERSION)
        and _package_version_satisfies("pypdf", _MINIMUM_PYPDF_VERSION)
    )


def trusted_image_decoder_available() -> bool:
    return trusted_media_pipeline_available()


def trusted_pdf_parser_available() -> bool:
    return trusted_media_pipeline_available()


def _verify_with_pillow(
    expected_format: str,
    content: bytes,
    structural_check: bool,
) -> bool:
    if not trusted_media_pipeline_available() or not structural_check:
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with Image.open(BytesIO(content)) as image:
                if image.format != expected_format:
                    return False
                if image.width <= 0 or image.height <= 0:
                    return False
                image.verify()
            with Image.open(BytesIO(content)) as image:
                if image.format != expected_format:
                    return False
                image.load()
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Warning):
        return False
    return True


def is_valid_png(content: bytes) -> bool:
    return _verify_with_pillow("PNG", content, _has_valid_png_structure(content))


def is_valid_jpeg(content: bytes) -> bool:
    return _verify_with_pillow(
        "JPEG",
        content,
        _has_valid_jpeg_structure(content),
    )


def is_valid_webp(content: bytes) -> bool:
    return _verify_with_pillow(
        "WEBP",
        content,
        _has_valid_webp_structure(content),
    )


def is_valid_pdf(content: bytes) -> bool:
    if not trusted_media_pipeline_available() or not _has_valid_pdf_structure(content):
        return False
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        root = reader.trailer.get("/Root")
        if root is None:
            return False
        catalog = root.get_object()
        if catalog.get("/Type") != "/Catalog":
            return False
        if len(reader.pages) < 1:
            return False
        return bool(reader.xref)
    except Exception:
        return False


def is_valid_image(content_type: str, content: bytes) -> bool:
    validator = {
        "image/png": is_valid_png,
        "image/jpeg": is_valid_jpeg,
        "image/webp": is_valid_webp,
    }.get(content_type)
    return validator(content) if validator is not None else False
