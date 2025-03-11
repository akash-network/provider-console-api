from packaging import version
import asyncio
import json
from typing import Dict, Tuple
from fastapi import status

from config.config import Config
from exception.application_error import ApplicationError
from utils.ssh_utils import get_ssh_client, run_ssh_command
from utils.logger import log
from model.machine_input import ControlMachineInput

class VersionService:
    def __init__(self):
        self.HELM_CHECK_CMD = "helm list -n akash-services -o json | jq '.[] | select(.name == \"akash-node\")'"

    def _get_deployed_version(self, ssh_client) -> str:
        """Get deployed version from helm release"""
        stdout, _ = run_ssh_command(ssh_client, self.HELM_CHECK_CMD, True)
        helm_data = json.loads(stdout)
        
        if not helm_data:
            raise ApplicationError(
                status_code=status.HTTP_404_NOT_FOUND,
                error_code="PROVIDER_002",
                payload={
                    "error": "Akash Node Not Found",
                    "message": "Could not find akash-node helm release"
                }
            )
        
        return helm_data.get("app_version")

    def _compare_versions(self, deployed_version: str, current_version: str) -> Tuple[bool, str, str]:
        """Compare deployed and current versions"""
        deployed_v = deployed_version.lstrip('v')
        current_v = current_version.lstrip('v')
        
        needs_upgrade = version.parse(deployed_v) < version.parse(current_v)
        
        return needs_upgrade, current_v, deployed_v

    async def check_upgrade_status(self, machine_input: ControlMachineInput) -> Dict:
        """Check if network upgrade is needed"""
        def get_version():
            ssh_client = get_ssh_client(machine_input)
            try:
                return self._get_deployed_version(ssh_client)
            finally:
                ssh_client.close()

        try:
            # Get the deployed version in a separate thread
            deployed_version = await asyncio.to_thread(get_version)
            
            # Compare versions
            needs_upgrade, current_version, deployed_version = self._compare_versions(
                deployed_version,
                Config.AKASH_VERSION
            )
            
            return {
                "needs_upgrade": needs_upgrade,
                "current_version": current_version,
                "deployed_version": deployed_version
            }
            
        except Exception as e:
            log.error(f"Error checking network upgrade status: {e}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="PROVIDER_003",
                payload={
                    "error": "Network Upgrade Check Error",
                    "message": str(e)
                }
            )
