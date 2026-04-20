#!/usr/bin/env python3
"""
consistent_hash.py - Consistent Hashing Ring

Implements a consistent hashing ring with virtual nodes for distributed
peer assignment. Used for deterministic mapping of keys/chunks to nodes.
"""

import hashlib
import logging
from typing import List, Optional, Dict, Set
from threading import RLock


logger = logging.getLogger(__name__)


class ConsistentHashRing:
    """Consistent hashing ring for distributed peer discovery."""
    
    def __init__(self, virtual_nodes: int = 160):
        """Initialize the hash ring.
        
        Args:
            virtual_nodes: Number of virtual nodes per physical node
        """
        self._ring: Dict[int, str] = {}
        self._nodes: Set[str] = set()
        self._virtual_nodes = virtual_nodes
        self._lock = RLock()
        
    def _hash_key(self, key: str) -> int:
        """Hash a key to an integer position on the ring."""
        hash_obj = hashlib.md5(key.encode('utf-8'))
        return int(hash_obj.hexdigest(), 16) % (2**31)
    
    def add_node(self, node_id: str) -> None:
        """Add a node to the ring."""
        with self._lock:
            if node_id in self._nodes:
                raise ValueError(f"Node {node_id} already exists")
            
            self._nodes.add(node_id)
            
            for i in range(self._virtual_nodes):
                virtual_key = f"{node_id}:{i}"
                hash_value = self._hash_key(virtual_key)
                self._ring[hash_value] = node_id
            
            logger.info(f"Added node {node_id} with {self._virtual_nodes} virtual nodes")
    
    def remove_node(self, node_id: str) -> None:
        """Remove a node from the ring."""
        with self._lock:
            if node_id not in self._nodes:
                raise ValueError(f"Node {node_id} not found")
            
            self._nodes.discard(node_id)
            keys_to_remove = [h for h, n in self._ring.items() if n == node_id]
            for h in keys_to_remove:
                del self._ring[h]
            
            logger.info(f"Removed node {node_id}")
    
    def get_node(self, key: str) -> Optional[str]:
        """Get the node responsible for a key."""
        with self._lock:
            if not self._ring:
                return None
            
            hash_value = self._hash_key(key)
            sorted_hashes = sorted(self._ring.keys())
            
            for ring_hash in sorted_hashes:
                if ring_hash >= hash_value:
                    return self._ring[ring_hash]
            
            return self._ring[sorted_hashes[0]]
    
    def get_replicas(self, key: str, num_replicas: int) -> List[str]:
        """Get multiple replica nodes for a key."""
        with self._lock:
            if not self._ring:
                return []
            
            replicas = []
            seen = set()
            hash_value = self._hash_key(key)
            sorted_hashes = sorted(self._ring.keys())
            
            start_idx = 0
            for i, h in enumerate(sorted_hashes):
                if h >= hash_value:
                    start_idx = i
                    break
            
            for i in range(len(sorted_hashes)):
                idx = (start_idx + i) % len(sorted_hashes)
                node = self._ring[sorted_hashes[idx]]
                
                if node not in seen:
                    replicas.append(node)
                    seen.add(node)
                    
                    if len(replicas) >= num_replicas:
                        break
            
            return replicas
    
    def get_all_nodes(self) -> List[str]:
        """Get all nodes in the ring."""
        with self._lock:
            return list(self._nodes)
    
    def rebalance_check(self) -> Dict[str, int]:
        """Check load distribution across nodes."""
        with self._lock:
            distribution = {}
            for node in self._nodes:
                count = sum(1 for n in self._ring.values() if n == node)
                distribution[node] = count
            return distribution


__all__ = ['ConsistentHashRing']
