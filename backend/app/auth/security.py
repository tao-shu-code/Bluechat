"""密码哈希与 JWT 签发/解析。"""

import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# bcrypt 哈希（与 02_schema.sql 预置 admin 密码哈希的生成方式一致）
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """生成 bcrypt 密码哈希。"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """校验明文密码与哈希是否匹配；哈希格式非法时按不匹配处理。"""
    try:
        return pwd_context.verify(plain_password, password_hash)
    except ValueError:
        return False


def create_access_token(user_id: uuid.UUID, username: str) -> str:
    """签发 JWT：sub=user_id，附带 username 与过期时间（settings.JWT_EXPIRE_MINUTES）。"""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "username": username, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """解析 JWT；无效或已过期返回 None。"""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
