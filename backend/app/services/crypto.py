from cryptography.fernet import Fernet, InvalidToken


class CryptoService:
    def __init__(self, key: str, key_version: str) -> None:
        self._fernet = Fernet(key.encode("ascii"))
        self.key_version = key_version

    def encrypt_text(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt_text(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("encrypted_value_cannot_be_decrypted") from exc
