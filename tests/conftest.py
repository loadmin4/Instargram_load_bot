import io
import random

import pytest
from PIL import Image, ImageDraw


def make_image(seed: int = 1, size: tuple[int, int] = (640, 480)) -> Image.Image:
    """테스트용으로 모양이 뚜렷한 이미지를 만든다."""
    rng = random.Random(seed)
    image = Image.new("RGB", size, (rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    draw = ImageDraw.Draw(image)
    for _ in range(12):
        x0, y0 = rng.randrange(size[0]), rng.randrange(size[1])
        x1, y1 = x0 + rng.randrange(40, 300), y0 + rng.randrange(40, 300)
        color = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        if rng.random() < 0.5:
            draw.rectangle((x0, y0, x1, y1), fill=color)
        else:
            draw.ellipse((x0, y0, x1, y1), fill=color)
    return image


def to_bytes(image: Image.Image, fmt: str = "JPEG", **kwargs) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


@pytest.fixture
def photo() -> Image.Image:
    return make_image(seed=1)


@pytest.fixture
def photo_bytes(photo) -> bytes:
    return to_bytes(photo, quality=95)
