"""배치 공통 트랜잭션 도우미."""

from contextlib import AbstractContextManager

from sqlalchemy import Connection


def transaction(conn: Connection) -> AbstractContextManager:
    """호출 쪽이 이미 트랜잭션 중이면 SAVEPOINT, 아니면 새 트랜잭션.

    with 블록 안에서 예외가 나면 그 안에서 한 일은 전부 되돌아가고 예외는 다시 올라간다.
    """
    return conn.begin_nested() if conn.in_transaction() else conn.begin()
