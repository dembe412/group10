#!/usr/bin/env python3
"""
peer_discovery.py - P2P Peer Discovery

Implements DHT-style peer discovery for the P2P network. Allows nodes to
find each other based on consistent hashing and maintain a peer list.
"""

import logging
import time
from typing import List, Dict, Set, Optional, Tuple
from threading import RLock
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


@dataclass
class PeerInfo:
    """Information about a peer in the network."""
    node_id: str
    host: str
    port: int
    last_seen: float = field(default_factory=time.time)
    
    def is_expired(self, ttl: int = 300) -> bool:
        """Check if peer info has expired."""
        return time.time() - self.last_seen > ttl


class PeerDiscovery:
    """Discovers and manages peers in the P2P network."""
    
    def __init__(self, node_id: str, host: str = "localhost", 
                 port: int = 50051, peer_ttl: int = 300):
        """Initialize peer discovery."""
        self._node_id = node_id
        self._host = host
        self._port = port
        self._peer_ttl = peer_ttl
        self._peers: Dict[str, PeerInfo] = {}
        self._seed_nodes: List[Tuple[str, str, int]] = []
        self._lock = RLock()
        
    def add_seed_node(self, node_id: str, host: str, port: int) -> None:
        """Add a bootstrap seed node."""
        self._seed_nodes.append((node_id, host, port))
        logger.info(f"Added seed node: {node_id}@{host}:{port}")
    
    def register_peer(self, node_id: str, host: str, port: int) -> None:
        """Register or update a peer."""
        with self._lock:
            self._peers[node_id] = PeerInfo(
                node_id=node_id,
                host=host,
                port=port,
                last_seen=time.time()
            )
            logger.debug(f"Registered peer {node_id}@{host}:{port}")
    
    def get_peer(self, node_id: str) -> Optional[PeerInfo]:
        """Get peer information by ID."""
        with self._lock:
            if node_id not in self._peers:
                return None
            
            peer = self._peers[node_id]
            if peer.is_expired(self._peer_ttl):
                del self._peers[node_id]
                return None
            
            return peer
    
    def get_all_peers(self) -> List[PeerInfo]:
        """Get all active peers."""
        with self._lock:
            expired = [nid for nid, peer in self._peers.items()
                      if peer.is_expired(self._peer_ttl)]
            for nid in expired:
                del self._peers[nid]
            
            return list(self._peers.values())
    
    def unregister_peer(self, node_id: str) -> bool:
        """Remove a peer from the registry."""
        with self._lock:
            if node_id in self._peers:
                del self._peers[node_id]
                logger.info(f"Unregistered peer {node_id}")
                return True
            return False
    
    def cleanup_expired_peers(self) -> int:
        """Remove all expired peers from registry."""
        with self._lock:
            expired = [nid for nid, peer in self._peers.items()
                      if peer.is_expired(self._peer_ttl)]
            for nid in expired:
                del self._peers[nid]
            
            if expired:
                logger.info(f"Cleaned up {len(expired)} expired peers")
            return len(expired)
    
    def get_peer_count(self) -> int:
        """Get number of active peers."""
        with self._lock:
            return len([p for p in self._peers.values()
                       if not p.is_expired(self._peer_ttl)])


__all__ = ['PeerDiscovery', 'PeerInfo']
