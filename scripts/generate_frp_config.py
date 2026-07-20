from __future__ import annotations

import argparse


def build_frp_client_config(
    *,
    server_addr: str,
    server_port: int = 7000,
    token: str = "",
    domain_suffix: str,
    prefix: str = "spiritkin",
    local_ip: str = "127.0.0.1",
    frontend_port: int = 8787,
    events_port: int = 8765,
    command_port: int = 8788,
    remote_worker_port: int | None = None,
) -> str:
    suffix = domain_suffix.strip().lstrip(".")
    safe_prefix = prefix.strip().strip(".-") or "spiritkin"
    lines = [
        f'serverAddr = "{server_addr}"',
        f"serverPort = {int(server_port)}",
        "",
    ]
    if token.strip():
        lines.extend(["[auth]", f'token = "{token.strip()}"', ""])

    proxies = [
        ("frontend", frontend_port, f"{safe_prefix}.{suffix}"),
        ("events", events_port, f"{safe_prefix}-events.{suffix}"),
        ("command", command_port, f"{safe_prefix}-command.{suffix}"),
    ]
    if remote_worker_port is not None:
        proxies.append(("worker", int(remote_worker_port), f"{safe_prefix}-worker.{suffix}"))

    for name, local_port, domain in proxies:
        lines.extend(
            [
                "[[proxies]]",
                f'name = "spiritkin-{name}"',
                'type = "http"',
                f'localIP = "{local_ip}"',
                f"localPort = {int(local_port)}",
                f'customDomains = ["{domain}"]',
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an frpc.toml template for SpiritKin mobile-data access")
    parser.add_argument("--server-addr", required=True, help="frps server address, e.g. frp.example.com")
    parser.add_argument("--server-port", type=int, default=7000, help="frps bind port, default 7000")
    parser.add_argument("--token", default="", help="frps auth token")
    parser.add_argument("--domain-suffix", required=True, help="domain suffix, e.g. example.com")
    parser.add_argument("--prefix", default="spiritkin", help="subdomain prefix, default spiritkin")
    parser.add_argument("--remote-worker-port", type=int, default=0, help="optional local remote worker port, e.g. 8790")
    args = parser.parse_args()

    print(
        build_frp_client_config(
            server_addr=args.server_addr,
            server_port=args.server_port,
            token=args.token,
            domain_suffix=args.domain_suffix,
            prefix=args.prefix,
            remote_worker_port=args.remote_worker_port or None,
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())