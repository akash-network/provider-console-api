# Gateway API Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add support in `provider-console-api` for installing and migrating Akash providers to v0.12.0 with NGINX Gateway Fabric + cert-manager replacing ingress-nginx.

**Architecture:** Two new service modules (`GatewayApiService`, `CertManagerService`) own the new install primitives. A `MigrationService` orchestrates the existing-provider migration path. New installs route through `AkashClusterService._create_provider_tasks` (modified). Existing v0.11.2 providers route through a new `POST /provider/migrate-gateway-api` endpoint. All host-side actions remain SSH command orchestration matching existing patterns.

**Tech Stack:** FastAPI 0.115, Pydantic v2, Helm 3, kubectl, K3s, cert-manager v1.19.1, NGINX Gateway Fabric (Gateway API CRDs ref `v2.5.1`), `akash-gateway` Helm chart.

**Reference:** GitHub issue [#65](https://github.com/akash-network/provider-console-api/issues/65). Project memory: `memory/project_gateway_api_migration.md`.

**Test framework note:** This repo currently has no test framework (`tests/`, pytest, conftest absent). Verification is manual on a real cluster. Each task includes a manual verification checkpoint; introducing pytest is out of scope here.

---

## File Structure

**New files:**
- `application/model/cert_manager_input.py` — Pydantic models for the `cert_manager` request block
- `application/service/gateway_api_service.py` — Gateway API CRDs, NGF, akash-gateway, NGF rollout-restart, self-signed `akash-default-tls` Secret
- `application/service/cert_manager_service.py` — cert-manager Helm install (idempotent), DNS provider Secret, ClusterIssuer, wildcard `Certificate`, wait-for-Ready
- `application/service/migration_service.py` — orchestrator for `/provider/migrate-gateway-api` (pre-flight + helm values backup + Gateway-API install + chart upgrades + ingress-nginx uninstall)

**Modified files:**
- `application/config/config.py` — version bumps + new pinned versions
- `application/model/provider_build_input.py` — new `cert_manager` field on `ProviderBuildInput`
- `application/service/provider_service.py` — delete `_install_nginx_ingress`; update cleanup paths in `uninstall_provider_service`
- `application/service/akash_cluster_service.py` — replace `install_nginx_ingress` task with the new Gateway-API + cert-manager task sequence; add `migrate_gateway_api` orchestrator
- `application/api/provider_build.py` — new `POST /provider/migrate-gateway-api` endpoint

---

## Task 1: Bump version defaults in `config.py`

**Files:**
- Modify: `application/config/config.py`

- [ ] **Step 1: Update default versions and add new pins**

Replace the Akash version block with:

```python
    # Akash Server Config
    AKASH_NODE_STATUS_CHECK = environ.get("AKASH_NODE_STATUS_CHECK")
    CHAIN_ID = environ.get("CHAIN_ID", "akashnet-2")
    GPU_DATA_URL = environ.get(
        "GPU_DATA_URL",
        "https://raw.githubusercontent.com/akash-network/provider-configs/main/devices/pcie/gpus.json",
    )
    KEYRING_BACKEND = environ.get("KEYRING_BACKEND", "file")
    AKASH_VERSION = environ.get("AKASH_VERSION", "v1.0.0")
    AKASH_NODE_HELM_CHART_VERSION = environ.get(
        "AKASH_NODE_HELM_CHART_VERSION", "14.0.0"
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
    GATEWAY_API_CRD_REF = environ.get("GATEWAY_API_CRD_REF", "v2.5.1")
    NGINX_GATEWAY_FABRIC_VERSION = environ.get("NGINX_GATEWAY_FABRIC_VERSION", "2.5.1")
    AKASH_GATEWAY_HELM_CHART_VERSION = environ.get("AKASH_GATEWAY_HELM_CHART_VERSION", "1.0.0")
    CERT_READY_TIMEOUT_SECONDS = int(environ.get("CERT_READY_TIMEOUT_SECONDS", "600"))
    LETSENCRYPT_PROD_SERVER = "https://acme-v02.api.letsencrypt.org/directory"
    LETSENCRYPT_STAGING_SERVER = "https://acme-staging-v02.api.letsencrypt.org/directory"
```

Delete `INGRESS_NGINX_VERSION = environ.get("INGRESS_NGINX_VERSION", "4.11.3")`.

- [ ] **Step 2: Manual verification**

```bash
python -c "from application.config.config import Config; print(Config.PROVIDER_SERVICES_VERSION, Config.CERT_MANAGER_VERSION, Config.GATEWAY_API_CRD_REF)"
```
Expected: `v0.12.0 v1.19.1 v2.5.1`

- [ ] **Step 3: Commit**

```bash
git add application/config/config.py
git commit -m "config: bump provider defaults to v0.12.0 and add Gateway API/cert-manager pins (#65)"
```

---

## Task 2: Add `CertManagerInput` Pydantic models

**Files:**
- Create: `application/model/cert_manager_input.py`

- [ ] **Step 1: Create model file**

```python
from base64 import b64decode
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, SecretStr, model_validator


class CloudflareConfig(BaseModel):
    api_token: SecretStr


class CloudDnsConfig(BaseModel):
    project: str
    service_account_json: SecretStr  # raw JSON or base64-encoded JSON

    @model_validator(mode="after")
    def _decode_if_base64(self):
        # Accept either raw JSON or base64-encoded JSON; normalize to raw JSON.
        raw = self.service_account_json.get_secret_value().strip()
        if not raw.startswith("{"):
            try:
                decoded = b64decode(raw).decode()
                if decoded.startswith("{"):
                    object.__setattr__(self, "service_account_json", SecretStr(decoded))
            except Exception:
                pass
        return self


class CertManagerInput(BaseModel):
    acme_email: Optional[EmailStr] = None
    use_staging: bool = False
    dns_provider: Literal["cloudflare", "clouddns"]
    cloudflare: Optional[CloudflareConfig] = None
    clouddns: Optional[CloudDnsConfig] = None

    class Config:
        extra = "forbid"

    @model_validator(mode="after")
    def _exactly_one_provider_block(self):
        if self.dns_provider == "cloudflare":
            if self.cloudflare is None or self.clouddns is not None:
                raise ValueError("dns_provider=cloudflare requires `cloudflare` block and no `clouddns` block")
        else:
            if self.clouddns is None or self.cloudflare is not None:
                raise ValueError("dns_provider=clouddns requires `clouddns` block and no `cloudflare` block")
        return self
```

- [ ] **Step 2: Manual verification**

```bash
python -c "
from application.model.cert_manager_input import CertManagerInput
ok = CertManagerInput(dns_provider='cloudflare', cloudflare={'api_token': 'tok'}, acme_email='a@b.com')
print('ok:', ok.dns_provider)
try:
    CertManagerInput(dns_provider='cloudflare', clouddns={'project': 'x', 'service_account_json': '{}'})
    print('FAIL: should have raised')
except ValueError as e:
    print('correctly rejected:', e)
"
```
Expected: prints `ok: cloudflare` then `correctly rejected: ...`.

- [ ] **Step 3: Commit**

```bash
git add application/model/cert_manager_input.py
git commit -m "model: add CertManagerInput for cert-manager DNS-01 config (#65)"
```

---

## Task 3: Wire `cert_manager` into `ProviderBuildInput`

**Files:**
- Modify: `application/model/provider_build_input.py`

- [ ] **Step 1: Add import and optional field**

At top of file, add:

```python
from application.model.cert_manager_input import CertManagerInput
```

Replace the `ProviderBuildInput` class:

```python
class ProviderBuildInput(BaseModel):
    wallet: Wallet
    nodes: List[Node]
    provider: Provider
    cert_manager: CertManagerInput

    @model_validator(mode="after")
    def _default_acme_email_from_provider_email(self):
        if self.cert_manager.acme_email is None and self.provider.config.email:
            object.__setattr__(self.cert_manager, "acme_email", self.provider.config.email)
        if self.cert_manager.acme_email is None:
            raise ValueError("cert_manager.acme_email is required (or set provider.config.email)")
        return self
```

- [ ] **Step 2: Manual verification**

```bash
python -c "
from application.model.provider_build_input import ProviderBuildInput
data = {
  'wallet': {'key_id': 'kid', 'import_mode': 'auto'},
  'nodes': [{'hostname':'h','username':'u','password':'p'}],
  'provider': {'attributes':[],'pricing':{},'config':{'domain':'x.com','email':'a@b.com'}},
  'cert_manager': {'dns_provider':'cloudflare','cloudflare':{'api_token':'t'}}
}
b = ProviderBuildInput(**data)
print('acme_email defaulted:', b.cert_manager.acme_email)
"
```
Expected: `acme_email defaulted: a@b.com`

- [ ] **Step 3: Commit**

```bash
git add application/model/provider_build_input.py
git commit -m "model: require cert_manager block on ProviderBuildInput (#65)"
```

---

## Task 4: Create `GatewayApiService`

**Files:**
- Create: `application/service/gateway_api_service.py`

- [ ] **Step 1: Create service**

```python
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
                    "message": f"Failed to install Gateway API CRDs: {str(e)}",
                },
            )

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
                    "message": f"Failed to install NGF: {str(e)}",
                },
            )

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
            cmds = [
                "openssl req -x509 -nodes -days 3650 -newkey rsa:2048 "
                "-keyout /tmp/akash-default.key -out /tmp/akash-default.crt "
                "-subj '/CN=default'",
                "kubectl -n akash-gateway create secret tls akash-default-tls "
                "--cert=/tmp/akash-default.crt --key=/tmp/akash-default.key",
                "rm -f /tmp/akash-default.key /tmp/akash-default.crt",
            ]
            for cmd in cmds:
                time.sleep(1)
                run_ssh_command(ssh_client, cmd, task_id=task_id)
            log.info("akash-default-tls Secret created.")
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="GATEWAY_003",
                payload={
                    "error": "akash-default-tls Creation Failed",
                    "message": f"Failed to create akash-default-tls: {str(e)}",
                },
            )

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
                    "message": f"Failed to install akash-gateway: {str(e)}",
                },
            )

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
                    "message": f"Failed to rollout-restart NGF: {str(e)}",
                },
            )
```

- [ ] **Step 2: Manual verification (import smoke test)**

```bash
python -c "from application.service.gateway_api_service import GatewayApiService; s=GatewayApiService(); print(s.NGF_VALUES_FILE)"
```
Expected: `~/provider/values-nginx-gateway-fabric.yaml`

- [ ] **Step 3: Commit**

```bash
git add application/service/gateway_api_service.py
git commit -m "service: add GatewayApiService for NGF + akash-gateway install (#65)"
```

---

## Task 5: Create `CertManagerService`

**Files:**
- Create: `application/service/cert_manager_service.py`

- [ ] **Step 1: Create service**

```python
import json
import shlex
import time
from base64 import b64encode
from typing import Optional

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
                    "message": f"Failed to install cert-manager: {str(e)}",
                },
            )

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
                    "message": f"Failed to apply Cloudflare API token Secret: {str(e)}",
                },
            )

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
                    "message": f"Failed to apply CloudDNS SA Secret: {str(e)}",
                },
            )

    def create_cluster_issuer(
        self, ssh_client, cert_manager_input: CertManagerInput, domain: str, task_id: str
    ):
        issuer = self.issuer_name(cert_manager_input)
        server = (
            Config.LETSENCRYPT_STAGING_SERVER
            if cert_manager_input.use_staging
            else Config.LETSENCRYPT_PROD_SERVER
        )
        email = cert_manager_input.acme_email
        zones = f"['{domain}', 'ingress.{domain}']"
        if cert_manager_input.dns_provider == "cloudflare":
            solver_block = (
                "    - dns01:\n"
                "        cloudflare:\n"
                "          apiTokenSecretRef:\n"
                "            key: api-token\n"
                "            name: cloudflare-api-token-secret\n"
                f"          email: {email}\n"
                "      selector:\n"
                f"        dnsZones: {zones}\n"
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
                f"        dnsZones: {zones}\n"
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
                    "message": f"Failed to apply ClusterIssuer: {str(e)}",
                },
            )

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
                    "message": f"Failed to apply wildcard Certificate: {str(e)}",
                },
            )

    def wait_for_certificate_ready(self, ssh_client, task_id: str):
        log.info("Waiting for wildcard-ingress Certificate Ready=True...")
        timeout = Config.CERT_READY_TIMEOUT_SECONDS
        check_interval = 10
        start = time.time()
        cmd = (
            "kubectl -n akash-gateway get certificate wildcard-ingress "
            "-o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' "
            "2>/dev/null || true"
        )
        while time.time() - start < timeout:
            try:
                stdout, _ = run_ssh_command(
                    ssh_client, cmd, check_exit_status=False, task_id=task_id
                )
                if stdout.strip() == "True":
                    log.info("wildcard-ingress Certificate is Ready.")
                    return
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
```

- [ ] **Step 2: Update `run_ssh_command` to support a `redact` flag**

If `application/utils/ssh_utils.py:run_ssh_command` does not already accept a `redact` keyword that suppresses logging the command body, add it. Read the current signature first; if it already redacts via another mechanism (e.g., key-based redaction), wire the calls through that mechanism instead. Concretely: open `application/utils/ssh_utils.py`, locate `run_ssh_command`, add a keyword parameter `redact: bool = False` and use it to gate any `log.debug/info` line that prints the command. Calls in `_create_cloudflare_secret` and `_create_clouddns_secret` already pass `redact=True`; if the helper signature can't accept it, change those two call sites to log a redacted summary manually before invoking the unmodified helper.

- [ ] **Step 3: Manual verification (import + issuer naming)**

```bash
python -c "
from application.model.cert_manager_input import CertManagerInput
from application.service.cert_manager_service import CertManagerService
s = CertManagerService()
prod = CertManagerInput(dns_provider='cloudflare', cloudflare={'api_token':'t'}, acme_email='a@b.com')
stg = CertManagerInput(dns_provider='cloudflare', cloudflare={'api_token':'t'}, acme_email='a@b.com', use_staging=True)
print(s.issuer_name(prod), s.issuer_name(stg))
"
```
Expected: `letsencrypt-prod letsencrypt-staging`

- [ ] **Step 4: Commit**

```bash
git add application/service/cert_manager_service.py application/utils/ssh_utils.py
git commit -m "service: add CertManagerService with DNS-01 cloudflare/clouddns + wait-for-Ready (#65)"
```

---

## Task 6: Drop ingress-nginx from `ProviderService`

**Files:**
- Modify: `application/service/provider_service.py`

- [ ] **Step 1: Delete `_install_nginx_ingress`**

Remove the entire `_install_nginx_ingress` method (currently at `provider_service.py:233-271`).

- [ ] **Step 2: Update `uninstall_provider_service` cleanup**

Replace `provider_service.py:569-578` with:

```python
    def uninstall_provider_service(self, ssh_client, task_id: str):
        log.info("Uninstalling provider service...")

        commands = [
            "/usr/local/bin/k3s-uninstall.sh",
            (
                "rm -rf ~/bin/ ~/calico.yaml ~/key.pem ~/provider/ "
                "~/.akash/ ~/.kube/ "
                "~/ingress-nginx-custom.yaml "
                "~/values-nginx-gateway-fabric.yaml"
            ),
        ]
        for cmd in commands:
            run_ssh_command(ssh_client, cmd, task_id=task_id)
        log.info("Provider service uninstalled successfully.")
```

`~/ingress-nginx-custom.yaml` stays in the cleanup list to handle hosts that were originally provisioned by older code.

- [ ] **Step 3: Manual verification (import smoke test)**

```bash
python -c "from application.service.provider_service import ProviderService; print('install_nginx_ingress removed:', not hasattr(ProviderService, '_install_nginx_ingress'))"
```
Expected: `install_nginx_ingress removed: True`

- [ ] **Step 4: Commit**

```bash
git add application/service/provider_service.py
git commit -m "service: drop ingress-nginx install from ProviderService (#65)"
```

---

## Task 7: Wire Gateway API + cert-manager into the build flow

**Files:**
- Modify: `application/service/akash_cluster_service.py`

- [ ] **Step 1: Imports and constructor**

Add imports near the top:

```python
from application.service.cert_manager_service import CertManagerService
from application.service.gateway_api_service import GatewayApiService
```

Update `AkashClusterService.__init__`:

```python
    def __init__(self):
        self.k3s_service = K3sService()
        self.provider_service = ProviderService()
        self.persistent_storage_service = PersistentStorageService()
        self.upgrade_service = UpgradeService()
        self.gateway_api_service = GatewayApiService()
        self.cert_manager_service = CertManagerService()
        self.task_manager = TaskManager()
```

- [ ] **Step 2: Replace `install_nginx_ingress` task with the new sequence**

In `_create_provider_tasks`, locate the `Task(... "install_nginx_ingress" ...)` block (currently lines 279-285). Pull `cert_manager_input` and `domain` from `provider_build_input` near the top of the method:

```python
        cert_manager_input = provider_build_input.cert_manager
        domain = provider_build_input.provider.config.domain
```

Replace the single `install_nginx_ingress` task with:

```python
            Task(
                str(uuid4()),
                "install_gateway_api_crds",
                "Install Gateway API CRDs",
                self.gateway_api_service.install_gateway_api_crds,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "install_nginx_gateway_fabric",
                "Install NGINX Gateway Fabric",
                self.gateway_api_service.install_nginx_gateway_fabric,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "install_cert_manager",
                "Install cert-manager",
                self.cert_manager_service.install_cert_manager,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "create_dns_provider_secret",
                "Create DNS provider Secret",
                self.cert_manager_service.create_dns_provider_secret,
                ssh_client,
                cert_manager_input,
            ),
            Task(
                str(uuid4()),
                "create_cluster_issuer",
                "Create ClusterIssuer",
                self.cert_manager_service.create_cluster_issuer,
                ssh_client,
                cert_manager_input,
                domain,
            ),
            Task(
                str(uuid4()),
                "create_wildcard_certificate",
                "Request wildcard Certificate",
                self.cert_manager_service.create_wildcard_certificate,
                ssh_client,
                cert_manager_input,
                domain,
            ),
            Task(
                str(uuid4()),
                "wait_for_certificate_ready",
                "Wait for wildcard Certificate Ready",
                self.cert_manager_service.wait_for_certificate_ready,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "create_akash_default_tls_secret",
                "Create akash-default-tls Secret",
                self.gateway_api_service.create_akash_default_tls_secret,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "install_akash_gateway",
                "Install akash-gateway",
                self.gateway_api_service.install_akash_gateway,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "rollout_restart_ngf",
                "Rollout-restart NGINX Gateway Fabric",
                self.gateway_api_service.rollout_restart_ngf,
                ssh_client,
            ),
```

Remove the `install_nginx_ingress` task entirely. Leave the `configure_gpu_support` and `check_akash_node_readiness` tasks below untouched.

- [ ] **Step 3: Manual verification (import smoke test)**

```bash
python -c "
from application.service.akash_cluster_service import AkashClusterService
s = AkashClusterService()
print('cm:', type(s.cert_manager_service).__name__, 'gw:', type(s.gateway_api_service).__name__)
"
```
Expected: `cm: CertManagerService gw: GatewayApiService`

- [ ] **Step 4: Commit**

```bash
git add application/service/akash_cluster_service.py
git commit -m "service: wire Gateway API + cert-manager into build-provider task flow (#65)"
```

---

## Task 8: Create `MigrationService`

**Files:**
- Create: `application/service/migration_service.py`

- [ ] **Step 1: Create service**

```python
import json
import time
from typing import Tuple

from fastapi import status
from packaging import version

from application.config.config import Config
from application.exception.application_error import ApplicationError
from application.model.cert_manager_input import CertManagerInput
from application.service.cert_manager_service import CertManagerService
from application.service.gateway_api_service import GatewayApiService
from application.utils.logger import log
from application.utils.ssh_utils import run_ssh_command


class MigrationService:
    """Orchestrates v0.11.x → v0.12.0 + Gateway API migration for an existing provider."""

    BACKUP_DIR = "/root/provider/backups"
    BACKUP_SUFFIX = ".pre-v0.12.0.values"

    def __init__(self):
        self.gateway_api_service = GatewayApiService()
        self.cert_manager_service = CertManagerService()

    def verify_pre_migration_version(self, ssh_client, task_id: str):
        log.info("Verifying provider version is v0.11.x...")
        cmd = (
            "helm list -n akash-services -o json "
            "| jq -r '.[] | select(.name==\"akash-provider\") | .app_version'"
        )
        stdout, _ = run_ssh_command(ssh_client, cmd, task_id=task_id)
        current = stdout.strip().lstrip("v")
        if not current:
            raise ApplicationError(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="MIGRATE_001",
                payload={
                    "error": "Provider Not Found",
                    "message": "Could not detect installed akash-provider release",
                },
            )
        parsed = version.parse(current)
        floor = version.parse("0.11.0")
        ceiling = version.parse("0.12.0")
        if not (floor <= parsed < ceiling):
            raise ApplicationError(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="MIGRATE_002",
                payload={
                    "error": "Unsupported Source Version",
                    "message": (
                        f"Migration requires v0.11.x; found v{current}. "
                        "Use /provider/upgrade for normal upgrades."
                    ),
                },
            )
        log.info("Source version v%s OK.", current)

    def backup_helm_values(self, ssh_client, task_id: str):
        log.info("Backing up helm values to %s...", self.BACKUP_DIR)
        cmds = [
            f"mkdir -p {self.BACKUP_DIR}",
            (
                f"for r in $(helm list -n akash-services -q); do "
                f"helm -n akash-services get values \"$r\" "
                f"> {self.BACKUP_DIR}/${{r}}{self.BACKUP_SUFFIX}; "
                f"done"
            ),
            (
                f"cp -f ~/provider/provider.yaml "
                f"{self.BACKUP_DIR}/provider.yaml{self.BACKUP_SUFFIX} "
                f"|| true"
            ),
        ]
        for cmd in cmds:
            run_ssh_command(ssh_client, cmd, task_id=task_id)
        log.info("Backups written.")

    def upgrade_operators_and_provider(self, ssh_client, task_id: str):
        log.info("Upgrading operators and provider to v0.12.0 using backed-up values...")
        repo = "akash" if Config.CHAIN_ID == "akashnet-2" else "akash-dev"
        devel = "" if Config.CHAIN_ID == "akashnet-2" else " --devel"
        hostname_v = Config.PROVIDER_HOSTNAME_OPERATOR_VERSION.lstrip("v")
        inventory_v = Config.PROVIDER_INVENTORY_OPERATOR_VERSION.lstrip("v")
        provider_v = Config.PROVIDER_SERVICES_VERSION.lstrip("v")

        cmds = [
            f"helm repo update {repo}",
            (
                f"helm -n akash-services upgrade akash-hostname-operator "
                f"{repo}/akash-hostname-operator "
                f"-f {self.BACKUP_DIR}/akash-hostname-operator{self.BACKUP_SUFFIX} "
                f"--set image.tag={hostname_v}{devel}"
            ),
            (
                f"helm -n akash-services upgrade inventory-operator "
                f"{repo}/akash-inventory-operator "
                f"-f {self.BACKUP_DIR}/inventory-operator{self.BACKUP_SUFFIX} "
                f"--set image.tag={inventory_v}{devel}"
            ),
            (
                f"helm -n akash-services upgrade akash-provider "
                f"{repo}/provider "
                "-f ~/provider/provider.yaml "
                "--set bidpricescript=\"$(cat ~/provider/price_script_generic.sh "
                "| openssl base64 -A)\" "
                f"--set image.tag={provider_v}{devel}"
            ),
        ]
        for cmd in cmds:
            time.sleep(1)
            run_ssh_command(ssh_client, cmd, task_id=task_id)
        log.info("Operator and provider chart upgrades complete.")

    def uninstall_ingress_nginx(self, ssh_client, task_id: str):
        log.info("Uninstalling ingress-nginx Helm release...")
        check_cmd = "helm list -n ingress-nginx -q | grep -w ingress-nginx || true"
        stdout, _ = run_ssh_command(ssh_client, check_cmd, task_id=task_id)
        if "ingress-nginx" not in stdout:
            log.info("ingress-nginx not present; skipping uninstall.")
            return
        run_ssh_command(
            ssh_client,
            "helm uninstall ingress-nginx -n ingress-nginx",
            task_id=task_id,
        )
        log.info("ingress-nginx uninstalled.")
```

- [ ] **Step 2: Manual verification (import + constants)**

```bash
python -c "
from application.service.migration_service import MigrationService
m = MigrationService()
print(m.BACKUP_DIR, m.BACKUP_SUFFIX)
"
```
Expected: `/root/provider/backups .pre-v0.12.0.values`

- [ ] **Step 3: Commit**

```bash
git add application/service/migration_service.py
git commit -m "service: add MigrationService for v0.11.x → v0.12.0 Gateway API migration (#65)"
```

---

## Task 9: Wire migration orchestrator + endpoint

**Files:**
- Modify: `application/service/akash_cluster_service.py`
- Modify: `application/api/provider_build.py`

- [ ] **Step 1: Add migration orchestrator on `AkashClusterService`**

Add import:

```python
from application.service.migration_service import MigrationService
from application.model.cert_manager_input import CertManagerInput
```

Add to `__init__`:

```python
        self.migration_service = MigrationService()
```

Add new method on `AkashClusterService`:

```python
    async def migrate_gateway_api(
        self,
        action_id,
        control_machine,
        cert_manager_input: CertManagerInput,
        domain: str,
        wallet_address,
    ):
        ssh_client = get_ssh_client(control_machine)
        tasks = [
            Task(
                str(uuid4()),
                "verify_pre_migration_version",
                "Verify source version is v0.11.x",
                self.migration_service.verify_pre_migration_version,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "backup_helm_values",
                "Backup helm values to /root/provider/backups",
                self.migration_service.backup_helm_values,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "install_gateway_api_crds",
                "Install Gateway API CRDs",
                self.gateway_api_service.install_gateway_api_crds,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "install_nginx_gateway_fabric",
                "Install NGINX Gateway Fabric",
                self.gateway_api_service.install_nginx_gateway_fabric,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "install_cert_manager",
                "Install cert-manager (idempotent)",
                self.cert_manager_service.install_cert_manager,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "create_dns_provider_secret",
                "Create DNS provider Secret",
                self.cert_manager_service.create_dns_provider_secret,
                ssh_client,
                cert_manager_input,
            ),
            Task(
                str(uuid4()),
                "create_cluster_issuer",
                "Create ClusterIssuer",
                self.cert_manager_service.create_cluster_issuer,
                ssh_client,
                cert_manager_input,
                domain,
            ),
            Task(
                str(uuid4()),
                "create_wildcard_certificate",
                "Request wildcard Certificate",
                self.cert_manager_service.create_wildcard_certificate,
                ssh_client,
                cert_manager_input,
                domain,
            ),
            Task(
                str(uuid4()),
                "wait_for_certificate_ready",
                "Wait for wildcard Certificate Ready",
                self.cert_manager_service.wait_for_certificate_ready,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "create_akash_default_tls_secret",
                "Create akash-default-tls Secret",
                self.gateway_api_service.create_akash_default_tls_secret,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "install_akash_gateway",
                "Install akash-gateway",
                self.gateway_api_service.install_akash_gateway,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "upgrade_operators_and_provider",
                "Upgrade operators + provider to v0.12.0",
                self.migration_service.upgrade_operators_and_provider,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "uninstall_ingress_nginx",
                "Uninstall ingress-nginx",
                self.migration_service.uninstall_ingress_nginx,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "rollout_restart_ngf",
                "Rollout-restart NGINX Gateway Fabric",
                self.gateway_api_service.rollout_restart_ngf,
                ssh_client,
            ),
        ]
        self.task_manager.create_action(action_id, "Migrate Gateway API", tasks)
        store_wallet_action_mapping(wallet_address, action_id)
        await self.task_manager.run_action(action_id)
        log.info("Gateway API migration completed for action %s", action_id)
```

- [ ] **Step 2: Add the FastAPI endpoint**

In `application/api/provider_build.py`, add:

```python
from application.model.cert_manager_input import CertManagerInput


@router.post("/provider/migrate-gateway-api", include_in_schema=False)
async def migrate_gateway_api(
    background_tasks: BackgroundTasks,
    machine_input: Dict,
    wallet_address: str = Depends(verify_token),
) -> Dict:
    try:
        control_machine = machine_input["control_machine"]
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(control_machine["keyfile"])
        control_machine_input = ControlMachineInput(**control_machine)

        cert_manager_input = CertManagerInput(**machine_input["cert_manager"])
        domain = machine_input["domain"]
        if not cert_manager_input.acme_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": "error",
                    "error": {
                        "message": "cert_manager.acme_email is required for migration",
                        "error_code": "VAL_007",
                    },
                },
            )

        action_id = str(uuid4())
        akash_cluster_service = AkashClusterService()
        background_tasks.add_task(
            akash_cluster_service.migrate_gateway_api,
            action_id,
            control_machine_input,
            cert_manager_input,
            domain,
            wallet_address,
        )
        return {
            "message": "Gateway API migration started successfully",
            "action_id": action_id,
        }
    except HTTPException:
        raise
    except ValidationError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "error": {
                    "message": "Invalid migration input",
                    "error_code": "VAL_008",
                    "details": [
                        {"field": err["loc"][0], "message": err["msg"]}
                        for err in ve.errors()
                    ],
                },
            },
        )
    except Exception as e:
        log.error("Error starting Gateway API migration: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred while starting migration: {e}",
                    "error_code": "PRV_009",
                },
            },
        )
```

- [ ] **Step 3: Manual verification (route registered)**

```bash
python -c "
from application.api.provider_build import router
paths = [r.path for r in router.routes]
print('migrate present:', '/provider/migrate-gateway-api' in paths)
"
```
Expected: `migrate present: True`

- [ ] **Step 4: Commit**

```bash
git add application/service/akash_cluster_service.py application/api/provider_build.py
git commit -m "api: add POST /provider/migrate-gateway-api endpoint (#65)"
```

---

## Task 10: Manual end-to-end verification on a test cluster

**Files:** none (operational verification)

This task does not produce code; it gates the PR. Run against a non-production cluster you control. Record outputs in the PR description.

- [ ] **Step 1: Fresh build path**

Provision a new provider via `POST /build-provider` with a payload that includes:
```json
{
  "cert_manager": {
    "dns_provider": "cloudflare",
    "acme_email": "ops@example.com",
    "use_staging": true,
    "cloudflare": { "api_token": "<scoped DNS:Edit token>" }
  },
  ...
}
```
Verify on the host:
```bash
helm list -A
kubectl -n akash-gateway describe certificate wildcard-ingress | grep -E 'Status|Ready'
kubectl -n nginx-gateway get pods
kubectl get crd | grep gateway.networking.k8s.io
```
Expected: charts at `provider-16.0.0` etc., `Ready: True`, NGF pod 2/2 Running, four Gateway API CRDs present.

- [ ] **Step 2: Migration path**

On a separate provider running v0.11.2, call `POST /provider/migrate-gateway-api` and verify after completion:
```bash
ls /root/provider/backups/
helm list -n ingress-nginx
helm list -n akash-services
kubectl -n akash-gateway get certificate wildcard-ingress
```
Expected: `*.pre-v0.12.0.values` files present, `ingress-nginx` namespace empty, `akash-services` charts at v0.12.0 / 16.0.0, certificate Ready.

- [ ] **Step 3: Negative — missing creds**

POST to `/build-provider` without the `cert_manager` block. Expected: HTTP 400 with `VAL_005` validation error.

- [ ] **Step 4: Idempotency — re-run migration**

Re-call `/provider/migrate-gateway-api` against an already-migrated provider. Expected: pre-flight rejects with `MIGRATE_002` (current version no longer in v0.11.x range).

- [ ] **Step 5: Credential leak check**

```bash
kubectl logs -n provider-console-api <pod> | grep -E 'api-token|service_account_json|key.json' || echo "no leaks"
```
Expected: `no leaks`. Also confirm the request body is not echoed back in API responses.

- [ ] **Step 6: HTTPS smoke**

```bash
echo "" | openssl s_client -connect test.ingress.<domain>:443 -showcerts 2>&1 \
  | openssl x509 -issuer -subject -noout
```
Expected: `issuer=C = US, O = Let's Encrypt, ...` (or staging issuer when `use_staging=true`).

---

## Self-Review Checklist

- **Spec coverage:** Issue #65 acceptance criteria — new builds on v0.12.0 (Tasks 1, 7), migration endpoint (Tasks 8, 9), helm `16.0.0` and ingress-nginx absent (Tasks 8, 9), wildcard cert Ready (Task 5), backup files persisted (Task 8), creds never logged (Tasks 5, 10).
- **Placeholder scan:** None.
- **Type consistency:** `CertManagerInput.acme_email` is `Optional[EmailStr]` set by validator; `MigrationService.BACKUP_DIR` and `BACKUP_SUFFIX` are referenced consistently; `issuer_name()` is the single source of truth for `letsencrypt-prod` vs `letsencrypt-staging`.

---

## Out of scope (deferred)

- Automated rollback (backups exist; manual restore documented separately if needed)
- Additional DNS providers (Route53, Azure, DigitalOcean) — schema is extensible
- Frontend UI changes in the Provider Console
- Introducing a Python test framework (pytest) — separate initiative
