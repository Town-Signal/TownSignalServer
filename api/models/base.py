"""ORM 공통 기반. 모델은 db/schema.sql과 1:1로 맞춘다 (전체 명세 5.5).

스키마는 schema.sql로 만든다. metadata.create_all()로 테이블을 만들지 않는다.
뷰는 Base.metadata에 넣지 않고 VIEW_METADATA에 둬서 테이블로 생성될 일이 없게 한다.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


VIEW_METADATA = MetaData()
