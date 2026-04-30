import shlex
import time
from base64 import b64encode

from fastapi import status

from application.config.config import Config
from application.exception.application_error import ApplicationError
from application.model.cert_manager_input import CertManagerInput
from application.utils.logger import log
from application.utils.ssh_utils import run_ssh_command


class CertManagerService:

    CLUSTER_ISSUER_PROD = "letsencrypt-prod"
    CLUSTER_ISSUER_STAGING = "letsencrypt-staging"

    def issuer_name(self, cert_manager_input: CertManagerInput) -> str:
        return self.CLUSTER_ISSUER_STAGING if cert_manager_input.use_staging else self.CLUSTER_ISSUER_PROD

    def is_cert_manager_installed(self, ssh_client, task_id: str) -> bool:
        check_cmd = "kubectl get crd certificates.cert-manager.io --ignore-not-found -o name"
        try:
            stdout, _ = run_ssh_command(ssh_client, check_cmd, task_id=task_id)
            return "certificates.cert-manager.io" in stdout
        except Exception:
            return False

    def install_cert_manager(self, ssh_client, task_id: str):
        if self.is_cert_manager_installed(ssh_client, task_id):
            log.info("cert-manager already installed; skipping.")
            return
        log.info("Installing cert-manager %s...", Config.CERT_MANAGER_VERSION)
        cmds = [
            "helm repo add jetstack https://charts.jetstack.io",
            "helm repo update jetstack",
            (
                "helm upgrade --install cert-manager jetstack/cert-manager "
                "--namespace cert-manager --create-namespace "
                f"--version {Config.CERT_MANAGER_VERSION} "
                "--set crds.enabled=true"
            ),
        ]
        try:
            for cmd in cmds:
                time.sleep(1)
                run_ssh_command(ssh_client, cmd, task_id=task_id)
            log.info("cert-manager installed.")
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="CERTMGR_001",
                payload={
                    "error": "cert-manager Install Failed",
                    "message": f"Failed to install cert-manager: {e!s}",
                },
            ) from e

    def create_dns_provider_secret(
        self, ssh_client, cert_manager_input: CertManagerInput, task_id: str
    ):
        if cert_manager_input.dns_provider == "cloudflare":
            self._create_cloudflare_secret(ssh_client, cert_manager_input, task_id)
        else:
            self._create_clouddns_secret(ssh_client, cert_manager_input, task_id)

    def _create_cloudflare_secret(self, ssh_client, cert_manager_input, task_id):
        token = cert_manager_input.cloudflare.api_token.get_secret_value()
        token_b64 = b64encode(token.encode()).decode()
        manifest = (
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: cloudflare-api-token-secret\n"
            "  namespace: cert-manager\n"
            "type: Opaque\n"
            "data:\n"
            f"  api-token: {token_b64}\n"
        )
        manifest_b64 = b64encode(manifest.encode()).decode()
        cmd = f"echo {shlex.quote(manifest_b64)} | base64 -d | kubectl apply -f -"
        try:
            run_ssh_command(ssh_client, cmd, task_id=task_id, redact=True)
            log.info("Cloudflare API token Secret applied (token redacted).")
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="CERTMGR_002",
                payload={
                    "error": "Cloudflare Secret Apply Failed",
                    "message": f"Failed to apply Cloudflare API token Secret: {e!s}",
                },
            ) from e

    def _create_clouddns_secret(self, ssh_client, cert_manager_input, task_id):
        sa_json = cert_manager_input.clouddns.service_account_json.get_secret_value()
        sa_b64 = b64encode(sa_json.encode()).decode()
        manifest = (
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: clouddns-gcp-dns01-solver-sa\n"
            "  namespace: cert-manager\n"
            "type: Opaque\n"
            "data:\n"
            f"  key.json: {sa_b64}\n"
        )
        manifest_b64 = b64encode(manifest.encode()).decode()
        cmd = f"echo {shlex.quote(manifest_b64)} | base64 -d | kubectl apply -f -"
        try:
            run_ssh_command(ssh_client, cmd, task_id=task_id, redact=True)
            log.info("CloudDNS SA Secret applied (key redacted).")
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="CERTMGR_003",
                payload={
                    "error": "CloudDNS Secret Apply Failed",
                    "message": f"Failed to apply CloudDNS SA Secret: {e!s}",
                },
            ) from e

    def create_cluster_issuer(
        self, ssh_client, cert_manager_input: CertManagerInput, domain: str, task_id: str
    ):
        issuer = self.issuer_name(cert_manager_input)
        server = (
            Config.LETSENCRYPT_STAGING_SERVER
            if cert_manager_input.use_staging
            else Config.LETSENCRYPT_PROD_SERVER
        )
        if not cert_manager_input.acme_email:
            raise ApplicationError(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="CERTMGR_007",
                payload={
                    "error": "Missing ACME Email",
                    "message": "cert_manager.acme_email is required to create the ClusterIssuer",
                },
            )
        email = cert_manager_input.acme_email
        zones_block = (
            f"        dnsZones:\n"
            f"          - {domain}\n"
            f"          - ingress.{domain}\n"
        )
        if cert_manager_input.dns_provider == "cloudflare":
            solver_block = (
                "    - dns01:\n"
                "        cloudflare:\n"
                "          apiTokenSecretRef:\n"
                "            key: api-token\n"
                "            name: cloudflare-api-token-secret\n"
                "      selector:\n"
                f"{zones_block}"
            )
        else:
            project = cert_manager_input.clouddns.project
            solver_block = (
                "    - dns01:\n"
                "        cloudDNS:\n"
                f"          project: \"{project}\"\n"
                "          serviceAccountSecretRef:\n"
                "            name: clouddns-gcp-dns01-solver-sa\n"
                "            key: key.json\n"
                "      selector:\n"
                f"{zones_block}"
            )
        manifest = (
            "apiVersion: cert-manager.io/v1\n"
            "kind: ClusterIssuer\n"
            "metadata:\n"
            f"  name: {issuer}\n"
            "spec:\n"
            "  acme:\n"
            f"    email: {email}\n"
            f"    server: {server}\n"
            "    privateKeySecretRef:\n"
            f"      name: {issuer}-issuer-account-key\n"
            "    solvers:\n"
            f"{solver_block}"
        )
        manifest_b64 = b64encode(manifest.encode()).decode()
        cmd = f"echo {shlex.quote(manifest_b64)} | base64 -d | kubectl apply -f -"
        try:
            run_ssh_command(ssh_client, cmd, task_id=task_id)
            log.info("ClusterIssuer %s applied.", issuer)
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="CERTMGR_004",
                payload={
                    "error": "ClusterIssuer Apply Failed",
                    "message": f"Failed to apply ClusterIssuer: {e!s}",
                },
            ) from e

    def create_wildcard_certificate(
        self, ssh_client, cert_manager_input: CertManagerInput, domain: str, task_id: str
    ):
        issuer = self.issuer_name(cert_manager_input)
        manifest = (
            "apiVersion: cert-manager.io/v1\n"
            "kind: Certificate\n"
            "metadata:\n"
            "  name: wildcard-ingress\n"
            "  namespace: akash-gateway\n"
            "spec:\n"
            "  secretName: wildcard-ingress-tls\n"
            "  issuerRef:\n"
            f"    name: {issuer}\n"
            "    kind: ClusterIssuer\n"
            f"  commonName: '*.{domain}'\n"
            "  dnsNames:\n"
            f"    - '*.{domain}'\n"
            f"    - '*.ingress.{domain}'\n"
        )
        manifest_b64 = b64encode(manifest.encode()).decode()
        cmds = [
            "kubectl create namespace akash-gateway --dry-run=client -o yaml | kubectl apply -f -",
            f"echo {shlex.quote(manifest_b64)} | base64 -d | kubectl apply -f -",
        ]
        try:
            for cmd in cmds:
                run_ssh_command(ssh_client, cmd, task_id=task_id)
            log.info("Wildcard Certificate applied.")
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="CERTMGR_005",
                payload={
                    "error": "Wildcard Certificate Apply Failed",
                    "message": f"Failed to apply wildcard Certificate: {e!s}",
                },
            ) from e

    def wait_for_certificate_ready(self, ssh_client, task_id: str):
        log.info("Waiting for wildcard-ingress Certificate Ready=True...")
        from application.utils.redis import get_redis_client

        redis_client = get_redis_client()
        timeout = Config.CERT_READY_TIMEOUT_SECONDS
        check_interval = 10
        heartbeat_every = 3  # one heartbeat per ~30s
        start = time.time()
        iteration = 0
        cmd = (
            "kubectl -n akash-gateway get certificate wildcard-ingress "
            "-o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' "
            "2>/dev/null || true"
        )
        if task_id:
            redis_client.xadd(
                f"task:{task_id}",
                {"stdout": "Waiting for wildcard-ingress Certificate Ready=True (up to %ss)..." % timeout},
            )
        while time.time() - start < timeout:
            iteration += 1
            try:
                # Suppress per-iteration task-log streaming for the kubectl probe
                # itself; emit a periodic heartbeat instead so the operator can
                # see progress without 60+ identical stdout lines.
                stdout, _ = run_ssh_command(
                    ssh_client, cmd, check_exit_status=False
                )
                if stdout.strip() == "True":
                    log.info("wildcard-ingress Certificate is Ready.")
                    if task_id:
                        redis_client.xadd(
                            f"task:{task_id}",
                            {"stdout": "wildcard-ingress Certificate is Ready"},
                        )
                    return
                if task_id and iteration % heartbeat_every == 0:
                    elapsed = int(time.time() - start)
                    redis_client.xadd(
                        f"task:{task_id}",
                        {"stdout": f"Still waiting for wildcard-ingress Certificate ({elapsed}s elapsed)..."},
                    )
            except Exception as e:
                log.debug("Cert readiness probe error (continuing): %s", e)
            time.sleep(check_interval)
        # Surface cert-manager events to aid debugging
        try:
            describe, _ = run_ssh_command(
                ssh_client,
                "kubectl -n akash-gateway describe certificate wildcard-ingress",
                check_exit_status=False,
                task_id=task_id,
            )
            log.error("Certificate not Ready within %ss. Describe:\n%s", timeout, describe)
        except Exception:
            pass
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="CERTMGR_006",
            payload={
                "error": "Certificate Not Ready",
                "message": f"wildcard-ingress did not reach Ready=True within {timeout}s",
            },
        )
