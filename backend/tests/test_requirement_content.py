from app.models.functional.requirement import Requirement, RequirementAttachment
from app.services.functional.requirement_content import (
    requirement_text_for_ai,
    usable_requirement_text,
)


def test_requirement_text_prefers_body():
    r = Requirement(title="T", body_md="body", extracted_text="extracted")
    assert requirement_text_for_ai(r) == "body"


def test_requirement_text_falls_back_to_extracted():
    r = Requirement(title="T", body_md="", extracted_text="from pdf")
    assert requirement_text_for_ai(r) == "from pdf"


def test_requirement_text_scanned_placeholder_from_attachments():
    r = Requirement(title="T", body_md="", extracted_text=None)
    r.attachments = [
        RequirementAttachment(
            filename="scan.pdf",
            mime="application/pdf",
            storage_key="1/requirements/1/x_scan.pdf",
            size_bytes=10,
        )
    ]
    text = requirement_text_for_ai(r)
    assert "scan.pdf" in text
    assert usable_requirement_text(text) == ""
