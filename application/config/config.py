from os import environ


class Config:
    # Set application configuration vars from k8s/deployment.yaml file.
    # General Config
    APP_NAME = environ.get("APP_NAME", "provider-console-api")
    LOG_LEVEL = environ.get("LOG_LEVEL", "DEBUG")
    PROVIDER_CONSOLE_FRONTEND_URL = environ.get(
        "PROVIDER_CONSOLE_FRONTEND_URL", "http://localhost:3000"
    )

    # MongoDB Config
    MONGO_DB_CONNECTION_STRING = environ.get("MONGO_DB_CONNECTION_STRING")
    MONGO_DB_NAME = environ.get("MONGO_DB_NAME")

    PROVIDER_CHECK_SSH_HOST = environ.get("PROVIDER_CHECK_SSH_HOST")
    PROVIDER_CHECK_SSH_USER = environ.get("PROVIDER_CHECK_SSH_USER")
    PROVIDER_CHECK_SSH_PORT = environ.get("PROVIDER_CHECK_SSH_PORT")
    PROVIDER_CHECK_SSH_PRIVATE_KEY = environ.get("PROVIDER_CHECK_SSH_PRIVATE_KEY")

    # Akash Server Config
    AKASH_NODE_STATUS_CHECK = environ.get("AKASH_NODE_STATUS_CHECK")
    AKASH_NODE_STATUS_CHECK_TESTNET = environ.get("AKASH_NODE_STATUS_CHECK_TESTNET")
    CHAIN_ID = environ.get("CHAIN_ID", "akashnet-2")
    CHAIN_ID_TESTNET = environ.get("CHAIN_ID_TESTNET", "sandbox-01")
    GPU_DATA_URL = environ.get(
        "GPU_DATA_URL",
        "https://raw.githubusercontent.com/akash-network/provider-configs/main/devices/pcie/gpus.json",
    )
    KEYRING_BACKEND = environ.get("KEYRING_BACKEND", "file")
    AKASH_VERSION = environ.get("AKASH_VERSION", "v0.36.0")
    AKASH_VERSION_TESTNET = environ.get("AKASH_VERSION_TESTNET", "v0.36.0")
    PROVIDER_SERVICES_VERSION = environ.get("PROVIDER_SERVICES_VERSION", "v0.6.4")
    PROVIDER_SERVICES_VERSION_TESTNET = environ.get(
        "PROVIDER_SERVICES_VERSION_TESTNET", "v0.6.4"
    )
    PROVIDER_PRICE_SCRIPT_URL = environ.get(
        "PROVIDER_PRICE_SCRIPT_URL",
        "https://raw.githubusercontent.com/akash-network/helm-charts/main/charts/akash-provider/scripts/price_script_generic.sh",
    )

    # Authentication
    HOST_NAME = environ.get("HOST_NAME")
    SECURITY_HOST = environ.get("SECURITY_HOST")
    PUBLIC_KEY = environ.get("PUBLIC_KEY")

    # Misc
    HELM_VERSION = environ.get("HELM_VERSION", "v3.11.0")
