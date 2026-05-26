from cryptography.fernet import Fernet
from config import settings


def _fernet() -> Fernet:
    return Fernet(settings.fernet_key.encode())


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
