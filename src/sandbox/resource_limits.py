"""Resource limits passed to docker-py's containers.run() call site, not
baked into the image, so they stay tunable without a rebuild. A hung test
suite or a fork-bomb in agent-generated code must not be able to affect the
host or other jobs.
"""

DEFAULT_LIMITS: dict = {
    "mem_limit": "512m",
    "nano_cpus": 1_000_000_000,  # 1 CPU
    "pids_limit": 256,
    "network_mode": "none",
    "read_only": False,  # workspace mount needs write access; see docker_manager's mount config
}
