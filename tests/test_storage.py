from types import SimpleNamespace

from app.services.storage import _content_type_for


def test_content_type_uses_upload_type_when_specific():
    upload = SimpleNamespace(content_type="image/jpeg")

    assert _content_type_for(upload, "uploads/1/photo.jpg") == "image/jpeg"


def test_content_type_infers_image_when_upload_is_octet_stream():
    upload = SimpleNamespace(content_type="application/octet-stream")

    assert _content_type_for(upload, "uploads/1/photo.jpg") == "image/jpeg"
