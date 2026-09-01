"""get_visible_kb_ids ACL 过滤逻辑测试（Task 4）。

用内存 SQLite 建最小表（仅 ACL 相关表，避开 JSONB 列），纯 DB 层验证可见性规则：
ALL 可见 / DEPARTMENT 匹配 / USER 匹配 / 创建者可见 / ADMIN 全可见 / 无权不可见。
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.common.deps import get_visible_kb_ids
from app.core.database import Base
from app.models import (
    Department,
    KbAcl,
    KbVisibility,
    KnowledgeBase,
    Role,
    User,
    UserRole,
)

# messages/audit_logs 使用 JSONB，与 SQLite 不兼容；本测试仅需 ACL 相关表
ACL_TABLES = [
    Department.__table__,
    User.__table__,
    Role.__table__,
    UserRole.__table__,
    KnowledgeBase.__table__,
    KbAcl.__table__,
]


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=ACL_TABLES)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def _make_user(db, username: str, dept: Department | None = None, roles: tuple = ()) -> User:
    user = User(
        username=username,
        password_hash="not-a-real-hash",
        department_id=dept.id if dept else None,
    )
    db.add(user)
    db.flush()
    for code in roles:
        role = db.scalars(select(Role).where(Role.code == code)).first()
        if role is None:
            role = Role(code=code, name=code)
            db.add(role)
            db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
    db.flush()
    return user


@pytest.fixture()
def seeded(db):
    """部门 A/B + 5 个知识库 + 若干用户，覆盖全部可见性分支。"""
    dept_a = Department(name="部门A")
    dept_b = Department(name="部门B")
    db.add_all([dept_a, dept_b])
    db.flush()

    admin = _make_user(db, "admin", roles=("ADMIN",))
    alice = _make_user(db, "alice", dept=dept_a)
    bob = _make_user(db, "bob", dept=dept_b)
    creator = _make_user(db, "creator", dept=dept_b)
    nobody = _make_user(db, "nobody")  # 无部门、无 ACL

    kb_all = KnowledgeBase(name="kb_all", visibility=KbVisibility.ALL)
    kb_dept_a = KnowledgeBase(name="kb_dept_a", visibility=KbVisibility.DEPARTMENT)
    kb_user_bob = KnowledgeBase(name="kb_user_bob", visibility=KbVisibility.USER)
    kb_owner = KnowledgeBase(
        name="kb_owner", visibility=KbVisibility.USER, created_by=creator.id
    )
    kb_dept_b = KnowledgeBase(name="kb_dept_b", visibility=KbVisibility.DEPARTMENT)
    db.add_all([kb_all, kb_dept_a, kb_user_bob, kb_owner, kb_dept_b])
    db.flush()

    db.add_all(
        [
            KbAcl(kb_id=kb_dept_a.id, department_id=dept_a.id),
            KbAcl(kb_id=kb_user_bob.id, user_id=bob.id),
            KbAcl(kb_id=kb_dept_b.id, department_id=dept_b.id),
        ]
    )
    db.commit()

    return {
        "users": {"admin": admin, "alice": alice, "bob": bob, "creator": creator, "nobody": nobody},
        "kb_ids": {
            "kb_all": kb_all.id,
            "kb_dept_a": kb_dept_a.id,
            "kb_user_bob": kb_user_bob.id,
            "kb_owner": kb_owner.id,
            "kb_dept_b": kb_dept_b.id,
        },
    }


def _visible_names(db, user, seeded) -> set[str]:
    visible = set(get_visible_kb_ids(db, user))
    return {name for name, kb_id in seeded["kb_ids"].items() if str(kb_id) in visible}


class TestGetVisibleKbIds:
    def test_admin_sees_all(self, db, seeded):
        assert _visible_names(db, seeded["users"]["admin"], seeded) == set(seeded["kb_ids"])

    def test_all_visibility_public(self, db, seeded):
        # ALL 类型对任何登录用户可见（含无部门用户）
        for username in ("alice", "bob", "nobody"):
            visible = _visible_names(db, seeded["users"][username], seeded)
            assert "kb_all" in visible

    def test_department_match(self, db, seeded):
        alice_visible = _visible_names(db, seeded["users"]["alice"], seeded)
        assert "kb_dept_a" in alice_visible  # 部门 A 命中
        assert "kb_dept_b" not in alice_visible  # 部门 B ACL 不命中

        bob_visible = _visible_names(db, seeded["users"]["bob"], seeded)
        assert "kb_dept_b" in bob_visible
        assert "kb_dept_a" not in bob_visible

    def test_user_match(self, db, seeded):
        bob_visible = _visible_names(db, seeded["users"]["bob"], seeded)
        assert "kb_user_bob" in bob_visible  # USER ACL 命中

        alice_visible = _visible_names(db, seeded["users"]["alice"], seeded)
        assert "kb_user_bob" not in alice_visible

    def test_creator_always_visible(self, db, seeded):
        # visibility=USER 且未配置任何 ACL，创建者仍可见
        creator_visible = _visible_names(db, seeded["users"]["creator"], seeded)
        assert "kb_owner" in creator_visible

    def test_no_permission_invisible(self, db, seeded):
        # 无部门无 ACL 的用户：仅能看到 ALL
        nobody_visible = _visible_names(db, seeded["users"]["nobody"], seeded)
        assert nobody_visible == {"kb_all"}

    def test_admin_bypasses_acl_check(self, db, seeded):
        # ADMIN 即使不在任何 ACL 中也全可见
        visible = set(get_visible_kb_ids(db, seeded["users"]["admin"]))
        assert len(visible) == 5

    def test_ids_returned_as_strings(self, db, seeded):
        visible = get_visible_kb_ids(db, seeded["users"]["bob"])
        assert all(isinstance(item, str) for item in visible)
