from fastapi import APIRouter, status, HTTPException, Depends
from application.utils.dependency import verify_token
from application.service.provider_status_service import (
    check_on_chain_provider_status,
    check_provider_online_status,
)
from application.utils.logger import log
from application.exception.application_error import ApplicationError

router = APIRouter()


@router.get("/provider/status/onchain")
async def provider_onchain_status_get(
    chainid: str, wallet_address: str = Depends(verify_token)
):
    try:
        provider_details = await check_on_chain_provider_status(chainid, wallet_address)
        return {"provider": False if provider_details is False else provider_details}
    except ApplicationError as ae:
        raise HTTPException(
            status_code=ae.status_code,
            detail={
                "error_code": ae.error_code,
                "error": ae.payload["error"],
                "message": ae.payload["message"],
            },
        )
    except Exception as e:
        log.error(
            f"Unexpected error in provider_onchain_status_get: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "PROVIDER_004",
                "error": "Unexpected Error",
                "message": f"An unexpected error occurred: {str(e)}",
            },
        )


@router.get("/provider/status/online")
async def provider_online_status_get(
    chainid: str, wallet_address: str = Depends(verify_token)
):
    try:
        provider_online_status = await check_provider_online_status(chainid, wallet_address)
        return {"online": False if provider_online_status is False else True}
    except ApplicationError as ae:
        raise HTTPException(
            status_code=ae.status_code,
            detail={
                "error_code": ae.error_code,
                "error": ae.payload["error"],
                "message": ae.payload["message"],
            },
        )
    except Exception as e:
        log.error(f"Unexpected error in provider_online_status_get: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "PROVIDER_005",
                "error": "Unexpected Error",
                "message": f"An unexpected error occurred: {str(e)}",
            },
        )


@router.get("/network/upgrade-status")
async def check_network_upgrade(
    machine_input: ControlMachineInput,
    wallet_address: str = Depends(verify_token)
) -> Dict:
    """Check if network upgrade is needed by comparing current and deployed versions"""
    
    def get_deployed_version():
        ssh_client = get_ssh_client(machine_input)
        try:
            command = "helm list -n akash-services -o json | jq '.[] | select(.name == \"akash-node\")'"
            stdout, _ = run_ssh_command(ssh_client, command, True)
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
        finally:
            ssh_client.close()

    try:
        # Get the deployed version in a separate thread
        deployed_version = await asyncio.to_thread(get_deployed_version)
        
        # Compare versions
        current_version = Config.AKASH_VERSION.lstrip('v')  # Remove 'v' prefix if present
        deployed_version = deployed_version.lstrip('v')
        
        needs_upgrade = version.parse(deployed_version) < version.parse(current_version)
        
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
