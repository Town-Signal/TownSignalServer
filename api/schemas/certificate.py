"""자격증 DTO (전체 명세 9장 api/schemas/certificate)."""

from pydantic import BaseModel


class CertificateItem(BaseModel):
    """⑰ 표준 자격증 목록 (8.6)"""

    name: str
    aliases: list[str]
    category: str | None
