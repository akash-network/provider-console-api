import time

from fastapi import status

from application.config.config import Config
from application.exception.application_error import ApplicationError
from application.utils.logger import log
from application.utils.ssh_utils import run_ssh_command


class GatewayApiService:

    NGF_VALUES_FILE = "~/provider/values-nginx-gateway-fabric.yaml"

    def install_gateway_api_crds(self, ssh_client, task_id: str):
        log.info("Installing Gateway API CRDs (ref %s)...", Config.GATEWAY_API_CRD_REF)
        cmd = (
            f"kubectl kustomize "
            f"\"https://github.com/nginx/nginx-gateway-fabric/config/crd/gateway-api/experimental"
            f"?ref={Config.GATEWAY_API_CRD_REF}\" | kubectl apply --server-side -f -"
        )
        try:
            run_ssh_command(ssh_client, cmd, task_id=task_id)
            log.info("Gateway API CRDs installed.")
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="GATEWAY_001",
                payload={
                    "error": "Gateway API CRD Install Failed",
                    "message": f"Failed to install Gateway API CRDs: {e!s}",
                },
            ) from e

    def install_nginx_gateway_fabric(self, ssh_client, task_id: str):
        log.info("Installing NGINX Gateway Fabric...")
        values_yaml = """
cat > """ + self.NGF_VALUES_FILE + """ << 'EOF'
nginxGateway:
  gatewayClassName: nginx
  gwAPIExperimentalFeatures:
    enable: true
  leaderElection:
    enable: true
  config:
    logging:
      level: info
  snippets:
    enable: true
  resources:
    requests:
      cpu: 1000m
      memory: 1Gi
    limits:
      cpu: 1000m
      memory: 1Gi

nginx:
  kind: daemonSet
  service:
    type: ClusterIP
  container:
    hostPorts:
      - port: 80
        containerPort: 80
      - port: 443
        containerPort: 443
      - port: 8443
        containerPort: 8443
      - port: 8444
        containerPort: 8444
      - port: 5002
        containerPort: 5002
    resources:
      requests:
        cpu: 1000m
        memory: 1Gi
      limits:
        cpu: 1000m
        memory: 1Gi
EOF
"""
        try:
            run_ssh_command(ssh_client, "mkdir -p ~/provider", task_id=task_id)
            time.sleep(1)
            run_ssh_command(ssh_client, values_yaml, task_id=task_id)
            time.sleep(1)
            install_cmd = (
                f"helm upgrade --install ngf "
                f"oci://ghcr.io/nginx/charts/nginx-gateway-fabric "
                f"--version {Config.NGINX_GATEWAY_FABRIC_VERSION} "
                f"--create-namespace -n nginx-gateway "
                f"-f {self.NGF_VALUES_FILE}"
            )
            run_ssh_command(ssh_client, install_cmd, task_id=task_id)
            log.info("NGINX Gateway Fabric installed.")
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="GATEWAY_002",
                payload={
                    "error": "NGINX Gateway Fabric Install Failed",
                    "message": f"Failed to install NGF: {e!s}",
                },
            ) from e

    def create_akash_default_tls_secret(self, ssh_client, task_id: str):
        log.info("Creating self-signed akash-default-tls Secret in akash-gateway ns...")
        try:
            run_ssh_command(
                ssh_client,
                "kubectl create namespace akash-gateway --dry-run=client -o yaml | kubectl apply -f -",
                task_id=task_id,
            )
            check_cmd = (
                "kubectl -n akash-gateway get secret akash-default-tls "
                "--ignore-not-found -o name"
            )
            existing, _ = run_ssh_command(ssh_client, check_cmd, task_id=task_id)
            if "secret/akash-default-tls" in existing:
                log.info("akash-default-tls already exists; skipping creation.")
                return
            openssl_cmd = (
                "openssl req -x509 -nodes -days 3650 -newkey rsa:2048 "
                "-keyout /tmp/akash-default.key -out /tmp/akash-default.crt "
                "-subj '/CN=default'"
            )
            kubectl_cmd = (
                "kubectl -n akash-gateway create secret tls akash-default-tls "
                "--cert=/tmp/akash-default.crt --key=/tmp/akash-default.key"
            )
            cleanup_cmd = "rm -f /tmp/akash-default.key /tmp/akash-default.crt"
            try:
                time.sleep(1)
                run_ssh_command(ssh_client, openssl_cmd, task_id=task_id)
                time.sleep(1)
                run_ssh_command(ssh_client, kubectl_cmd, task_id=task_id)
            finally:
                # Best-effort cleanup of temp key material; never mask the original error.
                try:
                    run_ssh_command(ssh_client, cleanup_cmd, task_id=task_id)
                except Exception as cleanup_err:
                    log.warning("akash-default-tls temp file cleanup failed: %s", cleanup_err)
            log.info("akash-default-tls Secret created.")
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="GATEWAY_003",
                payload={
                    "error": "akash-default-tls Creation Failed",
                    "message": f"Failed to create akash-default-tls: {e!s}",
                },
            ) from e

    def install_akash_gateway(self, ssh_client, task_id: str):
        log.info("Installing akash-gateway Helm chart...")
        try:
            install_cmd = (
                "helm upgrade --install akash-gateway akash/akash-gateway "
                "-n akash-gateway --create-namespace "
                "-f ~/provider/provider.yaml "
                f"--version {Config.AKASH_GATEWAY_HELM_CHART_VERSION}"
            )
            run_ssh_command(ssh_client, install_cmd, task_id=task_id)
            log.info("akash-gateway installed.")
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="GATEWAY_004",
                payload={
                    "error": "akash-gateway Install Failed",
                    "message": f"Failed to install akash-gateway: {e!s}",
                },
            ) from e

    def rollout_restart_ngf(self, ssh_client, task_id: str):
        log.info("Rolling out NGINX Gateway Fabric...")
        try:
            run_ssh_command(
                ssh_client,
                "kubectl -n nginx-gateway rollout restart deployment "
                "-l app.kubernetes.io/name=nginx-gateway-fabric",
                task_id=task_id,
            )
            log.info("NGF rollout-restart issued.")
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="GATEWAY_005",
                payload={
                    "error": "NGF Rollout Restart Failed",
                    "message": f"Failed to rollout-restart NGF: {e!s}",
                },
            ) from e
