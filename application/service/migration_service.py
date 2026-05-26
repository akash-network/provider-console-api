import time

from fastapi import status
from packaging import version

from application.config.config import Config
from application.exception.application_error import ApplicationError
from application.utils.logger import log
from application.utils.ssh_utils import run_ssh_command


class MigrationService:
    """Orchestrates v0.11.x → v0.12.0 + Gateway API migration for an existing provider."""

    BACKUP_DIR = "/root/provider/backups"
    BACKUP_SUFFIX = ".pre-v0.12.0.values"

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
                status_code=status.HTTP_404_NOT_FOUND,
                error_code="MIGRATE_001",
                payload={
                    "error": "Provider Not Found",
                    "message": "Could not detect installed akash-provider release",
                },
            )
        try:
            parsed = version.parse(current)
        except version.InvalidVersion:
            raise ApplicationError(
                status_code=status.HTTP_404_NOT_FOUND,
                error_code="MIGRATE_001",
                payload={
                    "error": "Provider Not Found",
                    "message": f"Could not parse app_version '{current}' from akash-provider release",
                },
            ) from None
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
            "mv -f ~/provider/price_script_generic.sh ~/provider/price_script_generic.sh.pre-v0.12.0 || true",
            f"wget -q -O ~/provider/price_script_generic.sh {Config.PROVIDER_PRICE_SCRIPT_URL}",
            "chmod +x ~/provider/price_script_generic.sh",
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
