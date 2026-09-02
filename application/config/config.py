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
    # PEM content of the CA certificate (not a file path), for self-hosted
    # MongoDB with TLS; leave unset for connections that need no custom CA.
    MONGO_TLS_CA_CERT = environ.get("MONGO_TLS_CA_CERT")

    # Akash Server Config
    AKASH_NODE_STATUS_CHECK = environ.get("AKASH_NODE_STATUS_CHECK")
    CHAIN_ID = environ.get("CHAIN_ID", "akashnet-2")
    GPU_DATA_URL = environ.get(
        "GPU_DATA_URL",
        "https://raw.githubusercontent.com/akash-network/provider-configs/main/devices/pcie/gpus.json",
    )
    KEYRING_BACKEND = environ.get("KEYRING_BACKEND", "file")
    AKASH_VERSION = environ.get("AKASH_VERSION", "v2.0.1")
    AKASH_NODE_HELM_CHART_VERSION = environ.get(
        "AKASH_NODE_HELM_CHART_VERSION", "15.0.0"
    )
    PROVIDER_SERVICES_VERSION = environ.get("PROVIDER_SERVICES_VERSION", "v0.12.0")
    PROVIDER_SERVICES_HELM_CHART_VERSION = environ.get(
        "PROVIDER_SERVICES_HELM_CHART_VERSION", "16.0.0"
    )
    PROVIDER_HOSTNAME_OPERATOR_VERSION = environ.get("PROVIDER_HOSTNAME_OPERATOR_VERSION", "v0.12.0")
    PROVIDER_INVENTORY_OPERATOR_VERSION = environ.get("PROVIDER_INVENTORY_OPERATOR_VERSION", "v0.12.0")
    PROVIDER_PRICE_SCRIPT_URL = environ.get(
        "PROVIDER_PRICE_SCRIPT_URL",
        "https://raw.githubusercontent.com/akash-network/helm-charts/main/charts/akash-provider/scripts/price_script_generic.sh",
    )
    NVIDIA_DEVICE_PLUGIN_VERSION = environ.get("NVIDIA_DEVICE_PLUGIN_VERSION", "0.14.5")
    ROOK_CEPH_VERSION = environ.get("ROOK_CEPH_VERSION", "1.15.3")

    # Gateway API + cert-manager (v0.12.0 Gateway API migration)
    CERT_MANAGER_VERSION = environ.get("CERT_MANAGER_VERSION", "v1.19.1")
    GATEWAY_API_CRD_REF = environ.get("GATEWAY_API_CRD_REF", "v2.5.1")  # git tag ref for kubectl kustomize ?ref= — v-prefix required
    NGINX_GATEWAY_FABRIC_VERSION = environ.get("NGINX_GATEWAY_FABRIC_VERSION", "2.5.1")  # helm --version, no v-prefix
    AKASH_GATEWAY_HELM_CHART_VERSION = environ.get("AKASH_GATEWAY_HELM_CHART_VERSION", "1.0.0")
    CERT_READY_TIMEOUT_SECONDS = int(environ.get("CERT_READY_TIMEOUT_SECONDS", "600"))
    LETSENCRYPT_PROD_SERVER = "https://acme-v02.api.letsencrypt.org/directory"
    LETSENCRYPT_STAGING_SERVER = "https://acme-staging-v02.api.letsencrypt.org/directory"

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
