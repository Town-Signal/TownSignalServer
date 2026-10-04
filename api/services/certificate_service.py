"""⑰ 표준 자격증 목록. common.certificate_normalizer의 사전을 그대로 내린다(DB를 쓰지 않는다)."""

from api.schemas.certificate import CertificateItem
from api.schemas.common import ListData
from common.certificate_normalizer import catalog


def certificates() -> ListData[CertificateItem]:
    items = [
        CertificateItem(name=entry.name, aliases=list(entry.aliases), category=entry.category)
        for entry in catalog()
    ]
    return ListData(items=items, total=len(items))
