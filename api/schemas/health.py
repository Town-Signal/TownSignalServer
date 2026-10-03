from pydantic import BaseModel


class HealthData(BaseModel):
    """⑯ 헬스체크 data. 봉투의 status와 헷갈리지 않게 service_status로 둔다(8.5)."""

    service_status: str
    env: str
