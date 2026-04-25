#!/usr/bin/env python3
"""
start_peer_nodes.py - Start actual peer nodes for distributed testing

This script starts 3 real peer nodes that can execute matrix computations.
Run this in one terminal, then run matrix_client.py in another terminal.

Usage:
  Terminal 1: python start_peer_nodes.py
  Terminal 2: python matrix_client.py
"""

import sys
import time
import signal
import logging
import subprocess
from p2p_node import create_local_cluster

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

BASE_PORT = 50051
NUM_NODES = 3


def kill_stale_port_holders():
    """Kill any processes still holding our ports from a previous run."""
    ports = [BASE_PORT + i for i in range(NUM_NODES)]
    pids_to_kill = set()

    for port in ports:
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) != 0:
                        pids_to_kill.add(int(pid))
        except Exception:
            pass

    if pids_to_kill:
        print(f"  [CLEANUP] Killing stale processes on ports: {pids_to_kill}")
        for pid in pids_to_kill:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True, timeout=5
                )
            except Exception:
                pass
        # Give OS time to release the ports
        time.sleep(1)


def main():
    print("\n" + "="*70)
    print("  STARTING P2P DISTRIBUTED MATRIX NETWORK")
    print("="*70)

    # Clean up any leftover processes from previous runs
    print("\nChecking for stale processes on ports...")
    kill_stale_port_holders()

    print(f"\nCreating cluster with {NUM_NODES} peer nodes...")
    for i in range(NUM_NODES):
        print(f"  - node-{i}: localhost:{BASE_PORT + i}")

    # Track started nodes so we can clean up on failure
    nodes = []
    try:
        nodes = create_local_cluster(num_nodes=NUM_NODES)
    except Exception as e:
        print(f"\n[ERROR] Failed to start cluster: {e}")
        # Stop any nodes that did start successfully
        for node in nodes:
            try:
                node.stop()
            except Exception:
                pass
        return 1

    print("\n[OK] All peer nodes are running and connected!")
    print("\nNow, in another terminal, run:")
    print("  $ python matrix_client.py")
    print("\nThe client will connect to these nodes and distribute matrix computation tasks.")
    print("\nPress Ctrl+C to stop all nodes...\n")

    def shutdown(signum=None, frame=None):
        print("\n\nShutting down peer nodes...")
        for node in nodes:
            try:
                node.stop()
            except Exception:
                pass
        print("[OK] All nodes stopped")

    # Register signal handlers for clean shutdown
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        # Keep nodes running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()
        return 0


if __name__ == "__main__":
    sys.exit(main())
