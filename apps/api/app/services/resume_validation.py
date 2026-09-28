from dataclasses import dataclass

from fastapi import HTTPException, UploadFile, status

PDF_CONTENT_TYPE = "application/pdf"


@dataclass(frozen=True)
class ValidatedResume:
    content: bytes
    original_filename: str
    content_type: str
    size_bytes: int
    extension: str


def sanitize_filename(filename: str | None) -> str:
    sanitized = (filename or "resume.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    return sanitized[:255] or "resume.pdf"


async def validate_resume_upload(
    resume: UploadFile,
    *,
    max_size_bytes: int,
) -> ValidatedResume:
    filename = sanitize_filename(resume.filename)
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Resume must use the .pdf extension",
        )
    if resume.content_type != PDF_CONTENT_TYPE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Resume must have the application/pdf content type",
        )

    content = await resume.read(max_size_bytes + 1)
    if len(content) > max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Resume exceeds the configured maximum file size",
        )
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Resume content is not a valid PDF",
        )
    return ValidatedResume(
        content=content,
        original_filename=filename,
        content_type=PDF_CONTENT_TYPE,
        size_bytes=len(content),
        extension="pdf",
    )
