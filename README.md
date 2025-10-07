# Provider Console Backend API

Welcome to the Provider Console Backend API, a core microservice of the Provider Console application, built using FastAPI. This project is designed to handle all the main operational logic and serves as the backbone for managing and processing data efficiently.

## Prerequisites

Before you begin, ensure you have the following installed:

- Python (3.10 or later)
- pip (latest version)

## Installation

Clone the repository and set up a virtual environment:

```bash
git clone https://github.com/akash-network/provider-console-api.git
cd provider-console-api
python -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`
pip install -r requirements.txt
```

## Configuration

The application relies on various environment variables to control its behavior and integrate with different services. Set these variables in your environment or a `.env` file. Below is a description of each:

```plaintext
# General Application Config
APP_NAME - The name of the application (e.g., provider-console-api)
LOG_LEVEL - The verbosity level of logs (e.g., DEBUG)
PROVIDER_CONSOLE_FRONTEND_URL - URL of the Provider Console frontend application

# MongoDB Config
MONGO_DB_CONNECTION_STRING - MongoDB connection URI
MONGO_DB_NAME - The database name to use with MongoDB

# Provider Check SSH Config
PROVIDER_CHECK_SSH_HOST - Hostname for SSH provider checks
PROVIDER_CHECK_SSH_USER - Username for SSH provider checks
PROVIDER_CHECK_SSH_PORT - Port for SSH provider checks
PROVIDER_CHECK_SSH_PRIVATE_KEY - Base64 encoded private key for SSH

# Authentication and Security
HOST_NAME - The hostname of the API server
SECURITY_HOST - Hostname of the security service
PUBLIC_KEY - Base64 encoded public key for authentication

# Akash Network Config
AKASH_NODE_STATUS_CHECK - URL to check the status of the main Akash node
AKASH_VERSION - Version of the Akash software
CHAIN_ID - Chain ID of the main Akash network
KEYRING_BACKEND - Backend for managing cryptographic keys in Akash

# Deployment Config
PROVIDER_SERVICES_VERSION - Version identifier for provider services
PROVIDER_PRICE_SCRIPT_URL - URL for the provider pricing script

#Miscellaneous
GPU_DATA_URL - URL for GPU data JSON file
HELM_VERSION - Version of Helm to use
```

## Running the Application

To run the application in development mode, use:

```bash
uvicorn asgi:app --proxy-headers --host 0.0.0.0 --port 80 --reload
```
