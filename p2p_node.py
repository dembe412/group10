#!/usr/bin/env python3
"""
p2p_node.py - Main P2P Node Implementation

Complete P2P node that combines peer discovery, health monitoring,
distributed state management, and gossip protocol.
"""

import logging
import time
import threading
import random
from typing import Dict, List, Optional, Any
from threading import Thread, RLock, Event

from consistent_hash import ConsistentHashRing
from peer_discovery import PeerDiscovery, PeerInfo
from peer_health import PeerHealthMonitor, PeerHealth
from distributed_state import DistributedState, ChunkState
from gossip_manager import GossipManager

import grpc
import numpy as np

# Set clean formatting for numpy matrices (1 decimal max, omit if integer)
np.set_printoptions(formatter={'float': lambda x: f"{int(x)}" if x % 1 == 0 else f"{x:.1f}"}, suppress=True)

from concurrent import futures
import matrix_pb2
import matrix_pb2_grpc


# Configure logging to both console and a universal file
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
root_logger = logging.getLogger()

# Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

# File Handler (Universal Log)
file_handler = logging.FileHandler('p2p_system.log')
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

root_logger.setLevel(logging.INFO)
logger = logging.getLogger(__name__)


class P2PNodeService(matrix_pb2_grpc.MatrixServiceServicer):
    """gRPC service for P2P Node to handle incoming requests."""
    
    def __init__(self, node):
        self.node = node
        
    def ComputeRows(self, request, context):
        try:
            client_id = request.client_id
            comp_id = request.computation_id
            start_row = request.start_row
            end_row = request.end_row
            cols_a = request.colsA
            cols_b = request.colsB
            
            # Increment active task count
            with self.node._lock:
                self.node._active_tasks += 1
            
            timestamp = time.strftime('%H:%M:%S')
            
            # Reconstruct matrices
            rows = end_row - start_row
            matrix_a = np.array(request.matrixA).reshape(rows, cols_a)
            matrix_b = np.array(request.matrixB).reshape(cols_a, cols_b)
            
            print(f"\n[{timestamp}] {'='*60}")
            print(f"[{self.node._node_id}] 📥 REQUEST FROM: {client_id}")
            print(f"[{self.node._node_id}] 📄 COMP_ID: {comp_id}")
            print(f"[{self.node._node_id}] ⚙️  CALCULATING: Row {start_row} to {end_row-1}")
            
            fmt = {'float_kind': lambda x: f"{int(round(x))}"}
            str_a = np.array2string(matrix_a, formatter=fmt, prefix="   ")
            str_b = np.array2string(matrix_b, formatter=fmt, prefix="   ")
            
            # Show the actual math logic for "actual working" visual
            print(f"[{self.node._node_id}] 🧪 MATH: C[{start_row}:{end_row}, :] = A[{start_row}:{end_row}, {cols_a}] @ B[{cols_a}, {cols_b}]")
            print(f"[{self.node._node_id}] 🔢 ACTUAL MULTIPLICATION:")
            print(f"   Matrix A:\n   {str_a}")
            print(f"   @")
            print(f"   Matrix B:\n   {str_b}")
            
            start_time = time.time()
            # The actual calculation
            result = np.matmul(matrix_a, matrix_b)
            elapsed_ms = (time.time() - start_time) * 1000
            
            str_res = np.array2string(result, formatter=fmt, prefix="   ")
            
            print(f"[{self.node._node_id}] ✅ RESULT READY: {result.shape} block computed")
            print(f"[{self.node._node_id}] 🧮 RESULT VALUES:\n   {str_res}")
            print(f"[{self.node._node_id}] ⏱️  TIME: {elapsed_ms:.2f}ms")
            print(f"{'='*60}\n")
            
            logger.info(f"[OP:COMPUTE] client:{client_id} comp:{comp_id} rows:{start_row}-{end_row} time:{elapsed_ms:.2f}ms")
            
            return matrix_pb2.MatrixReply(
                result=result.flatten().tolist(),
                rows=result.shape[0],
                cols=result.shape[1],
                computation_time_ms=int(elapsed_ms)
            )
        except Exception as e:
            logger.error(f"[ERROR:COMPUTE] {e}", exc_info=True)
            context.set_details(str(e))
            context.set_code(grpc.StatusCode.INTERNAL)
            return matrix_pb2.MatrixReply()
        finally:
            # Decrement active task count and increment total processed
            with self.node._lock:
                self.node._active_tasks = max(0, self.node._active_tasks - 1)
                self.node._computations_processed += 1

    def PeerProbe(self, request, context):
        # Respond to health checks with actual load
        return matrix_pb2.PeerProbeResponse(
            healthy=True,
            peer_suggestions=[p.node_id for p in self.node._discovery.get_all_peers()],
            estimated_load=self.node._active_tasks
        )

    def GossipStateUpdate(self, request, context):
        # Handle incoming gossip updates
        updates_processed = 0
        sender = request.sender_peer
        timestamp = time.strftime('%H:%M:%S')
        
        for update in request.updates:
            success = self.node._state.merge_update(
                update.chunk_id, 
                {"status": update.status, "assigned_to": update.assigned_to, "result": update.result}, 
                dict(request.vector_clock)
            )
            if success:
                updates_processed += 1
        
        if updates_processed > 0:
            print(f"[{timestamp}] [{self.node._node_id}] 🔄 GOSSIP: Synced {updates_processed} updates from {sender}")
            logger.info(f"[OP:SYNC] from:{sender} updates:{updates_processed} vclock:{dict(request.vector_clock)}")
            
        return matrix_pb2.GossipAck(received=True, updates_processed=updates_processed)

    def RequestMissingChunk(self, request, context):
        # Stub for chunk recovery
        chunk = self.node._state.get_chunk(request.chunk_id)
        if chunk and chunk.result:
            return matrix_pb2.ChunkData(chunk_id=request.chunk_id, status=chunk.status, result=chunk.result, found=True)
        return matrix_pb2.ChunkData(chunk_id=request.chunk_id, found=False)


