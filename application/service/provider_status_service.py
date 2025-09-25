import httpx
import urllib3

from application.utils.logger import log


async def check_provider_online_status_v2(chain_id: str, provider_uri: str):
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
            response = await client.get(f"{provider_uri}/status")
            response.raise_for_status()
            return response.json()
    except (httpx.RequestError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
        log.error(f"Error checking provider online status v2: {e}")
        return False