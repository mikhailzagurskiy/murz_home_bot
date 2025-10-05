from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


class Settings(BaseSettings):
  bot_token: SecretStr
  deluge_addr: str
  deluge_port: int
  deluge_username: SecretStr
  deluge_password: SecretStr
  model_config = SettingsConfigDict(
    env_file='.env', env_file_encoding='utf-8', env_prefix='murz_home_bot_', env_nested_delimiter='__')
