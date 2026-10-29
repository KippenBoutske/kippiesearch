from kippiesearch.config import Settings
from kippiesearch.itslearning import ItslearningSync
from kippiesearch.nas import smb_available
from kippiesearch.status import AppStatus


def test_defaults_have_no_personal_or_lan_values():
    settings = Settings()
    assert settings.nas_smb_host == ""
    assert settings.nas_smb_shares == []
    assert settings.nas_smb_username == ""
    assert settings.nas_smb_password == ""
    assert settings.nas_smb_enabled is False
    assert settings.itslearning_url == ""
    assert settings.itslearning_organisation == ""
    assert settings.itslearning_username == ""
    assert settings.itslearning_password == ""
    assert settings.api_token == ""
    assert settings.chat_model == "gemma3-1b"
    assert settings.chat_enabled is True
    assert settings.nas_roots == []
    assert "share" in settings.nas_roots_skip_reason().lower()
    dumped = settings.model_dump_json()
    assert "school" not in dumped.lower()
    assert "192.168.1.x" not in dumped
    assert "gebruiker" not in dumped.lower()


def test_empty_smb_and_itslearning_are_disabled_with_notice():
    settings = Settings(nas_smb_enabled=True, itslearning_enabled=True)
    assert settings.nas_smb_ready() is False
    assert "host" in settings.nas_smb_skip_reason()
    assert settings.itslearning_ready() is False
    assert "URL" in settings.itslearning_skip_reason()
    assert smb_available(settings) is False
    result = ItslearningSync(settings, AppStatus()).sync()
    assert result["ok"] is False
    assert "URL" in result["reason"]


def test_configured_sources_are_ready():
    settings = Settings(
        nas_smb_enabled=True,
        nas_smb_host="192.168.1.10",
        nas_smb_shares=["documents"],
        nas_smb_username="nasuser",
        itslearning_enabled=True,
        itslearning_url="https://school.itslearning.com",
        itslearning_username="leerling",
        itslearning_password="secret",
    )
    assert settings.nas_smb_ready() is True
    assert settings.nas_smb_skip_reason() == ""
    assert settings.itslearning_ready() is True
    assert settings.itslearning_skip_reason() == ""
