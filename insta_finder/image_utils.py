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


def hash_similarity(a: int, b: int, bits: int) -> float:
    return 1.0 - bin(a ^ b).count("1") / bits


def crop_to_aspect(image: Image.Image, aspect: float) -> Image.Image:
    """가운데를 기준으로 가로/세로 비율(aspect = 가로/세로)에 맞게 자른다."""
    width, height = image.size
    if width / height > aspect:
        new_width = max(1, round(height * aspect))
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = max(1, round(width / aspect))
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


# 64비트(크기·압축 변화에 강함)와 256비트(서로 다른 사진을 더 잘 구분) 해시를 함께 쓴다
_HASH_SIZES = (8, 16)
_COMPARE_SIDE = 512


def _hashes(image: Image.Image) -> tuple[int, ...]:
    return tuple(dhash(image, size) for size in _HASH_SIZES)


def _score(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return sum(hash_similarity(x, y, size * size) for x, y, size in zip(a, b, _HASH_SIZES)) / len(
        _HASH_SIZES
    )


class QueryImage:
    """검색할 원본 사진. 후보 이미지와의 유사도(0.0~1.0)를 계산한다.

    인스타그램은 사진을 1:1, 4:5 등으로 잘라 올리고 검색 썸네일도 잘려 있는 경우가 많으므로
    원본 그대로, 원본을 후보 비율로 자른 것, 후보를 원본 비율로 자른 것 중 가장 높은 점수를 쓴다.
    """

    def __init__(self, image: Image.Image) -> None:
        self.image = image.copy()
        self.image.thumbnail((_COMPARE_SIDE, _COMPARE_SIDE), Image.Resampling.LANCZOS)
        self.aspect = self.image.width / self.image.height
        self._full = _hashes(self.image)
        self._cropped: dict[float, tuple[int, ...]] = {}

    def _hashes_for_aspect(self, aspect: float) -> tuple[int, ...]:
        key = round(aspect, 2)
        if key not in self._cropped:
            self._cropped[key] = _hashes(crop_to_aspect(self.image, key))
        return self._cropped[key]

    def similarity(self, candidate: Image.Image) -> float:
        candidate = candidate.copy()
        candidate.thumbnail((_COMPARE_SIDE, _COMPARE_SIDE), Image.Resampling.LANCZOS)
        candidate_aspect = candidate.width / candidate.height
        candidate_hashes = _hashes(candidate)
        scores = [_score(self._full, candidate_hashes)]
        if abs(candidate_aspect - self.aspect) > 0.02:
            scores.append(_score(self._hashes_for_aspect(candidate_aspect), candidate_hashes))
            scores.append(_score(self._full, _hashes(crop_to_aspect(candidate, self.aspect))))
        return max(scores)
