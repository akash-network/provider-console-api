import requests
import urllib3

from application.config.config import Config
from application.utils.logger import log


def get_node_url(chain_id):
    return (
        Config.AKASH_NODE_STATUS_CHECK
        if chain_id == Config.CHAIN_ID
        else Config.AKASH_NODE_STATUS_CHECK_TESTNET
    )


async def check_provider_online_status_v2(chain_id: str, provider_uri: str):
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    try:
        response = requests.get(f"{provider_uri}/status", verify=False, timeout=20)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Error checking provider online status v2: {e}")
        return False