import base64
import io
from pathlib import Path

from PIL import Image

from app.ai_client import MAX_IMAGE_EDGE, _encode_image


def test_large_photo_is_downscaled_to_jpeg(tmp_path: Path):
    path = tmp_path / "big.png"
    Image.new("RGB", (4000, 3000), "white").save(path)

    media_type, data = _encode_image(path)

    assert media_type == "image/jpeg"
    with Image.open(io.BytesIO(base64.b64decode(data))) as img:
        assert max(img.size) == MAX_IMAGE_EDGE


def test_unreadable_file_is_sent_as_is(tmp_path: Path):
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"not-an-image")

    media_type, data = _encode_image(path)

    assert media_type == "image/jpeg"
    assert base64.b64decode(data) == b"not-an-image"
