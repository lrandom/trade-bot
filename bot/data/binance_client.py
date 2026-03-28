from binance import AsyncClient
from bot.config import settings

_client: AsyncClient | None = None


async def get_client() -> AsyncClient:
    global _client
    if _client is None:
        if settings.binance_testnet:
            _client = await AsyncClient.create(
                api_key=settings.binance_api_key,
                api_secret=settings.binance_secret_key,
                testnet=True,
            )
        else:
            _client = await AsyncClient.create(
                api_key=settings.binance_api_key,
                api_secret=settings.binance_secret_key,
            )
    return _client


async def close_client():
    global _client
    if _client:
        await _client.close_connection()
        _client = None
