#!/usr/bin/env python3
"""
CLI tool for Terraform Controller
Provides kubectl-like interface for managing Terraform resources
"""

import click
import requests
import json
import yaml
from tabulate import tabulate

API_BASE_URL = "http://localhost:8000/api/v1"


class TerraformControllerCLI:
    """CLI client for Terraform Controller"""

    def __init__(self, base_url: str = API_BASE_URL):
        self.base_url = base_url

    def _make_request(self, method: str, endpoint: str, **kwargs):
        """Make HTTP request to the API"""
        url = f"{self.base_url}{endpoint}"
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            click.echo(f"Error: {e}", err=True)
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_detail = e.response.json()
                    click.echo(f"Detail: {error_detail}", err=True)
                except (ValueError, json.JSONDecodeError):
                    click.echo(f"Response: {e.response.text}", err=True)
            return None


@click.group()
def cli():
    """Terraform Controller CLI - kubectl-like interface for Terraform resources"""
    pass


@cli.command()
@click.argument("filename", type=click.Path(exists=True))
def apply(filename):
    """Apply a resource from a YAML/JSON file"""
    client = TerraformControllerCLI()

    # Read file
    with open(filename, "r") as f:
        if filename.endswith(".yaml") or filename.endswith(".yml"):
            data = yaml.safe_load(f)
        else:
            data = json.load(f)

    # Create resource
    result = client._make_request("POST", "/resources", json=data)

    if result:
        click.echo("Resource created successfully!")
        click.echo(f"ID: {result['id']}")
        click.echo(f"Name: {result['name']}")
        click.echo(f"Status: {result['status']}")


@cli.command()
@click.option(
    "--output", "-o", type=click.Choice(["table", "json", "wide"]), default="table"
)
def get(output):
    """List all resources"""
    # client = TerraformControllerCLI()

    # For now, this would need the list endpoint to be implemented
    click.echo("Note: List endpoint needs to be implemented in API")


@cli.command()
@click.argument("resource_id", type=int)
@click.option("--output", "-o", type=click.Choice(["json", "yaml"]), default="json")
def describe(resource_id, output):
    """Describe a specific resource"""
    client = TerraformControllerCLI()

    result = client._make_request("GET", f"/resources/{resource_id}")

    if result:
        if output == "yaml":
            click.echo(yaml.dump(result, default_flow_style=False))
        else:
            click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("resource_id", type=int)
@click.argument("filename", type=click.Path(exists=True))
def update(resource_id, filename):
    """Update a resource from a file"""
    client = TerraformControllerCLI()

    # Read file
    with open(filename, "r") as f:
        if filename.endswith(".yaml") or filename.endswith(".yml"):
            data = yaml.safe_load(f)
        else:
            data = json.load(f)

    result = client._make_request("PUT", f"/resources/{resource_id}", json=data)

    if result:
        click.echo("Resource updated successfully!")
        click.echo(f"Generation: {result['generation']}")
        click.echo(f"Status: {result['status']}")


@cli.command()
@click.argument("resource_id", type=int)
@click.confirmation_option(prompt="Are you sure you want to delete this resource?")
def delete(resource_id):
    """Delete a resource (runs terraform destroy)"""
    client = TerraformControllerCLI()

    result = client._make_request("DELETE", f"/resources/{resource_id}")

    if result:
        click.echo("Resource marked for deletion")


@cli.command()
@click.argument("resource_id", type=int)
@click.option("--limit", "-l", default=10, help="Number of history entries to show")
def history(resource_id, limit):
    """Show reconciliation history for a resource"""
    client = TerraformControllerCLI()

    result = client._make_request(
        "GET", f"/resources/{resource_id}/history", params={"limit": limit}
    )

    if result:
        # Format as table
        headers = [
            "ID",
            "Generation",
            "Success",
            "Phase",
            "Created",
            "Updated",
            "Deleted",
            "Time",
        ]
        rows = []
        for entry in result:
            rows.append(
                [
                    entry["id"],
                    entry["generation"],
                    "✓" if entry["success"] else "✗",
                    entry["phase"],
                    entry["resources_created"],
                    entry["resources_updated"],
                    entry["resources_deleted"],
                    entry["reconcile_time"],
                ]
            )

        click.echo(tabulate(rows, headers=headers, tablefmt="grid"))


@cli.command()
@click.argument("resource_id", type=int)
def outputs(resource_id):
    """Show Terraform outputs for a resource"""
    client = TerraformControllerCLI()

    result = client._make_request("GET", f"/resources/{resource_id}/outputs")

    if result and "outputs" in result:
        click.echo(json.dumps(result["outputs"], indent=2))
    else:
        click.echo("No outputs available")


@cli.command()
@click.argument("resource_id", type=int)
@click.option(
    "--output", "-o", type=click.Choice(["json", "summary"]), default="summary"
)
def state(resource_id, output):
    """Show Terraform state for a resource"""
    client = TerraformControllerCLI()

    result = client._make_request("GET", f"/resources/{resource_id}/state")

    if result:
        if output == "json":
            click.echo(json.dumps(result, indent=2))
        else:
            # Show summary
            click.echo(f"Terraform Version: {result.get('terraform_version', 'N/A')}")
            click.echo(f"Serial: {result.get('serial', 'N/A')}")

            if "values" in result and "root_module" in result["values"]:
                resources = result["values"]["root_module"].get("resources", [])
                click.echo(f"\nResources ({len(resources)}):")
                for resource in resources:
                    click.echo(f"  - {resource['address']} ({resource['type']})")


@cli.command()
@click.argument("resource_id", type=int)
def reconcile(resource_id):
    """Manually trigger reconciliation for a resource"""
    client = TerraformControllerCLI()

    result = client._make_request("POST", f"/resources/{resource_id}/reconcile")

    if result:
        click.echo("Reconciliation triggered successfully")


@cli.command()
@click.argument("resource_id", type=int)
@click.option("--follow", "-f", is_flag=True, help="Follow status updates")
@click.option("--interval", "-i", default=5, help="Polling interval in seconds")
def status(resource_id, follow, interval):
    """Show status of a resource"""
    import time

    client = TerraformControllerCLI()

    def show_status():
        result = client._make_request("GET", f"/resources/{resource_id}")
        if result:
            click.clear()
            click.echo(f"Resource: {result['name']}")
            click.echo(f"Status: {result['status']}")
            click.echo(f"Message: {result.get('status_message', 'N/A')}")
            click.echo(f"Generation: {result['generation']}")
            click.echo(f"Observed Generation: {result['observed_generation']}")
            click.echo(f"Last Reconcile: {result.get('last_reconcile_time', 'Never')}")

            if result["generation"] != result["observed_generation"]:
                click.echo("\n⚠️  Resource is out of sync (reconciliation pending)")
            elif result["status"] == "ready":
                click.echo("\n✓ Resource is up to date")

    show_status()

    if follow:
        try:
            while True:
                time.sleep(interval)
                show_status()
        except KeyboardInterrupt:
            click.echo("\nStopped following")


if __name__ == "__main__":
    cli()
