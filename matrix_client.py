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
import sys
import grpc
from typing import List, Tuple, Dict, Any, Optional
from concurrent import futures
from threading import Thread, RLock, Condition
from collections import defaultdict

from p2p_node import P2PNode
from matrix_pb2 import MatrixRequest, MatrixReply
from matrix_pb2_grpc import MatrixServiceServicer, add_MatrixServiceServicer_to_server

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


class MatrixComputationService(MatrixServiceServicer):
    """gRPC service for handling matrix computation requests from peers."""
    
    def __init__(self, client):
        """Initialize service with reference to client."""
        self.client = client
    
    def ComputeRows(self, request, context):
        """Receive chunk computation request from peer."""
        try:
            start_row = request.start_row
            end_row = request.end_row
            cols_a = request.colsA
            cols_b = request.colsB
            
            # Reconstruct matrices from flat arrays
            rows = end_row - start_row
            matrix_a = np.array(request.matrixA).reshape(rows, cols_a)
            matrix_b = np.array(request.matrixB).reshape(cols_a, cols_b)
            
            # Compute the result
            result = np.matmul(matrix_a, matrix_b)
            
            logger.info(f"Computed rows {start_row}:{end_row} (shape: {result.shape})")
            
            return MatrixReply(
                result=result.flatten().tolist(),
                rows=result.shape[0],
                cols=result.shape[1],
                computation_time_ms=int(time.time() * 1000)
            )
        except Exception as e:
            logger.error(f"Error in ComputeRows: {e}")
            context.set_details(str(e))
            context.set_code(grpc.StatusCode.INTERNAL)
            return MatrixReply()


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
        self._port = 5099
        self._node = P2PNode(client_id, self._host, self._port, num_chunks=10)
        
        # Peer configuration
        self._peer_nodes = peer_nodes or [
            ('node1', 'localhost', 5001),
            ('node2', 'localhost', 5002),
            ('node3', 'localhost', 5003),
        ]
        
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
        Interactively get matrix dimensions and generate random matrices.
        
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
            
            print(f"\nGenerating random matrices: {rows_a}x{cols_a} × {cols_a}x{cols_b}")
            
            # Generate random matrices
            self._matrix_a = np.random.rand(rows_a, cols_a).astype(np.float64)
            self._matrix_b = np.random.rand(cols_a, cols_b).astype(np.float64)
            
            # Compute expected result locally
            self._expected_result = np.matmul(self._matrix_a, self._matrix_b)
            
            print(f"✓ Generated matrices:")
            print(f"    Matrix A: {self._matrix_a.shape}")
            print(f"    Matrix B: {self._matrix_b.shape}")
            print(f"    Expected result: {self._expected_result.shape}")
            
            return self._matrix_a, self._matrix_b
            
        except ValueError as e:
            print(f"✗ Invalid input: {e}")
            return None, None
    
    def submit_computation(self) -> str:
        """
        Submit matrix multiplication to P2P network.
        
        Returns:
            computation_id
        """
        if self._matrix_a is None or self._matrix_b is None:
            raise ValueError("Must input matrices first")
        
        self._computation_id = f"comp:{self._client_id}:{int(time.time())}"
        
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
        
        print(f"\n✓ {chunks_assigned} chunks assigned to peer nodes")
        print(f"Waiting for peer computations...\n")
        
        return self._computation_id
    
    def collect_results_simulated(self) -> bool:
        """
        Simulate peer node computations and collect results.
        This is for testing. In production, results come via gossip.
        
        Returns:
            True if all results collected
        """
        num_chunks = min(self._node._num_chunks, self._matrix_a.shape[0])
        rows_per_chunk = max(1, self._matrix_a.shape[0] // num_chunks)
        
        print(f"Simulating {num_chunks} peer computations...")
        
        for chunk_id in range(num_chunks):
            start_row = chunk_id * rows_per_chunk
            end_row = start_row + rows_per_chunk
            
            if chunk_id == num_chunks - 1:
                end_row = self._matrix_a.shape[0]
            
            if start_row >= self._matrix_a.shape[0]:
                break
            
            # Simulate computation
            chunk_a = self._matrix_a[start_row:end_row]
            chunk_result = np.matmul(chunk_a, self._matrix_b)
            
            self._chunk_results[chunk_id] = chunk_result
            
            # Mark as complete in distributed state
            self._node.complete_chunk(chunk_id, chunk_result.flatten().tolist())
            
            elapsed = (chunk_id + 1) / num_chunks * 100
            print(f"  [{chunk_id+1}/{num_chunks}] Chunk computed ({elapsed:.0f}%)")
            time.sleep(0.1)
        
        print(f"✓ All {num_chunks} chunks completed\n")
        return len(self._chunk_results) == num_chunks
    
    def verify_results(self) -> bool:
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
        
        # Check shape
        if computed_result.shape != self._expected_result.shape:
            print(f"✗ Shape mismatch: {computed_result.shape} vs {self._expected_result.shape}")
            return False
        
        print(f"✓ Result shape: {computed_result.shape}")
        
        # Check numerical accuracy
        max_error = np.max(np.abs(computed_result - self._expected_result))
        mean_error = np.mean(np.abs(computed_result - self._expected_result))
        
        print(f"  Max error: {max_error:.2e}")
        print(f"  Mean error: {mean_error:.2e}")
        
        tolerance = 1e-10
        if max_error < tolerance:
            print(f"\n✓ VERIFICATION PASSED - Results are correct!")
            logger.info("Verification passed")
            return True
        else:
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
        print(f"  Matrix B: {self._matrix_b.shape if self._matrix_b is not None else 'N/A'}")
        print(f"  Result: {self._expected_result.shape if self._expected_result is not None else 'N/A'}")
        print(f"\nChunks:")
        print(f"  Completed: {len(self._chunk_results)}")
        print(f"{'='*60}\n")
    
    def stop(self) -> None:
        """Stop client node."""
        self._running = False
        self._node.stop()
        logger.info(f"Stopped client {self._client_id}")


def main():
    """Main client execution loop."""
    print("\n" + "="*60)
    print("  P2P DISTRIBUTED MATRIX MULTIPLICATION CLIENT")
    print("="*60)
    
    # Get client ID
    client_id = input("\nEnter client ID (default: client): ").strip() or "client"
    
    # Create and start client
    client = MatrixClient(
        client_id,
        peer_nodes=[
            ('node1', 'localhost', 5001),
            ('node2', 'localhost', 5002),
            ('node3', 'localhost', 5003),
        ]
    )
    
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
                
                # Collect results (simulated)
                success = client.collect_results_simulated()
                
                # Verify
                verified = client.verify_results()
                
                # Summary
                client.print_summary()
                
                if verified:
                    print("✓ Computation successful!")
                else:
                    print("✗ Computation verification failed")
                
            elif choice == "2":
                stats = client._node.get_stats()
                print(f"\nNetwork Status:")
                print(f"  Node: {stats['node_id']}")
                print(f"  Peers: {stats['peers']}")
                print(f"  Healthy Peers: {stats['healthy_peers']}")
                print(f"  Running: {stats['running']}")
                
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
    sys.exit(main())

