import tempfile

from pymongo import MongoClient
from application.config.config import Config


def _mongo_client_options() -> dict:
    # pymongo only accepts a CA certificate as a file path, so when the PEM
    # content is supplied via MONGO_TLS_CA_CERT (e.g. from Doppler) it is
    # written to a temp file that lives for the lifetime of the process.
    if not Config.MONGO_TLS_CA_CERT:
        return {}
    ca_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".crt", prefix="mongo-ca-", delete=False
    )
    ca_file.write(Config.MONGO_TLS_CA_CERT)
    ca_file.close()
    return {"tls": True, "tlsCAFile": ca_file.name}


# mongo client to connect with the db
mongo_client = MongoClient(Config.MONGO_DB_CONNECTION_STRING, **_mongo_client_options())

# mongo db connection
provider_console_db = mongo_client[f"{Config.MONGO_DB_NAME}"]

# connection with respective collections
actions_collection = provider_console_db["actions"]
wallet_addresses_collection = provider_console_db["wallet_addresses"]
logs_collection = provider_console_db["logs"]
api_keys_collection = provider_console_db["api_keys"]
