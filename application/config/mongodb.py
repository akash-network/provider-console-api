from pymongo import MongoClient
from application.config.config import Config

# mongo client to connect with the db
mongo_client = MongoClient(Config.MONGO_DB_CONNECTION_STRING)

# mongo db connection
provider_console_db = mongo_client[f"{Config.MONGO_DB_NAME}"]

# connection with respective collections
actions_collection = provider_console_db["actions"]
wallet_addresses_collection = provider_console_db["wallet_addresses"]
logs_collection = provider_console_db["logs"]
