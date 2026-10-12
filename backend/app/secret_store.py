"""数据库里的密钥字段加密存放（API Key、微信 token）。

数据库文件和每一份备份里只剩密文；解密用的主密钥放在 macOS 钥匙串（服务 sandrone-agent，
账户 database-key），不进数据库也不进备份。其他系统存在 data/secret.key（权限 600）。
测试与 CI 用环境变量 SANDRONE_SECRET_KEY 指定临时主密钥，不碰钥匙串。

调用方不用改：EncryptedStr 在写库时加密、读出时解密；旧的明文值照常读出，
启动时 encrypt_existing_secrets() 会把它们改写成密文（改写前先备份）。
主密钥丢了（换电脑、钥匙串被清）时，密文读出为空串并记日志，重新填一次 Key 即可。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import String, TypeDecorator

from .config import DATA_DIR

logger = logging.getLogger(__name__)

PREFIX = "enc:v1:"
KEYCHAIN_SERVICE = "sandrone-agent"
KEYCHAIN_ACCOUNT = "database-key"
KEY_FILE = DATA_DIR / "secret.key"
# (表, 列)：启动时把旧明文改写成密文
SECRET_COLUMNS = (
    ("llm_config", "api_key"),
    ("tool_config", "search_api_key"),
    ("tool_config", "vision_api_key"),
    ("wechat_account", "token"),
)

_fernet: Fernet | None = None
_lock = threading.Lock()


class SecretStoreError(RuntimeError):
    pass


def _keychain_key() -> bytes:
    found = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if found.returncode == 0:
        return found.stdout.strip().encode()
    if found.returncode != 44:  # 44 = 没有这一项；其他（钥匙串锁着、拒绝访问）不能当成没有，否则会生成新密钥
        raise SecretStoreError(f"读取钥匙串失败（{found.returncode}）：{found.stderr.strip()[:200]}")
    key = Fernet.generate_key()
    added = subprocess.run(
        ["/usr/bin/security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT,
         "-l", "Sandrone Agent 数据库密钥", "-w", key.decode()],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if added.returncode != 0:
        raise SecretStoreError(f"写入钥匙串失败（{added.returncode}）：{added.stderr.strip()[:200]}")
    logger.info("已在钥匙串生成数据库密钥")
    return key


def _file_key() -> bytes:
    if KEY_FILE.is_file():
        return KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key)
    return key


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        with _lock:
            if _fernet is None:
                env_key = os.getenv("SANDRONE_SECRET_KEY", "").strip()
                if env_key:
                    key = env_key.encode()
                elif sys.platform == "darwin" and os.getenv("SANDRONE_SECRET_STORE", "keychain") == "keychain":
                    key = _keychain_key()
                else:
                    key = _file_key()
                _fernet = Fernet(key)
    return _fernet


def encrypt(value: str) -> str:
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + get_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value  # 空值或尚未改写的旧明文
    try:
        return get_fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, SecretStoreError, ValueError):
        logger.warning("密钥字段无法解密（主密钥可能已更换），按空值处理，需要重新填写")
        return ""


class EncryptedStr(TypeDecorator):
    """写库时加密、读出时解密的字符串列（库里仍是 TEXT，不需要改表结构）。"""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value) if isinstance(value, str) else value

    def process_result_value(self, value, dialect):
        return decrypt(value) if isinstance(value, str) else value


def plaintext_secret_count(connection) -> int:
    count = 0
    for table, column in SECRET_COLUMNS:
        try:
            rows = connection.exec_driver_sql(f"SELECT {column} FROM {table}").fetchall()
        except Exception:  # noqa: BLE001 - 表还不存在
            continue
        count += sum(1 for (value,) in rows if value and not str(value).startswith(PREFIX))
    return count


def encrypt_existing_secrets(connection) -> int:
    """把旧的明文密钥改写成密文；返回改写了几项。"""
    changed = 0
    for table, column in SECRET_COLUMNS:
        try:
            rows = connection.exec_driver_sql(f"SELECT rowid, {column} FROM {table}").fetchall()
        except Exception:  # noqa: BLE001
            continue
        for rowid, value in rows:
            if value and not str(value).startswith(PREFIX):
                connection.exec_driver_sql(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (encrypt(str(value)), rowid)
                )
                changed += 1
    return changed
