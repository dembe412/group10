#!/usr/bin/env python3
"""
matrix_client.py - Production Matrix Client Node

A production client node that:
1. Accepts user input for matrix dimensions
2. Generates random matrices locally
3. Submits computation to P2P network
4. Tracks progress across distributed peers
5. Collects and verifies results
6. Maintains consistency via gossip protocol
"""

import logging
import time
import numpy as np

# Set strict 0dps formatting for numpy matrices globally
np.set_printoptions(formatter={'float_kind': lambda x: f"{int(round(x))}"}, suppress=True)

import sys
import grpc
from typing import List, Tuple, Dict, Any, Optional
from concurrent import futures
from threading import Thread, RLock, Condition
from collections import defaultdict

from core.p2p_node import P2PNode
from core.assignment_strategy import RendezvousStrategy
from grpc_layer import matrix_pb2
from grpc_layer import matrix_pb2_grpc

# Configure logging strictly to file (remove console noise for clean UI)
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
root_logger = logging.getLogger()

# Clear existing handlers to avoid duplicates
root_logger.handlers = []

# File Handler (Universal Log) - enforce utf-8 to prevent charmap errors
file_handler = logging.FileHandler('p2p_system.log', encoding='utf-8')
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

root_logger.setLevel(logging.INFO)
logger = logging.getLogger(__name__)


