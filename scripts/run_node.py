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
import socket
import uuid
from core.p2p_node import P2PNode

class MockArgs:
    def __init__(self, node_id, host, port, seeds):
        self.node_id = node_id
        self.host = host
        self.port = port
        self.seeds = seeds

def interactive_menu():
    print("\n" + "="*60)
    print("  WELCOME TO THE DISTRIBUTED MATRIX NETWORK")
    print("="*60)
    
    # Automatically generate a node ID based on computer name
    hostname = socket.gethostname()
    random_id = str(uuid.uuid4())[:4]
    node_id = f"{hostname}-{random_id}"
    host = "0.0.0.0"
    
    print("1. Start a New Network (I am the first PC)")
    print("2. Join an Existing Network")
    
    choice = input("\nChoose an option (1 or 2): ").strip()
    
    if choice == '1':
        port_input = input("Enter local port to host on [default: 50051]: ").strip()
        port = int(port_input) if port_input else 50051
        seeds = None
    elif choice == '2':
        seed_ip = input("Enter the IP Address of the main seed node: ").strip()
        seed_port_input = input("Enter the port of the main seed node [default: 50051]: ").strip()
        seed_port = int(seed_port_input) if seed_port_input else 50051
        
        port_input = input("Enter local port to host on [default: 50052]: ").strip()
        port = int(port_input) if port_input else 50052
        
        # Format the seed string. We use a generic 'bootstrap' ID, which the gossip protocol will correct.
        seeds = f"bootstrap@{seed_ip}:{seed_port}"
    else:
        print("Invalid choice. Exiting.")
        sys.exit(1)
        
    return MockArgs(node_id, host, port, seeds)

def main():
    if len(sys.argv) == 1:
        args = interactive_menu()
    else:
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
