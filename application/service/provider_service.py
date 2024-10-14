from fastapi import status
import base64

from application.exception.application_error import ApplicationError
from application.config.config import Config
from application.utils.logger import log
from application.utils.ssh_utils import run_ssh_command


class ProviderService:

    async def _install_helm(self, ssh_client):
        log.info("Installing Helm...")
        helm_version = Config.HELM_VERSION
        commands = [
            f"wget https://get.helm.sh/helm-{helm_version}-linux-amd64.tar.gz",
            f"tar -zxvf helm-{helm_version}-linux-amd64.tar.gz",
            "sudo install linux-amd64/helm /usr/local/bin/helm",
            f"rm -rf linux-amd64 helm-{helm_version}-linux-amd64.tar.gz",
        ]
        try:
            for cmd in commands:
                output, _ = run_ssh_command(ssh_client, cmd, False)
            log.info("Helm installation completed successfully.")
        except Exception as e:
            log.error(f"Error during Helm installation: {str(e)}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="PROVIDER_001",
                payload={
                    "error": "Helm Installation Failed",
                    "message": f"Failed to install Helm: {str(e)}",
                },
            )

    async def _setup_helm_repos(self, ssh_client):
        log.info("Setting up Helm repositories...")
        commands = [
            "helm repo remove akash 2>/dev/null || true",
            "helm repo add akash https://akash-network.github.io/helm-charts",
            "helm repo update",
        ]
        for cmd in commands:
            run_ssh_command(ssh_client, cmd)
        log.info("Helm and Akash repository setup completed.")

    async def _install_akash_services(
        self, ssh_client, chain_id, provider_version, node_version
    ):
        log.info("Installing Akash services...")
        commands = [
            f"helm install akash-hostname-operator akash/akash-hostname-operator -n akash-services --set image.tag={provider_version}",
            f"helm install inventory-operator akash/akash-inventory-operator -n akash-services --set image.tag={provider_version}",
        ]
        if chain_id != "sandbox-01":
            commands.append(
                f"helm install akash-node akash/akash-node -n akash-services --set image.tag={node_version}"
            )
        for cmd in commands:
            run_ssh_command(ssh_client, cmd)
        log.info("Akash services installed.")

    async def _prepare_provider_config(
        self, ssh_client, account_address, key_password, domain, chain_id
    ):
        log.info("Preparing provider configuration...")
        config_content = f"""
---
from: "{account_address}"
key: "{self._get_base64_encoded_key(ssh_client)}"
keysecret: "{base64.b64encode(key_password.encode()).decode()}"
domain: "{domain}"
node: "http://akash-node-1:26657"
withdrawalperiod: 12h
chainid: "{chain_id}"
"""
        run_ssh_command(
            ssh_client,
            f"mkdir -p ~/provider && echo '{config_content}' > ~/provider/provider.yaml",
        )
        log.info("Provider configuration prepared.")

    async def _install_akash_crds(self, ssh_client, provider_version):
        log.info("Installing CRDs for Akash provider...")
        run_ssh_command(
            ssh_client,
            f"kubectl apply -f https://raw.githubusercontent.com/akash-network/provider/v{provider_version}/pkg/apis/akash.network/crd.yaml",
        )
        log.info("Akash provider CRDs installed.")

    async def _install_akash_provider(self, ssh_client, provider_version):
        log.info("Installing Akash provider...")
        try:
            # Get the pricing script content and encode it
            pricing_script = self._get_pricing_script(ssh_client)
            pricing_script_b64 = (
                base64.b64encode(pricing_script.encode()).decode()
                if pricing_script
                else None
            )

            # Prepare the Helm install command
            install_cmd = f"helm install akash-provider akash/provider -n akash-services -f ~/provider/provider.yaml --set image.tag={provider_version}"

            if pricing_script_b64:
                install_cmd += f" --set bidpricescript='{pricing_script_b64}'"

            # Run the Helm install command
            run_ssh_command(ssh_client, install_cmd)

            log.info("Akash provider installation completed.")
        except Exception as e:
            log.error(f"Failed to install Akash provider: {str(e)}")
            raise ApplicationError(
                error_code="PROVIDER_002",
                payload={
                    "error": "Akash Provider Installation Failed",
                    "message": f"Failed to install Akash provider: {str(e)}",
                },
            )

    def _get_pricing_script(self, ssh_client):
        try:
            # Check if the pricing script exists
            result = run_ssh_command(
                ssh_client,
                "test -f ~/provider/price_script_generic.sh && echo 'exists' || echo 'not found'",
            )
            if "exists" in result[0]:
                # If it exists, read its content
                content = run_ssh_command(
                    ssh_client, "cat ~/provider/price_script_generic.sh"
                )[0]
                return content.strip()
            else:
                # If pricing script URL is provided, download it
                pricing_script_url = Config.PROVIDER_PRICE_SCRIPT_URL
                if pricing_script_url:
                    log.info(f"Downloading pricing script from {pricing_script_url}")
                    run_ssh_command(
                        ssh_client,
                        f"wget {pricing_script_url} -O ~/provider/price_script_generic.sh",
                    )
                    content = run_ssh_command(
                        ssh_client, "cat ~/provider/price_script_generic.sh"
                    )[0]
                    return content.strip()
                log.info("Pricing script not found. Proceeding without it.")
                return None
        except Exception as e:
            log.warning(
                f"Error while trying to get pricing script: {str(e)}. Proceeding without it."
            )
            return None

    async def _install_nginx_ingress(self, ssh_client):
        log.info("Installing NGINX Ingress Controller...")
        ingress_config = """
controller:
  service:
    type: ClusterIP
  ingressClassResource:
    name: "akash-ingress-class"
  kind: DaemonSet
  hostPort:
    enabled: true
  admissionWebhooks:
    port: 7443
  config:
    allow-snippet-annotations: false
    compute-full-forwarded-for: true
    proxy-buffer-size: "16k"
  metrics:
    enabled: true
  extraArgs:
    enable-ssl-passthrough: true
tcp:
  "8443": "akash-services/akash-provider:8443"
  "8444": "akash-services/akash-provider:8444"
"""
        run_ssh_command(
            ssh_client, f"echo '{ingress_config}' > ~/ingress-nginx-custom.yaml"
        )
        commands = [
            "helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx",
            "helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx --version 4.10.0 --namespace ingress-nginx --create-namespace -f ~/ingress-nginx-custom.yaml",
            "kubectl label ns ingress-nginx app.kubernetes.io/name=ingress-nginx app.kubernetes.io/instance=ingress-nginx",
            "kubectl label ingressclass akash-ingress-class akash.network=true",
        ]
        for cmd in commands:
            run_ssh_command(ssh_client, cmd)
        log.info("NGINX Ingress Controller installation completed.")

    async def _configure_gpu_support(self, ssh_client, install_gpu_driver_nodes):
        try:
            log.info("Configuring NVIDIA Runtime Engine...")

            nvidia_runtime_class_config = """
kind: RuntimeClass
apiVersion: node.k8s.io/v1
metadata:
name: nvidia
handler: nvidia
"""
            run_ssh_command(
                ssh_client,
                f"echo '{nvidia_runtime_class_config}' > ~/nvidia-runtime-class.yaml",
            )

            run_ssh_command(ssh_client, "kubectl apply -f ~/nvidia-runtime-class.yaml")

            log.info("Labeling $node for NVIDIA support...")

            for node in install_gpu_driver_nodes:
                log.info(f"Labeling {node} for NVIDIA support...")
                label_command = f"kubectl label nodes {node} allow-nvdp=true"
                run_ssh_command(ssh_client, label_command)

            log.info("Adding NVIDIA Device Plugin Helm repository...")
            run_ssh_command(
                ssh_client,
                "helm repo add nvdp https://nvidia.github.io/k8s-device-plugin",
            )

            log.info("Updating Helm repositories...")
            run_ssh_command(ssh_client, "helm repo update")

            log.info("Installing NVIDIA Device Plugin...")
            nvidia_device_plugin_command = """
helm upgrade -i nvdp nvdp/nvidia-device-plugin \
--namespace nvidia-device-plugin \
--create-namespace \
--version 0.14.5 \
--set runtimeClassName="nvidia" \
--set deviceListStrategy=volume-mounts \
--set-string nodeSelector.allow-nvdp="true"
"""
            run_ssh_command(ssh_client, nvidia_device_plugin_command)
            log.info("NVIDIA Device Plugin installation completed.")
            log.info("NVIDIA Runtime Engine configuration completed.")
        except Exception as e:
            log.error(f"Error configuring NVIDIA Runtime Engine: {str(e)}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="PROVIDER_003",
                payload={
                    "error": "NVIDIA Runtime Engine Configuration Failed",
                    "message": f"Failed to configure NVIDIA Runtime Engine: {str(e)}",
                },
            )

    def _get_base64_encoded_key(self, ssh_client):
        log.info("Retrieving and encoding the key...")
        try:
            command = "cat ~/key.pem | openssl base64 -A"
            output, _ = run_ssh_command(ssh_client, command)
            encoded_key = output.strip()
            log.info("Key successfully retrieved and encoded.")
            return encoded_key
        except Exception as e:
            log.error(f"Error retrieving or encoding the key: {str(e)}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="PROVIDER_002",
                payload={
                    "error": "Key Retrieval Failed",
                    "message": f"Failed to retrieve or encode the key: {str(e)}",
                },
            )
