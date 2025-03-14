from packaging import version
import asyncio
import json
from typing import Dict, Tuple
from fastapi import status

from application.config.config import Config
from application.exception.application_error import ApplicationError
from application.utils.ssh_utils import run_ssh_command
from application.utils.logger import log


class VersionService:
    def __init__(self):
        self.HELM_CHECK_CMD = "helm list -n akash-services -o json | jq '.[] | select(.name == \"akash-node\")'"

    def _get_akash_node_helm_release_versions(self, ssh_client) -> Tuple[str, str]:
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

        chart_version = helm_data.get("chart", "").replace("akash-node-", "")
        app_version = helm_data.get("app_version")
        return app_version, chart_version

    def _compare_versions(
        self, current_version: str, desired_version: str
    ) -> Tuple[bool, str, str]:
        """Compare system and current versions"""
        current_v = current_version.lstrip("v")
        desired_v = desired_version.lstrip("v")

        needs_upgrade = version.parse(current_v) < version.parse(desired_v)

        return needs_upgrade, current_v, desired_v

    async def check_upgrade_status(self, ssh_client) -> Dict:
        """Check if network upgrade is needed"""

        def get_version():
            try:
                app_version, chart_version = self._get_akash_node_helm_release_versions(ssh_client)
                return app_version, chart_version
            finally:
                ssh_client.close()

        try:
            # Get the system version in a separate thread
            app_version, chart_version = await asyncio.to_thread(get_version)

            # Compare app and chart versions
            app_needs_upgrade, app_current_version, app_desired_version = self._compare_versions(
                app_version, Config.AKASH_VERSION
            )
            chart_needs_upgrade, chart_current_version, chart_desired_version = self._compare_versions(
                chart_version, Config.AKASH_NODE_HELM_CHART_VERSION
            )
            
            needs_upgrade = app_needs_upgrade or chart_needs_upgrade

            return {
                "needs_upgrade": True,
                "app_version": {"current": app_current_version, "desired": app_desired_version},
                "chart_version": {"current": chart_current_version, "desired": chart_desired_version},
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
            app_version = check_upgrade_status["app_version"]["desired"]
            chart_version = check_upgrade_status["chart_version"]["desired"]
            if not needs_upgrade:
                return {"status": "success", "message": "Network is up to date"}
            else:
                # Upgrade network
                # Delete the pod to trigger upgrade
                # Update Helm repositories
                log.info("Updating Helm repositories...")
                stdout, stderr = run_ssh_command(
                    ssh_client,
                    "helm repo update akash",
                    True,
                    task_id=task_id,
                )
                # Update Helm repositories
                log.info("Verifying akash-node chart availability...")
                stdout, stderr = run_ssh_command(
                    ssh_client,
                    "helm search repo akash-node",
                    True,
                    task_id=task_id,
                )

                # Check if akash-node chart exists in stdout
                if "akash-node" not in stdout:
                    raise ApplicationError(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        error_code="PROVIDER_006",
                        payload={
                            "error": "Helm Chart Not Found",
                            "message": "Could not find akash-node chart in helm repositories"
                        }
                    )

                # Upgrade akash-node deployment
                log.info(f"Upgrading akash-node to version {app_version}...")
                stdout, stderr = run_ssh_command(
                    ssh_client,
                    f"kubectl delete pod -n akash-services -l app=akash-node",
                    True,
                    task_id=task_id,
                )

                # Verify the upgrade was successful by checking if the release exists
                verify_stdout, _ = run_ssh_command(
                    ssh_client,
                    "helm list -n akash-services | grep akash-node",
                    True,
                    task_id=task_id,
                )
                
                if "akash-node" not in verify_stdout:
                    raise ApplicationError(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        error_code="PROVIDER_007",
                        payload={
                            "error": "Helm Upgrade Failed",
                            "message": "Could not verify akash-node helm release after upgrade"
                        }
                    )

                return {
                    "status": "success",
                    "message": "Network upgrade initiated successfully",
                }
        except ApplicationError as e:
            log.error(f"Error upgrading network: {e.payload["message"]}")
            raise e
        except Exception as e:
            log.error(f"Error upgrading network: {e}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="PROVIDER_004",
                payload={"error": "Network Upgrade Error", "message": str(e)},
            )
        finally:
            ssh_client.close()
