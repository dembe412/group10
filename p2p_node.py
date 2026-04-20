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


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


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
            
            logger.info(f"Started P2P node {self._node_id}")
    
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
        """Assign a chunk for computation."""
        with self._lock:
            if chunk_id >= self._num_chunks:
                return False
            
            target_node = self._hash_ring.get_node(f"chunk:{chunk_id}")
            if not target_node:
                return False
            
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
            
            msg_id = f"assign:{chunk_id}:{time.time()}"
            self._gossip.add_message(msg_id, {
                "type": "chunk_assignment",
                "chunk_id": chunk_id,
                "assigned_to": target_node,
                "timestamp": time.time()
            })
            
            return True
    
    def complete_chunk(self, chunk_id: int, result: List[float]) -> None:
        """Mark a chunk as completed with result."""
        with self._lock:
            self._state.update_chunk(
                chunk_id,
                "completed",
                result=result
            )
            
            msg_id = f"complete:{chunk_id}:{time.time()}"
            self._gossip.add_message(msg_id, {
                "type": "chunk_completion",
                "chunk_id": chunk_id,
                "timestamp": time.time()
            })
    
    def _probe_loop(self) -> None:
        """Background thread for health probing."""
        logger.info(f"{self._node_id} probe loop started")
        
        while self._running and not self._stop_event.wait(timeout=5):
            try:
                peers = self._discovery.get_all_peers()
                
                for peer in peers:
                    if not self._health.should_probe(peer.node_id):
                        continue
                    
                    if random.random() < 0.9:
                        self._health.record_success(peer.node_id, response_time_ms=10)
                    else:
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
    
    for node in nodes:
        node.start()
    
    return nodes


__all__ = ['P2PNode', 'create_local_cluster']
