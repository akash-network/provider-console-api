from packaging import version
import asyncio
import json
from typing import Dict, Tuple
from fastapi import status

from application.config.config import Config
from application.exception.application_error import ApplicationError
from application.utils.ssh_utils import run_ssh_command
from application.utils.logger import log


class UpgradeService:
    def __init__(self):
        self.AKASH_NODE_HELM_CHECK_CMD = "helm list -n akash-services -o json | jq '.[] | select(.name == \"akash-node\")'"
        self.PROVIDER_HELM_CHECK_CMD = "helm list -n akash-services -o json | jq '.[] | select(.name == \"akash-provider\")'"


    def _get_helm_release_versions(self, ssh_client, release_type: str) -> Tuple[str, str]:
        """Get helm release versions for akash node or provider
        
        Args:
            ssh_client: SSH client to run commands
            release_type: Either 'node' or 'provider' to specify which release to check
        """
        cmd = self.AKASH_NODE_HELM_CHECK_CMD if release_type == 'node' else self.PROVIDER_HELM_CHECK_CMD
        component_name = "akash-node" if release_type == 'node' else "provider"
        display_name = "Akash Node" if release_type == 'node' else "Akash Provider"

        stdout, _ = run_ssh_command(ssh_client, cmd, True)
        helm_data = json.loads(stdout)

        if not helm_data:
            raise ApplicationError(
                status_code=status.HTTP_404_NOT_FOUND,
                error_code="PROVIDER_002", 
                payload={
                    "error": f"{display_name} Not Found",
                    "message": f"Could not find {component_name} helm release",
                },
            )

        chart_version = helm_data.get("chart", "").replace(f"{component_name}-", "")
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
        """Check if upgrade is needed for both network and provider
        
        Args:
            ssh_client: SSH client to run commands
        """
        def get_versions():
            try:
                node_app_version, node_chart_version = self._get_helm_release_versions(ssh_client, 'node')
                provider_app_version, provider_chart_version = self._get_helm_release_versions(ssh_client, 'provider')
                return node_app_version, node_chart_version, provider_app_version, provider_chart_version
            finally:
                ssh_client.close()

        try:
            # Get both node and provider versions in a separate thread
            node_app_version, node_chart_version, provider_app_version, provider_chart_version = await asyncio.to_thread(get_versions)

            # Compare node versions
            node_app_needs_upgrade, node_app_current, node_app_desired = self._compare_versions(
                node_app_version, Config.AKASH_VERSION
            )
            node_chart_needs_upgrade, node_chart_current, node_chart_desired = self._compare_versions(
                node_chart_version, Config.AKASH_NODE_HELM_CHART_VERSION
            )
            
            # Compare provider versions
            provider_app_needs_upgrade, provider_app_current, provider_app_desired = self._compare_versions(
                provider_app_version, Config.PROVIDER_SERVICES_VERSION
            )
            provider_chart_needs_upgrade, provider_chart_current, provider_chart_desired = self._compare_versions(
                provider_chart_version, Config.PROVIDER_SERVICES_HELM_CHART_VERSION
            )

            node_needs_upgrade = node_app_needs_upgrade or node_chart_needs_upgrade
            provider_needs_upgrade = provider_app_needs_upgrade or provider_chart_needs_upgrade

            return {
                "node": {
                    "needs_upgrade": node_needs_upgrade,
                    "app_version": {
                        "current": node_app_current,
                        "desired": node_app_desired,
                        "needs_upgrade": node_app_needs_upgrade
                    },
                    "chart_version": {
                        "current": node_chart_current,
                        "desired": node_chart_desired,
                        "needs_upgrade": node_chart_needs_upgrade
                    }
                },
                "provider": {
                    "needs_upgrade": provider_needs_upgrade,
                    "app_version": {
                        "current": provider_app_current,
                        "desired": provider_app_desired,
                        "needs_upgrade": provider_app_needs_upgrade
                    },
                    "chart_version": {
                        "current": provider_chart_current,
                        "desired": provider_chart_desired,
                        "needs_upgrade": provider_chart_needs_upgrade
                    }
                }
            }

        except Exception as e:
            log.error(f"Error checking upgrade status: {e}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="PROVIDER_003",
                payload={"error": "Upgrade Check Error", "message": str(e)},
            )


    async def upgrade_network(self, ssh_client, task_id: str) -> Dict:
        try:
            # Get the system version in a separate thread
            check_upgrade_status = await self.check_upgrade_status(ssh_client)
            node_upgrade_status = check_upgrade_status["node"]

            needs_upgrade = node_upgrade_status["needs_upgrade"]
            app_version = node_upgrade_status["app_version"]["desired"]
            app_needs_upgrade = node_upgrade_status["app_version"]["needs_upgrade"]
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
                if app_needs_upgrade:
                    upgrade_command = f"helm upgrade --install akash-node akash/akash-node -n akash-services --set image.tag={app_version}"
                else:
                    upgrade_command = f"kubectl delete pod -n akash-services -l app=akash-node"
                
                stdout, stderr = run_ssh_command(
                    ssh_client,
                    upgrade_command,
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
                    "message": "Network upgrade completed successfully",
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



    async def upgrade_provider(self, ssh_client, task_id: str) -> Dict:
        try:
            # Get the system version in a separate thread
            check_upgrade_status = await self.check_upgrade_status(ssh_client)
            provider_upgrade_status = check_upgrade_status["provider"]

            needs_upgrade = provider_upgrade_status["needs_upgrade"]
            app_version = provider_upgrade_status["app_version"]["desired"]
            chart_version = provider_upgrade_status["chart_version"]["desired"]
            
            if not needs_upgrade:
                return {"status": "success", "message": "Provider is up to date"}
            
            # Update Helm repositories
            log.info("Updating Helm repositories...")
            stdout, stderr = run_ssh_command(
                ssh_client,
                "helm repo update akash",
                True,
                task_id=task_id,
            )

            log.info(f"Upgrading provider to version {app_version}...")
            stdout, stderr = run_ssh_command(
                ssh_client,
                "helm search repo provider",
                True,
                task_id=task_id,
            )

            if "provider" not in stdout:
                raise ApplicationError(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error_code="PROVIDER_009",
                    payload={"error": "Helm Chart Not Found", "message": "Could not find akash-provider chart in helm repositories"}
                )

            # Backup existing values
            log.info("Backing up helm values...")
            backup_cmd = "cd /root/provider && for i in $(helm list -n akash-services -q | grep -vw akash-node); do helm -n akash-services get values $i > ${i}.pre-v0.6.10.values; done"
            run_ssh_command(ssh_client, backup_cmd, True, task_id=task_id)

            # Upgrade hostname operator
            log.info("Upgrading hostname operator...")
            run_ssh_command(
                ssh_client,
                f"helm -n akash-services upgrade akash-hostname-operator akash/akash-hostname-operator --set image.tag={app_version}",
                True,
                task_id=task_id,
            )

            # Upgrade inventory operator
            log.info("Upgrading inventory operator...")
            run_ssh_command(
                ssh_client,
                f"helm -n akash-services upgrade inventory-operator akash/akash-inventory-operator --set image.tag={app_version}",
                True,
                task_id=task_id,
            )

            # Update price script
            log.info("Updating price script...")
            price_script_cmds = [
                "mv ~/provider/price_script_generic.sh ~/provider/price_script_generic.sh.old",
                f"wget -O ~/provider/price_script_generic.sh {Config.PROVIDER_PRICE_SCRIPT_URL}",
                "chmod +x ~/provider/price_script_generic.sh"
            ]
            for cmd in price_script_cmds:
                run_ssh_command(ssh_client, cmd, True, task_id=task_id)

            # Upgrade provider chart
            log.info("Upgrading provider chart...")
            provider_upgrade_cmd = (
                "helm upgrade akash-provider akash/provider -n akash-services -f ~/provider/provider.yaml "
                "--set bidpricescript=\"$(cat ~/provider/price_script_generic.sh | openssl base64 -A)\""
            )
            run_ssh_command(ssh_client, provider_upgrade_cmd, True, task_id=task_id)

            # Verify pod versions
            log.info("Verifying pod versions...")
            verify_cmd = "kubectl -n akash-services get pods -o custom-columns='NAME:.metadata.name,IMAGE:.spec.containers[*].image' | grep -v akash-node"
            stdout, _ = run_ssh_command(ssh_client, verify_cmd, True, task_id=task_id)

            if app_version not in stdout:
                raise ApplicationError(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error_code="PROVIDER_008",
                    payload={
                        "error": "Provider Upgrade Verification Failed",
                        "message": f"Could not verify provider version {app_version} after upgrade"
                    }
                )

            return {
                "status": "success",
                "message": "Provider upgrade completed successfully",
            }

        except ApplicationError as e:
            log.error(f"Error upgrading provider: {e.payload['message']}")
            raise e
        except Exception as e:
            log.error(f"Error upgrading provider: {e}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="PROVIDER_005",
                payload={"error": "Provider Upgrade Error", "message": str(e)},
            )
        finally:
            ssh_client.close()
                        