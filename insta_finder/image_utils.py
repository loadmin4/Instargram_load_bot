"""이미지 전처리와 지각 해시(perceptual hash) 비교."""

from __future__ import annotations

import io

from PIL import Image, ImageOps

try:  # 아이폰 HEIC 사진 지원 (선택 설치: pip install pillow-heif)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

# SerpApi 이미지 업로드 한도는 500KB. 여유를 둔다.
MAX_UPLOAD_BYTES = 480 * 1024
MAX_UPLOAD_SIDE = 1600


class InvalidImageError(ValueError):
    """이미지로 읽을 수 없는 데이터."""


def load_image(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # Pillow 는 형식에 따라 다양한 예외를 던진다
        raise InvalidImageError("이미지 파일을 읽을 수 없습니다.") from exc
    # 휴대폰 사진의 회전 정보(EXIF)를 반영
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        background = Image.new("RGB", image.size, (255, 255, 255))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.getchannel("A"))
        image = background
    return image


def prepare_for_upload(
    data: bytes, max_bytes: int = MAX_UPLOAD_BYTES, max_side: int = MAX_UPLOAD_SIDE
) -> bytes:
    """업로드용 JPEG 으로 변환한다.

    - 크기 제한(기본 480KB) 안으로 들어올 때까지 품질/해상도를 낮춘다.
    - 다시 인코딩하므로 GPS 등 EXIF 메타데이터는 제거된다.
    """
    image = load_image(data)
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    while True:
        for quality in (90, 80, 70, 60, 50, 40):
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=quality, optimize=True)
            if buf.tell() <= max_bytes:
                return buf.getvalue()
        if max(image.size) <= 200:
            return buf.getvalue()
        new_size = (max(1, int(image.width * 0.75)), max(1, int(image.height * 0.75)))
        image = image.resize(new_size, Image.Resampling.LANCZOS)


def dhash(image: Image.Image, hash_size: int = 8) -> int:
    """차이 해시(dHash). 크기 변경·재압축에 강하다."""
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = gray.tobytes()  # 8비트 흑백: 픽셀당 1바이트
    width = hash_size + 1
    bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * width + col]
            right = pixels[row * width + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def center_crop_square(image: Image.Image) -> Image.Image:
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    return image.crop((left, top, left + side, top + side))


def image_hashes(image: Image.Image) -> tuple[int, int]:
    """원본과 가운데 정사각형 크롭의 해시.

    인스타그램/검색 썸네일은 정사각형으로 잘리는 경우가 많아 둘 다 비교한다.
    """
    return dhash(image), dhash(center_crop_square(image))


def hash_similarity(a: int, b: int, bits: int = 64) -> float:
    return 1.0 - bin(a ^ b).count("1") / bits


def best_similarity(query_hashes: tuple[int, ...], candidate: Image.Image) -> float:
    candidate_hashes = image_hashes(candidate)
    return max(hash_similarity(q, c) for q in query_hashes for c in candidate_hashes)