class MatrixClient:
    """Production client node for distributed matrix computation."""
    
    def __init__(self, client_id: str = "client", 
                 peer_nodes: List[Tuple[str, str, int]] = None):
        """
        Initialize matrix client.
        
        Args:
            client_id: Unique identifier for this client
            peer_nodes: List of (node_id, host, port) tuples
        """
        self._client_id = client_id
        self._host = "localhost"
        self._port = 0 # Use OS-assigned port to avoid conflicts
        self._node = P2PNode(client_id, self._host, self._port, num_chunks=10)
        self._strategy = RendezvousStrategy() # New assignment strategy
        
        # Peer configuration
        self._peer_nodes = peer_nodes or []
        
        # Computation state
        self._computation_id = None
        self._matrix_a = None
        self._matrix_b = None
        self._expected_result = None
        self._chunk_results: Dict[int, np.ndarray] = {}
        
        # gRPC server
        self._grpc_server = None
        self._running = False
        
        logger.info(f"Initialized MatrixClient: {client_id}")
        self._peer_availability = {}
    
    def check_network_connectivity(self, quiet: bool = False) -> Dict[str, bool]:
        """Check connectivity to all peer nodes and display status."""
        if not quiet:
            print("\n" + "="*60)
            print("NETWORK CONNECTIVITY STATUS")
            print("="*60)
        
        connectivity = {}
        
        # Sync peer list from internal node discovery
        peers = self._node._discovery.get_all_peers()
        for peer in peers:
            node_id, host, port = peer.node_id, peer.host, peer.port
            
            # Try to reach the node
            try:
                channel = grpc.insecure_channel(f"{host}:{port}")
                # Use grpc.channel_ready_future to actually check connectivity
                grpc.channel_ready_future(channel).result(timeout=0.5)
                connectivity[node_id] = True
                if not quiet:
                    print(f"  ✓ {node_id} - {host}:{port} (REACHABLE)")
                channel.close()
            except Exception:
                connectivity[node_id] = False
                if not quiet:
                    print(f"  ✗ {node_id} - {host}:{port} (UNREACHABLE)")
        
        self._peer_availability = connectivity
        if not quiet:
            available = sum(1 for v in connectivity.values() if v)
            total = len(connectivity)
            print(f"\nResult: {available}/{total} nodes available\n")
        
        return connectivity
    
    def _generate_random_matrix(self, rows: int, cols: int, method: str = "random",
                               min_val: float = 0.0, max_val: float = 1.0) -> np.ndarray:
        """Generate a matrix with specified method (random, ones, zeros, or range)."""
        if method == "random":
            return np.random.rand(rows, cols).astype(np.float64)
        elif method == "ones":
            return np.ones((rows, cols), dtype=np.float64)
        elif method == "zeros":
            return np.zeros((rows, cols), dtype=np.float64)
        elif method == "range":
            return (np.random.rand(rows, cols) * (max_val - min_val) + min_val).astype(np.float64)
        elif method == "randint":
            return np.random.randint(int(min_val), int(max_val) + 1, size=(rows, cols)).astype(np.float64)
        else:
            raise ValueError(f"Unknown method: {method}")
    
    def start(self) -> bool:
        """Start client node."""
        try:
            # Register peer nodes
            for node_id, host, port in self._peer_nodes:
                if node_id != self._client_id:
                    self._node.add_peer(node_id, host, port)
                    logger.debug(f"Added peer: {node_id}@{host}:{port}")
            
            # Start P2P node
            self._node.start()
            
            # Remove client from hash ring so it does not perform computation
            try:
                self._node._hash_ring.remove_node(self._client_id)
            except Exception as e:
                logger.debug(f"Could not remove client from hash ring: {e}")
                pass
                
            self._running = True
            time.sleep(1)  # Wait for discovery
            
            stats = self._node.get_stats()
            logger.info(f"Client started - {stats['peers']} peers connected")
            
            return True
        except Exception as e:
            logger.error(f"Failed to start client: {e}")
            return False
    
    def input_matrices(self) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Interactively get matrix dimensions and generate with user choice of method.
        
        Returns:
            (matrix_a, matrix_b) or (None, None) on error
        """
        print("\n" + "="*60)
        print("MATRIX INPUT")
        print("="*60)
        
        try:
            print("\nEnter matrix dimensions:")
            rows_a = int(input("  Matrix A rows: "))
            cols_a = int(input("  Matrix A columns (= Matrix B rows): "))
            cols_b = int(input("  Matrix B columns: "))
            
            if rows_a <= 0 or cols_a <= 0 or cols_b <= 0:
                raise ValueError("All dimensions must be positive")
            
            # Generation method menu
            print(f"\nMatrix generation method:")
            print(f"  1. Random floats [0.0, 1.0] (default)")
            print(f"  2. Random integers [0, 10]")
            print(f"  3. Random integers [10, 50]")
            print(f"  4. Random integers [50, 100]")
            print(f"  5. Random integers [100, 1000] (Stress Test)")
            print(f"  6. Custom float range")
            print(f"  7. Custom integer range")
            print(f"  8. Ones (all values = 1.0)")
            print(f"  9. Zeros (all values = 0.0)")
            print(f"  10. Manual Input")
            method_choice = input("Select method (1-10, default 1): ").strip() or "1"
            
            if method_choice == "10":
                print(f"\nEnter {rows_a} rows for Matrix A, each row as {cols_a} numbers separated by spaces")
                A_rows = []
                for i in range(rows_a):
                    while True:
                        line = input(f"A row {i}: ").strip()
                        parts = line.split()
                        if len(parts) != cols_a:
                            print(f"Expected {cols_a} numbers, got {len(parts)}")
                            continue
                        A_rows.append([float(x) for x in parts])
                        break

                print(f"\nEnter {cols_a} rows for Matrix B, each row as {cols_b} numbers separated by spaces")
                B_rows = []
                for i in range(cols_a):
                    while True:
                        line = input(f"B row {i}: ").strip()
                        parts = line.split()
                        if len(parts) != cols_b:
                            print(f"Expected {cols_b} numbers, got {len(parts)}")
                            continue
                        B_rows.append([float(x) for x in parts])
                        break

                self._matrix_a = np.array(A_rows)
                self._matrix_b = np.array(B_rows)
                method = "manual"
            else:
                method_map = {
                    "1": ("random", 0.0, 1.0),
                    "2": ("randint", 0.0, 10.0),
                    "3": ("randint", 10.0, 50.0),
                    "4": ("randint", 50.0, 100.0),
                    "5": ("randint", 100.0, 1000.0),
                    "6": ("range", 0.0, 1.0),
                    "7": ("randint", 0.0, 10.0),
                    "8": ("ones", 0.0, 0.0),
                    "9": ("zeros", 0.0, 0.0),
                }
                
                if method_choice not in method_map:
                    raise ValueError(f"Invalid choice: {method_choice}")
                
                method, min_val, max_val = method_map[method_choice]
                
                # For range method, get min and max values
                if method in ("range", "randint") and method_choice in ("6", "7"):
                    min_val = float(input("  Enter minimum value: "))
                    max_val = float(input("  Enter maximum value: "))
                    if min_val >= max_val:
                        raise ValueError("Minimum must be less than maximum")
                
                print(f"\nGenerating matrices using method '{method}': {rows_a}x{cols_a} × {cols_a}x{cols_b}")
                
                # Generate matrices
                self._matrix_a = self._generate_random_matrix(rows_a, cols_a, method, min_val, max_val)
                self._matrix_b = self._generate_random_matrix(cols_a, cols_b, method, min_val, max_val)
            
            # Show preview
            print(f"\n✓ Generated matrices:")
            print(f"    Matrix A: {self._matrix_a.shape}")
            print(self._matrix_a)
            print(f"      Min: {self._matrix_a.min():.6f}, Max: {self._matrix_a.max():.6f}, Mean: {self._matrix_a.mean():.6f}")
            print(f"    Matrix B: {self._matrix_b.shape}")
            print(self._matrix_b)
            print(f"      Min: {self._matrix_b.min():.6f}, Max: {self._matrix_b.max():.6f}, Mean: {self._matrix_b.mean():.6f}")
            
            # Ask for confirmation
            confirm = input("\nUse these matrices? (y/n, default y): ").strip().lower() or "y"
            if confirm != "y":
                print("Matrices rejected. Try again.\n")
                return None, None
            
            # Compute expected result locally
            self._expected_result = np.matmul(self._matrix_a, self._matrix_b)
            
            logger.info(f"Generated matrices: A{self._matrix_a.shape} @ B{self._matrix_b.shape} → {self._expected_result.shape}")
            print(f"    Expected result: {self._expected_result.shape}")
            
            return self._matrix_a, self._matrix_b
            
        except ValueError as e:
            print(f"✗ Invalid input: {e}")
            logger.error(f"Matrix input error: {e}")
            return None, None
    
    def submit_computation(self, quiet: bool = False) -> str:
        """
        Submit matrix multiplication to P2P network.
        
        Returns:
            computation_id
        """
        if self._matrix_a is None or self._matrix_b is None:
            raise ValueError("Must input matrices first")
        
        self._computation_id = f"comp:{self._client_id}:{int(time.time())}"
        
        if not quiet:
            print(f"\n{'='*60}")
            print("SUBMITTING COMPUTATION")
            print(f"{'='*60}")
            print(f"Computation ID: {self._computation_id}")
            print(f"Matrix A: {self._matrix_a.shape}")
            print(f"Matrix B: {self._matrix_b.shape}")
        
        logger.info(f"Submitting computation {self._computation_id}")
        
        # Assign chunks to peer nodes via consistent hashing
        num_chunks = self._node._num_chunks
        rows_per_chunk = max(1, self._matrix_a.shape[0] // num_chunks)
        chunks_assigned = 0
        
        for chunk_id in range(num_chunks):
            start_row = chunk_id * rows_per_chunk
            end_row = start_row + rows_per_chunk
            
            if chunk_id == num_chunks - 1:
                end_row = self._matrix_a.shape[0]
            
            if start_row >= self._matrix_a.shape[0]:
                break
            
            # Assign chunk via consistent hashing
            success = self._node.assign_chunk(
                chunk_id,
                self._matrix_a[start_row:end_row].flatten().tolist()
            )
            
            if success:
                chunks_assigned += 1
                logger.debug(f"Assigned chunk {chunk_id} (rows {start_row}:{end_row})")
        
        if not quiet:
            print(f"\n✓ {chunks_assigned} chunks assigned to peer nodes")
        
        # Check network status
        if not self._peer_availability:
            self.check_network_connectivity(quiet=quiet)
        
        # Get live nodes and their loads for the strategy
        healthy_nodes = self._node._health.get_healthy_peers()
        if not healthy_nodes:
            # Fallback to all discovery nodes if health monitor is empty
            healthy_nodes = [p.node_id for p in self._node._discovery.get_all_peers()]
            
        node_loads = {nid: metrics.estimated_load for nid, metrics in self._node._health._metrics.items()}

        # Show which nodes received chunks
        if not quiet:
            print(f"\nChunk Distribution (Rendezvous Hashing):")
            chunk_nodes = {}
            for chunk_id in range(chunks_assigned):
                routing_key = f"{self._computation_id}:chunk:{chunk_id}"
                target = self._strategy.get_node(routing_key, healthy_nodes, node_loads)
                if target not in chunk_nodes:
                    chunk_nodes[target] = []
                chunk_nodes[target].append(chunk_id)
            
            for node_id, chunks in sorted(chunk_nodes.items()):
                status = "LOCAL" if node_id == self._client_id else ("AVAILABLE" if self._peer_availability.get(node_id, False) else "UNAVAILABLE")
                print(f"  {node_id} [{status}]: chunks {chunks}")
            
            print(f"\nWaiting for peer computations...\n")
        
        return self._computation_id
    
    def collect_results(self, quiet: bool = False) -> bool:
        """
        Execute computation by making real gRPC calls to the peer nodes.
        
        Returns:
            True if all results collected successfully
        """
        num_chunks = min(self._node._num_chunks, self._matrix_a.shape[0])
        rows_per_chunk = max(1, self._matrix_a.shape[0] // num_chunks)
        
        if not quiet:
            print(f"Executing {num_chunks} chunk computations on peer network...")
        self._node_attribution = {}
        
        for chunk_id in range(num_chunks):
            start_row = chunk_id * rows_per_chunk
            end_row = start_row + rows_per_chunk
            
            if chunk_id == num_chunks - 1:
                end_row = self._matrix_a.shape[0]
            
            if start_row >= self._matrix_a.shape[0]:
                break
            
            # Get live nodes and their loads for the strategy
            healthy_nodes = self._node._health.get_healthy_peers()
            if not healthy_nodes:
                healthy_nodes = [p.node_id for p in self._node._discovery.get_all_peers()]
            
            node_loads = {nid: metrics.estimated_load for nid, metrics in self._node._health._metrics.items()}
            
            # Identify target node using Rendezvous Hashing + Load Multiplier
            routing_key = f"{self._computation_id}:chunk:{chunk_id}"
            target_node_id = self._strategy.get_node(routing_key, healthy_nodes, node_loads)
            
            if target_node_id == self._node._node_id:
                host, port = "localhost", self._node._port
            else:
                peer_info = self._node._discovery.get_peer(target_node_id)
                if not peer_info:
                    logger.error(f"Cannot find peer address for {target_node_id}")
                    if not quiet:
                        print(f"  ✗ Chunk {chunk_id}: Peer {target_node_id} is unknown/offline")
                    return False
                host, port = peer_info.host, peer_info.port
                
            chunk_a = self._matrix_a[start_row:end_row]
            
            try:
                # Real gRPC call
                channel = grpc.insecure_channel(f"{host}:{port}")
                stub = matrix_pb2_grpc.MatrixServiceStub(channel)
                
                req = matrix_pb2.MatrixRequest(
                    client_id=self._client_id,
                    computation_id=self._computation_id,
                    start_row=start_row,
                    end_row=end_row,
                    matrixA=chunk_a.flatten().tolist(),
                    matrixB=self._matrix_b.flatten().tolist(),
                    rowsA=chunk_a.shape[0],
                    colsA=chunk_a.shape[1],
                    colsB=self._matrix_b.shape[1]
                )
                
                if not quiet:
                    print(f"  → Sending chunk {chunk_id} (rows {start_row}:{end_row}) to {target_node_id}...")
                reply = stub.ComputeRows(req, timeout=30)
                
                result_matrix = np.array(reply.result).reshape(reply.rows, reply.cols)
                self._chunk_results[chunk_id] = result_matrix
                self._node_attribution[chunk_id] = target_node_id
                
                self._node.complete_chunk(chunk_id, reply.result)
                if not quiet:
                    print(f"  ✓ Received chunk {chunk_id} result from {target_node_id} in {reply.computation_time_ms}ms")
                
            except grpc.RpcError as e:
                logger.error(f"RPC failed for chunk {chunk_id} to {target_node_id}: {e.details()}")
                if not quiet:
                    print(f"  ✗ RPC failed to {target_node_id}: {e.details()}")
                return False
        
        if not quiet:
            print(f"\n✓ All {num_chunks} chunks computed successfully\n")
        return len(self._chunk_results) == num_chunks
    
    def verify_results(self, quiet: bool = False) -> bool:
        """
        Verify computed results against expected values.
        
        Returns:
            True if correct, False otherwise
        """
        if not self._chunk_results:
            logger.warning("No results to verify")
            return False
        
        if self._expected_result is None:
            logger.warning("No expected result")
            return False
        
        if not quiet:
            print(f"{'='*60}")
            print("VERIFYING RESULTS")
            print(f"{'='*60}")
        
        logger.info("Verifying computation results")
        
        # Reconstruct full result from chunks
        computed_chunks = []
        num_chunks = len(self._chunk_results)
        
        for chunk_id in range(num_chunks):
            if chunk_id not in self._chunk_results:
                logger.error(f"Missing chunk {chunk_id}")
                return False
            computed_chunks.append(self._chunk_results[chunk_id])
        
        # Concatenate all chunks
        computed_result = np.vstack(computed_chunks)
        self._computed_result = computed_result
        
        # Check shape
        if computed_result.shape != self._expected_result.shape:
            if not quiet:
                print(f"✗ Shape mismatch: {computed_result.shape} vs {self._expected_result.shape}")
            return False
        
        if not quiet:
            print(f"✓ Result shape: {computed_result.shape}")
        
        # Check numerical accuracy
        max_error = np.max(np.abs(computed_result - self._expected_result))
        mean_error = np.mean(np.abs(computed_result - self._expected_result))
        
        if not quiet:
            print(f"  Max error: {max_error:.2e}")
            print(f"  Mean error: {mean_error:.2e}")
        
        tolerance = 1e-10
        if max_error < tolerance:
            if not quiet:
                print(f"\n✓ VERIFICATION PASSED - Results are correct!")
            logger.info("Verification passed")
            return True
        else:
            if not quiet:
                print(f"\n✗ VERIFICATION FAILED - Error exceeds tolerance ({tolerance:.2e})")
            logger.warning(f"Verification failed - error {max_error:.2e}")
            return False
    
    def print_summary(self) -> None:
        """Print final computation summary."""
        stats = self._node.get_stats()
        
        print(f"\n{'='*60}")
        print("COMPUTATION SUMMARY")
        print(f"{'='*60}")
        print(f"Client: {self._client_id}")
        print(f"Computation: {self._computation_id}")
        print(f"\nNetwork:")
        print(f"  Peers Connected: {stats['peers']}")
        print(f"  Healthy Peers: {stats['healthy_peers']}")
        print(f"\nMatrices:")
        print(f"  Matrix A: {self._matrix_a.shape if self._matrix_a is not None else 'N/A'}")
        if self._matrix_a is not None:
            print(self._matrix_a)
        print(f"  Matrix B: {self._matrix_b.shape if self._matrix_b is not None else 'N/A'}")
        if self._matrix_b is not None:
            print(self._matrix_b)
        print(f"  Result: {self._expected_result.shape if self._expected_result is not None else 'N/A'}")
        if hasattr(self, '_computed_result') and self._computed_result is not None:
            print(self._computed_result)
        elif self._expected_result is not None:
            print(self._expected_result)
        print(f"\nChunks:")
        print(f"  Completed: {len(self._chunk_results)}")
        
        print(f"\nNode Attribution:")
        if hasattr(self, '_node_attribution') and self._node_attribution:
            num_chunks = min(self._node._num_chunks, self._matrix_a.shape[0])
            rows_per_chunk = max(1, self._matrix_a.shape[0] // num_chunks)
            for chunk_id, node_id in sorted(self._node_attribution.items()):
                start_row = chunk_id * rows_per_chunk
                end_row = start_row + rows_per_chunk
                if chunk_id == num_chunks - 1:
                    end_row = self._matrix_a.shape[0]
                print(f"  Chunk {chunk_id} (Rows {start_row}-{end_row-1}, Cols 0-{self._matrix_b.shape[1]-1}) → Processed by: {node_id}")
        else:
            print(f"  No attribution data available.")
        
        print(f"{'='*60}\n")
    
    def stop(self) -> None:
        """Stop client node."""
        self._running = False
        self._node.stop()
        logger.info(f"Stopped client {self._client_id}")


import argparse
    
def main():
    """Main client execution loop."""
    parser = argparse.ArgumentParser(description="P2P Matrix Client")
    parser.add_argument("--client-id", default="client", help="Client ID")
    parser.add_argument("--seeds", help="Comma-separated list of seed nodes to connect to (format: node_id@host:port)")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("  P2P DISTRIBUTED MATRIX MULTIPLICATION CLIENT")
    print("="*60)
    
    client_id = args.client_id
    
    client = MatrixClient(client_id, peer_nodes=[])
    
    if args.seeds:
        seed_list = args.seeds.split(',')
        print("\nConnecting to seed nodes:")
        for seed in seed_list:
            if '@' in seed and ':' in seed:
                try:
                    seed_id, addr = seed.split('@')
                    seed_host, seed_port = addr.split(':')
                    client._node.add_peer(seed_id, seed_host, int(seed_port))
                    print(f"  ✓ Added seed: {seed_id} at {seed_host}:{seed_port}")
                except Exception as e:
                    print(f"  ✗ Failed to parse seed '{seed}': {e}")
            else:
                print(f"  ✗ Invalid seed format '{seed}'. Expected node_id@host:port")
    else:
        # Default local testing setup
        client._node.add_peer('node-0', 'localhost', 50051)
        client._node.add_peer('node-1', 'localhost', 50052)
        client._node.add_peer('node-2', 'localhost', 50053)
        print("\nUsing default local seed nodes (node-0, node-1, node-2).")
    
    print(f"\nStarting {client_id}...")
    if not client.start():
        print("✗ Failed to start client")
        return 1
    
    print(f"✓ Client started")
    
    try:
        while True:
            print("\n" + "-"*60)
            print("MENU")
            print("-"*60)
            print("1. Input matrices and compute")
            print("2. Show network status")
            print("3. Exit")
            print("-"*60)
            
            choice = input("Select option (1-3): ").strip()
            
            if choice == "1":
                # Get matrices
                matrix_a, matrix_b = client.input_matrices()
                
                if matrix_a is None:
                    continue
                
                # Submit computation
                comp_id = client.submit_computation()
                
                # Collect results
                success = client.collect_results()
                
                # Verify
                verified = client.verify_results()
                
                # Summary
                client.print_summary()
                
                if verified:
                    print("✓ Computation successful!")
                else:
                    print("✗ Computation verification failed")
                
            elif choice == "2":
                client.check_network_connectivity()
                stats = client._node.get_stats()
                print(f"\nLocal Node Status:")
                print(f"  Node ID: {stats['node_id']}")
                print(f"  Local Port: {client._port}")
                print(f"  Registered Peers: {stats['peers']}")
                print(f"  Healthy Peers: {stats['healthy_peers']}")
                print(f"\nTo test with real peer nodes, start them in another terminal:")
                print(f"  python -c 'from p2p_node import create_local_cluster; nodes = create_local_cluster(3); input()'")
                
            elif choice == "3":
                print("\nExiting...")
                break
            
            else:
                print("✗ Invalid option")
    
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    
    finally:
        client.stop()
    
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())

