import os

from cryptography.fernet import Fernet

# 测试用临时主密钥，绝不读写真实钥匙串
os.environ.setdefault("SANDRONE_SECRET_KEY", Fernet.generate_key().decode())
