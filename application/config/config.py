from os import environ


class Config:
    # Set application configuration vars from k8s/deployment.yaml file.
    # General Config
    APP_NAME = environ.get("APP_NAME", "provider-console-api")
    LOG_LEVEL = environ.get("LOG_LEVEL", "DEBUG")
    PROVIDER_CONSOLE_FRONTEND_URL = environ.get(
        "PROVIDER_CONSOLE_FRONTEND_URL", "http://localhost:3000"
    )

    # Internal API Config
    CONSOLE_API_BASE_URL = environ.get("CONSOLE_API_BASE_URL", "http://localhost:3080")

    # MongoDB Config
    MONGO_DB_CONNECTION_STRING = environ.get("MONGO_DB_CONNECTION_STRING")
    MONGO_DB_NAME = environ.get("MONGO_DB_NAME")


    # Akash Server Config
    AKASH_NODE_STATUS_CHECK = environ.get("AKASH_NODE_STATUS_CHECK")
    CHAIN_ID = environ.get("CHAIN_ID", "akashnet-2")
    GPU_DATA_URL = environ.get(
        "GPU_DATA_URL",
        "https://raw.githubusercontent.com/akash-network/provider-configs/main/devices/pcie/gpus.json",
    )
    KEYRING_BACKEND = environ.get("KEYRING_BACKEND", "file")
    AKASH_VERSION = environ.get("AKASH_VERSION", "v0.38.1")
    AKASH_NODE_HELM_CHART_VERSION = environ.get("AKASH_NODE_HELM_CHART_VERSION", "12.0.3")
    INGRESS_NGINX_VERSION = environ.get("INGRESS_NGINX_VERSION", "4.11.3")
    PROVIDER_SERVICES_VERSION = environ.get("PROVIDER_SERVICES_VERSION", "v0.6.10")
    PROVIDER_SERVICES_HELM_CHART_VERSION = environ.get("PROVIDER_SERVICES_HELM_CHART_VERSION", "11.6.0")
    PROVIDER_PRICE_SCRIPT_URL = environ.get(
        "PROVIDER_PRICE_SCRIPT_URL",
        "https://raw.githubusercontent.com/akash-network/helm-charts/main/charts/akash-provider/scripts/price_script_generic.sh",
    )
    NVIDIA_DEVICE_PLUGIN_VERSION = environ.get("NVIDIA_DEVICE_PLUGIN_VERSION", "0.14.5")
    ROOK_CEPH_VERSION = environ.get("ROOK_CEPH_VERSION", "1.15.3")

    # Authentication
    HOST_NAME = environ.get("HOST_NAME")
    SECURITY_HOST = environ.get("SECURITY_HOST")
    PUBLIC_KEY = environ.get("PUBLIC_KEY")

    # Redis
    REDIS_URI = environ.get("REDIS_URI")
    REDIS_PORT = environ.get("REDIS_PORT")
    REDIS_PASSWORD = environ.get("REDIS_PASSWORD")

    # Misc
    HELM_VERSION = environ.get("HELM_VERSION", "v3.11.0")
