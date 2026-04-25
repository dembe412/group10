#!/usr/bin/env python3
"""
run_node.py - Standalone Peer Node Runner

Runs a single P2P node on a specified port, optionally connecting to seed nodes.
Designed to be easily executed across multiple different PCs in a network.
"""

import argparse
import sys
import logging
import time
from p2p_node import P2PNode

def main():
    parser = argparse.ArgumentParser(description="Start a P2P Matrix Computation Node")
    parser.add_argument("--node-id", required=True, help="Unique identifier for this node (e.g., node-1)")
    parser.add_argument("--host", default="localhost", help="Host address to bind to (use 0.0.0.0 or your LAN IP for multi-PC)")
    parser.add_argument("--port", type=int, default=50051, help="Port to listen on")
    parser.add_argument("--seeds", help="Comma-separated list of seed nodes to connect to (format: node_id@host:port)")
    
    args = parser.parse_args()
    
    print("\n" + "="*60)
    print(f"  STARTING P2P NODE: {args.node_id}")
    print("="*60)
    
    node = P2PNode(
        node_id=args.node_id,
        host=args.host,
        port=args.port,
        num_chunks=10  # Standard chunks
    )
    
    if args.seeds:
        seed_list = args.seeds.split(',')
        print("\nConnecting to seed nodes:")
        for seed in seed_list:
            if '@' in seed and ':' in seed:
                try:
                    seed_id, addr = seed.split('@')
                    seed_host, seed_port = addr.split(':')
                    node.add_peer(seed_id, seed_host, int(seed_port))
                    print(f"  ✓ Added seed: {seed_id} at {seed_host}:{seed_port}")
                except Exception as e:
                    print(f"  ✗ Failed to parse seed '{seed}': {e}")
            else:
                print(f"  ✗ Invalid seed format '{seed}'. Expected node_id@host:port")
    
    print(f"\nStarting node gRPC server on 0.0.0.0:{args.port}...")
    node.start()
    
    print(f"✓ Node {args.node_id} is running and listening for connections.")
    print("\nPress Ctrl+C to shut down gracefully.\n")
    
    try:
        while True:
            time.sleep(10)
            stats = node.get_stats()
            peers = stats.get('peers', 0)
            healthy = stats.get('healthy_peers', 0)
            print(f"[{time.strftime('%H:%M:%S')}] Status: {peers} total peers known, {healthy} healthy")
    except KeyboardInterrupt:
        print("\n\nShutting down node...")
        node.stop()
        print("✓ Node stopped cleanly.")
        sys.exit(0)

if __name__ == "__main__":
    main()
