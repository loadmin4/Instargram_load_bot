import io
import os

import pytest
from PIL import Image

from insta_finder import image_utils
from tests.conftest import make_image, to_bytes


def test_prepare_for_upload_fits_limit_and_strips_exif():
    # 압축이 잘 안 되는 노이즈 이미지로 최악의 경우를 확인
    noisy = Image.frombytes("RGB", (3000, 2000), os.urandom(3000 * 2000 * 3))
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"  # Make
    original = to_bytes(noisy, quality=100, exif=exif)
    assert len(original) > image_utils.MAX_UPLOAD_BYTES

    prepared = image_utils.prepare_for_upload(original)

    assert len(prepared) <= image_utils.MAX_UPLOAD_BYTES
    result = Image.open(io.BytesIO(prepared))
    assert result.format == "JPEG"
    assert max(result.size) <= image_utils.MAX_UPLOAD_SIDE
    assert "SecretCamera" not in str(dict(result.getexif()))


def test_prepare_for_upload_handles_transparent_png():
    image = Image.new("RGBA", (200, 100), (255, 0, 0, 0))
    prepared = image_utils.prepare_for_upload(to_bytes(image, fmt="PNG"))
    result = Image.open(io.BytesIO(prepared))
    assert result.mode == "RGB"
    assert result.size == (200, 100)


def test_load_image_rejects_garbage():
    with pytest.raises(image_utils.InvalidImageError):
        image_utils.load_image(b"definitely not an image")


def test_similarity_same_photo_resized_and_recompressed(photo):
    query = image_utils.image_hashes(photo)
    thumb = photo.resize((160, 120))
    thumb = Image.open(io.BytesIO(to_bytes(thumb, quality=40)))
    assert image_utils.best_similarity(query, thumb) >= 0.9


def test_similarity_same_photo_center_cropped(photo):
    # 인스타그램/검색 썸네일처럼 정사각형으로 잘린 경우
    query = image_utils.image_hashes(photo)
    cropped = image_utils.center_crop_square(photo).resize((150, 150))
    assert image_utils.best_similarity(query, cropped) >= 0.9


def test_similarity_different_photo_is_low(photo):
    query = image_utils.image_hashes(photo)
    other = make_image(seed=42)
    assert image_utils.best_similarity(query, other) < 0.8
