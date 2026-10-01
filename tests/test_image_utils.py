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
    query = image_utils.QueryImage(photo)
    thumb = photo.resize((160, 120))
    thumb = Image.open(io.BytesIO(to_bytes(thumb, quality=40)))
    assert query.similarity(thumb) >= 0.9


@pytest.mark.parametrize("aspect", [1.0, 4 / 5, 1.91])
def test_similarity_instagram_crops(aspect):
    # 인스타그램은 1:1, 4:5, 1.91:1 로 잘라 올린다
    original = make_image(seed=3, size=(1200, 1600))
    query = image_utils.QueryImage(original)
    cropped = image_utils.crop_to_aspect(original, aspect).resize((1080, round(1080 / aspect)))
    assert query.similarity(cropped) >= 0.9


def test_similarity_when_query_is_the_cropped_one():
    # 반대로 내가 가진 사진이 잘린 사진이고 게시물이 원본인 경우
    original = make_image(seed=4, size=(1600, 1200))
    query = image_utils.QueryImage(image_utils.crop_to_aspect(original, 1.0))
    assert query.similarity(original) >= 0.9


def test_similarity_different_photo_is_low(photo):
    query = image_utils.QueryImage(photo)
    for seed in range(40, 50):
        assert query.similarity(make_image(seed=seed)) < 0.75


def test_crop_to_aspect():
    image = Image.new("RGB", (1000, 500))
    assert image_utils.crop_to_aspect(image, 1.0).size == (500, 500)
    assert image_utils.crop_to_aspect(image, 4.0).size == (1000, 250)
