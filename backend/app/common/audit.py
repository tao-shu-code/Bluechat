"""审计日志工具：向 audit_logs 表写入操作记录。"""

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit_log(
    db: Session,
    user_id: str | None,
    action: str,
    detail: dict | None = None,
    ip: str | None = None,
) -> None:
    """写一条审计记录并提交。

    在登录、登出及后续的上传/删除/提问等接口中调用。
    """
    db.add(AuditLog(user_id=user_id, action=action, detail=detail, ip=ip))
    db.commit()
