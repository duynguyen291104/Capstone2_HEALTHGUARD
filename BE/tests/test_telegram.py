from app.services.telegram import is_public_action_url


def test_telegram_action_url_requires_a_public_http_address():
    assert is_public_action_url("https://healthguard.example/hom-nay?occurrence=123")
    assert is_public_action_url("https://t.me/HealthGuardCare2026Bot")
    assert not is_public_action_url("http://localhost:3000/hom-nay")
    assert not is_public_action_url("http://127.0.0.1:3000/hom-nay")
    assert not is_public_action_url("http://192.168.1.2:3000/hom-nay")
    assert not is_public_action_url("javascript:alert(1)")
