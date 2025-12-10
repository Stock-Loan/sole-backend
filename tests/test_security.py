from datetime import timedelta

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    verify_password,
)


def test_password_hashing_and_verify():
    password = "S0meP@ss!WithLength"
    hashed = get_password_hash(password)
    assert hashed != password
    assert verify_password(password, hashed)
    assert verify_password("S0meP@ss!", hashed)


def test_access_and_refresh_tokens(monkeypatch, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_file = tmp_path / "priv.pem"
    pub_file = tmp_path / "pub.pem"
    priv_file.write_bytes(private_pem)
    pub_file.write_bytes(public_pem)

    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv_file))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub_file))
    monkeypatch.setenv("SECRET_KEY", "placeholder-secret-for-settings")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1")
    monkeypatch.setenv("REFRESH_TOKEN_EXPIRE_MINUTES", "2")

    access = create_access_token("user-xyz")
    refresh = create_refresh_token("user-xyz", expires_delta=timedelta(minutes=2), token_version=3)

    decoded_access = decode_token(access, expected_type="access")
    decoded_refresh = decode_token(refresh, expected_type="refresh")

    assert decoded_access["sub"] == "user-xyz"
    assert decoded_access["type"] == "access"
    assert decoded_refresh["sub"] == "user-xyz"
    assert decoded_refresh["type"] == "refresh"
    assert decoded_refresh["tv"] == 3
