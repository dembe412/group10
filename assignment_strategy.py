#!/usr/bin/env python3
"""
assignment_strategy.py - Decentralized Chunk Assignment Strategies

Implements Rendezvous Hashing (Highest Random Weight) for optimal
load distribution in small-to-medium clusters.
"""

import hashlib
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class RendezvousStrategy:
    """
    Implements Highest Random Weight (Rendezvous) Hashing.
    Provides uniform distribution and load-awareness without a central ring.
    """
    
    def __init__(self):
        pass

    def _calculate_weight(self, node_id: str, key: str) -> int:
        """
        Calculate weight for a node/key pair.
        Weight = Hash(node_id + key)
        """
        combined = f"{node_id}:{key}"
        hash_obj = hashlib.md5(combined.encode('utf-8'))
        # Use first 8 bytes for a 64-bit weight
        return int(hash_obj.hexdigest()[:16], 16)

    def get_node(self, key: str, nodes: List[str], node_loads: Dict[str, float] = None) -> Optional[str]:
        """
        Select the best node for a given key using HRW Hashing + Load Multiplier.
        
        Args:
            key: The chunk or task identifier
            nodes: List of available node IDs
            node_loads: Optional dictionary of {node_id: current_load}
            
        Returns:
            The selected node_id or None if no nodes available
        """
        if not nodes:
            return None

        best_node = None
        max_score = -1.0
        
        node_loads = node_loads or {}

        for node_id in nodes:
            # 1. Calculate raw hash weight
            raw_weight = self._calculate_weight(node_id, key)
            
            # 2. Apply load penalty (Load-Awareness)
            # Capacity Score = 1 / (1 + load)
            # This pushes work away from high-load nodes
            load = node_loads.get(node_id, 0.0)
            capacity_score = 1.0 / (1.0 + load)
            
            # 3. Final score
            score = raw_weight * capacity_score
            
            if score > max_score:
                max_score = score
                best_node = node_id
                
        return best_node

    def get_top_n(self, key: str, nodes: List[str], n: int) -> List[str]:
        """Get top N nodes (for replication/failover)."""
        if not nodes:
            return []
            
        weights = []
        for node_id in nodes:
            weight = self._calculate_weight(node_id, key)
            weights.append((weight, node_id))
            
        # Sort by weight descending
        weights.sort(key=lambda x: x[0], reverse=True)
        return [node_id for _, node_id in weights[:n]]