class P2PNode:
    """Main P2P node for distributed matrix computation."""
    
    def __init__(self, node_id: str, host: str = "localhost",
                 port: int = 50051, num_chunks: int = 10):
        """Initialize P2P node."""
        self._node_id = node_id
        self._host = host
        self._port = port
        self._num_chunks = num_chunks
        
        self._hash_ring = ConsistentHashRing(virtual_nodes=160)
        self._discovery = PeerDiscovery(node_id, host, port)
        self._health = PeerHealthMonitor()
        self._state = DistributedState(node_id, num_chunks)
        self._gossip = GossipManager(node_id, max_fanout=3)
        
        self._lock = RLock()
        self._running = False
        self._stop_event = Event()
        self._probe_thread: Optional[Thread] = None
        self._gossip_thread: Optional[Thread] = None
        
        self._computations_processed = 0
        self._bytes_sent = 0
        self._bytes_received = 0
        self._active_tasks = 0 # Track currently executing tasks
        
        logger.info(f"Initialized P2P node: {node_id}@{host}:{port}")
    
    def start(self) -> None:
        """Start the P2P node (background threads)."""
        with self._lock:
            if self._running:
                logger.warning(f"Node {self._node_id} already running")
                return
            
            self._running = True
            self._stop_event.clear()
            
            try:
                self._hash_ring.add_node(self._node_id)
            except ValueError:
                pass
            
            self._probe_thread = Thread(
                target=self._probe_loop,
                daemon=True,
                name=f"{self._node_id}-probe"
            )
            self._probe_thread.start()
            
            self._gossip_thread = Thread(
                target=self._gossip_loop,
                daemon=True,
                name=f"{self._node_id}-gossip"
            )
            self._gossip_thread.start()
            
            # Start gRPC Server
            self._grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
            matrix_pb2_grpc.add_MatrixServiceServicer_to_server(
                P2PNodeService(self), self._grpc_server
            )
            
            # Bind to all interfaces for multi-PC support
            # Use 0.0.0.0 (IPv4) — [::] (IPv6) may be unavailable on Windows
            listen_addr = f"0.0.0.0:{self._port}"
            bound_port = self._grpc_server.add_insecure_port(listen_addr)
            if bound_port == 0:
                raise RuntimeError(
                    f"Failed to bind to address {listen_addr}; "
                    "the port may be in use or the address is unavailable."
                )
            self._grpc_server.start()
            
            logger.info(f"Started P2P node {self._node_id} gRPC server on {listen_addr}")
    
    def stop(self) -> None:
        """Stop the P2P node and background threads."""
        with self._lock:
            if not self._running:
                return
            
            self._running = False
            self._stop_event.set()
        
        if self._probe_thread:
            self._probe_thread.join(timeout=5)
        if self._gossip_thread:
            self._gossip_thread.join(timeout=5)
            
        if hasattr(self, '_grpc_server') and self._grpc_server:
            self._grpc_server.stop(grace=2)
        
        logger.info(f"Stopped P2P node {self._node_id}")
    
    def add_peer(self, node_id: str, host: str, port: int) -> None:
        """Register a peer node."""
        with self._lock:
            if node_id == self._node_id:
                return
            
            try:
                self._hash_ring.add_node(node_id)
            except ValueError:
                pass
            
            self._discovery.register_peer(node_id, host, port)
            logger.info(f"Added peer {node_id}@{host}:{port}")
    
    def assign_chunk(self, chunk_id: int, data: List[float]) -> bool:
        """Assign a chunk for computation with enhanced logging."""
        with self._lock:
            if chunk_id >= self._num_chunks:
                return False
            
            target_node = self._hash_ring.get_node(f"chunk:{chunk_id}")
            if not target_node:
                return False
            
            # Use computation_id in the hash if possible (for future redirect features)
            # But for now, we keep it simple since the client does the heavy lifting
            
            if target_node != self._node_id:
                status = self._health.get_status(target_node)
                if status == PeerHealth.UNHEALTHY:
                    replicas = self._hash_ring.get_replicas(f"chunk:{chunk_id}", 3)
                    for replica in replicas:
                        if self._health.get_status(replica) != PeerHealth.UNHEALTHY:
                            target_node = replica
                            break
            
            self._state.update_chunk(
                chunk_id,
                "assigned",
                assigned_to=target_node
            )
            
            logger.info(f"Assigning chunk {chunk_id} to node {target_node} for computation")
            
            msg_id = f"assign:{chunk_id}:{time.time()}"
            self._gossip.add_message(msg_id, {
                "type": "chunk_assignment",
                "chunk_id": chunk_id,
                "assigned_to": target_node,
                "timestamp": time.time()
            })
            
            return True
    
    def complete_chunk(self, chunk_id: int, result: List[float]) -> None:
        """Mark a chunk as completed with result and enhanced logging."""
        with self._lock:
            self._state.update_chunk(
                chunk_id,
                "completed",
                result=result
            )
            
            vector_clock_version = self._state.get_vector_clock().get(self._node_id, 0)
            logger.info(f"[CHUNK COMPLETE] Chunk {chunk_id} marked as completed (vector_clock: {vector_clock_version})")
            
            msg_id = f"complete:{chunk_id}:{time.time()}"
            self._gossip.add_message(msg_id, {
                "type": "chunk_completion",
                "chunk_id": chunk_id,
                "timestamp": time.time()
            })
    
    def _probe_loop(self) -> None:
        """Background thread for health probing via real gRPC PeerProbe calls."""
        logger.info(f"{self._node_id} probe loop started")
        
        while self._running and not self._stop_event.wait(timeout=5):
            try:
                peers = self._discovery.get_all_peers()
                
                for peer in peers:
                    if not self._health.should_probe(peer.node_id):
                        continue
                    
                    probe_start = time.time()
                    try:
                        channel = grpc.insecure_channel(
                            f"{peer.host}:{peer.port}",
                            options=[("grpc.connect_timeout_ms", 2000)]
                        )
                        response = stub.PeerProbe(req, timeout=2)
                        response_ms = (time.time() - probe_start) * 1000
                        self._health.record_success(
                            peer.node_id, 
                            response_time_ms=response_ms,
                            estimated_load=response.estimated_load
                        )
                        channel.close()
                    except Exception:
                        self._health.record_failure(peer.node_id)
                
                self._discovery.cleanup_expired_peers()
                
            except Exception as e:
                logger.error(f"Error in probe loop: {e}")
    
    def _gossip_loop(self) -> None:
        """Background thread for gossip propagation."""
        logger.info(f"{self._node_id} gossip loop started")
        
        while self._running and not self._stop_event.wait(timeout=2):
            try:
                messages = self._gossip.get_messages_to_send(fanout=3)
                
                if messages:
                    peers = self._discovery.get_all_peers()
                    if peers:
                        targets = random.sample(
                            peers,
                            min(3, len(peers))
                        )
                        
                        for msg_id in messages:
                            target_nodes = [p.node_id for p in targets]
                            self._gossip.mark_sent(msg_id, target_nodes)
                
                self._gossip.cleanup_old_messages()
                
            except Exception as e:
                logger.error(f"Error in gossip loop: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get node statistics."""
        with self._lock:
            completion_stats = self._state.get_completion_stats()
            gossip_stats = self._gossip.get_stats()
            
            return {
                "node_id": self._node_id,
                "peers": self._discovery.get_peer_count(),
                "running": self._running,
                "computations": self._computations_processed,
                "bytes_sent": self._bytes_sent,
                "bytes_received": self._bytes_received,
                **completion_stats,
                **gossip_stats,
                "healthy_peers": len(self._health.get_healthy_peers())
            }
    
    def get_computation_log(self) -> Dict[int, Dict[str, Any]]:
        """Get a log of all computed chunks with their metadata."""
        with self._lock:
            log = {}
            all_chunks = self._state.get_all_chunks()
            
            for chunk_id, chunk_state in all_chunks.items():
                if chunk_state.status == "completed":
                    log[chunk_id] = {
                        "chunk_id": chunk_id,
                        "status": chunk_state.status,
                        "assigned_to": chunk_state.assigned_to,
                        "timestamp": chunk_state.timestamp,
                        "vector_clock": dict(chunk_state.vector_clock),
                        "result_length": len(chunk_state.result) if chunk_state.result else 0,
                    }
            
            logger.info(f"Computation log: {len(log)} chunks completed on {self._node_id}")
            return log


def create_local_cluster(num_nodes: int = 3) -> List[P2PNode]:
    """Create a local test cluster of P2P nodes."""
    nodes = []
    base_port = 50051
    
    for i in range(num_nodes):
        node = P2PNode(
            node_id=f"node-{i}",
            host="localhost",
            port=base_port + i,
            num_chunks=10
        )
        nodes.append(node)
    
    for i, node in enumerate(nodes):
        for j, other in enumerate(nodes):
            if i != j:
                node.add_peer(other._node_id, other._host, other._port)
    
    started = []
    for node in nodes:
        try:
            node.start()
            started.append(node)
        except Exception:
            # Stop already-started nodes to release their ports
            for n in started:
                try:
                    n.stop()
                except Exception:
                    pass
            raise
    
    return nodes


__all__ = ['P2PNode', 'create_local_cluster']
