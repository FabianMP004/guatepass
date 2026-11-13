from __future__ import annotations

import argparse
import json
import os

import boto3


OUTPUT_KEYS = (
    "RegistroAvailabilityParameterName",
    "RegistryAvailabilityParameterName",
)


def stack_outputs(stack_name: str, region: str) -> dict[str, str]:
    cloudformation = boto3.client(
        "cloudformation",
        region_name=region,
    )

    response = cloudformation.describe_stacks(
        StackName=stack_name,
    )

    stacks = response.get("Stacks", [])
    if not stacks:
        raise RuntimeError(f"No existe el stack: {stack_name}")

    return {
        item["OutputKey"]: item["OutputValue"]
        for item in stacks[0].get("Outputs", [])
    }


def resolve_parameter(outputs: dict[str, str]) -> str:
    for key in OUTPUT_KEYS:
        value = outputs.get(key)
        if value:
            return value

    available = ", ".join(sorted(outputs)) or "ninguno"
    raise RuntimeError(
        "No se encontró el Output del parámetro de disponibilidad. "
        f"Outputs disponibles: {available}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cambia la disponibilidad simulada del registro externo."
    )
    parser.add_argument("--stack", required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=("on", "off"),
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "us-east-1",
    )
    args = parser.parse_args()

    outputs = stack_outputs(args.stack, args.region)
    parameter_name = resolve_parameter(outputs)

    ssm = boto3.client("ssm", region_name=args.region)
    ssm.put_parameter(
        Name=parameter_name,
        Type="String",
        Value=args.mode,
        Overwrite=True,
    )

    print(
        json.dumps(
            {
                "stack": args.stack,
                "region": args.region,
                "parameter": parameter_name,
                "mode": args.mode,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
