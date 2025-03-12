from packaging import version
import asyncio
import json
from typing import Dict, Tuple
from fastapi import status

from application.config.config import Config
from application.exception.application_error import ApplicationError
from application.utils.ssh_utils import get_ssh_client, run_ssh_command
from application.utils.logger import log
from application.model.machine_input import ControlMachineInput


class VersionService:
    def __init__(self):
        self.HELM_CHECK_CMD = "helm list -n akash-services -o json | jq '.[] | select(.name == \"akash-node\")'"

    def _get_system_version(self, ssh_client) -> str:
        """Get system version from helm release"""
        stdout, _ = run_ssh_command(ssh_client, self.HELM_CHECK_CMD, True)
        helm_data = json.loads(stdout)

        if not helm_data:
            raise ApplicationError(
                status_code=status.HTTP_404_NOT_FOUND,
                error_code="PROVIDER_002",
                payload={
                    "error": "Akash Node Not Found",
                    "message": "Could not find akash-node helm release",
                },
            )

        return helm_data.get("app_version")

    def _compare_versions(
        self, system_version: str, current_version: str
    ) -> Tuple[bool, str, str]:
        """Compare system and current versions"""
        system_v = system_version.lstrip("v")
        current_v = current_version.lstrip("v")

        needs_upgrade = version.parse(system_v) < version.parse(current_v)

        return needs_upgrade, current_v, system_v

    async def check_upgrade_status(self, ssh_client) -> Dict:
        """Check if network upgrade is needed"""

        def get_version():
            try:
                return self._get_system_version(ssh_client)
            finally:
                ssh_client.close()

        try:
            # Get the system version in a separate thread
            system_version = await asyncio.to_thread(get_version)

            # Compare versions
            needs_upgrade, current_version, system_version = self._compare_versions(
                system_version, Config.AKASH_VERSION
            )

            return {
                "needs_upgrade": needs_upgrade,
                "current_network_version": current_version,
                "system_version": system_version,
            }

        except Exception as e:
            log.error(f"Error checking network upgrade status: {e}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="PROVIDER_003",
                payload={"error": "Network Upgrade Check Error", "message": str(e)},
            )

    async def upgrade_network(self, ssh_client, task_id: str) -> Dict:
        try:
            # Get the system version in a separate thread

            check_upgrade_status = await self.check_upgrade_status(ssh_client)
            
            needs_upgrade = check_upgrade_status["needs_upgrade"]
            if not needs_upgrade:
                return {"status": "success", "message": "Network is up to date"}
            else:
                # Upgrade network
                # Delete the pod to trigger upgrade
                stdout, stderr = run_ssh_command(
                    ssh_client,
                    "kubectl -n akash-services delete pod akash-node-1-0",
                    True,
                    task_id=task_id,
                )
                if stderr != "":
                    raise Exception(f"Failed to delete pod: {stderr}")

                return {
                    "status": "success",
                    "message": "Network upgrade initiated successfully",
                }
        except Exception as e:
            log.error(f"Error upgrading network: {e}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="PROVIDER_004",
                payload={"error": "Network Upgrade Error", "message": str(e)},
            )
        finally:
            ssh_client.close()
