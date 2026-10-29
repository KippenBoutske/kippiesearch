from kippiesearch.config import Settings


def test_default_synology_home_share_has_no_credentials():
    settings = Settings()
    assert settings.nas_smb_host == "192.168.0.33"
    assert settings.nas_smb_shares == ["home"]
    assert settings.nas_smb_username == ""
    assert settings.nas_smb_password == ""
    assert settings.nas_smb_enabled is False
    assert settings.api_token == ""
    assert settings.itslearning_password == ""
